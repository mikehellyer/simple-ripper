# -*- mode: python ; coding: utf-8 -*-
"""Shared PyInstaller spec for all three platforms.

Built with: pyinstaller packaging/pyinstaller/simple-ripper.spec
(run from the repo root, with the venv's PySide6 etc. on the path).
"""
import re
import sys
from pathlib import Path

# PySide6 6.12 drops a reference to None on every call to a method that
# returns nothing (QSlider.setValue, QLabel.setText, …). Before Python
# 3.12 None can actually be freed, so a few minutes of playback-progress
# updates used them all up and the app died with "Fatal Python error:
# none_dealloc". From 3.12 None is immortal and the bug is harmless —
# refuse to build a frozen app on anything older.
if sys.version_info < (3, 12):
    raise SystemExit(
        f"Simple-Ripper must be built with Python 3.12+ (this is {sys.version.split()[0]}) "
        "— see the comment in simple-ripper.spec"
    )

root = Path(SPECPATH).resolve().parent.parent
src = root / "src"
icons = root / "packaging" / "icons"

version = re.search(
    r'__version__ = "([^"]+)"', (src / "simple_ripper" / "__init__.py").read_text()
).group(1)

if sys.platform == "win32":
    icon_path = str(icons / "icon.ico")
elif sys.platform == "darwin":
    icon_path = str(icons / "icon.icns")
else:
    icon_path = None

# The ffmpeg that does all the encoding ships inside the imageio-ffmpeg
# wheel for each platform. Listed as a binary (not data) so it keeps its
# executable bit and, on macOS, gets code-signed along with everything
# else — an unsigned binary won't run on Apple Silicon.
import imageio_ffmpeg

ffmpeg_dir = Path(imageio_ffmpeg.__file__).parent / "binaries"
ffmpeg_binaries = [
    (str(f), "imageio_ffmpeg/binaries") for f in ffmpeg_dir.iterdir() if f.name.startswith("ffmpeg")
]

a = Analysis(
    [str(src / "simple_ripper" / "app.py")],
    pathex=[str(src)],
    binaries=ffmpeg_binaries,
    datas=[
        (str(src / "simple_ripper" / "gui" / "resources" / "icon.png"), "simple_ripper/gui/resources"),
    ],
    hiddenimports=["mutagen", "imageio_ffmpeg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Simple-Ripper",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Simple-Ripper",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Simple-Ripper.app",
        icon=icon_path,
        bundle_identifier="uk.hellyer.simpleripper",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
        },
    )
