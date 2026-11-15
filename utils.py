"""Shared helpers for Viral Clip Studio (Phase 1).

Free stack only: stdlib + FFmpeg/ffprobe. No paid API, no network calls.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


def slugify(name: str) -> str:
    """Turn a style name into a safe filename slug, e.g. 'Global Baatien Style' -> 'global-baatien-style'."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "style").strip().lower()).strip("-")
    return slug or "style"


def probe_video(path: Path) -> dict:
    """Validate a file with ffprobe.

    Returns dict with keys: valid (bool), duration (float seconds),
    width, height, fps, has_audio (bool), codec, error (str or "").
    Never raises for a bad file — returns valid=False instead.
    Falls back to `ffmpeg -i` parsing if ffprobe is missing.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return {"valid": False, "duration": 0.0, "width": 0, "height": 0,
                "fps": 0.0, "has_audio": False, "codec": "", "error": "file missing or empty"}
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,codec_name,r_frame_rate",
                 "-show_entries", "format=duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams") or []
            fmt = data.get("format") or {}
            if not streams:
                return {"valid": False, "duration": 0.0, "width": 0, "height": 0,
                        "fps": 0.0, "has_audio": False, "codec": "",
                        "error": "no video stream found"}
            stream = streams[0]
            duration = float(fmt.get("duration") or 0)
            if duration <= 0:
                return {"valid": False, "duration": 0.0,
                        "width": int(stream.get("width") or 0),
                        "height": int(stream.get("height") or 0),
                        "fps": 0.0, "has_audio": False,
                        "codec": str(stream.get("codec_name") or ""),
                        "error": "duration is 0 — not a playable video"}
            # Audio presence: separate cheap probe.
            has_audio = False
            try:
                audio = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "a",
                     "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                    capture_output=True, text=True, timeout=15)
                has_audio = bool((audio.stdout or "").strip())
            except Exception:
                has_audio = False
            num, den = 0, 0
            try:
                num, den = (int(x) for x in str(stream.get("r_frame_rate") or "0/1").split("/"))
            except Exception:
                pass
            fps = (num / den) if den else 0.0
            return {"valid": True, "duration": duration,
                    "width": int(stream.get("width") or 0),
                    "height": int(stream.get("height") or 0),
                    "fps": fps, "has_audio": has_audio,
                    "codec": str(stream.get("codec_name") or ""), "error": ""}
        except Exception as exc:  # ffprobe crashed / bad JSON — treat as invalid
            return {"valid": False, "duration": 0.0, "width": 0, "height": 0,
                    "fps": 0.0, "has_audio": False, "codec": "", "error": str(exc)}
    # Fallback: ffmpeg -i (no ffprobe on PATH)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"valid": False, "duration": 0.0, "width": 0, "height": 0,
                "fps": 0.0, "has_audio": False, "codec": "",
                "error": "ffprobe and ffmpeg not found"}
    try:
        result = subprocess.run([ffmpeg, "-i", str(path)],
                                capture_output=True, text=True, timeout=30)
        text = (result.stderr or "") + (result.stdout or "")
        dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
        if not dur:
            return {"valid": False, "duration": 0.0, "width": 0, "height": 0,
                    "fps": 0.0, "has_audio": False, "codec": "",
                    "error": "ffmpeg could not read duration — not a valid video"}
        duration = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
        wh = re.search(r"(\d+)x(\d+)", text)
        return {"valid": duration > 0, "duration": duration,
                "width": int(wh.group(1)) if wh else 0,
                "height": int(wh.group(2)) if wh else 0,
                "fps": 0.0, "has_audio": "Audio:" in text, "codec": "", "error": ""}
    except Exception as exc:
        return {"valid": False, "duration": 0.0, "width": 0, "height": 0,
                "fps": 0.0, "has_audio": False, "codec": "", "error": str(exc)}


def ffmpeg_status() -> dict:
    """Return {'available': bool, 'path': str, 'version': str}."""
    executable = shutil.which("ffmpeg")
    if not executable:
        return {"available": False, "path": "", "version": "FFmpeg was not found on PATH"}
    try:
        result = subprocess.run([executable, "-version"], capture_output=True, text=True, timeout=5)
        first_line = (result.stdout or result.stderr).splitlines()[0]
        return {"available": result.returncode == 0, "path": executable, "version": first_line}
    except Exception as exc:
        return {"available": False, "path": executable, "version": str(exc)}
