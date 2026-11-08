"""把 App 图标写入 Android 工程的 mipmap，并做最小 manifest/资源修补。"""
import os
import shutil
import sys

from PIL import Image

PROJ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apk", "android")
ICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "app", "icons", "icon-512.png")

MIPMAP_SIZES = {
    "mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192,
}


def main():
    if not os.path.isdir(PROJ):
        sys.exit("android 工程不存在，请先 npx cap add android")
    icon = Image.open(ICON).convert("RGBA")
    for d, size in MIPMAP_SIZES.items():
        p = os.path.join(PROJ, "app", "src", "main", "res", d)
        os.makedirs(p, exist_ok=True)
        im = icon.resize((size, size), Image.LANCZOS)
        im.save(os.path.join(p, "ic_launcher.png"))
        im.save(os.path.join(p, "ic_launcher_round.png"))
    # 移除自适应图标 XML，回退到 PNG（演示足够）
    anydpi = os.path.join(PROJ, "app", "src", "main", "res", "mipmap-anydpi-v26")
    if os.path.isdir(anydpi):
        shutil.rmtree(anydpi)
        print("removed adaptive icon xml")
    print("launcher icons replaced")


if __name__ == "__main__":
    main()
