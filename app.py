"""
Velocity backend — small Flask API that wraps yt-dlp.
Deploy this on a Docker-friendly host (Render, Railway, Fly.io).
Do NOT deploy this on Cloudflare Pages / Firebase Hosting — those are
static-only and cannot run yt-dlp or ffmpeg.
"""

import os
import uuid
import shutil
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)

# Lock this down to your actual frontend domain before going live.
CORS(app, resources={r"/api/*": {"origins": os.environ.get("ALLOWED_ORIGIN", "*")}})

# Simple shared-secret gate so randoms on the internet can't use your server.
ACCESS_KEY = os.environ.get("VELOCITY_ACCESS_KEY", "")

DOWNLOAD_ROOT = Path("/tmp/velocity_downloads")
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

QUALITY_MAP = {
    "best": "bestvideo*+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "audio": "bestaudio/best",
}


def _check_key():
    if not ACCESS_KEY:
        return True  # no key configured, gate disabled (fine for personal-only use behind auth elsewhere)
    supplied = request.headers.get("X-Access-Key", "")
    return supplied == ACCESS_KEY


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/download", methods=["POST"])
def download():
    if not _check_key():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = data.get("quality", "best")

    if not url:
        return jsonify({"error": "Missing 'url'"}), 400

    fmt = QUALITY_MAP.get(quality, QUALITY_MAP["best"])
    is_audio = quality == "audio"

    job_id = uuid.uuid4().hex[:12]
    job_dir = DOWNLOAD_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": fmt,
        "outtmpl": str(job_dir / "%(title).120B.%(ext)s"),
        "merge_output_format": "mp4" if not is_audio else None,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
    }
    if is_audio:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

    files = list(job_dir.glob("*"))
    if not files:
        return jsonify({"error": "Download produced no file"}), 500

    result_file = files[0]
    return jsonify({
        "job_id": job_id,
        "title": title,
        "filename": result_file.name,
        "download_url": f"/api/file/{job_id}/{result_file.name}",
    })


@app.route("/api/file/<job_id>/<path:filename>", methods=["GET"])
def get_file(job_id, filename):
    # No key check here so the browser's plain <a href> download works.
    # job_id is a random uuid, so this isn't guessable/listable.
    file_path = DOWNLOAD_ROOT / job_id / filename
    if not file_path.exists():
        return jsonify({"error": "Not found or expired"}), 404
    return send_file(file_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
