# PyInstaller build spec for the Windows .exe.
#   pip install -r requirements.txt -r requirements-build.txt
#   pyinstaller ChannelArchiver.spec
# Output: dist/ChannelArchiver.exe (a single, double-clickable file).
import os
from PyInstaller.utils.hooks import collect_submodules

# Bundle ffmpeg.exe from imageio-ffmpeg so merging works with zero setup.
binaries = []
try:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    binaries.append((ff, "imageio_ffmpeg/binaries"))
except Exception:
    pass

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=[("index.html", ".")],
    # yt-dlp loads extractors dynamically; pull them all in.
    hiddenimports=collect_submodules("yt_dlp") + ["waitress", "imageio_ffmpeg"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ChannelArchiver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # keep a small console so users can see status / close to quit
    disable_windowed_traceback=False,
    icon=None,
)
