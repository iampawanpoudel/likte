#!/usr/bin/env bash
# Build a Linux package with the matching Linux FFmpeg binaries bundled.
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: Python 3.10+ is required."
    exit 1
fi

if [[ -f ffmpeg/Linux/ffmpeg ]]; then
    chmod +x ffmpeg/Linux/ffmpeg
fi
if [[ -f ffmpeg/Linux/ffprobe ]]; then
    chmod +x ffmpeg/Linux/ffprobe
fi

python3 -m pip install -r requirements.txt
python3 -m pip install pyinstaller
python3 -m PyInstaller video_compressor.spec --noconfirm

echo "Build complete: dist/Likte"
