#!/usr/bin/env python3
"""Web UI for bulk-downloading an entire YouTube channel with yt-dlp.

A small Flask app: paste a channel URL in the browser, pick where to save, pick
options, and watch live progress. Downloads run in a background thread; a
per-channel download-archive ensures re-runs never miss or re-fetch a video.

Files are organized as:  <output folder>/<Channel Name>/<Year>/<video>
"""
import os
import re
import sys
import json
import time
import glob
import queue
import threading
from pathlib import Path

from flask import Flask, request, jsonify, Response
import yt_dlp

# Running as a frozen PyInstaller .exe behaves differently from a normal
# script: bundled files live in a temp dir, and there is no /downloads volume.
FROZEN = getattr(sys, "frozen", False)
DESKTOP = FROZEN or os.environ.get("DESKTOP") == "1"

CONFIG_PATH = Path.home() / ".channel_archiver.json"


def resource_path(name):
    """Path to a bundled data file (index.html), whether frozen or not."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def default_out_dir():
    """Where downloads go by default the very first time."""
    if os.environ.get("OUT_DIR"):
        return os.environ["OUT_DIR"]
    if DESKTOP:
        # Desktop app: a friendly folder in the user's home directory.
        return str(Path.home() / "Videos" / "Channels")
    return "/downloads"  # Docker volume


def find_ffmpeg():
    """Locate ffmpeg so yt-dlp can merge streams. In the .exe we bundle it via
    imageio-ffmpeg; otherwise rely on it being on PATH (as in the Docker image)."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return os.path.dirname(exe)
    except Exception:
        pass
    return None


def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# Current output base folder — user-configurable at runtime, remembered across
# launches via the config file.
output_dir = _load_config().get("output_dir") or default_out_dir()
try:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
except Exception:
    output_dir = default_out_dir()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

FFMPEG_DIR = find_ffmpeg()

app = Flask(__name__, static_folder=None)

# --------------------------------------------------------------------------- #
# Job state — a single download job at a time, guarded by a lock.
# --------------------------------------------------------------------------- #
_lock = threading.Lock()
_thread = None
_stop_flag = threading.Event()


def _fresh_state():
    return {
        "status": "idle",          # idle|preparing|downloading|stopping|done|error
        "channel": "",
        "channel_name": "",
        "dest": "",                # folder this channel is saved to
        "target": "",
        "total": 0,                # videos found in the channel
        "completed": 0,            # downloaded this run
        "skipped": 0,              # already in archive
        "errors": 0,
        "current": {
            "title": "", "index": 0, "n_entries": 0,
            "percent": 0.0, "speed": "", "eta": "", "size": "",
        },
        "log": [],
        "message": "",
        "started_at": None,
        "finished_at": None,
    }


state = _fresh_state()


def log(line):
    with _lock:
        state["log"].append({"t": time.strftime("%H:%M:%S"), "m": str(line)})
        if len(state["log"]) > 500:
            state["log"] = state["log"][-500:]


class _Logger:
    """Feeds yt-dlp's own messages into our state (skips/errors/notices)."""
    def debug(self, msg):
        if "has already been recorded in the archive" in msg:
            with _lock:
                state["skipped"] += 1

    def info(self, msg):
        pass

    def warning(self, msg):
        log("⚠ " + msg)

    def error(self, msg):
        with _lock:
            state["errors"] += 1
        log("✗ " + msg)


def _safe_name(name):
    """Turn a channel title into a folder name that's valid on Windows/macOS/Linux."""
    name = re.sub(r"\s*-\s*Videos$", "", (name or "").strip(), flags=re.I)
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or "channel"


def _channel_name(info):
    return (info.get("channel") or info.get("uploader")
            or info.get("title") or "channel")


def _archive_count(archive_path):
    try:
        with open(archive_path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def _progress_hook(d):
    if _stop_flag.is_set():
        raise yt_dlp.utils.DownloadCancelled("Stopped by user")

    info = d.get("info_dict", {}) or {}
    with _lock:
        cur = state["current"]
        cur["title"] = info.get("title") or cur["title"]
        cur["index"] = info.get("playlist_index") or cur["index"]
        cur["n_entries"] = info.get("n_entries") or state["total"] or cur["n_entries"]

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            cur["percent"] = round(done / total * 100, 1) if total else 0.0
            cur["speed"] = (d.get("_speed_str") or "").strip()
            cur["eta"] = (d.get("_eta_str") or "").strip()
            cur["size"] = (d.get("_total_bytes_str")
                           or d.get("_total_bytes_estimate_str") or "").strip()
        elif d["status"] == "finished":
            cur["percent"] = 100.0


def _normalize_target(url):
    url = url.strip()
    tails = ("/videos", "/streams", "/shorts", "/featured")
    if url.endswith(tails) or "playlist?list=" in url or "/watch?v=" in url:
        return url
    return url.rstrip("/") + "/videos"


def _build_opts(channel_dir, archive_path, fmt, audio_only, embed_subs, cookies):
    archive_before = _archive_count(archive_path)

    def completed_tracker(d):
        if d["status"] == "finished":
            with _lock:
                state["completed"] = max(0, _archive_count(archive_path) - archive_before)

    # <channel>/<year>/<date> - <title> [id].ext  (year is "NA" if unknown)
    outtmpl = os.path.join(
        channel_dir,
        "%(upload_date>%Y)s",
        "%(upload_date>%Y-%m-%d)s - %(title)s [%(id)s].%(ext)s",
    )
    opts = {
        "outtmpl": outtmpl,
        "download_archive": archive_path,
        "playlistreverse": True,           # oldest first
        "ignoreerrors": True,
        "continuedl": True,
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
        "writethumbnail": True,
        "writeinfojson": True,
        "logger": _Logger(),
        "progress_hooks": [_progress_hook, completed_tracker],
        "sleep_interval_requests": 1,
        "sleep_interval": 2,
        "max_sleep_interval": 8,
        "postprocessors": [],
    }
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR

    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"].append(
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": "0"}
        )
    else:
        opts["format"] = fmt or "bestvideo*+bestaudio/best"
        opts["merge_output_format"] = "mp4"

    opts["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})
    if not audio_only:
        opts["postprocessors"].append(
            {"key": "EmbedThumbnail", "already_have_thumbnail": False}
        )
    if embed_subs and not audio_only:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["en.*"]
        opts["postprocessors"].append(
            {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False}
        )

    if cookies:
        cpath = os.path.join(output_dir, "cookies.txt")
        if os.path.exists(cpath):
            opts["cookiefile"] = cpath
        else:
            log(f"⚠ cookies enabled but {cpath} not found — ignoring")
    return opts


def _run(channel_url, params):
    try:
        target = _normalize_target(channel_url)
        with _lock:
            state["channel"] = channel_url
            state["target"] = target
            state["status"] = "preparing"
            state["message"] = "Scanning channel for all videos…"
        log(f"Scanning {target}")

        # First pass: a flat extract to count videos and learn the channel name.
        with yt_dlp.YoutubeDL(
            {"quiet": True, "extract_flat": "in_playlist", "skip_download": True,
             "logger": _Logger()}
        ) as ydl:
            info = ydl.extract_info(target, download=False)
        entries = [e for e in (info.get("entries") or []) if e]

        # Build the per-channel destination folder and its own archive file.
        channel_name = _channel_name(info)
        channel_dir = os.path.join(output_dir, _safe_name(channel_name))
        Path(channel_dir).mkdir(parents=True, exist_ok=True)
        archive_path = os.path.join(channel_dir, "downloaded.txt")

        with _lock:
            state["channel_name"] = channel_name
            state["dest"] = channel_dir
            state["total"] = len(entries)
            state["status"] = "downloading"
            state["message"] = (f"Found {len(entries)} videos in “{channel_name}”. "
                                f"Downloading oldest first into year folders…")
        log(f"Channel: {channel_name}")
        log(f"Saving to: {channel_dir}")
        log(f"Found {len(entries)} videos. Starting download (oldest first).")

        if _stop_flag.is_set():
            raise yt_dlp.utils.DownloadCancelled()

        opts = _build_opts(channel_dir, archive_path, **params)

        # Second pass: the real download.
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([target])

        with _lock:
            if _stop_flag.is_set():
                state["status"] = "idle"
                state["message"] = "Stopped."
            else:
                state["status"] = "done"
                state["message"] = (
                    f"Finished. {state['completed']} downloaded, "
                    f"{state['skipped']} already had, {state['errors']} errors."
                )
            state["finished_at"] = time.time()
        log("Done.")
    except yt_dlp.utils.DownloadCancelled:
        with _lock:
            state["status"] = "idle"
            state["message"] = "Stopped by user."
            state["finished_at"] = time.time()
        log("Stopped by user.")
    except Exception as e:  # noqa: BLE001 — surface anything to the UI
        with _lock:
            state["status"] = "error"
            state["message"] = f"Error: {e}"
            state["finished_at"] = time.time()
        log("✗ " + str(e))


# --------------------------------------------------------------------------- #
# Native folder picker (desktop only) — a Tk dialog pumped on the main thread.
# --------------------------------------------------------------------------- #
_tk_ready = threading.Event()
_browse_q = queue.Queue()
_browse_results = {}
_browse_n = [0]


def _run_tk_loop():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    _tk_ready.set()

    def pump():
        try:
            while True:
                rid, initial = _browse_q.get_nowait()
                root.update()
                path = filedialog.askdirectory(
                    initialdir=initial if os.path.isdir(initial) else None,
                    title="Choose where to save downloaded videos",
                    mustexist=False,
                )
                _browse_results[rid] = path or ""
        except queue.Empty:
            pass
        root.after(150, pump)

    root.after(150, pump)
    root.mainloop()


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global output_dir
    if request.method == "POST":
        if state["status"] in ("preparing", "downloading", "stopping"):
            return jsonify({"ok": False, "error": "Can't change the folder mid-download."}), 409
        data = request.get_json(force=True, silent=True) or {}
        p = (data.get("output_dir") or "").strip()
        if not p:
            return jsonify({"ok": False, "error": "Please provide a folder path."}), 400
        try:
            Path(p).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Can't use that folder: {e}"}), 400
        output_dir = p
        cfg = _load_config()
        cfg["output_dir"] = p
        _save_config(cfg)
    return jsonify({"ok": True, "output_dir": output_dir, "desktop": DESKTOP,
                    "can_browse": _tk_ready.is_set()})


@app.route("/api/browse", methods=["POST"])
def api_browse():
    if not _tk_ready.is_set():
        return jsonify({"ok": False,
                        "error": "Folder picker isn't available here — type the path instead."}), 400
    _browse_n[0] += 1
    rid = _browse_n[0]
    _browse_q.put((rid, output_dir))
    for _ in range(1800):  # up to ~3 minutes for the user to choose
        if rid in _browse_results:
            return jsonify({"ok": True, "path": _browse_results.pop(rid)})
        time.sleep(0.1)
    return jsonify({"ok": False, "error": "Folder picker timed out."})


@app.route("/api/start", methods=["POST"])
def api_start():
    global _thread
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Please paste a channel URL."}), 400

    with _lock:
        if state["status"] in ("preparing", "downloading", "stopping"):
            return jsonify({"ok": False, "error": "A download is already running."}), 409
        st = _fresh_state()
        st["started_at"] = time.time()
        state.clear()
        state.update(st)

    _stop_flag.clear()
    params = {
        "fmt": data.get("format"),
        "audio_only": bool(data.get("audioOnly")),
        "embed_subs": bool(data.get("subs", True)),
        "cookies": bool(data.get("cookies")),
    }
    _thread = threading.Thread(target=_run, args=(url, params), daemon=True)
    _thread.start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    _stop_flag.set()
    with _lock:
        if state["status"] in ("preparing", "downloading"):
            state["status"] = "stopping"
            state["message"] = "Stopping after the current video…"
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(state)


@app.route("/api/files")
def api_files():
    exts = ("*.mp4", "*.mkv", "*.webm", "*.mp3", "*.m4a")
    files = []
    for ext in exts:
        for p in glob.glob(os.path.join(output_dir, "**", ext), recursive=True):
            try:
                files.append({
                    "name": os.path.relpath(p, output_dir),
                    "size": os.path.getsize(p),
                    "mtime": os.path.getmtime(p),
                })
            except OSError:
                pass
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"files": files[:200], "count": len(files)})


# --------------------------------------------------------------------------- #
# Frontend (single inlined page)
# --------------------------------------------------------------------------- #
with open(resource_path("index.html"), "r", encoding="utf-8") as _f:
    INDEX_HTML = _f.read()


def _serve(host, port):
    # Prefer waitress (a real WSGI server, bundled in the .exe) over Flask's
    # dev server; fall back to the dev server when waitress isn't installed.
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8, _quiet=True)
    except ImportError:
        app.run(host=host, port=port, threaded=True)


def main():
    host = "127.0.0.1" if DESKTOP else "0.0.0.0"
    port = int(os.environ.get("PORT", "8000"))

    if not DESKTOP:
        _serve(host, port)
        return

    # Desktop: find a free port, open the browser, run the server in the
    # background, and keep the main thread for the native folder picker.
    import socket
    import webbrowser
    s = socket.socket()
    try:
        s.bind((host, port))
        s.close()
    except OSError:
        s2 = socket.socket(); s2.bind((host, 0)); port = s2.getsockname()[1]; s2.close()

    url = f"http://{host}:{port}/"
    print("=" * 60)
    print(" Channel Archiver is running.")
    print(f" Open {url} in your browser if it didn't open automatically.")
    print(f" Saving videos under: {output_dir}")
    print(" Close this window to quit.")
    print("=" * 60)

    threading.Thread(target=_serve, args=(host, port), daemon=True).start()
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        _run_tk_loop()          # blocks; services folder-picker requests
    except Exception:
        # No GUI toolkit available — keep the server alive without a picker.
        threading.Event().wait()


if __name__ == "__main__":
    main()
