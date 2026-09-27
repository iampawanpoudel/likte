#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  Likte — macOS Build Script
#
#  Prerequisites:
#    - Python 3.10+ installed
#    - FFmpeg binaries placed in ffmpeg/macOS/
#      (ffmpeg and ffprobe)
#
#  Architecture:
#    This script builds for the CURRENT architecture.
#    - On Apple Silicon Mac: produces an arm64 build
#    - On Intel Mac: produces an x86_64 build
#
#    To build for a specific architecture:
#      arch -arm64 ./build_macos.sh    # Apple Silicon
#      arch -x86_64 ./build_macos.sh   # Intel (Rosetta 2 required on AS)
#
#    For a universal build, build on both architectures and
#    combine with lipo/create-dmg, or use the instructions
#    in README.md.
# ═══════════════════════════════════════════════════════════════

set -e

echo ""
echo "══════════════════════════════════════════"
echo "  Likte — macOS Build"
echo "  Architecture: $(uname -m)"
echo "══════════════════════════════════════════"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Please install Python 3.10+."
    exit 1
fi

echo "Python version: $(python3 --version)"

# Check FFmpeg binaries
if [ ! -f "ffmpeg/macOS/ffmpeg" ]; then
    echo "WARNING: ffmpeg/macOS/ffmpeg not found."
    echo "         The built app will not include bundled FFmpeg."
    echo "         Place ffmpeg and ffprobe binaries in ffmpeg/macOS/ before building."
    echo ""
fi

# Make FFmpeg binaries executable if they exist
if [ -f "ffmpeg/macOS/ffmpeg" ]; then
    chmod +x ffmpeg/macOS/ffmpeg
    echo "Set executable permission on ffmpeg/macOS/ffmpeg"
fi
if [ -f "ffmpeg/macOS/ffprobe" ]; then
    chmod +x ffmpeg/macOS/ffprobe
    echo "Set executable permission on ffmpeg/macOS/ffprobe"
fi

# Install dependencies
echo ""
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

# Install PyInstaller
echo "Installing PyInstaller..."
pip3 install pyinstaller

# Build
echo ""
echo "Building application..."
python3 -m PyInstaller video_compressor.spec --noconfirm

echo ""
echo "══════════════════════════════════════════"
echo "  Build complete!"
echo "  Output: dist/Likte.app"
echo ""
echo "  To run:"
echo "    open dist/Likte.app"
echo ""
echo "  Architecture: $(uname -m)"
echo "══════════════════════════════════════════"
echo ""
