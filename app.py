"""Viral Clip Studio — Phase 2 application: upload + style + transcribe + clips.

Phase 0 (verified): FastAPI, FFmpeg check, upload intake, SQLite metadata.
Phase 1 (verified): real style_extractor, ffprobe upload validation, style API,
  Style Card UI, default Global Baatien profile.
Phase 2 (new): local Whisper transcription (/transcribe) + viral clip
  finding (/find-clips, /clips) with Gemini-optional rule-based fallback.
Phase 3 (new): real 9:16 editor (/render-clip, /rendered) — reframe,
  zoom punch, colour grade, ASS karaoke captions, yuv420p export.
Phase 4 (new): workflow / history / batch / test-clip approval —
  project status, /approve-clip + /reject-clip, /render-all with an
  approval guard, /batch (background transcribe + find-clips),
  /projects + DELETE /projects/{id}.
Phase 5 (new): download/share — per-clip + whole-project ZIPs,
  caption JSON for copy, share guide; per-upload render namespace.
Phase 6 (not yet): GitHub + Render deploy (needs user's GitHub username).
V2 (16 Nov 2026): YouTube link -> fully edited clips, automatically —
  POST /youtube-job + GET /youtube-job/{id} (background worker in
  youtube_job.py: yt-dlp download -> transcribe -> find-clips ->
  render test clip -> approve -> render-all -> project ZIP).
"""
from __future__ import annotations

import asyncio
import io
import os
import sqlite3
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from style_extractor import (ensure_default_profile, extract_style,
                             list_styles, load_style, save_profile)
from transcriber import load_transcript, transcribe
from clip_finder import find_clips, load_clips
from editor import find_render_info, load_render_info, render_clip
from utils import ffmpeg_status, probe_video, slugify
from workflow import (create_batch, delete_project, get_batch,
                      get_clip_approvals, get_project_status,
                      has_approved_clip, init_workflow_tables,
                      is_clip_rendered_for_upload, list_projects,
                      list_rendered_for_upload, run_batch,
                      set_clip_approval)
from youtube_job import (create_youtube_job, get_youtube_job,
                         init_youtube_jobs_table, run_youtube_job,
                         validate_youtube_url, youtube_runtime_diagnostics)

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
THUMB_DIR = OUTPUT_DIR / "thumbnails"
STYLES_DIR = ROOT / "styles"
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "viral_clip_studio.sqlite3"
TEMPLATE_PATH = ROOT / "templates" / "index.html"
VIDEO_EXTENSIONS = {".mp4", ".mov"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB per file
CHUNK_SIZE = 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_default_profile(STYLES_DIR)
    status = ffmpeg_status()
    if status["available"]:
        print(f"[startup] FFmpeg OK: {status['version']}", flush=True)
    else:
        print(f"[startup] WARNING: {status['version']}", flush=True)
    yield


app = FastAPI(title="Viral Clip Studio", version="0.6.0-v2", lifespan=lifespan)


def init_db() -> None:
    for d in (UPLOAD_DIR, OUTPUT_DIR, THUMB_DIR, STYLES_DIR, STATIC_DIR,
              OUTPUT_DIR / "rendered"):
        d.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""")
        conn.commit()
    init_workflow_tables()
    init_youtube_jobs_table()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    if not TEMPLATE_PATH.is_file():
        raise HTTPException(status_code=500, detail="UI template is missing")
    return HTMLResponse(TEMPLATE_PATH.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> JSONResponse:
    # youtube diagnostics are cached in youtube_job (one yt-dlp
    # --version + one --list-impersonate-targets at first call) —
    # cheap, and they prove on the LIVE instance whether node and
    # browser impersonation are actually available there.
    return JSONResponse({"status": "ok", "phase": 5,
                         "ffmpeg": ffmpeg_status(),
                         "youtube": youtube_runtime_diagnostics()})


def _get_upload_row(upload_id: str) -> dict | None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT id, role, original_name, stored_path, size_bytes FROM uploads WHERE id=?",
            (upload_id,)).fetchone()
    if not row:
        return None
    return {"id": row[0], "role": row[1], "original_name": row[2],
            "stored_path": row[3], "size_bytes": row[4]}


async def save_upload(file: UploadFile, role: str) -> dict:
    """Save an upload, then validate it with ffprobe.

    Rejects non-video / 0-duration files with a clear 400 error
    'Ye valid video file nahi hai' and deletes the bad file — Phase 0
    accepted a fake 4-byte MP4, Phase 1 does not.
    """
    original_name = Path(file.filename or "video").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"{role}: only MP4 and MOV files are accepted")

    upload_id = uuid.uuid4().hex
    destination = UPLOAD_DIR / f"{upload_id}{suffix}"
    size = 0
    try:
        with destination.open("wb") as target:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"{role}: file exceeds 8 GiB limit")
                target.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail=f"{role}: uploaded file is empty")
        # --- Phase 1 validation (new) ---
        probe = probe_video(destination)
        if not probe["valid"]:
            destination.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=f"Ye valid video file nahi hai ({role}): {probe['error']}")
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO uploads VALUES (?, ?, ?, ?, ?, ?)",
                (upload_id, role, original_name, str(destination.relative_to(ROOT)), size,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        return {"id": upload_id, "role": role, "filename": original_name,
                "size_bytes": size, "duration_sec": round(probe["duration"], 2),
                "width": probe["width"], "height": probe["height"],
                "has_audio": probe["has_audio"]}
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.post("/upload")
async def upload_videos(
    long_video: UploadFile = File(...),
    reference_video: UploadFile | None = File(default=None),
) -> dict:
    primary = await save_upload(long_video, "long video")
    reference = await save_upload(reference_video, "reference video") if reference_video else None
    return {"message": "Upload saved and validated. Style extraction is available at POST /extract-style.",
            "long_video": primary, "reference_video": reference}


@app.post("/extract-style")
async def extract_style_endpoint(
    reference_video: UploadFile | None = File(default=None),
    reference_id: str | None = Form(default=None),
    style_name: str = Form(default="Extracted Style"),
) -> dict:
    """Extract a style profile from a reference video.

    Accepts either a fresh upload (reference_video file) or an already
    uploaded reference (reference_id from /upload). Returns the 22-field
    profile + measured analysis. The profile JSON is saved under styles/.
    """
    if reference_video is not None and reference_video.filename:
        saved = await save_upload(reference_video, "reference video")
        video_path = ROOT / _get_upload_row(saved["id"])["stored_path"]
    elif reference_id:
        row = _get_upload_row(reference_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"reference upload not found: {reference_id}")
        video_path = ROOT / row["stored_path"]
        if not video_path.is_file():
            raise HTTPException(status_code=404, detail="reference file missing on disk")
    else:
        raise HTTPException(
            status_code=400,
            detail="reference video file ya reference_id me se ek do")
    try:
        result = extract_style(video_path, style_name=style_name or "Extracted Style",
                               styles_dir=STYLES_DIR, thumb_dir=THUMB_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "Style extracted and saved.",
            "profile": result["profile"], "analysis": result["analysis"]}


@app.get("/styles")
def list_styles_endpoint() -> dict:
    items = list_styles(STYLES_DIR)
    return {"count": len(items),
            "styles": [{"name": i["name"], "slug": i["slug"], "file": i["file"]} for i in items]}


@app.get("/styles/{name}")
def get_style_endpoint(name: str) -> dict:
    try:
        return {"profile": load_style(name, STYLES_DIR)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Style not found: {name}")


@app.post("/styles")
async def save_style_endpoint(profile: dict) -> dict:
    """Save / approve an edited style profile (Style Card Approve)."""
    try:
        out = save_profile(profile, STYLES_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "Style saved.", "file": out.name,
            "slug": slugify(str(profile.get("style_name", "")))}


@app.post("/transcribe")
async def transcribe_endpoint(
    upload_id: str = Form(...),
    model: str | None = Form(default=None),
    initial_prompt: str | None = Form(default=None),
    language: str = Form(default="en"),
) -> dict:
    """Transcribe an uploaded long video with local Whisper.

    DEFAULT LANGUAGE IS ENGLISH (user correction, Phase 4): language
    "en" forces English decoding, "hinglish" = Roman Hinglish,
    "hi" = Hindi. This is a decoding language, NOT a translator —
    Hindi audio forced to "en" will not become clean English; the
    response records language_requested + language_note honestly.
    """
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    video_path = ROOT / row["stored_path"]
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="uploaded file missing on disk")
    try:
        # CPU work runs in a worker thread (Phase 6 fix, 15 Nov 2026):
        # a sync transcribe inside this async endpoint blocked the
        # event loop for minutes — /health hung and Render's proxy
        # cut the client connection while the server was still
        # working. to_thread keeps the loop (and /health) responsive.
        result = await asyncio.to_thread(
            transcribe, video_path, upload_id, model_name=model or None,
            initial_prompt=initial_prompt or None,
            language=language or "en")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "message": "Transcript ready.",
        "upload_id": upload_id,
        "model": result["model"],
        "language": result["language"],
        "language_requested": result.get("language_requested", "en"),
        "language_note": result.get("language_note", ""),
        "transcript_script": result.get("transcript_script", "latin"),
        "duration_sec": result["duration_sec"],
        "word_count": result["word_count"],
        "word_timestamps_real": result["word_timestamps_real"],
        "text_preview": result["text"][:500],
        "transcript_json": result["transcript_json"],
        "transcript_txt": result["transcript_txt"],
    }


@app.get("/transcript/{upload_id}")
def get_transcript_endpoint(upload_id: str) -> dict:
    try:
        return load_transcript(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/find-clips")
async def find_clips_endpoint(
    upload_id: str = Form(...),
    top_n: int = Form(default=5),
    style_name: str | None = Form(default=None),
    language: str | None = Form(default=None),
) -> dict:
    """Find top viral clips for an upload.

    Runs transcription first if no transcript exists yet (in English
    by default). Scoring is Gemini if GEMINI_API_KEY is set, else
    rule-based — the response always says which one was used
    (scoring_method) and which language bank scored (scoring_language).
    """
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    video_path = ROOT / row["stored_path"]
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="uploaded file missing on disk")
    try:
        transcript = load_transcript(upload_id)
    except FileNotFoundError:
        try:
            # Same worker-thread rule as /transcribe (see above).
            transcript = await asyncio.to_thread(
                transcribe, video_path, upload_id,
                language=language or "en")
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not transcript.get("words"):
        raise HTTPException(
            status_code=400,
            detail="Transcript khaali hai — video me boli hui baat nahi mili, clips nahi ban sakte")
    result = find_clips(transcript, upload_id, top_n=top_n, style_name=style_name,
                        language=language or transcript.get("language_requested") or "en")
    return {"message": "Clips ready.", **result}


@app.get("/clips/{upload_id}")
def get_clips_endpoint(upload_id: str) -> dict:
    try:
        return load_clips(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/render-clip")
async def render_clip_endpoint(
    upload_id: str = Form(...),
    clip_id: str = Form(...),
    style_name: str = Form(default="global-baatien"),
) -> dict:
    """Render ONE clip to 1080x1920 with captions + grading.

    Runs in a worker thread (asyncio.to_thread) — a ~47s clip takes
    1-3 min on CPU. Returns render metadata (paths, resolution,
    caption script, reframe mode, etc.).
    """
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    video_path = ROOT / row["stored_path"]
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="uploaded file missing on disk")
    try:
        clips_data = load_clips(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    clip = next((c for c in clips_data.get("clips", []) if c.get("clip_id") == clip_id), None)
    if not clip:
        raise HTTPException(
            status_code=404,
            detail=f"clip not found: {clip_id} — pehle POST /find-clips karo")
    try:
        transcript = load_transcript(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        # Worker thread, same reason as /transcribe (see above) —
        # a ~47s clip takes 1-3 min on CPU.
        meta = await asyncio.to_thread(
            render_clip, video_path, upload_id, clip,
            style_name=style_name or "global-baatien",
            transcript=transcript)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"message": "Clip rendered.", **meta}


def _render_info(clip_id: str, upload_id: str | None = None) -> dict:
    """Load a render sidecar, namespaced first, legacy fallback."""
    try:
        return load_render_info(clip_id, upload_id=upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/rendered/{upload_id}/{clip_id}")
def download_rendered_namespaced_endpoint(upload_id: str, clip_id: str):
    """Download a rendered MP4 (Phase 5 namespaced path)."""
    info = _render_info(clip_id, upload_id)
    path = Path(info["output_mp4"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="rendered file missing on disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/rendered/{upload_id}/{clip_id}/srt")
def download_srt_namespaced_endpoint(upload_id: str, clip_id: str):
    """Download a rendered clip's SRT captions (namespaced)."""
    info = _render_info(clip_id, upload_id)
    path = Path(info["output_srt"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="SRT file missing on disk")
    return FileResponse(path, media_type="text/plain", filename=path.name)


@app.get("/rendered/{upload_id}/{clip_id}/info")
def render_info_namespaced_endpoint(upload_id: str, clip_id: str) -> dict:
    return _render_info(clip_id, upload_id)


@app.get("/rendered/{clip_id}")
def download_rendered_endpoint(clip_id: str, upload_id: str | None = None):
    """Download a rendered MP4 (legacy flat path — still works).

    Pass ?upload_id=<id> to disambiguate when two uploads both
    rendered clip_01 (Phase 5 namespace); without it the first
    sidecar found is returned.
    """
    info = _render_info(clip_id, upload_id)
    path = Path(info["output_mp4"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="rendered file missing on disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/rendered/{clip_id}/srt")
def download_srt_endpoint(clip_id: str, upload_id: str | None = None):
    """Download a rendered clip's SRT captions (legacy path)."""
    info = _render_info(clip_id, upload_id)
    path = Path(info["output_srt"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="SRT file missing on disk")
    return FileResponse(path, media_type="text/plain", filename=path.name)


@app.get("/rendered/{clip_id}/info")
def render_info_endpoint(clip_id: str, upload_id: str | None = None) -> dict:
    return _render_info(clip_id, upload_id)


# ---------------------------------------------------------- Phase 5: download / share

def _clip_files(upload_id: str, clip_id: str) -> dict:
    """All on-disk files for one rendered clip (namespaced or legacy)."""
    info = _render_info(clip_id, upload_id)
    files: dict[str, Path] = {}
    for key, label in (("output_mp4", "mp4"), ("output_srt", "srt"),
                       ("output_ass", "ass"), ("output_txt", "txt"),
                       ("output_caption_txt", "caption_txt"),
                       ("output_json", "json")):
        path = Path(str(info.get(key, "")))
        if path.is_file():
            files[label] = path
    if "mp4" not in files:
        raise HTTPException(status_code=404, detail="rendered file missing on disk")
    return {"info": info, "files": files}


def _zip_bytes(entries: list[tuple[str, Path]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in entries:
            zf.write(path, arcname)
    return buf.getvalue()


# NOTE (Phase 5 fix, 14 Nov 2026): this literal "/all" route MUST stay
# BEFORE "/download/{upload_id}/{clip_id}" below — FastAPI matches
# routes in order, and "{clip_id}" would otherwise swallow "all"
# (returned 404 "Rendered clip nahi mila: all" in the fresh E2E test).
@app.get("/download/{upload_id}/all")
def download_all_zip_endpoint(upload_id: str):
    """ZIP of ALL rendered clips of a project (one folder per clip)."""
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    rendered = list_rendered_for_upload(upload_id)
    if not rendered:
        raise HTTPException(status_code=404, detail="Is project ka koi rendered clip nahi hai — pehle render karo")
    arc = {"mp4": ".mp4", "srt": ".srt", "ass": ".ass", "txt": ".txt",
           "caption_txt": "_caption.txt", "json": ".json"}
    entries: list[tuple[str, Path]] = []
    for meta in sorted(rendered, key=lambda m: str(m.get("clip_id"))):
        cid = str(meta.get("clip_id"))
        for key, ext in (("output_mp4", "mp4"), ("output_srt", "srt"),
                         ("output_ass", "ass"), ("output_txt", "txt"),
                         ("output_caption_txt", "caption_txt"),
                         ("output_json", "json")):
            path = Path(str(meta.get(key, "")))
            if path.is_file():
                entries.append((f"{cid}/{cid}{arc[ext]}", path))
    if not entries:
        raise HTTPException(status_code=404, detail="Rendered files disk par nahi mile")
    return _zip_response(_zip_bytes(entries), f"{upload_id}_all_clips.zip")


@app.get("/download/{upload_id}/{clip_id}")
def download_clip_zip_endpoint(upload_id: str, clip_id: str):
    """ZIP: MP4 + SRT + ASS + transcript .txt + caption .txt + style JSON.

    Style JSON is the style profile used for the render (from
    styles/<slug>.json), falling back to the render sidecar JSON.
    """
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    got = _clip_files(upload_id, clip_id)
    info, files = got["info"], got["files"]
    arc = {"mp4": f"{clip_id}.mp4", "srt": f"{clip_id}.srt",
           "ass": f"{clip_id}.ass", "txt": f"{clip_id}.txt",
           "caption_txt": f"{clip_id}_caption.txt", "json": f"{clip_id}.json"}
    entries = [(arc[ext], path) for ext, path in files.items() if ext in arc]
    slug = str(info.get("style_slug") or "global-baatien")
    style_path = STYLES_DIR / f"{slug}.json"
    if style_path.is_file():
        entries.append((f"{clip_id}_style_{slug}.json", style_path))
    data = _zip_bytes(entries)
    return _zip_response(data, f"{upload_id}_{clip_id}.zip")


def _zip_response(data: bytes, filename: str):
    from fastapi.responses import Response
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/caption/{upload_id}/{clip_id}")
def caption_endpoint(upload_id: str, clip_id: str) -> dict:
    """Title + caption + hashtags as JSON, ready for the Copy button.

    Parsed from the clip's _caption.txt (title = first line, hashtags
    = last #-line). Falls back to the clip hook + transcript text.
    Caption text is the transcript text as Whisper returned it —
    English words when the audio was English.
    """
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    got = _clip_files(upload_id, clip_id)
    info, files = got["info"], got["files"]
    title = str(info.get("hook_line") or "Viral clip")
    caption = ""
    hashtags = ""
    cap_path = files.get("caption_txt")
    if cap_path is not None:
        lines = cap_path.read_text(encoding="utf-8").splitlines()
        lines = [ln for ln in lines if ln.strip() != ""]
        if lines:
            title = lines[0].strip()
            tag_lines = [ln for ln in lines if ln.strip().startswith("#")]
            if tag_lines:
                hashtags = tag_lines[-1].strip()
                body = [ln for ln in lines[1:] if not ln.strip().startswith("#")]
            else:
                body = lines[1:]
            caption = "\n".join(body).strip()
    if not caption:
        try:
            clips_data = load_clips(upload_id)
            clip = next((c for c in clips_data.get("clips", []) if c.get("clip_id") == clip_id), None)
            if clip:
                caption = str(clip.get("transcript_text") or "")[:300]
        except FileNotFoundError:
            caption = ""
    if not hashtags:
        hashtags = "#viralreels #reels #shorts #trendingnow"
    return {"upload_id": upload_id, "clip_id": clip_id, "title": title,
            "caption": caption, "hashtags": hashtags,
            "full_text": f"{title}\n\n{caption}\n\n{hashtags}".strip(),
            "language_note": info.get("language_note", "")}


@app.get("/share-guide", response_class=HTMLResponse)
def share_guide_endpoint() -> HTMLResponse:
    """How to post a rendered clip to Instagram / Facebook from phone.

    No Meta API posting in Phase 5 — no API keys are configured, so
    the honest flow is download -> phone -> post. Said plainly here
    and in the README, not hidden.
    """
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Share Guide — Viral Clip Studio</title>
<style>body{background:#09090b;color:#f4f4f5;font-family:sans-serif;max-width:640px;margin:40px auto;padding:0 20px;line-height:1.6}</style>
</head><body>
<h1>Instagram / Facebook par kaise post karein</h1>
<p><b>Note:</b> Is app me abhi direct auto-post NAHI hai — Meta API keys
set nahi hain. Download karke phone se post karo, 2 minute ka kaam hai.</p>
<ol>
<li><b>Download karo:</b> History ya Render section se clip ka ZIP download karo (MP4 + SRT + caption txt andar hai), ya sirf MP4 download karo.</li>
<li><b>Phone par bhejo:</b> ZIP/MP4 ko WhatsApp, Telegram, Google Drive ya USB se apne phone me bhej do. ZIP ko phone par unzip karo.</li>
<li><b>Instagram:</b> Instagram app &rarr; + &rarr; Reel &rarr; video chuno &rarr; caption file se title + caption + hashtags copy-paste karo &rarr; Share.</li>
<li><b>Facebook:</b> Facebook app &rarr; + &rarr; Reel &rarr; video chuno &rarr; wahi caption paste karo &rarr; Share.</li>
<li><b>Best time:</b> Shaam 7–9 PM IST par post karo, aur pehle 1 ghante me aaye comments ka reply do.</li>
</ol>
<p>Export specs: 1080x1920 (9:16), 30fps, H.264, yuv420p — Instagram aur Facebook Reels dono ke liye ready.</p>
<p><a href="/" style="color:#a78bfa">← Wapas Viral Clip Studio par jao</a></p>
</body></html>"""
    return HTMLResponse(html)


@app.get("/projects")
def list_projects_endpoint() -> dict:
    """All past projects with status + clips + renders + downloads."""
    projects = list_projects()
    return {"count": len(projects), "projects": projects}


@app.get("/projects/{upload_id}")
def get_project_endpoint(upload_id: str) -> dict:
    try:
        return get_project_status(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/projects/{upload_id}")
def delete_project_endpoint(upload_id: str) -> dict:
    """Delete one project and its files (upload, transcript, clips,
    its own renders, approvals). Rendered files of OTHER uploads are
    never touched — delete only removes files whose sidecar upload_id
    matches."""
    try:
        result = delete_project(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"message": "Project deleted.", **result}


@app.post("/approve-clip")
async def approve_clip_endpoint(
    upload_id: str = Form(...),
    clip_id: str = Form(...),
) -> dict:
    """Approve ONE test clip. This unlocks POST /render-all."""
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    try:
        clips_data = load_clips(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not any(c.get("clip_id") == clip_id for c in clips_data.get("clips", [])):
        raise HTTPException(status_code=404, detail=f"clip not found: {clip_id}")
    result = set_clip_approval(upload_id, clip_id, "approved")
    return {"message": "Test clip approved — ab POST /render-all se baaki clips render kar sakte ho.",
            **result}


@app.post("/reject-clip")
async def reject_clip_endpoint(
    upload_id: str = Form(...),
    clip_id: str = Form(...),
    reason: str = Form(default=""),
) -> dict:
    """Reject a clip (it stays in the list, marked rejected)."""
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    try:
        clips_data = load_clips(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not any(c.get("clip_id") == clip_id for c in clips_data.get("clips", [])):
        raise HTTPException(status_code=404, detail=f"clip not found: {clip_id}")
    result = set_clip_approval(upload_id, clip_id, "rejected", reason=reason or "")
    return {"message": "Clip rejected.", **result}


@app.post("/render-all")
async def render_all_endpoint(
    upload_id: str = Form(...),
    style_name: str = Form(default="global-baatien"),
) -> dict:
    """Render ALL clips of an upload, sequentially.

    GUARD: works ONLY after at least one test clip was approved
    (POST /approve-clip). Without approval -> 400
    "Pehle 1 test clip approve karo". Already-rendered clips (sidecar
    upload_id matches) are skipped, not re-rendered. Each clip runs
    in a worker thread — 1-3 min on CPU per clip, so a 4-clip video
    takes ~8 min; /health stays responsive throughout.
    """
    row = _get_upload_row(upload_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")
    if not has_approved_clip(upload_id):
        raise HTTPException(
            status_code=400,
            detail="Pehle 1 test clip approve karo — POST /approve-clip se ek clip approve karo, phir /render-all chalega")
    video_path = ROOT / row["stored_path"]
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="uploaded file missing on disk")
    try:
        clips_data = load_clips(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        transcript = load_transcript(upload_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    results: list[dict] = []
    approvals = get_clip_approvals(upload_id)
    for clip in clips_data.get("clips", []):
        cid = str(clip.get("clip_id"))
        if approvals.get(cid, {}).get("status") == "rejected":
            results.append({"clip_id": cid, "status": "skipped_rejected",
                            "reason": approvals[cid].get("reason", "")})
            continue
        existing = is_clip_rendered_for_upload(upload_id, cid)
        if existing is not None:
            results.append({"clip_id": cid, "status": "skipped_already_rendered",
                            "output_mp4": existing.get("output_mp4"),
                            "duration_sec": existing.get("duration_sec")})
            continue
        try:
            # Worker thread, same reason as /transcribe (see above).
            meta = await asyncio.to_thread(
                render_clip, video_path, upload_id, clip,
                style_name=style_name or "global-baatien",
                transcript=transcript)
            results.append({"clip_id": cid, "status": "rendered",
                            "output_mp4": meta.get("output_mp4"),
                            "duration_sec": meta.get("duration_sec"),
                            "file_size_bytes": meta.get("file_size_bytes")})
        except (FileNotFoundError, ValueError) as exc:
            results.append({"clip_id": cid, "status": "error", "error": str(exc)})
        except RuntimeError as exc:
            results.append({"clip_id": cid, "status": "error", "error": str(exc)})
    rendered_n = sum(1 for r in results if r["status"] == "rendered")
    skipped_n = sum(1 for r in results if r["status"] == "skipped_already_rendered")
    rejected_n = sum(1 for r in results if r["status"] == "skipped_rejected")
    error_n = sum(1 for r in results if r["status"] == "error")
    return {"message": f"Render-all done: {rendered_n} rendered, {skipped_n} skipped (pehle se rendered), {rejected_n} skipped (rejected), {error_n} error.",
            "upload_id": upload_id, "results": results,
            "project": get_project_status(upload_id)}


@app.post("/batch")
async def create_batch_endpoint(
    background_tasks: BackgroundTasks,
    payload: dict,
) -> dict:
    """Queue transcribe + find-clips for several uploads.

    Body: {"upload_ids": [...], "style_name": "global-baatien",
    "language": "en"}. Language default English — batch transcribes
    every video in that language.
    Returns a batch_id at once; the work runs in the background
    (Whisper is slow on CPU). Poll GET /batch/{batch_id} for progress.
    Rendering is NEVER part of a batch — each video still needs a
    test-clip approval first.
    """
    upload_ids = payload.get("upload_ids") or []
    if not isinstance(upload_ids, list) or not upload_ids:
        raise HTTPException(status_code=400, detail="upload_ids list khaali hai — kam se kam 1 upload id do")
    if len(upload_ids) > 10:
        raise HTTPException(status_code=400, detail="Ek batch me max 10 videos")
    for uid in upload_ids:
        if not _get_upload_row(str(uid)):
            raise HTTPException(status_code=404, detail=f"upload not found: {uid}")
    batch = create_batch([str(u) for u in upload_ids],
                         style_name=str(payload.get("style_name") or ""),
                         language=str(payload.get("language") or "en"))
    background_tasks.add_task(run_batch, batch["batch_id"])
    return {"message": "Batch queued — transcribe + find-clips background me chal raha hai. GET /batch/{batch_id} se progress dekho.",
            "batch_id": batch["batch_id"], "total": len(upload_ids),
            "status": "queued"}


@app.get("/batch/{batch_id}")
def get_batch_endpoint(batch_id: str) -> dict:
    try:
        return get_batch(batch_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------- V2: YouTube job

@app.post("/youtube-job")
async def create_youtube_job_endpoint(payload: dict) -> dict:
    """Start a full YouTube -> clips job. Returns at once; poll status.

    Body: {"url": "<youtube link>", "language": "en", "top_n": 8}.
    Language default English; top_n 1-8 (default 8). Only youtube.com /
    youtu.be links are accepted — anything else is 400.
    The worker (daemon thread) downloads with yt-dlp, transcribes,
    finds clips, renders a test clip, approves it, renders the rest,
    and leaves the project ZIP ready. Poll GET /youtube-job/{job_id}.
    """
    url = str(payload.get("url") or "")
    try:
        clean_url = validate_youtube_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    language = str(payload.get("language") or "en")
    try:
        top_n = int(payload.get("top_n") or 8)
    except (TypeError, ValueError):
        top_n = 8
    job = create_youtube_job(clean_url, language=language, top_n=top_n)
    thread = threading.Thread(target=run_youtube_job, args=(job["job_id"],),
                              daemon=True)
    thread.start()
    return {"job_id": job["job_id"], "status": "queued",
            "message": "YouTube job queued — download se lekar render tak "
                       "background me chalega. GET /youtube-job/{job_id} se "
                       "progress dekho."}


@app.get("/youtube-job/{job_id}")
def get_youtube_job_endpoint(job_id: str) -> dict:
    """Job status + progress + (when done) clips and ZIP link."""
    try:
        return get_youtube_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def run() -> None:
    host = os.getenv("VCS_HOST", "127.0.0.1")
    port = int(os.getenv("VCS_PORT", "8000"))
    uvicorn.run("app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
