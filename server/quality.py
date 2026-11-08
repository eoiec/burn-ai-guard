"""图像质量评估：手机拍摄的创面照片经常过暗、模糊、过曝，直接送进模型会产生
"高置信度的错误结论"——这是最危险的失效模式。因此先做质量筛查，不合格的案例
一律转人工并提示重拍，而不是让它生成一个看起来很自信的结论。
"""
import numpy as np
from PIL import Image


def _laplacian_var(gray: np.ndarray) -> float:
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    h, w = gray.shape
    if h < 5 or w < 5:
        return 0.0
    # 直接向量化卷积（步长 1，忽略边缘）
    acc = np.zeros((h - 2, w - 2), dtype=np.float32)
    for dy in range(3):
        for dx in range(3):
            if k[dy, dx] == 0:
                continue
            acc += k[dy, dx] * gray[dy:dy + h - 2, dx:dx + w - 2]
    return float(acc.var())


def assess(img: Image.Image) -> dict:
    """返回 {score: 0..1, flags: [...], metrics: {...}}，score 越高越可用。"""
    rgb = img.convert("RGB")
    # 统一到较小尺寸评估，保证耗时稳定
    small = rgb.copy()
    small.thumbnail((384, 384))
    arr = np.asarray(small, dtype=np.float32) / 255.0
    gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    brightness = float(gray.mean())
    contrast = float(gray.std())
    lap = _laplacian_var(gray)
    blur_score = min(1.0, lap / 0.0025)  # 经验标定：laplacian 方差 0.0025 视为清晰
    over = float((gray > 0.97).mean())
    under = float((gray < 0.05).mean())
    # 色度饱和度：创面判读依赖颜色信息
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = float((mx - mn).mean())

    flags = []
    if brightness < 0.16:
        flags.append("过暗：请在光线充足处（自然光或室内白光）拍摄")
    elif brightness > 0.88:
        flags.append("过曝：请避免闪光灯直射或强反光")
    if blur_score < 0.45:
        flags.append("模糊：请靠近创面、对准对焦后拍摄，避免手抖")
    if contrast < 0.06:
        flags.append("对比度低：创面与背景区分不清")
    if over > 0.18:
        flags.append("高光溢出面积过大：请调整角度避免反光")
    if under > 0.30:
        flags.append("暗部面积过大：请补光后重拍")
    if sat < 0.05:
        flags.append("色彩信息不足：图像可能接近灰度，影响深度判读")

    # 分项打分（0..1），取加权
    s_bright = 1.0 - min(1.0, abs(brightness - 0.5) / 0.42)
    s_blur = blur_score
    s_contrast = min(1.0, contrast / 0.16)
    s_expo = 1.0 - min(1.0, (over + under) / 0.42)
    s_sat = min(1.0, sat / 0.16)
    score = 0.22 * s_bright + 0.34 * s_blur + 0.18 * s_contrast + 0.14 * s_expo + 0.12 * s_sat

    return {
        "score": round(float(score), 4),
        "flags": flags,
        "metrics": {
            "brightness": round(brightness, 4),
            "contrast": round(contrast, 4),
            "blur_score": round(float(blur_score), 4),
            "overexposure_ratio": round(over, 4),
            "underexposure_ratio": round(under, 4),
            "saturation": round(sat, 4),
        },
    }
