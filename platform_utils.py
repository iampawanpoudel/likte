"""
Platform utilities for cross-platform FFmpeg binary resolution,
PyInstaller resource paths, and OS-specific helpers.
"""

import os
import sys
import shutil
import subprocess
import json
import logging
import math

logger = logging.getLogger(__name__)


def is_frozen():
    """Check if running inside a PyInstaller bundle."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_base_path():
    """
    Return the base path for bundled resources.
    - PyInstaller frozen: sys._MEIPASS (the temp extraction directory)
    - Development: the directory containing this script
    """
    if is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_platform():
    """Return normalized platform string: 'windows', 'macos', or 'linux'."""
    s = sys.platform
    if s.startswith('win'):
        return 'windows'
    elif s == 'darwin':
        return 'macos'
    return 'linux'


def _find_bundled_binary(name):
    """
    Locate a bundled binary (ffmpeg or ffprobe) for the current platform.
    Returns the absolute path if found, else None.
    """
    base = get_base_path()
    plat = get_platform()

    if plat == 'windows':
        exe_name = name + '.exe'
        folder = 'Windows'
    elif plat == 'macos':
        exe_name = name
        folder = 'macOS'
    else:
        exe_name = name
        folder = 'Linux'

    # Primary location: ffmpeg/<Platform>/binary
    bundled = os.path.join(base, 'ffmpeg', folder, exe_name)
    if os.path.isfile(bundled):
        return bundled

    # Also check directly in base (PyInstaller may flatten the structure)
    flat = os.path.join(base, exe_name)
    if os.path.isfile(flat):
        return flat

    return None


def get_ffmpeg_path():
    """
    Return the path to the ffmpeg binary.
    Priority: bundled binary → system PATH.
    """
    bundled = _find_bundled_binary('ffmpeg')
    if bundled:
        logger.info("Using bundled ffmpeg: %s", bundled)
        return bundled

    system = shutil.which('ffmpeg')
    if system:
        logger.info("Using system ffmpeg: %s", system)
        return system

    logger.error("ffmpeg not found (bundled or system)")
    return None


def get_ffprobe_path():
    """
    Return the path to the ffprobe binary.
    Priority: bundled binary → system PATH.
    """
    bundled = _find_bundled_binary('ffprobe')
    if bundled:
        logger.info("Using bundled ffprobe: %s", bundled)
        return bundled

    system = shutil.which('ffprobe')
    if system:
        logger.info("Using system ffprobe: %s", system)
        return system

    logger.error("ffprobe not found (bundled or system)")
    return None


def get_creation_flags():
    """
    Return subprocess creation flags.
    On Windows, returns CREATE_NO_WINDOW to prevent console popups.
    On other platforms, returns 0.
    """
    if get_platform() == 'windows':
        return subprocess.CREATE_NO_WINDOW
    return 0


def get_video_info(filepath):
    """
    Use ffprobe to get video metadata.
    Returns dict with the primary video/audio stream properties needed to make
    a sensible compression decision.  In particular, bitrate and frame rate
    let the compressor avoid applying a one-size-fits-all bitrate to every
    resolution.
    Returns None on failure.
    """
    ffprobe = get_ffprobe_path()
    if not ffprobe:
        return None

    try:
        cmd = [
            ffprobe, '-v', 'error',
            '-show_entries', (
                'stream=index,codec_type,codec_name,width,height,duration,'
                'bit_rate,avg_frame_rate,r_frame_rate,pix_fmt,field_order,'
                'color_transfer:format=duration,size,bit_rate'
            ),
            '-of', 'json',
            filepath,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            creationflags=get_creation_flags()
        )
        data = json.loads(r.stdout)

        def number(value, default=0.0):
            """Convert ffprobe's numeric strings (including N/A) safely."""
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) else default
            except (TypeError, ValueError):
                return default

        def frame_rate(value):
            """Convert an FFprobe rational frame rate such as 30000/1001."""
            if not value or value == '0/0':
                return 0.0
            try:
                numerator, denominator = str(value).split('/', 1)
                denominator = float(denominator)
                return float(numerator) / denominator if denominator else 0.0
            except (TypeError, ValueError, ZeroDivisionError):
                return 0.0

        info = {
            'size_bytes': os.path.getsize(filepath),
            'width': None,
            'height': None,
            'codec': None,
            'duration': 0,
            'bitrate': 0,
            'video_bitrate': 0,
            'audio_bitrate': 0,
            'audio_stream_count': 0,
            'fps': 0,
            'has_audio': False,
            'pix_fmt': None,
            'field_order': None,
            'color_transfer': None,
        }

        # Extract the first real video stream and the aggregate audio rate.
        # Some containers have attached artwork, subtitles, or several audio
        # tracks; those must not be mistaken for the main video stream.
        streams = data.get('streams', [])
        video_stream = next(
            (s for s in streams if s.get('codec_type') == 'video'),
            None,
        )
        if video_stream:
            s = video_stream
            info['width'] = s.get('width')
            info['height'] = s.get('height')
            info['codec'] = s.get('codec_name')
            info['video_bitrate'] = number(s.get('bit_rate'))
            info['fps'] = frame_rate(
                s.get('avg_frame_rate') or s.get('r_frame_rate')
            )
            info['pix_fmt'] = s.get('pix_fmt')
            info['field_order'] = s.get('field_order')
            info['color_transfer'] = s.get('color_transfer')
            info['duration'] = number(s.get('duration'))

        audio_streams = [
            s for s in streams if s.get('codec_type') == 'audio'
        ]
        if audio_streams:
            info['has_audio'] = True
            info['audio_stream_count'] = len(audio_streams)
            info['audio_bitrate'] = sum(
                number(s.get('bit_rate')) for s in audio_streams
            )

        # Format-level duration (more reliable fallback)
        fmt = data.get('format', {})
        if fmt.get('duration'):
            info['duration'] = number(fmt['duration'])
        info['bitrate'] = number(fmt.get('bit_rate'))

        return info

    except Exception as e:
        logger.error("ffprobe failed for %s: %s", filepath, e)
        return None


def open_folder(path):
    """Open a folder in the system file manager."""
    plat = get_platform()
    try:
        if plat == 'windows':
            os.startfile(path)
        elif plat == 'macos':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception as e:
        logger.error("Failed to open folder %s: %s", path, e)


def check_disk_space(path, required_bytes):
    """
    Check if there's enough disk space at the given path.
    Returns True if enough space, False otherwise.
    """
    try:
        usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
        # Require at least the estimated size plus a 100 MB buffer
        return usage.free >= (required_bytes + 100 * 1024 * 1024)
    except Exception:
        # If we can't check, assume it's fine
        return True


def format_size(size_bytes):
    """Format byte count into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds):
    """Format seconds into human-readable duration string."""
    if seconds is None or seconds <= 0:
        return "Unknown"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"
