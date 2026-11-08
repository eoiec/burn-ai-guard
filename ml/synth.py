"""合成创面图像生成器（Bootstrap 演示数据集）。

为什么需要它
------------
真实临床数据必须走医院数据使用协议 + 伦理审批，项目启动初期拿不到。为了能把
"数据 -> 训练 -> 校准 -> 阈值 -> 转人工流程"整条链路先跑通并做工程验证，这里用
程序化方式合成三类热液烫伤创面图像：

    0 浅度   ：红斑、水肿、表面湿润有反光，边界弥散，可伴小水泡
    1 深二度 ：红白相间（苍白区 + 充血岛）、水泡较多、渗液、湿润反光明显
    2 三度   ：焦痂 / 蜡白皮革样、干燥无光泽、皲裂、边界清楚、外周红晕

同时叠加肤色、光照、模糊、噪点、JPEG 压缩等干扰因素，并让 15% 的样本带上"邻类
部分特征"以模拟临床上真实存在的边界不清病例 —— 这些样本正是低置信度转人工机制
需要抓住的对象。

注意：合成数据只用于工程链路验证，绝不可用于任何临床结论。真实数据到位后，
运行 `python ml/train.py --data data/skin_burn` 即可替换。
"""
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CLASS_KEYS = ["superficial", "deep_partial", "full"]

SKIN_TONES = [
    (238, 200, 176), (226, 184, 158), (214, 170, 142),
    (198, 152, 124), (180, 134, 108), (160, 116, 92),
]

ERYTHEMA = np.array([214, 96, 92], dtype=np.float32)
PALE = np.array([226, 214, 204], dtype=np.float32)
ESCHAR_DARK = np.array([96, 58, 40], dtype=np.float32)
ESCHAR_WAXY = np.array([222, 218, 208], dtype=np.float32)
BLISTER = np.array([236, 228, 196], dtype=np.float32)
EXUDATE = np.array([222, 196, 138], dtype=np.float32)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _blur(arr: np.ndarray, radius: float) -> np.ndarray:
    if radius <= 0:
        return arr
    # 0..1 的浮点掩膜需要先放大到 0..255 再做 uint8 高斯模糊，否则全部被截为 0
    scale = 255.0 if (np.issubdtype(arr.dtype, np.floating) and float(arr.max()) <= 1.5) else 1.0
    a = np.clip(arr * scale, 0, 255).astype(np.uint8)
    if a.ndim == 2:
        im = Image.fromarray(a, mode="L")
        return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / scale
    im = Image.fromarray(a, mode="RGB")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / scale


def _lowfreq(shape, rng, radius):
    n = rng.normal(0, 1, shape).astype(np.float32)
    n = _blur(n * 40 + 128, radius)
    return (n - 128) / 40.0


def _mix(img, color, alpha):
    a = np.clip(alpha, 0, 1)[..., None]
    return img * (1 - a) + np.array(color, dtype=np.float32) * a


def _skin(size, rng):
    tone = np.array(SKIN_TONES[rng.integers(len(SKIN_TONES))], dtype=np.float32)
    img = np.ones((size, size, 3), dtype=np.float32) * tone
    # 大尺度肤色不均
    for _ in range(3):
        img += _lowfreq((size, size), rng, rng.uniform(20, 60))[..., None] * rng.uniform(4, 12)
    # 细颗粒纹理
    img += rng.normal(0, rng.uniform(1.5, 4.0), (size, size, 1)).astype(np.float32)
    # 光照梯度
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    ang = rng.uniform(0, 2 * np.pi)
    grad = (np.cos(ang) * (xs - 0.5) + np.sin(ang) * (ys - 0.5)) * rng.uniform(6, 22)
    img += grad[..., None]
    return np.clip(img, 0, 255)


def _blob(size, rng, sharp=6.0):
    """不规则创面掩膜（0..1）。"""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    mask = np.zeros((size, size), dtype=np.float32)
    n = rng.integers(1, 3)
    for _ in range(n):
        cx, cy = rng.uniform(0.35, 0.65) * size, rng.uniform(0.35, 0.65) * size
        r = rng.uniform(0.22, 0.42) * size
        a, b = r, r * rng.uniform(0.6, 1.0)
        th = rng.uniform(0, np.pi)
        x = (xs - cx) * np.cos(th) + (ys - cy) * np.sin(th)
        y = -(xs - cx) * np.sin(th) + (ys - cy) * np.cos(th)
        wobble = _lowfreq((size, size), rng, rng.uniform(8, 22)) * 0.16
        d = (x / a) ** 2 + (y / b) ** 2 + wobble
        mask = np.maximum(mask, 1.0 / (1.0 + np.exp((d - 1.0) * sharp)))
    return mask


def _circle_layer(size, rng, n, rmin, rmax, blur):
    layer = np.zeros((size, size), dtype=np.float32)
    for _ in range(n):
        cx, cy = rng.uniform(0.3, 0.7) * size, rng.uniform(0.3, 0.7) * size
        r = rng.uniform(rmin, rmax) * size
        ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / r
        layer = np.maximum(layer, np.clip(1.2 - d, 0, 1))
    return _blur(layer, blur) if blur else layer


def _cracks(size, rng, mask):
    im = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(im)
    for _ in range(rng.integers(3, 9)):
        x, y = rng.uniform(0.2, 0.8) * size, rng.uniform(0.2, 0.8) * size
        pts = [(x, y)]
        ang = rng.uniform(0, 2 * np.pi)
        for _ in range(rng.integers(3, 8)):
            ang += rng.normal(0, 0.7)
            x += np.cos(ang) * size * rng.uniform(0.04, 0.11)
            y += np.sin(ang) * size * rng.uniform(0.04, 0.11)
            pts.append((x, y))
        d.line(pts, fill=int(rng.uniform(120, 255)), width=int(rng.integers(1, 3)))
    cracks = np.asarray(im.filter(ImageFilter.GaussianBlur(0.7)), dtype=np.float32) / 255.0
    return np.clip(cracks * mask, 0, 1)


def _post(img, rng, jpeg=True):
    """成像干扰：曝光、对比、模糊、旋转、噪点、JPEG。"""
    img = img * rng.uniform(0.80, 1.14) + rng.uniform(-12, 14)
    mean = img.mean()
    img = (img - mean) * rng.uniform(0.85, 1.18) + mean
    img = np.clip(img, 0, 255)
    if rng.random() < 0.35:
        img = _blur(img, rng.uniform(0.4, 2.2))
    pil = Image.fromarray(img.astype(np.uint8))
    if rng.random() < 0.5:
        fill = tuple(int(v) for v in img.reshape(-1, 3).mean(axis=0))
        pil = pil.rotate(rng.uniform(-12, 12), resample=Image.BILINEAR, expand=False, fillcolor=fill)
    arr = np.asarray(pil, dtype=np.float32) + rng.normal(0, rng.uniform(0.6, 2.4), (img.shape[0], img.shape[1], 1))
    arr = np.clip(arr, 0, 255)
    pil = Image.fromarray(arr.astype(np.uint8))
    if jpeg:
        import io

        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=int(rng.integers(55, 92)))
        buf.seek(0)
        pil = Image.open(buf).convert("RGB")
    return pil


# --------------------------------------------------------------------------- #
# 三类创面
# --------------------------------------------------------------------------- #
def gen_superficial(size, rng, partial=None):
    img = _skin(size, rng)
    m = _blob(size, rng, sharp=rng.uniform(3.0, 6.0))
    m = _blur(m, rng.uniform(5, 14))
    img = _mix(img, ERYTHEMA, m * rng.uniform(0.42, 0.72))
    # 湿润反光
    gloss = _lowfreq((size, size), rng, 10)
    gloss = np.clip(gloss, 0, 1) ** 2
    img = img + (gloss * m * rng.uniform(14, 30))[..., None]
    # 浅Ⅱ度小水泡
    if rng.random() < 0.55:
        b = _circle_layer(size, rng, int(rng.integers(1, 4)), 0.02, 0.055, 1.5) * m
        img = _mix(img, BLISTER, b * rng.uniform(0.25, 0.45))
        img = img + (b * 12)[..., None]
    if partial == 1:  # 与深二度的过渡表现
        p = np.clip(_lowfreq((size, size), rng, 14), 0, 1)
        img = _mix(img, PALE, np.clip(p - 0.35, 0, 1) * m * 0.30)
    return _post(img, rng)


def gen_deep_partial(size, rng, partial=None):
    img = _skin(size, rng)
    m = _blob(size, rng, sharp=rng.uniform(5.0, 9.0))
    m = _blur(m, rng.uniform(3, 8))
    img = _mix(img, ERYTHEMA, m * rng.uniform(0.40, 0.62))
    # 红白相间的苍白区
    p = np.clip(_lowfreq((size, size), rng, rng.uniform(9, 20)), -0.2, 1.2)
    patch = np.clip((p - 0.05) * 1.6, 0, 1) * m
    img = _mix(img, PALE, patch * rng.uniform(0.35, 0.62))
    # 多个水泡 + 渗液
    b = _circle_layer(size, rng, int(rng.integers(2, 6)), 0.025, 0.075, 1.2) * m
    img = _mix(img, BLISTER, b * rng.uniform(0.30, 0.55))
    ex = _circle_layer(size, rng, int(rng.integers(1, 3)), 0.02, 0.05, 3.0) * m
    img = _mix(img, EXUDATE, ex * rng.uniform(0.15, 0.30))
    # 湿润镜面反光
    gloss = np.clip(_lowfreq((size, size), rng, 6), 0.2, 1.4)
    img = img + (gloss * m * rng.uniform(10, 26))[..., None]
    img = img + (b * 18)[..., None]
    if partial == 2:
        img = _mix(img, ESCHAR_DARK, m * 0.18)
    return _post(img, rng)


def gen_full(size, rng, partial=None):
    img = _skin(size, rng)
    m = _blob(size, rng, sharp=rng.uniform(12.0, 26.0))  # 边界清楚
    m = _blur(m, rng.uniform(0.8, 3.0))
    dark = rng.random() < 0.68
    core = ESCHAR_DARK if dark else ESCHAR_WAXY
    img = _mix(img, core, m * rng.uniform(0.62, 0.92))
    # 干燥粗糙：轻度去饱和（保留焦痂的棕褐色调）+ 高频纹理
    gray = img.mean(axis=2, keepdims=True)
    img = img * (1 - 0.32 * m[..., None]) + gray * (0.32 * m[..., None])
    tex = rng.normal(0, rng.uniform(5, 12), (size, size, 1)).astype(np.float32)
    img = img + tex * m[..., None]
    # 皲裂纹
    cr = _cracks(size, rng, m)
    img = img * (1 - 0.55 * cr[..., None])
    # 外周红晕
    ring = np.clip(_blur(m, rng.uniform(6, 14)) - m, 0, 1)
    img = _mix(img, ERYTHEMA, ring * rng.uniform(0.25, 0.45))
    if partial == 1:  # 边缘残留深二度湿润组织
        p = np.clip((_blur(m, 5) - m) * 2.0, 0, 1)
        img = _mix(img, PALE, p * 0.25)
    return _post(img, rng)


GENERATORS = [gen_superficial, gen_deep_partial, gen_full]


# --------------------------------------------------------------------------- #
# 数据集构建
# --------------------------------------------------------------------------- #
def build(out_dir, n_per_class=400, size=256, seed=2026, split=(0.7, 0.15, 0.15)):
    rng = np.random.default_rng(seed)
    random.seed(seed)
    counts = {}
    for ci, gen in enumerate(GENERATORS):
        idx = rng.permutation(n_per_class)
        n_train = int(round(n_per_class * split[0]))
        n_val = int(round(n_per_class * split[1]))
        parts = {
            "train": idx[:n_train],
            "val": idx[n_train:n_train + n_val],
            "test": idx[n_train + n_val:],
        }
        for sp, ids in parts.items():
            d = os.path.join(out_dir, sp, CLASS_KEYS[ci])
            os.makedirs(d, exist_ok=True)
            for k in ids:
                # 15% 边界不清样本：混入邻类部分特征，标签仍为真实类别
                partial = None
                if rng.random() < 0.15:
                    partial = ci + 1 if (ci == 0 or (ci == 1 and rng.random() < 0.5)) else ci - 1
                    partial = max(0, min(2, partial))
                img = gen(size, rng, partial=partial)
                img.save(os.path.join(d, f"{CLASS_KEYS[ci]}_{k:05d}.jpg"), quality=92)
        counts[CLASS_KEYS[ci]] = {sp: len(v) for sp, v in parts.items()}
    return counts


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "synthetic")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    c = build(os.path.abspath(out), n_per_class=n)
    print("合成数据集完成：", c)
