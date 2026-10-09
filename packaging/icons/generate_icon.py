"""One-off script: draws the Simple-Ripper app icon and exports every
size/format the platform installers need. Not part of the app itself and
not run automatically — re-run manually if the icon design ever changes.

Usage: venv/bin/python packaging/icons/generate_icon.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).parent
APP_RESOURCES = OUT_DIR.parent.parent / "src" / "simple_ripper" / "gui" / "resources"
SIZE = 1024

# "Disc and arrow" — a silver CD with an arrow sweeping down out of it,
# on a deep teal tile so it sits beside Simple-Jukebox's plum one.
TEAL = (18, 84, 96, 255)
SILVER = (226, 232, 236, 255)
SHEEN = (196, 214, 224, 255)
HUB = (18, 84, 96, 255)
ORANGE = (240, 140, 50, 255)


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = SIZE * 0.06

    draw.rounded_rectangle(
        [margin, margin, SIZE - margin, SIZE - margin],
        radius=SIZE * 0.22,
        fill=TEAL,
    )

    # The disc, sitting a little high to leave room for the arrow.
    cx, cy = SIZE * 0.5, SIZE * 0.43
    r = SIZE * 0.29
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SILVER)
    for frac in (0.86, 0.70):
        gr = r * frac
        draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], outline=SHEEN, width=max(2, int(SIZE * 0.008)))
    # A light-catching wedge, like the rainbow sheen on a real CD.
    draw.pieslice([cx - r * 0.92, cy - r * 0.92, cx + r * 0.92, cy + r * 0.92], 200, 245, fill=(255, 255, 255, 255))
    hr = r * 0.30
    draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=SHEEN)
    hole = r * 0.13
    draw.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=HUB)

    # The arrow: a thick shaft from the disc's centre down to a chevron.
    shaft_w = SIZE * 0.085
    top = cy + r * 0.45
    tip = SIZE * 0.86
    head_h = SIZE * 0.13
    draw.rounded_rectangle([cx - shaft_w / 2, top, cx + shaft_w / 2, tip - head_h * 0.6], radius=shaft_w / 2, fill=ORANGE)
    head_w = SIZE * 0.22
    draw.polygon([(cx - head_w / 2, tip - head_h), (cx + head_w / 2, tip - head_h), (cx, tip)], fill=ORANGE)

    return img


def main() -> None:
    icon = draw_icon()
    master_png = OUT_DIR / "icon.png"
    icon.save(master_png)
    print(f"wrote {master_png}")

    APP_RESOURCES.mkdir(parents=True, exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(APP_RESOURCES / "icon.png")
    print(f"wrote {APP_RESOURCES / 'icon.png'}")

    # Windows .ico (multi-resolution)
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_path = OUT_DIR / "icon.ico"
    icon.save(ico_path, sizes=[(s, s) for s in ico_sizes])
    print(f"wrote {ico_path}")

    # Linux: a few common hicolor sizes as plain PNGs.
    for s in (16, 32, 48, 64, 128, 256, 512):
        icon.resize((s, s), Image.LANCZOS).save(OUT_DIR / f"icon_{s}.png")
    print("wrote icon_16..512.png")

    # macOS .icns: iconutil on a Mac; elsewhere Pillow's own ICNS writer is
    # good enough to commit, so CI on macOS always has a file to use.
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = OUT_DIR / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for s in (16, 32, 128, 256, 512):
            icon.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            icon.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_DIR / "icon.icns")],
            check=True,
        )
        for f in iconset.iterdir():
            f.unlink()
        iconset.rmdir()
    else:
        icon.save(OUT_DIR / "icon.icns")
    print(f"wrote {OUT_DIR / 'icon.icns'}")


if __name__ == "__main__":
    main()
