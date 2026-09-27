# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Likte.

Usage:
  pyinstaller video_compressor.spec --noconfirm

This spec file auto-detects the platform and bundles the appropriate
FFmpeg binaries.
"""

import sys
import os

block_cipher = None

# Determine platform-specific FFmpeg folder
if sys.platform.startswith('win'):
    ffmpeg_folder = 'Windows'
    ffmpeg_binaries = ['ffmpeg.exe', 'ffprobe.exe']
    icon_file = os.path.join('assets', 'likte.ico')
elif sys.platform == 'darwin':
    ffmpeg_folder = 'macOS'
    ffmpeg_binaries = ['ffmpeg', 'ffprobe']
    icon_file = None  # Set a .icns path here if you have one
else:
    ffmpeg_folder = 'Linux'
    ffmpeg_binaries = ['ffmpeg', 'ffprobe']
    icon_file = None

# Collect FFmpeg binaries as data files
ffmpeg_datas = []
ffmpeg_dir = os.path.join('ffmpeg', ffmpeg_folder)
if os.path.isdir(ffmpeg_dir):
    for binary in ffmpeg_binaries:
        src = os.path.join(ffmpeg_dir, binary)
        if os.path.isfile(src):
            # Bundle into ffmpeg/<Platform>/ inside the dist
            ffmpeg_datas.append((src, os.path.join('ffmpeg', ffmpeg_folder)))

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),   # HTML/CSS/JS
    ] + ffmpeg_datas,
    hiddenimports=[
        'compressor',
        'platform_utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Likte',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window for end users
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# macOS .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Likte.app',
        icon=icon_file,
        bundle_identifier='com.Likte.app',
        info_plist={
            'CFBundleName': 'Likte',
            'CFBundleDisplayName': 'Likte',
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
        },
    )
