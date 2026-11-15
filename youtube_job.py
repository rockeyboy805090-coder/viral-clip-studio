"""V2 (16 Nov 2026): YouTube link -> fully edited viral clips, automatically.

One job, no manual steps: paste a YouTube URL, the worker downloads the
video with yt-dlp, registers it as a normal upload/project, transcribes
it (faster-whisper), finds viral clips, renders a test clip, approves it,
renders the rest, and leaves a project ZIP ready to download.

Free stack only: yt-dlp + the existing Phase 1-6 modules (transcriber,
clip_finder, editor, workflow). No API keys, no money, no card.

Design notes (kept deliberately boring so the existing machinery does
the real work):
- Job state lives in the existing SQLite DB (table youtube_jobs) so a
  status check works across requests. If the server restarts mid-job
  the job stays at its last recorded stage — the status says so
  honestly ("failed" with a restart note is only written when the
  worker itself catches an error; a stale "rendering" after a restart
  means the worker died with the process — do not fake completion).
- The worker runs in a plain daemon thread (like /batch's background
  task). /health keeps answering while a job runs, because the heavy
  CPU work (transcribe/render) already runs off the event loop
  (asyncio.to_thread in app.py; here the whole worker is a thread).
- yt-dlp is invoked as a subprocess. It must be installed
  (requirements.txt lists yt-dlp; the pip package ships the `yt-dlp`
  console script). If the binary is not on PATH we fall back to
  `python -m yt_dlp`.
- YouTube can block downloads from datacenter IPs (Render) with a
  bot-check ("Sign in to confirm you're not a bot"). The download
  first probes with player clients in order android -> ios ->
  tv_embedded -> mediaconnect -> web_safari -> web (each 120s) and
  downloads with the first client that works — YouTube often still
  serves the mobile/TV clients when it 403s the web client. The
  probe/download also impersonate a real Chrome browser
  (`--impersonate chrome` via curl-cffi, Firefox fallback) so the
  TLS fingerprint looks like a browser, not a script. If every
  client is blocked, the job fails with a plain Hinglish message
  (try later, or upload the file and use V1).
- yt-dlp also needs a JS runtime (`node`) or it warns that
  extraction without one is deprecated. LESSON (round 2, 15 Nov
  2026): recent yt-dlp enables ONLY `deno` by default — node on
  PATH is NOT enough, and `--js-runtimes node` alone still leaves
  the EJS challenge-solver warning on yt-dlp >= 2026.10. So this
  module passes the node binary by EXPLICIT absolute path
  (`--js-runtimes node:<path>`, found via shutil.which plus
  ROOT/.node/bin and /opt/render/project/src/.node/bin) AND
  `--remote-components ejs:github` (downloads the EJS challenge
  solver, cached). Render's build downloads an official Node 20
  tarball into .node/ (render.yaml) — the build ALSO verifies
  `.node/bin/node --version` actually runs, and the startCommand
  logs whether the bundled node is present. Every job records
  diagnostics (node path found or null, yt-dlp version,
  impersonation target, clients tried) in its result so a failed
  job is diagnosable, and /health exposes the same diagnostics.
- The yt-dlp command prefers `python -m yt_dlp` from the venv when
  curl_cffi is importable there (that is the ONLY way
  --impersonate works — the standalone PyInstaller binary cannot
  impersonate: "Impersonate target chrome is not available").
  Otherwise the standalone binary is used, else plain module.
  Locally (this VM) the venv module + impersonation also fixes
  the MITM-proxy SSL failure (libcurl honours SSL_CERT_FILE).
- Round 3 (15 Nov 2026): yt-dlp STILL 403s from Render's datacenter
  IP, so a DIFFERENT download route is now the fallback — Invidious
  (public API instance, no key): GET /api/v1/videos/<id> returns
  direct googlevideo stream URLs, which are downloaded and merged
  with ffmpeg. Cobalt was TESTED and is NOT wired in: the official
  api.cobalt.tools now needs a JWT (Turnstile) auth key, and every
  community instance tried (co.eepy.au, cobalt.canine.tools,
  cobalt-api.kwiatekmiki.pl) returned empty/unreachable from this
  VM; the public instance lists are closed (opt-in only). Piped was
  also TESTED and is NOT wired in: piped.video, pipedapi.kavin.rocks,
  pipedapi-libre.kavin.rocks, pipedapi.adminforge.de,
  pipedapi.leptons.xyz, pipedapi.reallyaweso.me, api-piped.mha.fi,
  pipedapi.osphost.org — all dead/empty from this VM. Invidious
  (yt.chocolatemoo53.com, the ONLY api:true instance in the public
  list) VERIFIED working locally 15 Nov 2026: 720p60 mp4 + m4a AAC
  streams returned, merged with ffmpeg, ffprobe-valid. Honest limit:
  a single community instance is fragile, and nobody has proven the
  googlevideo URLs work from Render's IP yet — local success does
  NOT prove live success. Order: yt-dlp first, Invidious second;
  the winner is recorded in result["downloader"]. Env knob
  VCS_DOWNLOADERS (default "ytdlp,invidious") lets a test force one
  route, e.g. VCS_DOWNLOADERS=invidious.
- Round 3 JS-warning fix: the "web" player-client entry used to pass
  NO --extractor-args at all, so the last probe fell back to yt-dlp's
  DEFAULT client selection — the ONLY code path that can print the
  "No supported JavaScript runtime ... deprecated" warning when no
  runtime is detected. It now passes player_client=web explicitly,
  like every other client. Belt and braces: known warning lines
  (JS runtime, challenge solving, SABR) are filtered OUT of the
  user-facing error message — warnings are not the error — while
  the FULL stderr tail is kept in result["download_stderr_tail"]
  for diagnostics.
- Round 3 vanished-job fix: the job row is written to SQLite BEFORE
  the worker thread starts (create_youtube_job, in app.py), so a
  restart mid-download leaves a record when the disk survives. On
  Render Free the disk itself is wiped on restart, so the record can
  still vanish — the real fix is the UI: a 404 on poll now shows a
  plain Hinglish "server restart ho gaya, dobara submit karo"
  message instead of raw JSON.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "viral_clip_studio.sqlite3"
UPLOAD_DIR = ROOT / "uploads"
YT_DIR = ROOT / "yt_downloads"

JOB_STATUSES = ("queued", "downloading", "transcribing", "finding_clips",
                "rendering", "done", "failed")

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}


def init_youtube_jobs_table() -> None:
    """Create the youtube_jobs table (idempotent)."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS youtube_jobs (
            job_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'en',
            top_n INTEGER NOT NULL DEFAULT 8,
            status TEXT NOT NULL DEFAULT 'queued',
            message TEXT NOT NULL DEFAULT '',
            upload_id TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_youtube_url(url: str) -> str:
    """Accept only real YouTube video URLs; return the cleaned URL.

    Raises ValueError with a plain message for anything else (wrong
    host, no video id, empty). Shorts / live / embed / watch / youtu.be
    forms are all accepted — yt-dlp handles them.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("YouTube link khaali hai — video ka link paste karo")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().split(":")[0]
    if host not in _YOUTUBE_HOSTS:
        raise ValueError(
            "Sirf YouTube links allowed hain (youtube.com / youtu.be) — "
            f"ye link YouTube ka nahi hai: {host or raw[:60]}")
    video_id = ""
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        q = parse_qs(parsed.query)
        video_id = (q.get("v") or [""])[0]
        if not video_id:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
                video_id = parts[1]
    if not video_id:
        raise ValueError(
            "Is YouTube link me video id nahi mila — poora video link paste karo")
    return raw


def create_youtube_job(url: str, language: str = "en", top_n: int = 8) -> dict:
    """Insert a queued job row and return its public dict."""
    job_id = uuid.uuid4().hex[:12]
    lang = (language or "en").strip().lower()
    if lang not in ("en", "hinglish", "hi"):
        lang = "en"
    top_n = max(1, min(8, int(top_n or 8)))
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO youtube_jobs (job_id, url, language, top_n, status,"
            " message, upload_id, result, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, url, lang, top_n, "queued",
             "Job queued — download shuru hoga.", "", "{}", _now(), _now()))
        conn.commit()
    return get_youtube_job(job_id)


def get_youtube_job(job_id: str) -> dict:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT job_id, url, language, top_n, status, message,"
            " upload_id, result, created_at, updated_at"
            " FROM youtube_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise FileNotFoundError(f"youtube job not found: {job_id}")
    result = json.loads(row[7] or "{}")
    return {"job_id": row[0], "url": row[1], "language": row[2],
            "top_n": row[3], "status": row[4], "message": row[5],
            "upload_id": row[6], "result": result,
            "created_at": row[8], "updated_at": row[9],
            "download_zip_url": (f"/download/{row[6]}/all" if row[6] and row[4] == "done" else None)}


def _update_job(job_id: str, status: str | None = None,
                message: str | None = None, upload_id: str | None = None,
                result: dict | None = None) -> None:
    """Partial update of a job row (only given fields)."""
    fields: list[str] = []
    values: list = []
    if status is not None:
        fields.append("status=?")
        values.append(status)
    if message is not None:
        fields.append("message=?")
        values.append(message)
    if upload_id is not None:
        fields.append("upload_id=?")
        values.append(upload_id)
    if result is not None:
        fields.append("result=?")
        values.append(json.dumps(result))
    fields.append("updated_at=?")
    values.append(_now())
    values.append(job_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            f"UPDATE youtube_jobs SET {', '.join(fields)} WHERE job_id=?",
            values)
        conn.commit()


@lru_cache(maxsize=1)
def _curl_cffi_available() -> bool:
    """True when `import curl_cffi` works in this interpreter.

    curl-cffi is what makes yt-dlp --impersonate work. The venv
    module (sys.executable -m yt_dlp) can use it; the standalone
    PyInstaller binary can NOT (it bundles its own interpreter
    without curl_cffi) — verified 15 Nov 2026: the binary fails
    with 'Impersonate target "chrome" is not available'.
    """
    try:
        __import__("curl_cffi")
        return True
    except Exception:
        return False


def _yt_dlp_cmd() -> list[str]:
    """yt-dlp command, chosen so --impersonate works when possible.

    Order: (1) venv `python -m yt_dlp` when curl_cffi is
    importable — the ONLY impersonation-capable path (and on this
    VM it also fixes the MITM-proxy SSL failure, because curl_cffi
    via libcurl honours SSL_CERT_FILE); (2) standalone `yt-dlp`
    binary (~/.local/bin or PATH); (3) plain `python -m yt_dlp`.
    On Render the pip-installed console script runs under the venv
    Python, so (1) applies there once curl-cffi is in
    requirements.txt.
    """
    if _curl_cffi_available():
        return [sys.executable, "-m", "yt_dlp"]
    for cand in (shutil.which("yt-dlp"),
                 str(Path.home() / ".local/bin/yt-dlp")):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return [cand]
    return [sys.executable, "-m", "yt_dlp"]


@lru_cache(maxsize=1)
def _yt_dlp_version() -> str:
    """yt-dlp version string ('' when it cannot run). Cached."""
    try:
        r = subprocess.run(_yt_dlp_cmd() + ["--version"],
                           capture_output=True, text=True, timeout=60,
                           env=_yt_dlp_env())
        return (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 else ""
    except Exception:
        return ""


def _find_node() -> str | None:
    """Absolute path of a usable `node` binary, or None.

    Checks, in order: PATH, ROOT/.node/bin/node (Render build),
    $PWD/.node/bin/node, /opt/render/project/src/.node/bin/node
    (Render's default project dir — covers a startCommand whose
    CWD differs from the code dir). A path is only returned when
    the file exists AND is executable.
    """
    cands: list[str] = []
    w = shutil.which("node")
    if w:
        cands.append(w)
    for base in (ROOT, Path.cwd(), Path("/opt/render/project/src")):
        p = base / ".node" / "bin" / "node"
        if p.is_file() and os.access(p, os.X_OK):
            cands.append(str(p))
    env_node = os.environ.get("NODE_PATH", "").strip()
    if env_node and Path(env_node).is_file() and os.access(env_node, os.X_OK):
        cands.append(env_node)
    for c in cands:
        try:
            r = subprocess.run([c, "--version"], capture_output=True,
                               text=True, timeout=15)
            if r.returncode == 0 and (r.stdout or "").strip().startswith("v"):
                return c
        except Exception:
            continue
    return None


@lru_cache(maxsize=1)
def _impersonation_target() -> str | None:
    """'chrome' / 'firefox' when yt-dlp can impersonate it, else None.

    Detected from `yt-dlp --list-impersonate-targets` (no network
    call): a target counts only when its line does NOT say
    "(unavailable)". Chrome first, Firefox fallback — the standard
    bypass for YouTube 403s (real-browser TLS fingerprint).
    """
    try:
        r = subprocess.run(_yt_dlp_cmd() + ["--list-impersonate-targets"],
                           capture_output=True, text=True, timeout=60,
                           env=_yt_dlp_env())
    except Exception:
        return None
    out = (r.stdout or "") + (r.stderr or "")
    for target in ("Chrome", "Firefox"):
        for line in out.splitlines():
            if line.strip().startswith(target) and "(unavailable)" not in line:
                return target.lower()
    return None


def _impersonation_args() -> list[str]:
    target = _impersonation_target()
    return ["--impersonate", target] if target else []


def _js_runtime_args() -> list[str]:
    """Explicit `--js-runtimes node:<absolute path>` when node exists.

    MUST be the explicit path: recent yt-dlp enables only `deno`
    by default, so node on PATH alone is ignored (verified 15 Nov
    2026 — warning persisted with node on PATH, vanished with the
    explicit path). Returns [] when no node binary is found, so a
    missing node degrades to a warning instead of a hard failure.
    """
    node = _find_node()
    return ["--js-runtimes", f"node:{node}"] if node else []


def _base_yt_dlp_args() -> list[str]:
    """Args on EVERY yt-dlp call: JS runtime + EJS + impersonation.

    `--remote-components ejs:github` fetches yt-dlp's JS challenge
    solver (cached in ~/.cache/yt-dlp; warns and continues if
    GitHub is unreachable). Without it yt-dlp >= 2026.10 warns
    'n challenge solving failed: Some formats may be missing'.
    """
    return (_js_runtime_args()
            + ["--remote-components", "ejs:github"]
            + _impersonation_args())


def youtube_runtime_diagnostics() -> dict:
    """Cheap cached diagnostics for /health and job records."""
    node = _find_node()
    return {"node_found": node,
            "js_runtimes_arg": _js_runtime_args(),
            "yt_dlp_version": _yt_dlp_version(),
            "curl_cffi": _curl_cffi_available(),
            "impersonation": _impersonation_target(),
            "yt_dlp_cmd": " ".join(_yt_dlp_cmd()),
            "downloaders": _downloaders_enabled()}


def _downloaders_enabled() -> list[str]:
    """Download routes for this process (env knob, default both).

    VCS_DOWNLOADERS=invidious forces the fallback (used by tests);
    VCS_DOWNLOADERS=ytdlp forces yt-dlp only. Unknown names are
    ignored; an empty list falls back to both routes.
    """
    raw = os.environ.get("VCS_DOWNLOADERS", "ytdlp,invidious")
    out = [p.strip().lower() for p in raw.split(",")
           if p.strip().lower() in ("ytdlp", "invidious")]
    return out or ["ytdlp", "invidious"]


# --- Player-client fallback + JS runtime + impersonation (round 2) --
# Live failure on Render (15 Nov 2026, job 857552aa618a): default web
# client got HTTP 403 ("Unable to download API page ... Failed to
# extract any player response") and android/ios did NOT help — the
# Render datacenter IP itself is blocked, not just the web client.
# Round 2 widens the probe order AND impersonates a real browser:
# android -> ios -> tv_embedded -> mediaconnect -> web_safari -> web
# (120s each). Client names verified against live YouTube with the
# installed yt-dlp (15 Nov 2026): tv_embedded + mediaconnect
# returned full JSON; web_safari is a valid client but got a
# bot-check on this VM (kept — it may work from Render);
# android_music / ios_music are NOT valid clients ("Skipping
# unsupported client") and are NOT in this list. tv_embedded and
# mediaconnect need no custom user-agent — yt-dlp sets the correct
# per-client UA itself. The winning client is recorded in the job
# result (result["player_client"]) and in the status message.
_PLAYER_CLIENTS: tuple[tuple[str, list[str]], ...] = (
    ("android", ["--extractor-args", "youtube:player_client=android",
                 "--user-agent",
                 "com.google.android.youtube/19.09.37 "
                 "(Linux; U; Android 11) gzip"]),
    ("ios", ["--extractor-args", "youtube:player_client=ios",
             "--user-agent",
             "com.google.ios.youtube/19.09.3 "
             "(iPhone14,3; U; CPU iOS 15_6 like Mac OS X)"]),
    ("tv_embedded", ["--extractor-args",
                     "youtube:player_client=tv_embedded"]),
    ("mediaconnect", ["--extractor-args",
                      "youtube:player_client=mediaconnect"]),
    ("web_safari", ["--extractor-args",
                    "youtube:player_client=web_safari"]),
    # Round 3: web MUST pass its client explicitly too. It used to
    # be [] — with no player_client arg yt-dlp falls back to DEFAULT
    # client selection, the only path that can print the "No
    # supported JavaScript runtime ... deprecated" warning when no
    # runtime is detected. Explicit web keeps every probe identical.
    ("web", ["--extractor-args", "youtube:player_client=web"]),
)
_PROBE_TIMEOUT = 120  # seconds per player-client probe attempt


def _yt_dlp_env() -> dict:
    """Subprocess env for yt-dlp: found node bin dir first on PATH.

    Render's build extracts the official Node 20 tarball into
    <project>/.node (render.yaml); locally the system node is used.
    The explicit `--js-runtimes node:<path>` flag is the real fix
    (see _js_runtime_args); PATH is still prepended as a belt-and-
    braces measure for any other tool yt-dlp spawns.
    """
    env = dict(os.environ)
    node = _find_node()
    dirs: list[str] = []
    if node:
        dirs.append(str(Path(node).parent))
    node_dir = ROOT / ".node" / "bin"
    if node_dir.is_dir():
        dirs.append(str(node_dir))
    path = env.get("PATH", "")
    for d in dirs:
        if d not in path:
            path = f"{d}:{path}"
    env["PATH"] = path
    return env


def _strip_impersonate(args: list[str]) -> list[str]:
    """Remove a `--impersonate <target>` pair from an arg list."""
    out: list[str] = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a == "--impersonate":
            skip_next = True
            continue
        out.append(a)
    return out


def _run_yt_dlp(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run yt-dlp; if impersonation itself is the failure, retry
    once without it (a broken impersonation flag must never kill a
    job that plain yt-dlp could have done)."""
    proc = subprocess.run(_yt_dlp_cmd() + args, capture_output=True,
                          text=True, timeout=timeout, env=_yt_dlp_env())
    if proc.returncode != 0 and "--impersonate" in args:
        text = ((proc.stderr or "") + (proc.stdout or "")).lower()
        if "impersonate" in text and ("not available" in text
                                      or "curl-cffi" in text
                                      or "curl_cffi" in text):
            proc = subprocess.run(
                _yt_dlp_cmd() + _strip_impersonate(args),
                capture_output=True, text=True, timeout=timeout,
                env=_yt_dlp_env())
    return proc


def _check_yt_dlp_installed() -> None:
    """Fail fast with a CLEAR error if yt-dlp is missing.

    Without this, a missing yt-dlp surfaces as a download failure and
    the job wrongly blames the video (private / bot-check). Checked
    once per download, before any network call.
    """
    try:
        r = _run_yt_dlp(["--version"], timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "yt-dlp install nahi hai — pip install yt-dlp "
            "(requirements.txt me hai)") from exc
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0 or "No module named" in out or not out:
        raise RuntimeError(
            "yt-dlp install nahi hai — pip install yt-dlp "
            "(requirements.txt me hai)")


def _is_blocked(tail: str) -> bool:
    """True when the yt-dlp error looks like a YouTube bot-check/403."""
    low = (tail or "").lower()
    return ("403" in tail or "forbidden" in low
            or "sign in to confirm" in low or "not a bot" in low
            or "failed to extract any player response" in low)


_WARNING_LINE_MARKERS = ("javascript runtime", "challenge solving failed",
                        "some formats may be missing", "sabr-only")


def _clean_tail(tail: str) -> str:
    """Drop known yt-dlp WARNING lines from a user-facing error tail.

    Warnings are not the error: the JS-runtime deprecation, the
    n/sig challenge-solving and the SABR-experiment warnings all
    show up in stderr even when the real failure is a plain 403,
    and they confuse the user (and look like a second bug). The
    full unfiltered tail is kept in the job result's
    download_stderr_tail for diagnostics — nothing is hidden.
    """
    kept = []
    for line in (tail or "").splitlines():
        low = line.lower()
        if any(m in low for m in _WARNING_LINE_MARKERS):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _download_failed_message(tail: str) -> str:
    """Plain Hinglish failure message (+ yt-dlp tail for debugging)."""
    tail = _clean_tail((tail or "").strip()[-500:])
    if _is_blocked(tail):
        return ("YouTube ne is server ko block kar diya hai (bot-check / 403) — "
                "thodi der baad phir try karo, ya video file upload karke V1 flow "
                f"use karo. yt-dlp: {tail or 'no error text'}")
    return ("YouTube video download nahi ho paya — video private, removed, "
            f"age-restricted ya bot-check ho sakta hai. yt-dlp: {tail or 'no error text'}")


class _DownloadError(RuntimeError):
    """Download failure that also carries the FULL stderr tail.

    The message shown to the user is the cleaned one; the worker
    copies full_tail into the job result for diagnostics.
    """

    def __init__(self, message: str, full_tail: str = ""):
        super().__init__(message)
        self.full_tail = full_tail or ""


# --- Invidious fallback (round 3) ---------------------------------
# Cobalt and Piped were both tested on 15 Nov 2026 and are NOT
# usable from this VM (see module docstring), so the fallback is
# Invidious: a public API instance returns direct googlevideo.com
# stream URLs for a video id — no yt-dlp, no YouTube player API,
# no key. Only ONE api:true instance exists in the public list
# (api.invidious.io/instances.json, checked 15 Nov 2026):
# yt.chocolatemoo53.com (uptime 99.978). That single instance is
# the fragility of this route, stated plainly.
_INVIDIOUS_INSTANCES: tuple[str, ...] = (
    "https://yt.chocolatemoo53.com",
)
_HTTP_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_INVIDIOUS_API_TIMEOUT = 60
_INVIDIOUS_DL_TIMEOUT = 1800  # a 720p video stream can be big


def _http_get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA,
                                              "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Invidious API error: HTTP {exc.code} on {url}") from exc
    except Exception as exc:
        raise RuntimeError(f"Invidious API unreachable ({url}): {exc}") from exc


def _http_download(url: str, dest: Path, timeout: int = 1800) -> int:
    """Stream a URL to disk. Returns bytes written; raises on failure.

    Verifies the byte count against the response Content-Length
    (googlevideo URLs carry clen=... and a Content-Length header):
    a mid-stream connection cut looks like a CLEAN EOF to urllib
    (chunked reads just stop), so without this check a truncated
    download silently becomes a 9-second "video" — verified 15 Nov
    2026, first Invidious test merged to 9.03s of a 282s video.
    One retry on truncation, then a loud failure.
    """
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
        total = 0
        expected: int | None = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp, \
                    dest.open("wb") as out:
                try:
                    expected = int(resp.headers.get("Content-Length") or 0) or None
                except (TypeError, ValueError):
                    expected = None
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    total += len(chunk)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            if attempt == 0:
                continue
            raise RuntimeError(f"Stream download fail ho gaya: {exc}") from exc
        if expected is not None and total < expected:
            dest.unlink(missing_ok=True)
            if attempt == 0:
                continue
            raise RuntimeError(
                f"Stream download adhura raha ({total}/{expected} bytes) — "
                "connection beech me toot gaya")
        if total < 10_000:
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"Stream download bahut chhota raha ({total} bytes) — link expired ho sakta hai")
        return total
    raise RuntimeError("Stream download fail ho gaya")


def _pick_invidious_streams(data: dict) -> tuple[dict | None, dict | None, dict | None]:
    """Best <=720p mp4 streams from an Invidious /videos response.

    Returns (muxed, video_only, audio). Muxed (formatStreams) is
    preferred when present; YouTube rarely ships muxed >=360p now,
    so the usual path is video-only mp4 (avc1) + audio m4a (AAC).
    """
    def height(fmt: dict) -> int:
        # "720p60" -> 720 (leading digits only — a naive digit-strip
        # would read 72060 and reject the 720p stream entirely).
        label = str(fmt.get("qualityLabel") or "")
        m = re.match(r"\s*(\d+)", label)
        return int(m.group(1)) if m else 0

    muxed = None
    for fmt in data.get("formatStreams") or []:
        if fmt.get("container") == "mp4" and fmt.get("url") \
                and 0 < height(fmt) <= 720:
            if muxed is None or height(fmt) > height(muxed):
                muxed = fmt
    video = None
    for fmt in data.get("adaptiveFormats") or []:
        if not str(fmt.get("type", "")).startswith("video"):
            continue
        if fmt.get("container") != "mp4" or "avc1" not in str(fmt.get("type", "")):
            continue
        if not fmt.get("url") or not (0 < height(fmt) <= 720):
            continue
        if video is None or height(fmt) > height(video):
            video = fmt
    audio = None
    for fmt in data.get("adaptiveFormats") or []:
        if not str(fmt.get("type", "")).startswith("audio"):
            continue
        # Invidious reports AAC audio as container "m4a" (type
        # audio/mp4; codecs="mp4a.40.2") — accept mp4 and m4a.
        if fmt.get("container") not in ("mp4", "m4a") \
                or "mp4a" not in str(fmt.get("type", "")):
            continue
        if not fmt.get("url"):
            continue
        if audio is None or int(fmt.get("bitrate") or 0) > int(audio.get("bitrate") or 0):
            audio = fmt
    return muxed, video, audio


def _merge_av(video_file: Path, audio_file: Path, out: Path) -> None:
    """ffmpeg merge video-only + audio into one mp4 (copy first)."""
    from utils import ffmpeg_status  # local import: avoids cycles

    ffmpeg = ffmpeg_status().get("path") or "ffmpeg"
    cmd = [ffmpeg, "-y", "-i", str(video_file), "-i", str(audio_file),
           "-c", "copy", "-shortest", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        # Copy failed (codec/container mismatch) — re-encode audio.
        cmd = [ffmpeg, "-y", "-i", str(video_file), "-i", str(audio_file),
               "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(
            "Invidious streams merge nahi ho paye (ffmpeg): "
            f"{(r.stderr or '')[-300:]}")


def _download_via_invidious(video_id: str, job_dir: Path) -> tuple[Path, dict]:
    """Download a YouTube video through a public Invidious instance.

    Returns (video_path, info dict with title/duration). Raises
    RuntimeError with a plain message on any failure — the caller
    turns it into the job's failed message.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    last_err = ""
    for base in _INVIDIOUS_INSTANCES:
        try:
            data = _http_get_json(
                f"{base}/api/v1/videos/{video_id}",
                timeout=_INVIDIOUS_API_TIMEOUT)
        except RuntimeError as exc:
            last_err = str(exc)
            continue
        title = str(data.get("title") or "youtube video")
        duration = data.get("lengthSeconds")
        muxed, video, audio = _pick_invidious_streams(data)
        try:
            if muxed is not None:
                out = job_dir / "video.mp4"
                _http_download(str(muxed["url"]), out,
                               timeout=_INVIDIOUS_DL_TIMEOUT)
                return out, {"title": title, "duration": duration}
            if video is None:
                raise RuntimeError(
                    "Invidious ne <=720p mp4 video stream nahi diya")
            if audio is None:
                raise RuntimeError(
                    "Invidious ne mp4 audio stream nahi diya")
            v_file = job_dir / "video_only.mp4"
            a_file = job_dir / "audio_only.m4a"
            _http_download(str(video["url"]), v_file,
                           timeout=_INVIDIOUS_DL_TIMEOUT)
            _http_download(str(audio["url"]), a_file, timeout=600)
            out = job_dir / "video.mp4"
            _merge_av(v_file, a_file, out)
            v_file.unlink(missing_ok=True)
            a_file.unlink(missing_ok=True)
            # Duration check: a silently truncated stream must fail
            # LOUDLY here, not become a 9s video downstream (see
            # _http_download note — verified 15 Nov 2026).
            from utils import probe_video  # local import: avoids cycles

            probe = probe_video(out)
            if not probe["valid"]:
                raise RuntimeError(
                    f"Invidious merge valid nahi hai: {probe['error']}")
            expected_dur = float(duration or 0)
            if expected_dur > 0 and probe["duration"] < 0.9 * expected_dur:
                raise RuntimeError(
                    f"Invidious download adhura raha: merged video "
                    f"{probe['duration']:.1f}s hai, expected "
                    f"{expected_dur:.1f}s")
            return out, {"title": title, "duration": duration}
        except RuntimeError as exc:
            last_err = str(exc)
            continue
    raise RuntimeError(
        "Invidious fallback bhi fail ho gaya — "
        f"{last_err or 'koi stream nahi mila'}")


def _video_id_from_url(url: str) -> str:
    """Extract the 11-char video id (validate_youtube_url already ran)."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    vid = (q.get("v") or [""])[0]
    if vid:
        return vid
    parts = [p for p in parsed.path.split("/") if p]
    return parts[1] if len(parts) >= 2 else parts[0] if parts else ""


def _probe_video(url: str) -> tuple[dict, str, list[str]]:
    """--dump-json probe, trying player clients in order.

    Returns (info_dict, winning_client, clients_tried). The probe is
    the step that fails with 403 on a blocked IP ("Failed to extract
    any player response"), so it is the step that needs the fallback.
    Raises RuntimeError (plain Hinglish) when every client fails.
    """
    last_tail = ""
    tried: list[str] = []
    for client, extra in _PLAYER_CLIENTS:
        tried.append(client)
        try:
            probe = _run_yt_dlp(
                _base_yt_dlp_args() + extra +
                ["--dump-json", "--no-download", "--no-playlist", url],
                timeout=_PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_tail = f"[{client}] probe timeout ({_PROBE_TIMEOUT}s)"
            continue
        if probe.returncode == 0:
            try:
                return json.loads(probe.stdout or "{}"), client, tried
            except Exception:
                return {}, client, tried
        last_tail = (probe.stderr or probe.stdout or "").strip()[-500:]
    raise RuntimeError(_download_failed_message(last_tail))


def _download_via_ytdlp(url: str, job_dir: Path) -> tuple[Path, dict, str, list[str]]:
    """yt-dlp download (probe clients in order, then download).

    Returns (video_path, info_dict, winning_client, clients_tried).
    Raises _DownloadError (cleaned user message + full stderr tail)
    on failure.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    _check_yt_dlp_installed()
    info, client, tried = _probe_video(url)
    out_tmpl = str(job_dir / "video.%(ext)s")
    fmt = ("bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
           "best[height<=720][ext=mp4]/best")
    order = ([c for c in _PLAYER_CLIENTS if c[0] == client]
             + [c for c in _PLAYER_CLIENTS if c[0] != client])
    dl = None
    last_tail = ""
    for attempt_no, (cname, extra) in enumerate(order):
        timeout = 1800 if attempt_no == 0 else 900
        try:
            dl = _run_yt_dlp(
                _base_yt_dlp_args() + extra +
                ["-f", fmt, "--merge-output-format", "mp4",
                 "-o", out_tmpl, "--no-playlist", url], timeout=timeout)
        except subprocess.TimeoutExpired:
            last_tail = f"[{cname}] download timeout ({timeout}s)"
            dl = None
            continue
        if dl.returncode == 0:
            break
        last_tail = (dl.stderr or dl.stdout or "").strip()[-500:]
        if not _is_blocked(last_tail):
            break  # non-403 error: retrying another client won't help
    else:
        raise _DownloadError(_download_failed_message(last_tail), last_tail)
    if dl is None or dl.returncode != 0:
        raise _DownloadError(_download_failed_message(last_tail), last_tail)
    video_path = job_dir / "video.mp4"
    if not video_path.is_file():
        # yt-dlp sometimes keeps another extension when merge fails.
        cands = sorted(job_dir.glob("video.*"))
        if not cands:
            raise _DownloadError(
                "yt-dlp chala par video file nahi mili — download adhura raha.",
                last_tail)
        video_path = cands[0]
    return video_path, info, client, tried


def _download_video(url: str, job_dir: Path) -> tuple[Path, dict, str | None, list[str], str]:
    """Download a YouTube video to <job_dir>/video.mp4 (best <=720p).

    Route order (round 3): yt-dlp first (all player clients), then
    the Invidious fallback when yt-dlp fails. Returns
    (video_path, info_dict, winning_client_or_None, clients_tried,
    downloader) where downloader is "yt-dlp" or "invidious".
    Raises RuntimeError with a plain Hinglish message when every
    route fails.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    routes = _downloaders_enabled()
    ytdlp_err: _DownloadError | None = None
    if "ytdlp" in routes:
        try:
            video_path, info, client, tried = _download_via_ytdlp(url, job_dir)
            return video_path, info, client, tried, "yt-dlp"
        except _DownloadError as exc:
            ytdlp_err = exc
        except RuntimeError as exc:
            # _probe_video raises plain RuntimeError (already a clean
            # Hinglish message); keep it the same way.
            ytdlp_err = _DownloadError(str(exc), str(exc))
    if "invidious" in routes:
        try:
            video_path, info = _download_via_invidious(
                _video_id_from_url(url), job_dir)
            return video_path, info, None, [], "invidious"
        except RuntimeError as exc:
            inv_err = str(exc)
            base = (str(ytdlp_err) if ytdlp_err else
                    "yt-dlp route try nahi hua")
            err = _DownloadError(
                f"{base} — aur Invidious fallback bhi fail: {inv_err}. "
                "Thodi der baad phir try karo, ya video file upload karke "
                "V1 flow use karo.",
                (ytdlp_err.full_tail if ytdlp_err else "")[:2000])
            raise err from exc
    if ytdlp_err is not None:
        raise ytdlp_err
    raise RuntimeError("Koi download route enabled nahi hai (VCS_DOWNLOADERS dekho)")


def _register_download(video_path: Path, title: str) -> dict:
    """Register a downloaded file as a normal upload row (like /upload).

    Validates with ffprobe first — a bad download is rejected, not
    silently registered. Returns the upload dict (id, duration, size).
    """
    from utils import probe_video  # local import: avoids cycles

    probe = probe_video(video_path)
    if not probe["valid"]:
        raise ValueError(f"Downloaded video valid nahi hai: {probe['error']}")
    upload_id = uuid.uuid4().hex
    safe_title = "".join(c for c in (title or "youtube video")
                         if c not in "/\\:*?\"<>|").strip() or "youtube video"
    original_name = f"{safe_title}.mp4"
    size = video_path.stat().st_size
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?, ?, ?)",
            (upload_id, "youtube", original_name,
             str(video_path.relative_to(ROOT)), size,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return {"id": upload_id, "role": "youtube", "filename": original_name,
            "size_bytes": size, "duration_sec": round(probe["duration"], 2),
            "width": probe["width"], "height": probe["height"],
            "has_audio": probe["has_audio"]}


def run_youtube_job(job_id: str) -> None:
    """Background worker: download -> transcribe -> clips -> render-all.

    Reuses the existing internal functions (transcribe, find_clips,
    render_clip, set_clip_approval) — no duplicated pipeline logic.
    Every stage updates the job row, so GET /youtube-job/{id} shows
    real progress. Any failure marks the job "failed" with the exact
    error; partial results (upload_id, word_count) are kept in the
    result so a failed job is still diagnosable.
    """
    # Local imports: app.py imports this module, so importing the
    # pipeline modules at module level would risk cycles.
    from clip_finder import find_clips
    from editor import render_clip
    from transcriber import load_transcript, transcribe
    from workflow import is_clip_rendered_for_upload, set_clip_approval

    try:
        job = get_youtube_job(job_id)
    except FileNotFoundError:
        return
    language = job["language"]
    top_n = job["top_n"]
    result: dict = {"upload_id": "", "word_count": 0, "clips": [],
                    "rendered": []}
    try:
        # --- download ---
        # Diagnostics FIRST, so even a failed job says exactly what
        # the downloader had to work with (node path or null,
        # yt-dlp version, impersonation target).
        result["diagnostics"] = youtube_runtime_diagnostics()
        _update_job(job_id, status="downloading",
                    message="YouTube se video download ho raha hai…",
                    result=result)
        job_dir = YT_DIR / job_id
        video_path, info, client, tried, downloader = _download_video(
            job["url"], job_dir)
        title = str(info.get("title") or "youtube video")
        duration = info.get("duration")
        if duration:
            result["video_duration_sec"] = round(float(duration), 1)
        result["video_title"] = title
        result["downloader"] = downloader
        if client:
            result["player_client"] = client
            result["clients_tried"] = tried
            result["impersonation"] = result["diagnostics"].get("impersonation")
        dl_note = (f"player client: {client}" if client
                   else f"downloader: {downloader}")

        # --- register as a normal upload/project ---
        upload = _register_download(video_path, title)
        upload_id = upload["id"]
        result["upload_id"] = upload_id
        _update_job(job_id, upload_id=upload_id, result=result,
                    message=f"Download ho gaya ({dl_note}, "
                            f"{upload['duration_sec']}s) — "
                            "ab transcribe ho raha hai…")

        # --- transcribe ---
        _update_job(job_id, status="transcribing",
                    message="Transcript ban raha hai (faster-whisper)…")
        transcript = transcribe(video_path, upload_id, language=language)
        if not transcript.get("words"):
            raise ValueError(
                "Transcript khaali hai — video me boli hui baat nahi mili, "
                "clips nahi ban sakte")
        result["word_count"] = transcript.get("word_count", 0)
        result["transcript_script"] = transcript.get("transcript_script", "")
        _update_job(job_id, result=result,
                    message=f"Transcript ready ({result['word_count']} words) — "
                            "viral clips dhoondh raha hoon…")

        # --- find clips ---
        _update_job(job_id, status="finding_clips",
                    message="Viral clips dhoondh raha hoon…")
        clips_data = find_clips(transcript, upload_id, top_n=top_n,
                                language=language)
        clips = clips_data.get("clips", [])
        if not clips:
            raise ValueError(
                "Transcript mila, par koi viral clip window nahi bani — "
                "video bahut chhoti hai ya speech bahut kam hai")
        result["clips"] = [
            {"clip_id": c.get("clip_id"), "score": c.get("score"),
             "duration": c.get("duration"), "start": c.get("start"),
             "end": c.get("end"), "hook_line": c.get("hook_line")}
            for c in clips]
        result["scoring_method"] = clips_data.get("scoring_method")
        _update_job(job_id, result=result,
                    message=f"{len(clips)} clips mile — pehla test clip render "
                            "ho raha hai…")

        # --- render: test clip first, approve, render the rest ---
        _update_job(job_id, status="rendering",
                    message="Test clip render ho raha hai…")
        transcript = load_transcript(upload_id)
        rendered: list[dict] = []
        first = clips[0]
        first_clip = next(c for c in clips_data["clips"]
                          if c.get("clip_id") == first["clip_id"])
        meta = render_clip(video_path, upload_id, first_clip,
                           style_name="global-baatien", transcript=transcript)
        rendered.append({"clip_id": first["clip_id"], "status": "rendered",
                         "duration_sec": meta.get("duration_sec"),
                         "output_mp4": meta.get("output_mp4"),
                         "file_size_bytes": meta.get("file_size_bytes")})
        set_clip_approval(upload_id, first["clip_id"], "approved")
        result["rendered"] = rendered
        _update_job(job_id, result=result,
                    message="Test clip approved — baaki clips render ho rahe hain…")
        for clip in clips_data["clips"][1:]:
            cid = str(clip.get("clip_id"))
            existing = is_clip_rendered_for_upload(upload_id, cid)
            if existing is not None:
                rendered.append({"clip_id": cid,
                                 "status": "skipped_already_rendered",
                                 "output_mp4": existing.get("output_mp4"),
                                 "duration_sec": existing.get("duration_sec")})
                continue
            try:
                m = render_clip(video_path, upload_id, clip,
                                style_name="global-baatien",
                                transcript=transcript)
                rendered.append({"clip_id": cid, "status": "rendered",
                                 "duration_sec": m.get("duration_sec"),
                                 "output_mp4": m.get("output_mp4"),
                                 "file_size_bytes": m.get("file_size_bytes")})
            except Exception as exc:  # one bad clip must not kill the job
                rendered.append({"clip_id": cid, "status": "error",
                                 "error": str(exc)[:300]})
            result["rendered"] = rendered
            _update_job(job_id, result=result,
                        message=f"Rendered {len(rendered)}/{len(clips)} clips…")

        ok_n = sum(1 for r in rendered if r["status"] == "rendered")
        if ok_n == 0:
            raise RuntimeError("Koi clip render nahi ho paya — render errors dekho")
        _update_job(job_id, status="done",
                    message=f"Ho gaya — {ok_n}/{len(clips)} clips rendered, "
                            "ZIP download ke liye ready hai.",
                    result=result)
    except Exception as exc:
        # Honest failure: keep whatever partial result exists. The
        # FULL yt-dlp stderr tail (warnings included) is kept in
        # the result for diagnostics; the user-facing message
        # already had the warning lines filtered out.
        if isinstance(exc, _DownloadError) and exc.full_tail:
            result["download_stderr_tail"] = exc.full_tail[:2000]
        _update_job(job_id, status="failed",
                    message=f"Job fail ho gaya: {str(exc)[:600]}",
                    result=result)
