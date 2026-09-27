"""
Likte — Desktop Application Entry Point.
"""

import os
import socket
import threading
import logging
import tempfile
import atexit
import shutil
import webview

from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from platform_utils import (
    get_base_path, get_ffmpeg_path, get_ffprobe_path,
    get_video_info, open_folder, format_size, format_duration,
    is_frozen, get_platform
)
from compressor import (
    PRESETS, SUPPORTED_EXTENSIONS, batch_processor, cancel_job, get_job,
    normalize_preset,
)

# ── Logging ────────────────────────────────────────────────────────────
log_level = logging.WARNING if is_frozen() else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Flask app ──────────────────────────────────────────────────────────
# Resolve templates directory for both dev and PyInstaller modes
base = get_base_path()
template_dir = os.path.join(base, 'templates')

app = Flask(__name__, template_folder=template_dir)
# Most selection uses native filesystem paths.  Browsers that intentionally
# hide those paths can still send a dropped file as multipart form data.
app.config['MAX_CONTENT_LENGTH'] = None

# Reference to the PyWebView window (set after creation)
_webview_window = None
_staging_dirs = set()


def cleanup_staging_dirs():
    """Remove only the temporary copies created for pathless file drops."""
    for staging_dir in tuple(_staging_dirs):
        shutil.rmtree(staging_dir, ignore_errors=True)
        _staging_dirs.discard(staging_dir)


atexit.register(cleanup_staging_dirs)


def video_file_payload(filepath, *, uploaded=False):
    """Build the file metadata returned to the selection view."""
    info = get_video_info(filepath)
    return {
        'path': filepath,
        'name': os.path.basename(filepath),
        'size': os.path.getsize(filepath),
        'size_formatted': format_size(os.path.getsize(filepath)),
        'width': info.get('width') if info else None,
        'height': info.get('height') if info else None,
        'duration': info.get('duration', 0) if info else 0,
        'duration_formatted': format_duration(info.get('duration', 0)) if info else 'Unknown',
        'uploaded': uploaded,
    }


# ── Routes ─────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/select-files")
def select_files():
    """Open native file dialog via PyWebView to select video files."""
    global _webview_window
    if not _webview_window:
        return jsonify(error="Window not ready"), 500

    file_types = ('Video Files (*.mp4;*.mov;*.mkv;*.webm;*.m4v;*.avi)',)
    try:
        result = _webview_window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=file_types
        )
    except Exception as e:
        logger.error("File dialog error: %s", e)
        return jsonify(error=str(e)), 500

    if not result:
        return jsonify(files=[])

    files = []
    for fp in result:
        fp = str(fp)
        ext = os.path.splitext(fp)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        files.append(video_file_payload(fp))

    return jsonify(files=files)


@app.post("/select-folder")
def select_folder():
    """Open native folder dialog via PyWebView for output location."""
    global _webview_window
    if not _webview_window:
        return jsonify(error="Window not ready"), 500

    try:
        result = _webview_window.create_file_dialog(webview.FOLDER_DIALOG)
    except Exception as e:
        logger.error("Folder dialog error: %s", e)
        return jsonify(error=str(e)), 500

    if not result:
        return jsonify(folder=None)

    folder = str(result[0]) if isinstance(result, (list, tuple)) else str(result)
    return jsonify(folder=folder)


@app.post("/compress")
def compress():
    """
    Start batch compression.
    Expects JSON: { files: ["/path/to/video.mp4", ...], preset: "balanced", output_dir: null }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify(error="Invalid request."), 400

    file_paths = data.get('files', [])
    preset = normalize_preset(data.get('preset', 'balanced'))
    output_dir = data.get('output_dir')  # None = same as source

    if not file_paths:
        return jsonify(error="No files selected."), 400
    if preset not in PRESETS:
        return jsonify(error="Invalid preset."), 400

    # Validate all files exist and have supported extensions
    validated = []
    for fp in file_paths:
        if not os.path.isfile(fp):
            return jsonify(error=f"File not found: {os.path.basename(fp)}"), 400
        ext = os.path.splitext(fp)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return jsonify(error=f"Unsupported format: {ext}"), 400
        validated.append(fp)

    # Validate output directory
    if output_dir and not os.path.isdir(output_dir):
        return jsonify(error="Output folder does not exist."), 400

    # Start batch
    batch_id, job_ids = batch_processor.start_batch(validated, preset, output_dir)
    if batch_id is None:
        return jsonify(error="A compression is already in progress."), 409

    return jsonify(batch_id=batch_id, job_ids=job_ids)


@app.get("/progress")
def progress():
    """Return current batch status including all jobs."""
    status = batch_processor.get_batch_status()
    if not status:
        return jsonify(error="No active batch."), 404
    return jsonify(status)


@app.get("/progress/<job_id>")
def job_progress(job_id):
    """Return a single job's progress (kept for compatibility)."""
    job = get_job(job_id)
    if not job:
        return jsonify(error="Job not found."), 404
    # Remove internal fields
    job.pop('_process', None)
    return jsonify(job)


@app.post("/cancel")
def cancel():
    """Cancel the active batch."""
    batch_processor.cancel_batch()
    return jsonify(ok=True)


@app.post("/cancel/<job_id>")
def cancel_single(job_id):
    """Cancel a specific job."""
    success = cancel_job(job_id)
    return jsonify(ok=success)


@app.post("/open-folder")
def open_folder_route():
    """Open a folder in the system file manager."""
    data = request.get_json(silent=True)
    if not data or not data.get('path'):
        return jsonify(error="No path provided."), 400

    path = data['path']
    # If it's a file path, open its parent directory
    if os.path.isfile(path):
        path = os.path.dirname(path)

    if not os.path.isdir(path):
        return jsonify(error="Folder not found."), 404

    open_folder(path)
    return jsonify(ok=True)


@app.get("/check-ffmpeg")
def check_ffmpeg():
    """Check if FFmpeg and FFprobe are available."""
    ff = get_ffmpeg_path()
    fp = get_ffprobe_path()
    return jsonify(
        ffmpeg=ff is not None,
        ffprobe=fp is not None,
        ffmpeg_path=ff,
        ffprobe_path=fp
    )


# Handle dropped files — get info for files dropped via drag & drop
@app.post("/file-info")
def file_info():
    """Get video info for a list of file paths (used by drag & drop)."""
    data = request.get_json(silent=True)
    if not data or not data.get('paths'):
        return jsonify(error="No paths provided."), 400

    files = []
    for fp in data['paths']:
        fp = str(fp)
        if not os.path.isfile(fp):
            continue
        ext = os.path.splitext(fp)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        files.append(video_file_payload(fp))

    return jsonify(files=files)


@app.post("/upload-dropped-files")
def upload_dropped_files():
    """Stage dropped files when the embedded browser does not expose paths."""
    uploads = request.files.getlist('files')
    if not uploads:
        return jsonify(error="No files were dropped."), 400

    staging_dir = tempfile.mkdtemp(prefix='video-compressor-drop-')
    _staging_dirs.add(staging_dir)
    files = []
    try:
        for upload in uploads:
            filename = secure_filename(upload.filename or '')
            ext = os.path.splitext(filename)[1].lower()
            if not filename or ext not in SUPPORTED_EXTENSIONS:
                continue

            destination = os.path.join(staging_dir, filename)
            stem, suffix = os.path.splitext(filename)
            sequence = 2
            while os.path.exists(destination):
                destination = os.path.join(staging_dir, f"{stem}-{sequence}{suffix}")
                sequence += 1

            upload.save(destination)
            files.append(video_file_payload(destination, uploaded=True))
    except OSError as e:
        logger.error("Could not stage dropped files: %s", e)
        shutil.rmtree(staging_dir, ignore_errors=True)
        _staging_dirs.discard(staging_dir)
        return jsonify(error="Could not save the dropped files."), 500

    if not files:
        shutil.rmtree(staging_dir, ignore_errors=True)
        _staging_dirs.discard(staging_dir)
        return jsonify(error="Drop a supported video file (MP4, MOV, MKV, WebM, M4V, or AVI)."), 400

    return jsonify(files=files)


# ── Startup ────────────────────────────────────────────────────────────

def find_free_port():
    """Find an available port for the Flask server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start_flask(port):
    """Run Flask in a background thread."""
    app.run(
        host='127.0.0.1',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )


def main():
    global _webview_window

    # Check FFmpeg availability early
    ffmpeg_path = get_ffmpeg_path()
    ffprobe_path = get_ffprobe_path()
    if not ffmpeg_path or not ffprobe_path:
        logger.warning(
            "FFmpeg/FFprobe not found. The app will show an error to the user."
        )

    port = find_free_port()
    logger.info("Starting Flask on port %d", port)

    # Start Flask in a daemon thread
    flask_thread = threading.Thread(
        target=start_flask,
        args=(port,),
        daemon=True
    )
    flask_thread.start()

    # Create PyWebView window
    _webview_window = webview.create_window(
        title="Likte",
        url=f"http://127.0.0.1:{port}",
        width=960,
        height=720,
        min_size=(780, 580),
        resizable=True,
        text_select=False,
    )

    # Start PyWebView (blocks until window is closed)
    webview.start(debug=not is_frozen())


if __name__ == "__main__":
    main()
