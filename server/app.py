"""Flask 后端：AI 判读 API + 低置信度转人工工单流 + 医生端接口 + 静态前端。

启动：python -m server.app   (默认 http://127.0.0.1:5000)
"""
import hmac
import io
import json
import os
import time
import uuid
from hashlib import sha256
from functools import wraps

from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory

from . import advice as advice_mod
from . import config, db
from .engine import get_engine, sha256_of
from .quality import assess as quality_assess

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES


@app.after_request
def _cors(resp):
    """允许 App 壳（Capacitor 内为 http://localhost）与局域网 PWA 跨域调用 API。"""
    if request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "86400"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/<path:_p>", methods=["OPTIONS"])
def _api_preflight(_p):
    return ("", 204)

SECRET = os.environ.get("BURN_SECRET", "burn-guard-demo-secret").encode()
TOKEN_TTL = 12 * 3600


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def make_token(username: str) -> str:
    exp = int(time.time()) + TOKEN_TTL
    body = f"{username}.{exp}"
    sig = hmac.new(SECRET, body.encode(), sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    try:
        username, exp, sig = token.split(".")
        body = f"{username}.{exp}"
        good = hmac.new(SECRET, body.encode(), sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good) or int(exp) < time.time():
            return None
        return username
    except Exception:
        return None


def require_doctor(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        token = request.headers.get("X-Token", "")
        user = verify_token(token)
        if not user:
            return jsonify({"ok": False, "error": "未登录或登录已过期"}), 401
        acct = next((x for x in config.DOCTOR_ACCOUNTS if x["username"] == user), None)
        if not acct:
            return jsonify({"ok": False, "error": "账号不存在"}), 401
        g.doctor = acct
        return fn(*a, **kw)

    return wrapper


def parse_meta(raw) -> dict:
    if not raw:
        return {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}
    allowed = {"age_group", "site", "area_hand", "cause", "duration_min", "red_flags", "note"}
    meta = {k: v for k, v in meta.items() if k in allowed}
    if isinstance(meta.get("red_flags"), str):
        meta["red_flags"] = [x for x in meta["red_flags"].split(",") if x]
    return meta


# --------------------------------------------------------------------------- #
# 静态页面
# --------------------------------------------------------------------------- #
@app.get("/")
def page_user():
    return send_from_directory(config.FRONTEND_DIR, "index.html")


@app.get("/doctor")
def page_doctor():
    return send_from_directory(config.FRONTEND_DIR, "doctor.html")


@app.get("/model")
def page_model():
    return send_from_directory(config.FRONTEND_DIR, "model.html")


@app.get("/sus")
def page_sus():
    return send_from_directory(config.FRONTEND_DIR, "sus.html")


@app.get("/app")
@app.get("/app/")
def page_app():
    """移动端 App（可安装 PWA，也是 Android 壳内的本地资源）。"""
    resp = send_from_directory(os.path.join(config.FRONTEND_DIR, "app"), "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/app/<path:p>")
def app_static(p):
    resp = send_from_directory(os.path.join(config.FRONTEND_DIR, "app"), p)
    if p.endswith((".webmanifest", ".json")):
        resp.headers["Content-Type"] = "application/manifest+json"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/assets/<path:p>")
def assets(p):
    return send_from_directory(os.path.join(config.FRONTEND_DIR, "assets"), p)


@app.get("/reports/<path:p>")
def reports(p):
    return send_from_directory(config.REPORT_DIR, p)


@app.get("/uploads/<path:p>")
def uploads(p):
    return send_from_directory(config.UPLOAD_DIR, p)


# --------------------------------------------------------------------------- #
# 用户端 API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    eng = get_engine()
    return jsonify({
        "ok": True,
        "model_ready": eng.ready,
        "model_error": eng.error,
        "review_threshold": eng.threshold,
        "classes": config.CLASS_LABELS,
        "time": time.time(),
    })


@app.post("/api/analyze")
def analyze():
    eng = get_engine()
    if not eng.ready:
        return jsonify({"ok": False, "error": eng.error or "模型未就绪"}), 503

    if "image" not in request.files:
        return jsonify({"ok": False, "error": "缺少图像文件字段 image"}), 400
    f = request.files["image"]
    ext = os.path.splitext(f.filename or "x.jpg")[1].lower() or ".jpg"
    if ext not in config.ALLOWED_EXT:
        return jsonify({"ok": False, "error": f"不支持的图像格式 {ext}"}), 400
    raw = f.read()
    if not raw:
        return jsonify({"ok": False, "error": "空文件"}), 400

    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception as e:
        return jsonify({"ok": False, "error": f"图像解析失败：{e}"}), 400

    meta = parse_meta(request.form.get("meta"))
    device_id = (request.form.get("device_id") or "anonymous")[:64]

    result = eng.analyze(img, meta)

    cid = db.new_id("C")
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    fname = f"{cid}{ext if ext != '.heic' else '.jpg'}"
    path = os.path.join(config.UPLOAD_DIR, fname)
    img.convert("RGB").save(path, quality=92)

    advice = advice_mod.build_advice(
        result["class_index"], meta, result["need_review"], result["need_review"]
    )

    ticket_id = None
    status = "auto"
    if result["need_review"]:
        status = "pending_review"
        ticket_id = db.create_ticket(cid, result["priority"])

    db.create_case(
        id=cid,
        device_id=device_id,
        created_at=time.time(),
        image_path=fname,
        image_sha256=sha256_of(path),
        width=img.width,
        height=img.height,
        ai_class=result["class_index"],
        ai_probs=result["probs"],
        ai_raw_probs=result["raw_probs"],
        ai_confidence=result["confidence"],
        temperature=float(result["temperature"].get("head3", 1.0)),
        quality_score=result["quality"]["score"],
        quality_flags=result["quality"]["flags"],
        review_reasons=result["review_reasons"],
        user_meta=meta,
        status=status,
        final_class=result["class_index"],
        advice_level=advice["level"],
        ticket_id=ticket_id,
    )
    db.audit("user", "analyze", cid, {
        "class": result["class_index"], "conf": result["confidence"],
        "need_review": result["need_review"], "latency_ms": result["latency_ms"],
    })

    return jsonify({
        "ok": True,
        "case_id": cid,
        "status": status,
        "ticket_id": ticket_id,
        "ai": result,
        "advice": advice,
        "image_url": f"/uploads/{fname}",
    })


@app.get("/api/samples")
def demo_samples():
    """为可用性测试提供示例创面图像（取自演示数据集测试集，每类 2 张）。"""
    demo_dir = os.path.join(config.REPORT_DIR, "demo")
    os.makedirs(demo_dir, exist_ok=True)
    out = []
    src_root = os.path.join(config.DATA_DIR, "synthetic", "test")
    for ci, key in enumerate(config.CLASS_KEYS):
        cd = os.path.join(src_root, key)
        if not os.path.isdir(cd):
            continue
        files = sorted(f for f in os.listdir(cd) if f.lower().endswith((".jpg", ".png")))[:2]
        for fi, fn in enumerate(files):
            dst = f"sample_{key}_{fi}.jpg"
            dst_path = os.path.join(demo_dir, dst)
            if not os.path.exists(dst_path):
                from PIL import Image

                Image.open(os.path.join(cd, fn)).convert("RGB").save(dst_path, quality=88)
            out.append({"url": f"/reports/demo/{dst}", "label": config.CLASS_NAMES[ci]})
    return jsonify({"ok": True, "samples": out[:6]})


@app.get("/api/case/<cid>")
def case_detail(cid):
    case = db.get_case(cid)
    if not case:
        abort(404)
    eng = get_engine()
    final_class = case["final_class"] if case["final_class"] is not None else case["ai_class"]
    meta = case.get("user_meta") or {}
    reviews = db.get_reviews(cid)
    need_review = bool(case.get("review_reasons"))
    advice = advice_mod.build_advice(
        final_class, meta, need_review, case["status"] in ("pending_review", "in_review")
    )
    return jsonify({
        "ok": True,
        "case": {
            **case,
            "image_url": f"/uploads/{case['image_path']}",
            "class_name": config.CLASS_NAMES[final_class],
            "class_label": config.CLASS_LABELS[final_class],
            "ai_class_name": config.CLASS_NAMES[case["ai_class"]],
        },
        "reviews": reviews,
        "advice": advice,
        "threshold": eng.threshold,
    })


# --------------------------------------------------------------------------- #
# 医生端 API
# --------------------------------------------------------------------------- #
@app.post("/api/doctor/login")
def doctor_login():
    body = request.get_json(silent=True) or {}
    u = (body.get("username") or "").strip()
    p = body.get("password") or ""
    for acct in config.DOCTOR_ACCOUNTS:
        if acct["username"] == u and hmac.compare_digest(acct["password"], p):
            db.audit(u, "doctor_login")
            return jsonify({
                "ok": True,
                "token": make_token(u),
                "doctor": {"name": acct["name"], "title": acct["title"], "role": acct["role"]},
            })
    return jsonify({"ok": False, "error": "账号或口令不正确"}), 401


@app.get("/api/doctor/tickets")
@require_doctor
def doctor_tickets():
    status = request.args.get("status") or None
    rows = db.list_tickets(status)
    now = time.time()
    for r in rows:
        r["overdue"] = bool(r.get("sla_due_at") and r["status"] != "done" and now > r["sla_due_at"])
        r["waited_s"] = int(now - r["created_at"])
        r["image_url"] = f"/uploads/{r['image_path']}"
        r["ai_class_name"] = config.CLASS_NAMES[r["ai_class"]]
        r["final_class_name"] = config.CLASS_NAMES[r["final_class"]] if r.get("final_class") is not None else None
    return jsonify({"ok": True, "tickets": rows, "doctor": g.doctor["name"]})


@app.post("/api/doctor/tickets/<tid>/claim")
@require_doctor
def doctor_claim(tid):
    ok = db.claim_ticket(tid, g.doctor["name"])
    if not ok:
        return jsonify({"ok": False, "error": "工单已被领取或已完成"}), 409
    db.audit(g.doctor["name"], "claim_ticket", None, {"ticket": tid})
    return jsonify({"ok": True})


@app.post("/api/doctor/tickets/<tid>/review")
@require_doctor
def doctor_review(tid):
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    if decision not in ("confirm", "correct"):
        return jsonify({"ok": False, "error": "decision 必须是 confirm 或 correct"}), 400
    corrected = body.get("corrected_class")
    if decision == "correct":
        try:
            corrected = int(corrected)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "修正结论必须选择类别"}), 400
        if corrected not in (0, 1, 2):
            return jsonify({"ok": False, "error": "类别取值 0/1/2"}), 400
    note = (body.get("note") or "").strip()[:1000]
    try:
        out = db.finish_ticket(tid, g.doctor["name"], decision, corrected, note)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    db.audit(g.doctor["name"], "review", out["case_id"], {
        "ticket": tid, "decision": decision, "final_class": out["final_class"], "note": note,
    })
    return jsonify({"ok": True, **out})


@app.get("/api/doctor/stats")
@require_doctor
def doctor_stats():
    s = db.stats()
    s.update(db.sus_stats())
    return jsonify({"ok": True, "stats": s})


# --------------------------------------------------------------------------- #
# 模型卡 / 可用性
# --------------------------------------------------------------------------- #
@app.get("/api/model")
def model_card():
    eng = get_engine()
    card = eng.model_card()
    card["classes"] = config.CLASS_LABELS
    card["risk_prob_threshold"] = config.FULL_THICKNESS_RISK_PROB
    card["quality_min"] = config.QUALITY_MIN
    card["reports"] = [
        f for f in ["reliability.png", "tradeoff.png", "confusion_matrix.png", "training_curves.png"]
        if os.path.exists(os.path.join(config.REPORT_DIR, f))
    ]
    return jsonify({"ok": True, "card": card})


@app.post("/api/sus")
def submit_sus():
    body = request.get_json(silent=True) or {}
    answers = body.get("answers") or []
    try:
        answers = [int(x) for x in answers]
    except Exception:
        return jsonify({"ok": False, "error": "答案格式错误"}), 400
    if len(answers) != 10 or any(a < 1 or a > 5 for a in answers):
        return jsonify({"ok": False, "error": "SUS 需要 10 道题，每题 1–5 分"}), 400
    # 标准 SUS 计分：奇数题 (score-1)，偶数题 (5-score)，求和 * 2.5
    total = sum((a - 1) if i % 2 == 0 else (5 - a) for i, a in enumerate(answers)) * 2.5
    sid = db.save_sus(
        (body.get("device_id") or "anonymous")[:64], answers, total,
        float(body.get("task_time_s") or 0), bool(body.get("completed", True)),
    )
    db.audit("user", "sus_submit", None, {"sus": sid, "total": total})
    return jsonify({"ok": True, "id": sid, "total": total, "stats": db.sus_stats()})


@app.get("/api/sus/stats")
def sus_stats():
    return jsonify({"ok": True, "stats": db.sus_stats()})


@app.errorhandler(413)
def too_large(_):
    return jsonify({"ok": False, "error": "图像超过 12MB，请压缩后重试"}), 413


def lan_ip() -> str:
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    db.init_db()
    eng = get_engine()
    ip = lan_ip()
    print("=" * 68)
    print("  AI 防护 · 家庭热液烫伤深度三分类与低置信度转人工机制")
    print("=" * 68)
    print(f"  模型状态：{'就绪' if eng.ready else '未就绪 -> ' + str(eng.error)}")
    print(f"  转人工阈值：{eng.threshold * 100:.0f}%   温度参数：{eng.calib.get('temperature')}")
    print(f"  用户端：http://127.0.0.1:5000/")
    print(f"  手机 App：http://{ip}:5000/app   （同 WiFi 手机浏览器打开，可添加到主屏幕）")
    print(f"  医生端：http://127.0.0.1:5000/doctor  （doctor / burn2026）")
    print(f"  模型卡：http://127.0.0.1:5000/model")
    print("=" * 68)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)


if __name__ == "__main__":
    main()
