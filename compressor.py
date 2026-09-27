
"""
Platform-independent video compression engine.

Features:
- Automatic hardware encoder detection
- NVIDIA NVENC
- Intel Quick Sync Video (QSV)
- AMD AMF
- Apple VideoToolbox
- libx264 software fallback
- FFmpeg job execution
- Progress parsing
- ETA and encoding speed
- Cancel support
- Batch processing
- "Output larger" guard

The user-facing presets describe quality/compression goals rather than
specific encoders. The best available encoder is selected automatically.
"""

import os
import re
import time
import uuid
import math
import threading
import subprocess
import logging

from platform_utils import (
    get_ffmpeg_path,
    get_ffprobe_path,
    get_creation_flags,
    get_video_info,
    check_disk_space,
)

logger = logging.getLogger(__name__)


# ── Compression presets ────────────────────────────────────────────────
#
# Presets target *total output size*, based on the actual input bitrate.  A
# constant-quality switch alone is not portable: it has different meanings on
# NVENC, QSV, AMF and VideoToolbox and can leave a file virtually unchanged.
#
# The minimum bits-per-pixel-per-frame protects normal-looking video when a
# very high-bitrate source is compressed.  It is a floor, not a request to
# enlarge already efficient video; those files are kept unchanged.
#
PRESETS = {
    "high": {
        "target_ratio": 0.78,
        "min_bpp": 0.060,
        "audio_kbps": 160,
        "label": "High quality",
    },
    "balanced": {
        "target_ratio": 0.58,
        "min_bpp": 0.040,
        "audio_kbps": 128,
        "label": "Balanced",
    },
    "low": {
        "target_ratio": 0.40,
        "min_bpp": 0.025,
        "audio_kbps": 96,
        "label": "Low size",
    },
}

# The first hardware version exposed "small". Keep it accepted by the local
# API so existing UI instances and automation do not break during upgrade.
PRESET_ALIASES = {"small": "low"}


def normalize_preset(preset):
    """Return a supported preset name, including legacy API names."""
    if not isinstance(preset, str):
        return ""
    return PRESET_ALIASES.get(preset, preset)


SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".m4v",
    ".avi",
}


# ── Encoder configuration ──────────────────────────────────────────────
#
# Order matters. Hardware encoders are preferred over CPU encoding.
#
# NVENC  = NVIDIA
# QSV    = Intel
# AMF    = AMD
# VideoToolbox = Apple
#
HARDWARE_ENCODERS = (
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_videotoolbox",
)

SOFTWARE_ENCODER = "libx264"


# ── Job store ──────────────────────────────────────────────────────────

_jobs = {}
_lock = threading.Lock()


def get_job(job_id):
    """Return a snapshot of a job's state."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def get_all_jobs():
    """Return snapshots of all jobs."""
    with _lock:
        return {k: dict(v) for k, v in _jobs.items()}


def _update_job(job_id, **kwargs):
    """Thread-safe job update."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


# ── Encoder detection ──────────────────────────────────────────────────

def _get_ffmpeg_encoders(ffmpeg):
    """
    Return FFmpeg's encoder list.

    This only checks whether FFmpeg exposes an encoder.
    Actual encoder initialization is tested separately.
    """
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            creationflags=get_creation_flags(),
        )

        return result.stdout

    except Exception as e:
        logger.warning("Could not inspect FFmpeg encoders: %s", e)
        return ""


def _encoder_is_available(encoder_list, encoder):
    """Check whether an encoder exists in FFmpeg's encoder list."""
    return bool(
        re.search(
            rf"\b{re.escape(encoder)}\b",
            encoder_list,
        )
    )


def _test_encoder(ffmpeg, encoder):
    """
    Actually initialize an encoder using a tiny test encode.

    This is more reliable than checking `ffmpeg -encoders` alone because
    an encoder can exist in the FFmpeg build while the required hardware
    or driver is unavailable.
    """
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "lavfi",
                "-i", "color=c=black:s=64x64:r=1",
                "-frames:v", "1",
                "-c:v", encoder,
                "-f", "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            creationflags=get_creation_flags(),
        )

        if result.returncode == 0:
            return True

        logger.debug(
            "Encoder %s failed test: %s",
            encoder,
            result.stderr.strip(),
        )

        return False

    except subprocess.TimeoutExpired:
        logger.debug("Encoder %s test timed out", encoder)
        return False

    except Exception as e:
        logger.debug("Encoder %s test failed: %s", encoder, e)
        return False


def detect_video_encoder(ffmpeg):
    """
    Detect the best available H.264 encoder.

    Priority:
        1. NVIDIA NVENC
        2. Intel QSV
        3. AMD AMF
        4. Apple VideoToolbox
        5. libx264 software encoding

    Returns:
        Encoder name.
    """
    encoder_list = _get_ffmpeg_encoders(ffmpeg)

    for encoder in HARDWARE_ENCODERS:
        if not _encoder_is_available(encoder_list, encoder):
            continue

        logger.info("Testing hardware encoder: %s", encoder)

        if _test_encoder(ffmpeg, encoder):
            logger.info("Selected hardware encoder: %s", encoder)
            return encoder

        logger.info(
            "Hardware encoder %s is unavailable at runtime",
            encoder,
        )

    if _encoder_is_available(encoder_list, SOFTWARE_ENCODER):
        logger.info("Using software encoder: %s", SOFTWARE_ENCODER)
        return SOFTWARE_ENCODER

    raise RuntimeError(
        "No usable H.264 encoder was found in the FFmpeg installation."
    )


# Cache encoder detection because testing hardware for every video would
# waste time.
_encoder_cache = None
_encoder_cache_lock = threading.Lock()


def get_video_encoder(ffmpeg):
    """Return the cached best available encoder."""
    global _encoder_cache

    with _encoder_cache_lock:
        if _encoder_cache:
            return _encoder_cache

        _encoder_cache = detect_video_encoder(ffmpeg)

        return _encoder_cache


def reset_encoder_cache():
    """
    Reset encoder detection.

    Useful if the user changes drivers or FFmpeg configuration while
    the application is running.
    """
    global _encoder_cache

    with _encoder_cache_lock:
        _encoder_cache = None


# ── FFmpeg video arguments ─────────────────────────────────────────────

def _rate_arg(kbps):
    """Return a conservative FFmpeg bitrate argument from a numeric rate."""
    return f"{max(100, int(round(float(kbps))))}k"


def _estimate_target_rates(info, original_size, preset):
    """Calculate video and audio budgets for one source file.

    Returns a dict with kbps values, or ``None`` when the source is already
    below the visual-quality floor.  That outcome is intentional: transcoding
    such a file only makes it larger or visibly worse.
    """
    try:
        duration = float(info.get("duration") or 0)
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        fps = float(info.get("fps") or 0)
    except (TypeError, ValueError, OverflowError):
        return None

    if (
        not math.isfinite(duration)
        or not math.isfinite(fps)
        or duration <= 0
        or width <= 0
        or height <= 0
    ):
        return None

    # File size is more reliable than container bit_rate metadata, which is
    # often absent or wrong for phone recordings and screen captures.
    source_total_kbps = original_size * 8 / duration / 1000
    if source_total_kbps <= 0:
        return None

    config = PRESETS[preset]
    source_audio_kbps = float(info.get("audio_bitrate") or 0) / 1000
    audio_stream_count = int(info.get("audio_stream_count") or 0)
    if info.get("has_audio"):
        # Keep all audio tracks. FFmpeg's -b:a applies to each stream, so the
        # budget here is an aggregate while audio_per_stream_kbps is passed to
        # FFmpeg. Do not increase a stream whose source rate is known.
        audio_stream_count = max(1, audio_stream_count)
        source_per_stream_kbps = source_audio_kbps / audio_stream_count
        audio_per_stream_kbps = min(
            config["audio_kbps"],
            source_per_stream_kbps or config["audio_kbps"],
        )
        audio_kbps = audio_per_stream_kbps * audio_stream_count
    else:
        audio_kbps = 0
        audio_per_stream_kbps = 0

    desired_total_kbps = source_total_kbps * config["target_ratio"]
    # If FFprobe could not identify a frame rate, use 30 fps. This gives a
    # conservative floor and still lets unusual variable-rate files encode.
    effective_fps = min(max(fps or 30.0, 12.0), 120.0)
    quality_floor_kbps = (
        width * height * effective_fps * config["min_bpp"] / 1000
    )

    # Reserve 2% for MP4/container overhead.  This is also why a target is
    # deliberately below the source ratio rather than equal to it.
    video_kbps = max(
        quality_floor_kbps,
        desired_total_kbps - audio_kbps,
    )
    target_total_kbps = video_kbps + audio_kbps

    # There is no lossless way to make a video below this quality floor smaller
    # in a broad H.264-compatible output. Preserve the original instead.
    if target_total_kbps >= source_total_kbps * 0.96:
        return None

    return {
        "source_total_kbps": source_total_kbps,
        "video_kbps": max(100, video_kbps),
        "audio_kbps": audio_kbps,
        "audio_per_stream_kbps": audio_per_stream_kbps,
        "target_total_kbps": target_total_kbps,
        "quality_floor_kbps": quality_floor_kbps,
    }


def _get_video_args(encoder, video_kbps):
    """
    Build a portable single-pass capped-VBR command.

    Hardware acceleration remains the fast path.  Every backend receives an
    explicit average bitrate, peak rate, and buffer rather than a backend-
    specific quality setting with an implicit/default bitrate.
    """
    bitrate = _rate_arg(video_kbps)
    maxrate = _rate_arg(video_kbps * 1.15)
    buffer = _rate_arg(video_kbps * 2.0)

    # NVIDIA NVENC
    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-rc", "vbr",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", buffer,
            "-preset", "p5",
        ]

    # Intel Quick Sync
    if encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", buffer,
            "-preset", "medium",
        ]

    # AMD AMF
    if encoder == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-rc", "vbr_peak",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", buffer,
        ]

    # Apple VideoToolbox
    if encoder == "h264_videotoolbox":
        return [
            "-c:v", "h264_videotoolbox",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", buffer,
        ]

    # CPU fallback
    if encoder == "libx264":
        return [
            "-c:v", "libx264",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", buffer,
            "-preset", "veryfast",
        ]

    raise ValueError(f"Unsupported video encoder: {encoder}")


# ── Output filename generation ─────────────────────────────────────────

def _make_output_path(source_path, output_dir=None):
    """
    Generate a non-colliding output path like video_compressed.mp4.

    If output_dir is None, uses the same directory as the source.
    """
    src_dir = os.path.dirname(source_path)
    dest_dir = output_dir if output_dir else src_dir
    basename = os.path.splitext(os.path.basename(source_path))[0]

    candidate = os.path.join(
        dest_dir,
        f"{basename}_compressed.mp4",
    )

    counter = 1

    while os.path.exists(candidate):
        candidate = os.path.join(
            dest_dir,
            f"{basename}_compressed_{counter}.mp4",
        )
        counter += 1

    return candidate


# ── FFmpeg progress parsing ────────────────────────────────────────────

_TIME_RE = re.compile(
    r"time=(\d+):(\d+):(\d+(?:\.\d+)?)"
)

_SPEED_RE = re.compile(
    r"speed=\s*([\d.]+)x"
)


def _parse_progress_line(line):
    """
    Parse an FFmpeg stderr line.

    Returns:
        (current_seconds, speed_multiplier)
    """
    current = None
    speed = None

    match = _TIME_RE.search(line)

    if match:
        current = (
            int(match.group(1)) * 3600
            + int(match.group(2)) * 60
            + float(match.group(3))
        )

    speed_match = _SPEED_RE.search(line)

    if speed_match:
        try:
            speed = float(speed_match.group(1))
        except ValueError:
            pass

    return current, speed


# ── Single file compression ────────────────────────────────────────────

def compress_file(
    job_id,
    source_path,
    preset,
    output_dir=None,
    encoder_override=None,
):
    """
    Compress a single video file.

    Runs FFmpeg in a background thread, reports progress, supports
    cancellation, and discards the output if it is larger than the
    original.
    """

    ffmpeg = get_ffmpeg_path()

    if not ffmpeg:
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error=(
                "FFmpeg not found. Please ensure FFmpeg is bundled "
                "or installed."
            ),
        )
        return

    preset = normalize_preset(preset)

    if preset not in PRESETS:
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error=f"Unknown preset: {preset}",
        )
        return

    if not os.path.isfile(source_path):
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error=(
                f"Source file not found: "
                f"{os.path.basename(source_path)}"
            ),
        )
        return

    original_size = os.path.getsize(source_path)
    output_path = _make_output_path(source_path, output_dir)

    if original_size <= 0:
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error="The source file is empty.",
        )
        return

    # Get video duration via ffprobe.
    info = get_video_info(source_path)
    duration = info["duration"] if info else 0

    if not info or not info.get("width") or not info.get("height"):
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error="No readable video stream was found in this file.",
        )
        return

    try:
        duration = float(duration)
    except (TypeError, ValueError, OverflowError):
        duration = 0

    if not math.isfinite(duration) or duration <= 0:
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error="Unable to determine the video duration needed for compression.",
        )
        return

    rates = _estimate_target_rates(info, original_size, preset)
    if rates is None:
        _update_job(
            job_id,
            status="larger",
            stage="Already optimized",
            progress=100,
            elapsed=0,
            eta=0,
            original_size=original_size,
            output_size=original_size,
            saved=0,
            error=(
                "The source is already at or below this preset's visual "
                "quality floor, so it was kept to avoid a larger or "
                "visibly worse file."
            ),
        )
        return

    try:
        encoder = encoder_override or get_video_encoder(ffmpeg)
    except Exception as e:
        logger.error("Encoder detection failed: %s", e)

        _update_job(
            job_id,
            status="error",
            stage="Error",
            error=f"No usable video encoder found: {e}",
        )
        return

    # Check disk space.
    if not check_disk_space(output_path, original_size):
        _update_job(
            job_id,
            status="error",
            stage="Error",
            error="Insufficient disk space for compression.",
        )
        return

    _update_job(
        job_id,
        status="processing",
        stage="Encoding video",
        progress=0,
        elapsed=0,
        eta=None,
        speed=None,
        duration=duration,
        original_size=original_size,
        output_path=output_path,
        source_path=source_path,
        encoder=encoder,
        target_video_kbps=round(rates["video_kbps"]),
        target_audio_kbps=round(rates["audio_kbps"]),
        source_bitrate_kbps=round(rates["source_total_kbps"]),
    )

    video_args = _get_video_args(
        encoder,
        rates["video_kbps"],
    )

    audio_args = ["-an"]
    if info.get("has_audio"):
        audio_args = [
            "-c:a", "aac",
            "-b:a", _rate_arg(rates["audio_per_stream_kbps"]),
        ]

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i", source_path,
        "-map", "0:v:0",
        "-map", "0:a?",
        *video_args,
        # 8-bit 4:2:0 H.264 plays on the broadest range of Windows, Linux,
        # Android, iOS and Apple Silicon devices. It also prevents a 4:4:4
        # source from making a hardware encoder reject an otherwise valid job.
        "-pix_fmt", "yuv420p",
        *audio_args,
        "-sn",
        "-dn",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(
        "Using encoder=%s video=%sk audio=%sk source=%sk",
        encoder,
        round(rates["video_kbps"]),
        round(rates["audio_kbps"]),
        round(rates["source_total_kbps"]),
    )

    logger.info(
        "FFmpeg command: %s",
        " ".join(cmd),
    )

    start_time = time.time()

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=get_creation_flags(),
        )

    except Exception as e:
        logger.error(
            "Failed to start FFmpeg: %s",
            e,
        )

        _update_job(
            job_id,
            status="error",
            stage="Error",
            error=f"Failed to start FFmpeg: {e}",
        )

        return

    # Store process reference so cancel_job() can terminate it.
    _update_job(
        job_id,
        _process=process,
    )

    # ── Read FFmpeg stderr for progress ──

    ffmpeg_errors = []

    try:
        for line in process.stderr:

            # Check if cancelled.
            with _lock:
                job = _jobs.get(job_id, {})

                if job.get("status") == "cancelled":
                    break

            current, speed = _parse_progress_line(line)

            # Keep a short diagnostic tail for a useful user-facing error,
            # without retaining unbounded FFmpeg output for a long video.
            stripped = line.strip()
            if stripped and (
                "error" in stripped.lower()
                or "invalid" in stripped.lower()
                or "failed" in stripped.lower()
            ):
                ffmpeg_errors.append(stripped)
                del ffmpeg_errors[:-3]

            elapsed = time.time() - start_time

            progress = 0
            eta = None

            if current is not None and duration > 0:

                progress = min(
                    99.9,
                    current / duration * 100,
                )

                # Prefer FFmpeg's speed measurement.
                if speed and speed > 0:

                    remaining_duration = duration - current

                    eta = max(
                        0,
                        remaining_duration / speed,
                    )

                elif current > 0 and elapsed > 2:

                    eta = max(
                        0,
                        (duration - current)
                        / (current / elapsed),
                    )

            _update_job(
                job_id,
                progress=progress,
                elapsed=elapsed,
                eta=eta,
                speed=speed,
            )

    except Exception as e:
        logger.error(
            "Error reading FFmpeg output: %s",
            e,
        )

    # ── Wait for FFmpeg ────────────────────────────────────────────────

    try:
        rc = process.wait(timeout=10)

    except subprocess.TimeoutExpired:
        process.kill()
        rc = -1

    elapsed = time.time() - start_time

    # ── Check cancellation ─────────────────────────────────────────────

    with _lock:
        job = _jobs.get(job_id, {})
        was_cancelled = job.get("status") == "cancelled"

    if was_cancelled:

        _cleanup_output(output_path)

        _update_job(
            job_id,
            stage="Cancelled",
            elapsed=elapsed,
        )

        logger.info(
            "Job %s cancelled",
            job_id,
        )

        return

    # ── Handle completion ─────────────────────────────────────────────

    if rc == 0 and os.path.exists(output_path):

        output_size = os.path.getsize(output_path)

        # A successful-but-oversized hardware result means the driver did not
        # honor its rate-control request. Retry that exceptional case with the
        # portable software encoder before giving up on compression.
        if output_size >= original_size:

            if encoder != SOFTWARE_ENCODER:
                _cleanup_output(output_path)
                _update_job(
                    job_id,
                    stage="Retrying with compatible encoder",
                    progress=0,
                    fallback_from=encoder,
                )
                logger.warning(
                    "Hardware encoder %s exceeded its bitrate budget; "
                    "retrying %s with libx264",
                    encoder,
                    source_path,
                )
                compress_file(
                    job_id,
                    source_path,
                    preset,
                    output_dir,
                    encoder_override=SOFTWARE_ENCODER,
                )
                return

            _cleanup_output(output_path)

            _update_job(
                job_id,
                status="larger",
                stage="Original kept",
                progress=100,
                elapsed=elapsed,
                eta=0,
                speed=None,
                original_size=original_size,
                output_size=original_size,
                saved=0,
                error=(
                    "The compressed version would be larger than "
                    "the original, so the original file was kept."
                ),
            )

            logger.info(
                "Job %s: output larger than original, discarded",
                job_id,
            )

            return

        saved_pct = (
            100
            * (original_size - output_size)
            / original_size
        )

        _update_job(
            job_id,
            status="done",
            stage="Complete",
            progress=100,
            elapsed=elapsed,
            eta=0,
            speed=None,
            original_size=original_size,
            output_size=output_size,
            saved=saved_pct,
            output_path=output_path,
        )

        logger.info(
            "Job %s complete: %.1f%% reduction using %s",
            job_id,
            saved_pct,
            encoder,
        )

    else:

        _cleanup_output(output_path)

        # A driver can pass the tiny availability probe but fail on a real
        # source (unsupported pixel format, device reset, or driver bug).
        # Falling back once keeps hardware as the fast path without making it
        # a single point of failure.
        if encoder != SOFTWARE_ENCODER:
            _update_job(
                job_id,
                stage="Retrying with compatible encoder",
                progress=0,
                fallback_from=encoder,
            )
            logger.warning(
                "Hardware encoder %s failed for %s; retrying with libx264",
                encoder,
                source_path,
            )
            compress_file(
                job_id,
                source_path,
                preset,
                output_dir,
                encoder_override=SOFTWARE_ENCODER,
            )
            return

        detail = " ".join(ffmpeg_errors[-2:])
        message = "FFmpeg could not encode this video."
        if detail:
            message = f"{message} {detail[:500]}"

        _update_job(
            job_id,
            status="error",
            stage="Error",
            progress=0,
            elapsed=elapsed,
            error=message,
        )

        logger.error(
            "Job %s failed with return code %d",
            job_id,
            rc,
        )


# ── Cleanup ────────────────────────────────────────────────────────────

def _cleanup_output(path):
    """Delete an output file if it exists."""
    try:

        if path and os.path.exists(path):
            os.remove(path)

            logger.info(
                "Cleaned up: %s",
                path,
            )

    except OSError as e:

        logger.error(
            "Failed to clean up %s: %s",
            path,
            e,
        )


# ── Cancel support ────────────────────────────────────────────────────

def cancel_job(job_id):
    """
    Cancel a running compression job.

    Terminates FFmpeg and removes the partial output.
    """

    with _lock:

        job = _jobs.get(job_id)

        if not job:
            return False

        if job.get("status") not in (
            "queued",
            "processing",
        ):
            return False

        job["status"] = "cancelled"

        process = job.get("_process")

    if process and process.poll() is None:

        try:

            process.terminate()

            try:
                process.wait(timeout=5)

            except subprocess.TimeoutExpired:
                process.kill()

        except Exception as e:

            logger.error(
                "Error terminating FFmpeg for job %s: %s",
                job_id,
                e,
            )

    return True


# ── Batch processing ──────────────────────────────────────────────────

class BatchProcessor:
    """
    Manages sequential compression of multiple files.

    Tracks overall batch progress and supports cancellation.
    """

    def __init__(self):

        self.batch_id = None
        self.job_ids = []
        self.file_paths = []
        self.total_files = 0
        self.current_index = 0
        self.is_running = False
        self.is_cancelled = False
        self._thread = None
        self._lock = threading.Lock()

    def start_batch(
        self,
        file_paths,
        preset,
        output_dir=None,
    ):
        """
        Start compressing files sequentially.

        Returns:
            (batch_id, job_ids)
        """

        if self.is_running:
            return None, []

        self.batch_id = uuid.uuid4().hex
        self.file_paths = list(file_paths)
        self.total_files = len(file_paths)
        self.current_index = 0
        self.is_running = True
        self.is_cancelled = False
        self.job_ids = []

        # Create job entries.
        for fp in file_paths:

            job_id = uuid.uuid4().hex

            self.job_ids.append(job_id)

            with _lock:

                _jobs[job_id] = {
                    "status": "queued",
                    "stage": "Queued",
                    "progress": 0,
                    "elapsed": 0,
                    "eta": None,
                    "speed": None,
                    "duration": 0,
                    "original_size": (
                        os.path.getsize(fp)
                        if os.path.isfile(fp)
                        else 0
                    ),
                    "filename": os.path.basename(fp),
                    "source_path": fp,
                    "batch_id": self.batch_id,
                }

        self._thread = threading.Thread(
            target=self._run_batch,
            args=(preset, output_dir),
            daemon=True,
        )

        self._thread.start()

        return self.batch_id, self.job_ids

    def _run_batch(
        self,
        preset,
        output_dir,
    ):
        """Process files sequentially."""

        for i, (job_id, fp) in enumerate(
            zip(
                self.job_ids,
                self.file_paths,
            )
        ):

            if self.is_cancelled:

                _update_job(
                    job_id,
                    status="cancelled",
                    stage="Cancelled",
                )

                continue

            with self._lock:
                self.current_index = i

            _update_job(
                job_id,
                stage="Preparing",
            )

            # Blocking compression of current file.
            compress_file(
                job_id,
                fp,
                preset,
                output_dir,
            )

            job = get_job(job_id)

            if (
                job
                and job.get("status") == "cancelled"
            ):

                self.is_cancelled = True

                for remaining_id in self.job_ids[i + 1:]:

                    _update_job(
                        remaining_id,
                        status="cancelled",
                        stage="Cancelled",
                    )

                break

        self.is_running = False

    def cancel_batch(self):
        """Cancel the entire batch."""

        self.is_cancelled = True

        with self._lock:

            if self.current_index < len(
                self.job_ids
            ):

                cancel_job(
                    self.job_ids[
                        self.current_index
                    ]
                )

    def get_batch_status(self):
        """Return batch progress overview."""

        if not self.batch_id:
            return None

        completed = 0
        failed = 0
        cancelled = 0

        total_original = 0
        total_compressed = 0

        results = []

        for job_id in self.job_ids:

            job = get_job(job_id)

            if not job:
                continue

            status = job.get(
                "status",
                "queued",
            )

            if status == "done":

                completed += 1

                total_original += job.get(
                    "original_size",
                    0,
                )

                total_compressed += job.get(
                    "output_size",
                    0,
                )

            elif status == "larger":

                completed += 1

                total_original += job.get(
                    "original_size",
                    0,
                )

                total_compressed += job.get(
                    "original_size",
                    0,
                )

            elif status == "error":

                failed += 1

            elif status == "cancelled":

                cancelled += 1

            results.append(
                {
                    "job_id": job_id,
                    "filename": job.get(
                        "filename",
                        "",
                    ),
                    "status": status,
                    "stage": job.get(
                        "stage",
                        "",
                    ),
                    "progress": job.get(
                        "progress",
                        0,
                    ),
                    "elapsed": job.get(
                        "elapsed",
                        0,
                    ),
                    "eta": job.get("eta"),
                    "speed": job.get(
                        "speed",
                    ),
                    "encoder": job.get(
                        "encoder",
                    ),
                    "original_size": job.get(
                        "original_size",
                        0,
                    ),
                    "output_size": job.get(
                        "output_size",
                        0,
                    ),
                    "saved": job.get(
                        "saved",
                        0,
                    ),
                    "error": job.get(
                        "error",
                    ),
                    "output_path": job.get(
                        "output_path",
                    ),
                }
            )

        return {
            "batch_id": self.batch_id,
            "total_files": self.total_files,
            "current_index": self.current_index,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "is_running": self.is_running,
            "is_cancelled": self.is_cancelled,
            "total_original": total_original,
            "total_compressed": total_compressed,
            "jobs": results,
        }


# ── Singleton batch processor ─────────────────────────────────────────

batch_processor = BatchProcessor()
