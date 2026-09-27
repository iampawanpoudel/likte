@echo off
REM ═══════════════════════════════════════════════════════════════
REM  Likte — Windows Build Script
REM  
REM  Prerequisites:
REM    - Python 3.10+ installed
REM    - FFmpeg binaries placed in ffmpeg\Windows\
REM      (ffmpeg.exe and ffprobe.exe)
REM ═══════════════════════════════════════════════════════════════

echo.
echo ══════════════════════════════════════════
echo   Likte — Windows Build
echo ══════════════════════════════════════════
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.10+.
    pause
    exit /b 1
)

REM Check FFmpeg binaries
if not exist "ffmpeg\Windows\ffmpeg.exe" (
    echo WARNING: ffmpeg\Windows\ffmpeg.exe not found.
    echo          The built app will not include bundled FFmpeg.
    echo          Place ffmpeg.exe and ffprobe.exe in ffmpeg\Windows\ before building.
    echo.
)

REM Install dependencies
echo Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

REM Install PyInstaller
echo Installing PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller.
    pause
    exit /b 1
)

REM Build
echo.
echo Building application...
pyinstaller video_compressor.spec --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ══════════════════════════════════════════
echo   Build complete!
echo   Output: dist\Likte.exe
echo   Run:    dist\Likte.exe
echo ══════════════════════════════════════════
echo.
pause
