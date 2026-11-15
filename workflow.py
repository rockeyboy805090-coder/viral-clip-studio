"""Phase 4: workflow, history, batch, test-clip approval.

Free stack only: SQLite + stdlib + existing Phase 1-3 modules.
No paid API, no network.

Project status (derived from files on disk + approvals table, never
invented — a stage only counts when its artefact actually exists):
  uploaded       — upload row exists, no transcript yet
  transcribed    — outputs/transcripts/<id>.json exists
  clips_found    — outputs/clips/<id>_clips.json exists
  test_rendered  — at least one rendered sidecar with upload_id == id
  approved       — at least one clip approved AND not all clips rendered
  all_rendered   — every clip rendered (implies approval, since
                   render-all requires it; test renders can also
                   reach this state one clip at a time)
  done           — approved AND every clip rendered

Approval gate (enforced in app.py, not just the UI):
  POST /render-all only works after POST /approve-clip for at least
  one clip of that upload. Otherwise 400 "Pehle 1 test clip approve karo".

Batch: transcribe + find-clips for several uploads, in a background
task (Whisper is slow on CPU — the API returns a batch_id at once
and GET /batch/{id} shows progress). Rendering is NEVER part of a
batch without per-video approval.

Render naming (Phase 5 FIX): rendered files live per upload —
outputs/rendered/<upload_id>/<clip_id>.mp4 — so two uploads' clip_01
files can never overwrite each other. Legacy flat files
(outputs/rendered/<clip_id>.mp4, Phase 3/4) are still read and
deleted by sidecar path, but new renders always use the namespace.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "viral_clip_studio.sqlite3"
OUTPUT_DIR = ROOT / "outputs"
UPLOAD_DIR = ROOT / "uploads"
RENDERED_DIR = OUTPUT_DIR / "rendered"
CLIPS_DIR = OUTPUT_DIR / "clips"
TRANSCRIPTS_DIR = OUTPUT_DIR / "transcripts"

STATUSES = ("uploaded", "transcribed", "clips_found", "test_rendered",
            "approved", "all_rendered", "done")


def init_workflow_tables() -> None:
    """Create Phase 4 tables (idempotent — safe to call on every startup)."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS clip_approvals (
            upload_id TEXT NOT NULL,
            clip_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('approved','rejected')),
            reason TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (upload_id, clip_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            upload_ids TEXT NOT NULL,
            style_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '{}'
        )""")
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- approvals

def set_clip_approval(upload_id: str, clip_id: str, status: str,
                      reason: str = "") -> dict:
    if status not in ("approved", "rejected"):
        raise ValueError("status approved ya rejected hona chahiye")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO clip_approvals (upload_id, clip_id, status, reason, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(upload_id, clip_id) DO UPDATE SET"
            " status=excluded.status, reason=excluded.reason,"
            " updated_at=excluded.updated_at",
            (upload_id, clip_id, status, reason or "", _now()))
        conn.commit()
    return {"upload_id": upload_id, "clip_id": clip_id, "status": status,
            "reason": reason or ""}


def get_clip_approvals(upload_id: str) -> dict[str, dict]:
    """{clip_id: {status, reason, updated_at}} for one upload."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT clip_id, status, reason, updated_at FROM clip_approvals"
            " WHERE upload_id=?", (upload_id,)).fetchall()
    return {r[0]: {"status": r[1], "reason": r[2], "updated_at": r[3]}
            for r in rows}


def has_approved_clip(upload_id: str) -> bool:
    return any(a["status"] == "approved"
               for a in get_clip_approvals(upload_id).values())


# ---------------------------------------------------------------- renders

def list_rendered_for_upload(upload_id: str) -> list[dict]:
    """Rendered sidecars whose recorded upload_id matches this upload.

    Scans outputs/rendered/*.json (small directory) and returns the
    sidecar dicts. Files without a sidecar, or with a different
    upload_id, are NOT counted for this project.
    """
    out = []
    if not RENDERED_DIR.is_dir():
        return out
    # Phase 5: scan recursively — new namespaced renders live in
    # outputs/rendered/<upload_id>/*.json, legacy ones flat in
    # outputs/rendered/*.json. Both count.
    for path in sorted(RENDERED_DIR.rglob("*.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(meta.get("upload_id")) == str(upload_id):
            out.append(meta)
    return out


def is_clip_rendered_for_upload(upload_id: str, clip_id: str) -> dict | None:
    """Sidecar for this (upload_id, clip_id) if the MP4 exists, else None.

    Phase 5: checks the namespaced path first, then the legacy flat
    path, then any sidecar found by recursive scan.
    """
    for path in (RENDERED_DIR / str(upload_id) / f"{clip_id}.json",
                 RENDERED_DIR / f"{clip_id}.json"):
        if not path.is_file():
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(meta.get("upload_id")) != str(upload_id):
            continue
        mp4 = Path(str(meta.get("output_mp4", "")))
        if mp4.is_file():
            return meta
    return None


# ---------------------------------------------------------------- projects

def _load_clips_file(upload_id: str) -> list[dict]:
    path = CLIPS_DIR / f"{upload_id}_clips.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("clips", [])
    except Exception:
        return []


def get_project_status(upload_id: str, upload_row: dict | None = None) -> dict:
    """Full project snapshot. Raises FileNotFoundError if no upload row."""
    if upload_row is None:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            row = conn.execute(
                "SELECT id, role, original_name, stored_path, size_bytes, created_at"
                " FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"project not found: {upload_id}")
        upload_row = {"id": row[0], "role": row[1], "original_name": row[2],
                      "stored_path": row[3], "size_bytes": row[4],
                      "created_at": row[5]}
    transcript_path = TRANSCRIPTS_DIR / f"{upload_id}.json"
    clips_path = CLIPS_DIR / f"{upload_id}_clips.json"
    has_transcript = transcript_path.is_file()
    has_clips = clips_path.is_file()
    clips = _load_clips_file(upload_id)
    clip_ids = [str(c.get("clip_id")) for c in clips]
    approvals = get_clip_approvals(upload_id)
    approved = [c for c, a in approvals.items() if a["status"] == "approved"]
    rejected = [c for c, a in approvals.items() if a["status"] == "rejected"]
    rendered = list_rendered_for_upload(upload_id)
    rendered_ids = [str(m.get("clip_id")) for m in rendered]
    all_rendered = bool(clip_ids) and all(c in rendered_ids for c in clip_ids)

    if not has_transcript:
        status = "uploaded"
    elif not has_clips:
        status = "transcribed"
    elif not rendered:
        status = "clips_found"
    elif approved and all_rendered:
        status = "done"
    elif all_rendered:
        status = "all_rendered"
    elif approved:
        status = "approved"
    else:
        status = "test_rendered"

    return {
        "upload_id": upload_id,
        "original_name": upload_row.get("original_name"),
        "role": upload_row.get("role"),
        "size_bytes": upload_row.get("size_bytes"),
        "created_at": upload_row.get("created_at"),
        "status": status,
        "has_transcript": has_transcript,
        "has_clips": has_clips,
        "clips_count": len(clip_ids),
        "clip_ids": clip_ids,
        "approved_clips": sorted(approved),
        "rejected_clips": sorted(rejected),
        "rendered_clips": sorted(rendered_ids),
        "rendered_count": len(rendered_ids),
        "all_rendered": all_rendered,
        "approvals": approvals,
        "renders": [{"clip_id": m.get("clip_id"),
                     "duration_sec": m.get("duration_sec"),
                     "width": m.get("width"), "height": m.get("height"),
                     "output_mp4": m.get("output_mp4")} for m in rendered],
    }


def list_projects() -> list[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, role, original_name, stored_path, size_bytes, created_at"
            " FROM uploads ORDER BY created_at DESC").fetchall()
    projects = []
    for r in rows:
        row = {"id": r[0], "role": r[1], "original_name": r[2],
               "stored_path": r[3], "size_bytes": r[4], "created_at": r[5]}
        try:
            projects.append(get_project_status(r[0], upload_row=row))
        except FileNotFoundError:
            continue
    return projects


def delete_project(upload_id: str) -> dict:
    """Delete one project: upload file, transcript, clips file, its own
    rendered files (sidecar upload_id must match), approvals, DB row.
    Returns what was deleted. Raises FileNotFoundError if no upload row.
    """
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT stored_path FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"project not found: {upload_id}")
    deleted: list[str] = []
    src = ROOT / str(row[0])
    if src.is_file():
        src.unlink()
        deleted.append(str(src))
    for path in (TRANSCRIPTS_DIR / f"{upload_id}.json",
                 TRANSCRIPTS_DIR / f"{upload_id}.txt",
                 CLIPS_DIR / f"{upload_id}_clips.json"):
        if path.is_file():
            path.unlink()
            deleted.append(str(path))
    for meta in list_rendered_for_upload(upload_id):
        # Phase 5: delete by the sidecar's recorded paths (namespaced
        # or legacy) — never by a guessed flat path, so other uploads'
        # clip_01 files are never touched.
        for key in ("output_mp4", "output_srt", "output_ass", "output_txt",
                    "output_caption_txt", "output_json"):
            p = Path(str(meta.get(key, "")))
            if p.is_file():
                p.unlink()
                deleted.append(str(p))
        # Remove the now-empty namespaced dir if it is empty.
        ns_dir = RENDERED_DIR / str(upload_id)
        try:
            if ns_dir.is_dir() and not any(ns_dir.iterdir()):
                ns_dir.rmdir()
        except Exception:
            pass
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM clip_approvals WHERE upload_id=?", (upload_id,))
        conn.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
        conn.commit()
    return {"upload_id": upload_id, "deleted_count": len(deleted),
            "deleted": deleted}


# ---------------------------------------------------------------- batches

def create_batch(upload_ids: list[str], style_name: str = "",
                 language: str = "en") -> dict:
    batch_id = uuid.uuid4().hex[:12]
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO batches (batch_id, upload_ids, style_name, status,"
            " created_at, updated_at, result) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (batch_id, json.dumps(upload_ids), style_name or "", "queued",
             _now(), _now(), json.dumps({"items": [], "language": language or "en"})))
        conn.commit()
    return get_batch(batch_id)


def get_batch(batch_id: str) -> dict:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT batch_id, upload_ids, style_name, status, created_at,"
            " updated_at, result FROM batches WHERE batch_id=?",
            (batch_id,)).fetchone()
    if not row:
        raise FileNotFoundError(f"batch not found: {batch_id}")
    result = json.loads(row[6] or "{}")
    return {"batch_id": row[0], "upload_ids": json.loads(row[1]),
            "style_name": row[2], "status": row[3],
            "created_at": row[4], "updated_at": row[5], **result}


def update_batch(batch_id: str, status: str, result: dict) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE batches SET status=?, updated_at=?, result=?"
            " WHERE batch_id=?",
            (status, _now(), json.dumps(result), batch_id))
        conn.commit()


def run_batch(batch_id: str) -> None:
    """Background worker: transcribe + find-clips for each upload, in order.

    Rendering is deliberately NOT part of a batch — each video still
    needs a test-clip approval first (see render-all guard).
    Runs in a background thread; each item records ok/error honestly.
    """
    from clip_finder import find_clips  # local import: avoids cycles
    from transcriber import load_transcript, transcribe

    try:
        batch = get_batch(batch_id)
    except FileNotFoundError:
        return
    upload_ids = batch["upload_ids"]
    language = str(batch.get("language") or "en")
    items: list[dict] = []
    update_batch(batch_id, "running", {"items": items, "done": 0,
                                       "total": len(upload_ids)})
    for uid in upload_ids:
        item: dict = {"upload_id": uid, "status": "running"}
        items.append(item)
        update_batch(batch_id, "running", {"items": items, "done": 0,
                                           "total": len(upload_ids)})
        try:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                row = conn.execute(
                    "SELECT stored_path FROM uploads WHERE id=?", (uid,)).fetchone()
            if not row:
                raise FileNotFoundError(f"upload not found: {uid}")
            video_path = ROOT / str(row[0])
            if not video_path.is_file():
                raise FileNotFoundError("uploaded file missing on disk")
            try:
                transcript = load_transcript(uid)
                item["transcript"] = "existing"
            except FileNotFoundError:
                transcript = transcribe(video_path, uid, language=language)
                item["transcript"] = "new"
            if not transcript.get("words"):
                raise ValueError("Transcript khaali hai — clips nahi ban sakte")
            result = find_clips(transcript, uid, top_n=5, language=language)
            item.update({"status": "done",
                         "clips_count": len(result.get("clips", [])),
                         "scoring_method": result.get("scoring_method"),
                         "word_count": transcript.get("word_count")})
        except Exception as exc:
            item.update({"status": "error", "error": str(exc)})
        update_batch(batch_id, "running",
                     {"items": items,
                      "done": sum(1 for i in items if i["status"] in ("done", "error")),
                      "total": len(upload_ids)})
    final = "done" if all(i["status"] == "done" for i in items) else "partial"
    update_batch(batch_id, final,
                 {"items": items,
                  "done": sum(1 for i in items if i["status"] == "done"),
                  "total": len(upload_ids),
                  "note": "Batch sirf transcribe + find-clips karta hai."
                          " Render ke liye har video ka test clip approve karo,"
                          " phir POST /render-all."})
