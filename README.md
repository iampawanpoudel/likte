# Likte (लिक्ते)

A cross-platform desktop application for reducing video file size while maintaining good visual quality. Built with Python, PyWebView, Flask, and FFmpeg.

## Features

- **Desktop application** — Opens as a native window, no browser required
- **Drag & drop** — Drop videos directly onto the app
- **Batch processing** — Compress multiple videos sequentially
- **Real-time progress** — Percentage, ETA, elapsed time, encoding speed
- **Cancel support** — Stop compression at any time, no corrupt files left behind
- **Smart size guard** — If compressed output would be larger than the original, the original is kept
- **Cross-platform** — Windows, macOS Intel, macOS Apple Silicon
- **Bundled FFmpeg** — End users don't need to install FFmpeg separately

## Compression Presets

| Preset   | Target                          | Use case                                      |
| -------- | ------------------------------- | --------------------------------------------- |
| High     | about 78% of the source bitrate | Highest visual quality, modest reduction      |
| Balanced | about 58% of the source bitrate | Strong everyday quality/size trade-off        |
| Low      | about 40% of the source bitrate | Smallest practical file, stronger compression |

Targets are calculated from the source duration, size, resolution, frame rate,
and audio tracks. Every encoder receives an explicit capped-VBR bitrate budget;
this fixes the hardware-encoder behaviour where a backend-specific quality
switch could produce a nearly unchanged file. Resolution is preserved, AAC
audio tracks are retained, and output is broadly compatible H.264/MP4. A video
already below a preset's visual-quality floor is kept unchanged rather than
made bigger or visibly worse.

Hardware encoding is tried first (NVENC, QSV, AMF, or VideoToolbox). If a
driver fails a real encode or ignores its bitrate budget, the file retries once
with libx264 automatically.

---

## Project Structure

```
Likte/
├── app.py                  # Entry point: Flask + PyWebView
├── compressor.py           # FFmpeg compression engine & batch processing
├── platform_utils.py       # OS detection, FFmpeg path resolution
├── templates/
│   └── index.html          # Application UI
├── ffmpeg/
│   ├── Windows/
│   │   ├── ffmpeg.exe      ← YOU place these
│   │   └── ffprobe.exe     ← YOU place these
│   └── macOS/
│       ├── ffmpeg           ← YOU place these
│       └── ffprobe          ← YOU place these
├── requirements.txt
├── video_compressor.spec   # PyInstaller build configuration
├── build_windows.bat       # Windows build script
├── build_macos.sh          # macOS build script
├── .gitignore
└── README.md
```

---

## Development Setup

### Prerequisites

- **Python 3.10+** (3.11 or 3.12 recommended)
- **FFmpeg** installed in PATH (for development) or placed in `ffmpeg/` directories (for packaging)

### Install Dependencies

```bash
pip install -r requirements.txt
```

### FFmpeg for Development

During development, the app will use FFmpeg from your system PATH as a fallback. Verify it's available:

```bash
ffmpeg -version
ffprobe -version
```

### Run in Development

```bash
python app.py
```

A native desktop window will open automatically. No need to open a browser.

---

## FFmpeg Binary Setup (for Packaging)

Before building the packaged application, place FFmpeg binaries in the correct directories:

### Windows

Download static FFmpeg builds from:

- https://www.gyan.dev/ffmpeg/builds/ (recommended)
- https://github.com/BtbN/FFmpeg-Builds/releases

Place the binaries:

```
ffmpeg/Windows/ffmpeg.exe
ffmpeg/Windows/ffprobe.exe
```

### macOS

Download static FFmpeg builds from:

- https://evermeet.cx/ffmpeg/ (recommended)
- https://ffmpeg.org/download.html

Place the binaries:

```
ffmpeg/macOS/ffmpeg
ffmpeg/macOS/ffprobe
```

**Important:** Make them executable:

```bash
chmod +x ffmpeg/macOS/ffmpeg ffmpeg/macOS/ffprobe
```

---

## Building for Windows

### Requirements

- Python 3.10+ installed
- FFmpeg binaries in `ffmpeg/Windows/`

### Build Command

```batch
build_windows.bat
```

Or manually:

```batch
pip install -r requirements.txt
pip install pyinstaller
pyinstaller video_compressor.spec --noconfirm
```

### Output

The built application will be at:

```
dist\Likte.exe
```

Distribute this single executable file.

### Create a Windows Installer

The project includes an [Inno Setup](https://jrsoftware.org/isdl.php) installer
definition. It installs Likte under the current user's Programs folder, adds a
Start Menu entry, offers an optional desktop shortcut, and supplies a normal
uninstaller in Windows Settings.

1. Install **Inno Setup 6** once from
   [jrsoftware.org](https://jrsoftware.org/isdl.php). The default installation
   location is detected automatically.
2. Run `build_windows.bat` to make a fresh `dist\Likte.exe`. This also embeds
   `assets\likte.ico` in the application.
3. Run:

```batch
build_installer.bat
```

The distributable installer will be created at:

```
dist\installer\Likte-Setup-1.0.0.exe
```

Share this setup file rather than the raw executable. It can be uninstalled
from **Settings > Apps > Installed apps** like a native Windows application.

---

## Building for macOS

### Requirements

- Python 3.10+ installed
- FFmpeg binaries in `ffmpeg/macOS/`
- Xcode Command Line Tools (`xcode-select --install`)

### Build Command

```bash
chmod +x build_macos.sh
./build_macos.sh
```

Or manually:

```bash
chmod +x ffmpeg/macOS/ffmpeg ffmpeg/macOS/ffprobe
pip3 install -r requirements.txt
pip3 install pyinstaller
python3 -m PyInstaller video_compressor.spec --noconfirm
```

### Output

The built application will be at:

```
dist/Likte.app
```

### macOS Intel (x86_64)

Build on an Intel Mac, or on Apple Silicon with Rosetta:

```bash
arch -x86_64 ./build_macos.sh
```

> **Note:** You need an x86_64 Python installation and x86_64 FFmpeg binaries for this.

### macOS Apple Silicon (arm64)

Build natively on an Apple Silicon Mac:

```bash
./build_macos.sh
```

> **Note:** Use arm64 FFmpeg binaries for this build.

### Universal macOS Build

To create a universal binary that runs on both Intel and Apple Silicon:

1. **Build separately** on both architectures (or using `arch -x86_64` and `arch -arm64`)
2. **Combine** using `lipo`:

```bash
# After building both versions:
mkdir -p dist/Likte-Universal.app

# Copy one .app as the base
cp -R dist/Likte.app dist/Likte-Universal.app

# Use lipo to combine the main executable
lipo -create \
  dist-x86_64/Likte.app/Contents/MacOS/Likte \
  dist-arm64/Likte.app/Contents/MacOS/Likte \
  -output dist/Likte-Universal.app/Contents/MacOS/Likte
```

Alternatively, use a tool like [create-dmg](https://github.com/create-dmg/create-dmg) to package the `.app` into a `.dmg`.

---

## Building for Linux

Place executable Linux builds of `ffmpeg` and `ffprobe` in `ffmpeg/Linux/`,
then run:

```bash
bash build_linux.sh
```

The executable is written to `dist/Likte`. Linux systems also need a
PyWebView-supported GUI backend (for example GTK/WebKitGTK).

---

## Security & Privacy

- All video processing happens **locally** on your machine
- No files are uploaded to any server
- No internet connection is required after installation
- Flask binds only to `127.0.0.1` (localhost)
- Original files are **never** overwritten or deleted

---

## Troubleshooting

### "FFmpeg not found" warning

- **Development:** Make sure `ffmpeg` and `ffprobe` are in your system PATH
- **Packaged app:** Ensure the binaries were placed in `ffmpeg/Windows/` or `ffmpeg/macOS/` before building

### Window doesn't open

- Make sure `pywebview` is installed: `pip install pywebview`
- On Linux, you may need additional WebKit dependencies: `sudo apt install python3-gi gir1.2-webkit2-4.1`

### Build fails on macOS

- Install Xcode Command Line Tools: `xcode-select --install`
- Ensure FFmpeg binaries have execute permissions: `chmod +x ffmpeg/macOS/*`

### Compression produces larger files

This is expected for already-compressed videos. The app will detect this and keep the original file, showing a message to the user.
