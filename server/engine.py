"""推理引擎：预处理 -> 多头前向 -> 温度缩放校准 -> 级联融合 -> 低置信度判定。

这是"AI 防护"的核心：模型不仅要给出类别，还必须给出**可信的概率**，
并在概率不足时主动让渡决策权给医生。
"""
import hashlib
import json
import os
import threading
import time

import numpy as np
from PIL import Image

from . import config, quality as quality_mod

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def fuse(p3, pa, pb):
    a = 0.5 * (p3[:, 0] + pa[:, 0])
    out = np.stack([a, (1 - a) * pb[:, 0], (1 - a) * pb[:, 1]], axis=1)
    out = np.clip(out, 1e-9, 1.0)
    return out / out.sum(axis=1, keepdims=True)


def load_calibration() -> dict:
    if os.path.exists(config.CALIBRATION_PATH):
        with open(config.CALIBRATION_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "temperature": {"head3": 1.0, "head_a": 1.0, "head_b": 1.0},
        "review_threshold": config.DEFAULT_REVIEW_THRESHOLD,
        "calibrated": False,
        "ece": {},
        "metrics": {},
    }


class InferenceEngine:
    """单例式引擎：进程内只加载一次模型。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.model = None
        self.calib = load_calibration()
        self.ready = False
        self.error = None
        self.meta = {}
        self._load()

    # ---------------- 加载 ----------------
    def _load(self):
        try:
            import torch  # noqa: F401
            from .net import BurnNet
        except Exception as e:  # pragma: no cover
            self.error = f"PyTorch 不可用：{e}"
            return
        if not os.path.exists(config.CHECKPOINT_PATH):
            self.error = (
                "未找到模型权重 models/burnnet_resnet50.pt，"
                "请先运行 `python ml/build_demo_model.py` 或 `python ml/train.py`"
            )
            return
        import torch

        ckpt = torch.load(config.CHECKPOINT_PATH, map_location="cpu", weights_only=False)
        net = BurnNet(num_classes=3, pretrained=False)
        net.load_state_dict(ckpt["state_dict"])
        net.eval()
        self.model = net
        self.meta = ckpt.get("meta", {})
        self.calib = {**load_calibration(), **ckpt.get("calibration", {})}
        self.ready = True
        self.error = None

    def reload(self):
        self.ready = False
        self._load()

    # ---------------- 属性 ----------------
    @property
    def threshold(self) -> float:
        return float(self.calib.get("review_threshold", config.DEFAULT_REVIEW_THRESHOLD))

    def model_card(self) -> dict:
        return {
            "ready": self.ready,
            "error": self.error,
            "arch": "ResNet50 + 三头级联（3 分类 / 浅度二分类 / 深二度-三度二分类）",
            "checkpoint": os.path.basename(config.CHECKPOINT_PATH),
            "temperature": self.calib.get("temperature", {}),
            "review_threshold": self.threshold,
            "calibrated": bool(self.calib.get("calibrated")),
            "ece": self.calib.get("ece", {}),
            "metrics": self.calib.get("metrics", {}),
            "sensitivity": self.calib.get("sensitivity", []),
            "train_meta": self.meta,
        }

    # ---------------- 前处理 ----------------
    @staticmethod
    def preprocess(img: Image.Image, size: int = 224) -> np.ndarray:
        img = img.convert("RGB").resize((size, size), Image.BILINEAR)
        x = np.asarray(img, dtype=np.float32) / 255.0
        x = (x - IMAGENET_MEAN) / IMAGENET_STD
        return np.transpose(x, (2, 0, 1))

    # ---------------- 推理 ----------------
    def predict_probs(self, imgs: list) -> dict:
        """批量推理，返回校准前/后的融合概率。"""
        import torch

        batch = np.stack([self.preprocess(i) for i in imgs])
        with self.lock, torch.no_grad():
            logits3, logits_a, logits_b = self.model(torch.from_numpy(batch))
        l3 = logits3.numpy()
        la = logits_a.numpy()
        lb = logits_b.numpy()
        T = self.calib.get("temperature", {})
        t3 = float(T.get("head3", 1.0))
        ta = float(T.get("head_a", 1.0))
        tb = float(T.get("head_b", 1.0))
        raw = fuse(softmax(l3), softmax(la), softmax(lb))
        cal = fuse(softmax(l3 / t3), softmax(la / ta), softmax(lb / tb))
        return {"raw": raw, "calibrated": cal, "logits": {"head3": l3, "head_a": la, "head_b": lb}}

    # ---------------- 决策 ----------------
    def analyze(self, img: Image.Image, meta: dict | None = None) -> dict:
        if not self.ready:
            raise RuntimeError(self.error or "模型未就绪")
        t0 = time.time()
        q = quality_mod.assess(img)
        out = self.predict_probs([img])
        probs = out["calibrated"][0]
        raw = out["raw"][0]
        cls = int(np.argmax(probs))
        conf = float(probs[cls])
        tau = self.threshold

        reasons = []
        if conf < tau:
            reasons.append({
                "code": "low_confidence",
                "severity": 2,
                "text": f"AI 置信度 {conf * 100:.1f}% 低于转人工阈值 {tau * 100:.0f}%，已强制转医生复核",
            })
        if probs[2] >= config.FULL_THICKNESS_RISK_PROB and cls != 2:
            reasons.append({
                "code": "full_thickness_risk",
                "severity": 2,
                "text": f"检测到三度烧伤风险信号（P(三度)={probs[2] * 100:.1f}%），为避免漏诊转人工确认",
            })
        if q["score"] < config.QUALITY_MIN:
            reasons.append({
                "code": "image_quality",
                "severity": 1,
                "text": "图像质量不足，AI 判读可靠性低，已转人工并建议重拍",
            })
        meta = meta or {}
        if meta.get("red_flags"):
            reasons.append({
                "code": "user_red_flag",
                "severity": 2 if any(k in meta["red_flags"] for k in
                                     ("breath", "unconscious", "large_area", "deep_dark", "chemical"))
                else 1,
                "text": "用户自述存在红旗症状，需医生确认后给出处置建议",
            })

        need_review = len(reasons) > 0
        priority = "normal"
        if any(r["severity"] >= 2 for r in reasons) and (
            probs[2] >= config.FULL_THICKNESS_RISK_PROB or conf < tau or meta.get("red_flags")
        ):
            priority = "high"
        if probs[2] >= 0.45:
            priority = "high"

        return {
            "class_index": cls,
            "class_key": config.CLASS_KEYS[cls],
            "class_name": config.CLASS_NAMES[cls],
            "class_label": config.CLASS_LABELS[cls],
            "confidence": round(conf, 4),
            "probs": [round(float(p), 4) for p in probs],
            "raw_probs": [round(float(p), 4) for p in raw],
            "raw_confidence": round(float(raw[cls]), 4),
            "need_review": need_review,
            "review_reasons": reasons,
            "priority": priority,
            "quality": q,
            "threshold": tau,
            "calibrated": bool(self.calib.get("calibrated")),
            "temperature": self.calib.get("temperature", {}),
            "latency_ms": int((time.time() - t0) * 1000),
        }


_ENGINE = None


def get_engine() -> InferenceEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = InferenceEngine()
    return _ENGINE


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
