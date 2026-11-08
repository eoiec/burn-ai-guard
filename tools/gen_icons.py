"""生成 App 图标（PWA / APK 共用）。纯 PIL，无外部资源依赖。"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "app", "icons")
os.makedirs(OUT, exist_ok=True)

S = 512


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def gradient(size, c1, c2, diag=True):
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / (size - 1)
    t = (x * 0.55 + y * 0.45) if diag else y
    t = np.clip(t, 0, 1)[..., None]
    a = np.array(c1, np.float32)
    b = np.array(c2, np.float32)
    return Image.fromarray((a * (1 - t) + b * t).astype(np.uint8), "RGB").convert("RGBA")


def make(size, pad_ratio=0.0, radius_ratio=0.22):
    base = gradient(size, (15, 118, 110), (20, 184, 166))
    # 右上角光晕
    glow = Image.new("L", (size, size), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([size * 0.45, -size * 0.35, size * 1.3, size * 0.55], fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.06))
    white = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    base = Image.composite(white, base, glow.point(lambda v: int(v * 0.45)))

    d = ImageDraw.Draw(base)
    # 白色医疗十字
    cw = size * 0.135          # 笔画宽
    cl = size * 0.46           # 臂长
    cx = cy = size / 2
    r = cw * 0.55
    d.rounded_rectangle([cx - cw / 2, cy - cl / 2, cx + cw / 2, cy + cl / 2], radius=r, fill=(255, 255, 255, 255))
    d.rounded_rectangle([cx - cl / 2, cy - cw / 2, cx + cl / 2, cy + cw / 2], radius=r, fill=(255, 255, 255, 255))
    # 十字上的小水滴（暗示烫伤），用底色镂空
    dr = size * 0.035
    d.ellipse([cx - dr, cy + size * 0.055, cx + dr, cy + size * 0.055 + dr * 1.35], fill=(15, 118, 110, 235))

    if pad_ratio > 0:
        inner = int(size * (1 - pad_ratio * 2))
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ic = base.resize((inner, inner), Image.LANCZOS)
        img.paste(ic, (int(size * pad_ratio), int(size * pad_ratio)), rounded_mask(inner, int(inner * radius_ratio)))
        return img

    base.putalpha(rounded_mask(size, int(size * radius_ratio)))
    return base


if __name__ == "__main__":
    make(512).save(os.path.join(OUT, "icon-512.png"))
    make(192).save(os.path.join(OUT, "icon-192.png"))
    make(180).save(os.path.join(OUT, "apple-touch-icon.png"))
    # maskable：内容留 18% 安全边距，不切圆角（由系统裁切）
    mk = Image.new("RGBA", (512, 512), (15, 118, 110, 255))
    inner = make(512, pad_ratio=0.18, radius_ratio=0.5)
    mk.alpha_composite(inner)
    mk.save(os.path.join(OUT, "icon-maskable-512.png"))
    print("icons ->", sorted(os.listdir(OUT)))
