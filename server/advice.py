"""分级送医建议引擎（规则 + 安全升级）。

临床逻辑
--------
1) 基础分级由 AI 判读类别给出：浅度 -> 居家处理；深二度 -> 尽快就医；三度 -> 立即急救送医。
2) 再用"红旗条件"做**只升不降**的安全升级（fail-safe）：特殊部位、大面积、特殊人群、
   化学/电击/吸入性损伤等，任一条命中即至少升到"尽快就医"，严重者直接升到"立即急救"。
   —— 这是"AI 不确定时让渡决策权"原则在建议层的体现：宁可多送一次医，不可漏掉一次重症。
3) 未复核的低置信度结果，标题必须写明"初步判读，仅供参考"。
"""

RED_FLAG_ITEMS = [
    ("breath", "呼吸困难 / 声音嘶哑 / 烟灰痰（可能吸入性损伤）", 2),
    ("unconscious", "意识不清、面色苍白、四肢湿冷（可能休克）", 2),
    ("large_area", "成人烫伤面积超过 10 个手掌 / 儿童超过 5 个手掌", 2),
    ("deep_dark", "创面焦黑、发白如皮革、触之无痛（提示全层烧伤）", 2),
    ("chemical", "化学灼伤或电击伤", 2),
    ("stuck_cloth", "衣物与创面粘连无法分离", 1),
    ("chronic", "糖尿病、免疫抑制、心脏病等基础疾病", 1),
]

LEVELS = [
    {
        "label": "居家处理",
        "short": "可先居家观察",
        "color": "#16a34a",
        "headline": "暂不需要急诊，按下列步骤居家处理并密切观察",
        "eta": "24–48 小时内自行观察创面变化",
    },
    {
        "label": "尽快就医",
        "short": "建议 24 小时内就医",
        "color": "#ea9a12",
        "headline": "建议 24 小时内到烧伤科 / 急诊外科就诊",
        "eta": "6–24 小时内完成就诊，不要拖延过夜",
    },
    {
        "label": "立即急救送医",
        "short": "立即拨打 120",
        "color": "#dc2626",
        "headline": "立即拨打 120 或前往最近医院急诊，不要自行观察",
        "eta": "立即（黄金 1 小时内）",
    },
]

FIRST_AID_COMMON = [
    "立即脱离热源，小心剪开（不要撕扯）创面周围衣物。",
    "用 15–25℃ 流动清水持续冲洗 15–30 分钟，直到疼痛明显缓解；不要用冰块直接敷。",
    "不要在创面涂抹牙膏、酱油、香油、紫药水或任何偏方，会掩盖伤情并增加感染。",
    "用无菌纱布或清洁干燥的保鲜膜松散覆盖创面，不要包得过紧。",
    "注意保暖、抬高患处（高于心脏水平）以减轻肿胀。",
]

AVOID = [
    "不要挑破水泡（除非医生处理），破溃会增加感染风险。",
    "不要用酒精、碘酒、双氧水反复冲洗深部创面。",
    "不要给患者大量喝白开水，大面积烫伤应少量多次口服补液盐。",
]

WATCH_SIGNS = [
    "创面颜色由红转白 / 变暗、出现焦痂",
    "疼痛突然减轻甚至完全无痛（提示深度加重）",
    "出现发热 ≥38℃、创面渗液变黄绿或有异味（感染征象）",
    "尿量明显减少、口渴、头晕（脱水 / 休克早期）",
]


def _worst(a: int, b: int) -> int:
    return a if a >= b else b


def build_advice(ai_class: int, meta: dict | None, need_review: bool, review_pending: bool) -> dict:
    """返回结构化建议。ai_class 为 0/1/2。"""
    meta = meta or {}
    level = int(ai_class)  # 0/1/2 与紧急度等级天然对齐

    flags = []
    flag_keys = set(meta.get("red_flags") or [])
    for key, text, lvl in RED_FLAG_ITEMS:
        if key in flag_keys:
            flags.append({"key": key, "text": text, "level": lvl})
            level = _worst(level, lvl)

    site = meta.get("site") or ""
    if site in ("face_neck", "hand_foot", "perineum", "joint"):
        flags.append({"key": "special_site", "text": "特殊部位（面部/颈/手足/关节/会阴），易致功能障碍", "level": 1})
        level = _worst(level, 1)

    age = meta.get("age_group") or ""
    if age in ("infant", "elder"):
        flags.append({"key": "vulnerable_age", "text": "5 岁以下或 70 岁以上，皮肤薄、代偿能力差", "level": 1})
        level = _worst(level, 1)

    area = meta.get("area_hand")
    try:
        area = int(area) if area is not None else 0
    except (TypeError, ValueError):
        area = 0
    if area >= 3:
        flags.append({"key": "area_gt10", "text": "自评面积 >10 个手掌（成人），已达重度烧伤标准", "level": 2})
        level = _worst(level, 2)
    elif area == 2:
        flags.append({"key": "area_5_10", "text": "自评面积 5–10 个手掌，需医生评估补液与包扎", "level": 1})
        level = _worst(level, 1)

    cause = meta.get("cause") or ""
    if cause in ("chemical", "electrical"):
        flags.append({"key": "cause_high_risk", "text": "化学灼伤 / 电击伤，损伤常深于外观", "level": 2})
        level = _worst(level, 2)

    lv = LEVELS[level]
    escalated = level > int(ai_class)

    actions = list(FIRST_AID_COMMON)
    if level == 0:
        actions.insert(0, "继续用流动清水冲洗至疼痛缓解，之后保持创面清洁干燥。")
        actions.append("若 48 小时内出现水泡、创面变白或疼痛加剧，请立即就医。")
    elif level == 1:
        actions.insert(0, "冲洗后覆盖清洁敷料，尽快前往有烧伤专科的医院（优先 6 小时内）。")
        actions.append("就诊前不要进食大量食物，便于可能需要的外科处理。")
    else:
        actions.insert(0, "边冲洗边等待急救车；若创面在四肢，冲洗同时抬高患肢。")
        actions.append("大面积烫伤注意保暖，用干净被单包裹躯干，避免体温过低。")
        actions.append("如患者口渴，可少量多次口服补液盐，不要一次大量饮水。")

    headline = lv["headline"]
    if need_review and review_pending:
        headline = "初步判读结果（仅供参考）：" + headline
    elif need_review:
        headline = "初步判读结果（仅供参考）：" + headline

    return {
        "level": level,
        "label": lv["label"],
        "short": lv["short"],
        "color": lv["color"],
        "headline": headline,
        "eta": lv["eta"],
        "escalated": escalated,
        "escalation_reasons": flags,
        "actions": actions,
        "avoid": AVOID,
        "watch": WATCH_SIGNS,
        "disclaimer": (
            "本结果由 AI 影像模型给出，不能替代医生面诊。"
            "低置信度或存在红旗症状的案例已转入医生复核流程，请以医生复核结论为准。"
        ),
    }
