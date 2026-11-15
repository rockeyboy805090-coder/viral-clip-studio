# Viral Clip Studio

Local-first video clipping app, built in phases. **Current status: Phase 6 COMPLETE — live on Render Free since 15 Nov 2026 (https://viral-clip-studio-iy6g.onrender.com, `/health` = `{"status":"ok","phase":5}`). GitHub: https://github.com/rockeyboy805090-coder/viral-clip-studio (public).**

## V2 (16 Nov 2026) — YouTube link se clips, automatic

**User order:** "Kisi bhi video ki link du, uski sari clips viral wali nikaal kar edit karke direct mujhko de de — main sirf video ki link dunga."

**UI:** homepage ke top par "YouTube Link se Clips Banao (V2)" section — URL input, language selector (default English), clips 1-8 (default 8), "Clips Banao" button. Job status har 10 sec me poll hota hai; done hote hi clip list + Download ZIP button dikhta hai. V1 sections (upload, style, transcribe, render, batch, history) untouched hain.

**API:**
- `POST /youtube-job` — body `{"url": "<youtube link>", "language": "en", "top_n": 8}`. Sirf youtube.com / youtu.be links allowed — baaki sab 400. Turant `{"job_id", "status": "queued"}` return karta hai; poora kaam background thread me chalta hai.
- `GET /youtube-job/{job_id}` — status (`queued|downloading|transcribing|finding_clips|rendering|done|failed`), progress message, aur done hone par: `upload_id`, transcript word count, clips (id/score/duration/hook), `download_zip_url` (`/download/<upload_id>/all`). Job state SQLite table `youtube_jobs` me persist hoti hai.

**Job flow (worker, `youtube_job.py`):** yt-dlp download (best ≤720p mp4, per-job dir `yt_downloads/<job_id>/`) → normal upload/project ki tarah register (ffprobe validation ke saath) → transcribe (faster-whisper, language from request) → find-clips (top_n) → test clip render → approve → render-all → ZIP ready. Existing internal functions reuse hote hain (transcribe / find_clips / render_clip / set_clip_approval) — pipeline logic duplicate nahi ki gayi.

**V2 fix (15 Nov 2026) — player-client fallback + JS runtime:** Live Render par download 403 se fail hua ("Unable to download API page: HTTP Error 403 … Failed to extract any player response") — YouTube web client ko datacenter IP se block karta hai. Fix: download se pehle `--dump-json` probe player clients ko is order me try karta hai — **android → ios → web** (har attempt 120s) — aur download usi client se hota hai jo probe me kaam kiya; agar download me bhi 403 aaye to baaki clients retry hote hain. Kaunsa client kaam kiya, wo job result me `player_client` field aur status message me likha hota hai. JS runtime ke liye Render build official **Node 20** tarball download karke `.node/` me extract karta hai (`render.yaml` buildCommand; startCommand bhi `.node/bin` ko PATH me rakhta hai) — yt-dlp ko `--js-runtimes node` milta hai, "without a JS runtime" deprecation warning khatm. Local par system node use hota hai. UI me job submit karne par **Job ID** bhi dikhta hai (failed job ko lookup karne ke liye).

**V2 fix round 2 (15 Nov 2026) — browser impersonation + wider clients + node diagnosis:** Live re-test 17:48–17:53 IST (job `857552aa618a`, same video `yuNCDGLGwS0`) FAILED again at downloading with the same 403 — android/ios retry did NOT help; the Render datacenter IP itself is blocked, not just the web client. And the JS-runtime warning was STILL in the live error tail. Round-2 root causes, all verified locally:
1. **Impersonation was impossible before:** `--impersonate chrome` needs curl-cffi, which was NOT installed anywhere — the standalone yt-dlp binary can never impersonate ("Impersonate target chrome is not available"). Fix: `curl-cffi>=0.10` added to `requirements.txt`; `youtube_job.py` now prefers the venv `python -m yt_dlp` when curl_cffi imports (that path also fixes the VM's MITM-proxy SSL failure, since curl_cffi/libcurl honours `SSL_CERT_FILE`), and passes `--impersonate chrome` (Firefox fallback, plain yt-dlp if neither is available; a failed impersonation flag is retried once without it instead of killing the job).
2. **Player clients widened** (each verified against live YouTube with the installed yt-dlp): **android → ios → tv_embedded → mediaconnect → web_safari → web** (120s probe each, 900s download retry on 403). `tv_embedded` and `mediaconnect` returned full JSON locally; `web_safari` is a valid client but bot-checked from this VM (kept — it may work from Render). `android_music` / `ios_music` are NOT valid clients ("Skipping unsupported client") — deliberately NOT included.
3. **Node warning root cause found:** recent yt-dlp enables ONLY `deno` by default — node sitting on PATH is ignored. Verified: warning persisted with `/usr/bin/node` on PATH, vanished with explicit `--js-runtimes node:/usr/bin/node`. Fix: node is now passed by explicit absolute path (searched at PATH, `ROOT/.node/bin`, `$PWD/.node/bin`, `/opt/render/project/src/.node/bin`), plus `--remote-components ejs:github` (yt-dlp ≥ 2026.10 needs the EJS challenge solver or warns "n challenge solving failed: Some formats may be missing"). `render.yaml` build now re-extracts Node if `.node/bin/node --version` fails, and the startCommand logs whether the bundled node exists.
4. **Diagnostics:** every job records `diagnostics` (node path found or null, yt-dlp version, curl_cffi true/false, impersonation target, clients tried) in its result, and `GET /health` exposes the same under `"youtube"` — so a live failure can be diagnosed without guessing.

**V2 fix round 3 (15 Nov 2026) — Invidious fallback downloader + JS-warning fix + vanished-job message:** Live re-test #3 (19:17–19:22 IST) FAILED again: attempt 1, job `463fcc266e61`, VANISHED mid-run (~60s in, status poll returned 404 "youtube job not found" — server restarted, ephemeral SQLite wiped); attempt 2, job `687f5676e18d`, FAILED at downloading ~3.5 min in with the same 403 ("Unable to download API page: HTTP Error 403 … Failed to extract any player response"), JS-runtime warning STILL in the error tail. Round 3 stops polishing yt-dlp and adds a DIFFERENT download route:
- **Download order is now yt-dlp first, Invidious second** (`youtube_job.py`, `result["downloader"]` records the winner: `"yt-dlp"` or `"invidious"`). Invidious = public API instance, no key: `GET {instance}/api/v1/videos/<video_id>` returns direct googlevideo.com stream URLs; best ≤720p mp4 video-only (avc1) + best m4a AAC audio are downloaded and merged with ffmpeg (`-c copy`, AAC re-encode fallback). **Verified locally 15 Nov 2026** on `yuNCDGLGwS0` (282s): merged file 37,246,570 bytes, 1280x720 h264 + AAC, duration 282.38s, ffprobe-valid.
- **Cobalt was TESTED and is NOT wired in:** official `api.cobalt.tools` now needs a JWT (Turnstile) auth key — `{"status":"error","error":{"code":"error.api.auth.jwt.missing"}}`; community instances all dead from this VM — `co.eepy.au` empty (GET http=000), `cobalt.canine.tools` GET 200 but POST `/` and `/api` both empty, `cobalt-api.kwiatekmiki.pl` empty (GET http=000); the public instance lists are closed (opt-in only, after scraping abuse). **Piped was TESTED and is NOT wired in either:** `piped.video` (frontend HTML, not API), `pipedapi.kavin.rocks` / `pipedapi-libre.kavin.rocks` / `pipedapi.reallyaweso.me` / `api-piped.mha.fi` / `pipedapi.osphost.org` all empty, `pipedapi.adminforge.de` 301 to the frontend, `pipedapi.leptons.xyz` 502.
- **Invidious instance:** `yt.chocolatemoo53.com` — the ONLY `api:true` instance in the public list (`api.invidious.io/instances.json`, uptime 99.978, checked 15 Nov 2026). Single-instance fragility is stated plainly; if it dies, the fallback dies with it until another instance is added to `_INVIDIOUS_INSTANCES`.
- **Silent-truncation bug found and fixed during testing:** the FIRST Invidious test merged to only 9.03s of a 282s video — a mid-stream connection cut looks like a clean EOF to urllib (chunked reads just stop). Fix: `_http_download` now verifies the byte count against the response Content-Length (one retry on truncation, then a loud failure), and the merged file's duration is ffprobe-checked against the expected length (must be ≥90%, else the job fails loudly instead of producing 9s clips).
- **JS-warning fix (two parts):** (1) the `web` player-client entry used to pass NO `--extractor-args` at all, so the last probe fell back to yt-dlp's DEFAULT client selection — the only code path that can print the "No supported JavaScript runtime … deprecated" warning. It now passes `player_client=web` explicitly, like every other client. (Verified locally: with `--js-runtimes node:<path>` passed, NO warning appears even for the web-style probe; without the flag the warning appears — so the flag IS the fix and IS passed on every call.) (2) Belt and braces: known warning lines (JS runtime, challenge solving, SABR) are filtered OUT of the user-facing error message — warnings are not the error — while the FULL stderr tail is kept in `result["download_stderr_tail"]` for diagnostics. `/health` now also shows the exact `js_runtimes_arg` passed and the enabled `downloaders`.
- **Vanished-job fix:** the job row is written to SQLite BEFORE the worker thread starts (already true since V2), so a restart mid-download leaves a record when the disk survives — but on Render Free the disk itself is wiped, so the record can still vanish. The real fix is the UI: a 404 on status poll now shows "Server restart ho gaya — job kho gaya. Dobara submit karo: link phir se paste karke Clips Banao dabao." instead of raw JSON.
- **Test knob:** `VCS_DOWNLOADERS` env var (default `"ytdlp,invidious"`) forces one route — `VCS_DOWNLOADERS=invidious` for fallback tests, `=ytdlp` for yt-dlp-only.
- **Local test round 3 (15 Nov 2026, video `yuNCDGLGwS0`, 282s):** (a) Invidious-forced full job `43f486d18462` — download 37.2 MB merged → **666 words** → 4 clips (scores 64/61/44/40) → **4/4 rendered** (clip_01 ffprobe-verified 1080x1920 h264 yuv420p 30fps) → ZIP HTTP 200, 102,827,028 bytes, 24 entries, testzip OK. (b) yt-dlp regression job `39a54a85fe7e` — downloader `yt-dlp`, player client `android`, **634 words**, 3 clips (66/63/56), **3/3 rendered**, ZIP HTTP 200, 69,119,248 bytes, 18 entries. (Word counts differ between runs — Whisper output varies slightly run to run; both are real transcripts of the same 282s video.)
**V2 fix round 4 (15 Nov 2026) — fallback invisibility bug + per-attempt diagnostics + live reachability check:** Live re-test #4 (20:23–20:25 IST, job `1705a95b05db`, same video) FAILED at downloading in ~2 min with the pure yt-dlp 403 — and the job record showed NO sign the Invidious fallback ever ran (no per-attempt record, no invidious field). Root cause was INVISIBILITY, proven by reading the code + the live record: (a) `run_youtube_job` truncated the failure message to `[:600]` chars, but the combined yt-dlp+Invidious message is ~820 chars with the Invidious part starting past char 660 — so even if the fallback ran and failed, its mention was cut off; (b) `clients_tried`/`player_client` were only written on SUCCESS and nothing ever recorded a failed attempt; (c) `download_stderr_tail` carried the yt-dlp tail only. Live timing (119.5s ≈ fast-failing probes + a ~60s Invidious connect timeout) is consistent with the fallback having run and hung on an unreachable instance — but nothing recorded it, so it could not be proven either way. Fix (`youtube_job.py` only): every probe/download/Invidious attempt is recorded in `result["attempts"]` (`{downloader, stage, client, status, error, duration_s}`), the Invidious outcome in `result["invidious"]` (`{tried, instance, http_status, error, streams_found, duration_s}`), the fall-through is guaranteed (ANY yt-dlp failure — probe or download, any error type — falls through to Invidious; `_DownloadError`/`_InvidiousError` carry the records on the exception itself), the combined user message is SHORT by construction (one line naming each route tried, e.g. "yt-dlp: HTTP 403 Forbidden (bot-check) (6 client try kiye) — Invidious (yt.chocolatemoo53.com): instance unreachable. …"), and `/health`'s `youtube` block now shows live Invidious reachability from the server (`GET {instance}/api/v1/stats`, 5s timeout, 60s cache) — so the parent can tell from Render whether the instance is reachable there at all.
- **Local test round 4 (15 Nov 2026):** (a) Chain test — yt-dlp forced to raise a simulated 403 on `Y6bbMQXQ180` (Richard St. John, 227s) → Invidious ran and downloaded 5,186,330 bytes, `attempts` = 6 failed yt-dlp probes + 1 ok Invidious, `invidious` diag `http_status 200, streams_found true`; both-fail simulation → combined message 192 chars naming both routes, 7 attempts recorded. (b) Invidious-forced full job `f8c3c4a62d0c` (same video, `VCS_DOWNLOADERS=invidious`, ~9 min) — **556 words** → 4 clips (56/53/47/41) → **4/4 rendered** (clip_01 + clip_02 ffprobe-verified 1080x1920 h264 yuv420p 30fps) → ZIP HTTP 200, 44,441,809 bytes, 24 entries, testzip OK; Invidious download itself took ~230s (googlevideo streams are slow from this VM). (c) yt-dlp regression job `8602cd8d4b59` (`yuNCDGLGwS0`, 282s, ~16.5 min) — downloader `yt-dlp`, client `android` (probe ok 6.9s, download ok 15.3s), **638 words**, 6 clips (75/56/55/36/36/32), **6/6 rendered** (clip_01 ffprobe-verified 1080x1920 h264 yuv420p 30fps) → ZIP HTTP 200, 144,327,532 bytes, 36 entries, testzip OK. (d) `/health` locally shows `"invidious": {"instance": "https://yt.chocolatemoo53.com", "reachable": true, "http_status": 200, "error": null}`.
**Honest limit (round 3):** local success does NOT prove live success — the 403 is IP-based, and nobody has proven the Invidious googlevideo URLs work from Render's IP yet (they may also be throttled/blocked there, or the single Invidious instance may be down when the job runs). If the live job still fails, the job error now says exactly which routes were tried, and V1 file upload still always works.
**Honest limit (unchanged):** a 403 is IP-based. Local success does NOT prove live success — the live job may still 403 from Render's IP even with impersonation + all six clients. If it does, the remaining free options are: wait and retry later, or V1 file upload (download the video on your phone, upload the file — always works).

**Limits (honest):**
- Player-client fallback 403 ko aksar bypass karta hai, par guarantee nahi — YouTube datacenter IPs ko kabhi-kabhi sab clients par block kar deta hai. Sab fail hon to job error saaf Hinglish me batata hai: "YouTube ne is server ko block kar diya hai (bot-check / 403) — thodi der baad phir try karo, ya video file upload karke V1 flow use karo" (+ yt-dlp error tail debugging ke liye). Local VM par download kaam karta hai.
- Lambi video = lamba job: pehla transcribe model download karta hai, har clip render 1-3 min. Job thread me chalta hai — `/health` beech me bhi answer karta hai.
- Server restart ho jaye mid-job to job apne last stage par atka dikhega — completion fake nahi ki jati.
- FO588 proof (15 Nov 2026): 142-min video → 25,946 words → 8 clips (scores 69-74) → 8 verified renders (1080x1920 yuv420p) + 121 MB ZIP. Ye manual pipeline se hua tha; V2 wahi pipeline automatic chalata hai.

## Phase 6 fix (15 Nov 2026) — faster-whisper replaces openai-whisper

Live `/transcribe` CRASHED the Render Free instance twice (openai-whisper + torch need ~2 GB RAM; Render Free has 512 MB). The service restarted and the uploaded project was lost (ephemeral storage). Fix: `transcriber.py` now uses **faster-whisper** (CTranslate2, CPU int8, no torch) — same Whisper model names (`tiny`/`base`/`small`), same word timestamps, same transcript JSON/TXT format, same `WHISPER_LANGUAGE`/`WHISPER_MODEL` env vars. `requirements.txt` no longer lists openai-whisper (torch is gone — do NOT re-add it). First transcribe downloads the base model (~150 MB) from HuggingFace, then it is cached. Local + live re-test numbers are in the Phase 6 fix section at the end of this README.

**Default language: English. Captions in English words.**
- Transcription default is Whisper `language="en"` (env `WHISPER_LANGUAGE=en`). UI has a Language selector: English (default), Hinglish (Roman), Hindi.
- Clip scoring uses the **English keyword bank as primary** (secret, mistake, money, never, stop, how to, etc.). Hindi/Urdu banks are fallback only when language is not English.
- Captions keep the transcript text exactly as Whisper returned it. If English was requested but the transcript comes back in Urdu/Devanagari script, the sidecar + UI record it honestly (`language_note`) — nothing is silently "converted". **English captions need English audio** — forcing "en" on Hindi audio does not translate it.
- `WHISPER_LANGUAGE` / `WHISPER_MODEL` in `.env.example`.

## Phase 4 includes

- **Workflow / project status** (`workflow.py`, SQLite tables `clip_approvals` + `batches`):
  - Status per upload, derived from real artefacts only: `uploaded → transcribed → clips_found → test_rendered → approved → all_rendered → done`
  - API: `GET /projects`, `GET /projects/{upload_id}`, `DELETE /projects/{upload_id}` (deletes only that project's files — upload, transcript, clips, its own renders; other projects untouched)
- **Test-clip approval gate (enforced in backend, not just UI):**
  - `POST /approve-clip {upload_id, clip_id}`, `POST /reject-clip {upload_id, clip_id, reason?}`
  - `POST /render-all {upload_id, style_name}` works ONLY after at least one test clip is approved — otherwise **400 "Pehle 1 test clip approve karo"**. Renders sequentially, skips already-rendered clips, skips rejected clips, returns per-clip status.
- **Batch** (`POST /batch {upload_ids[], style_name, language}`, `GET /batch/{batch_id}`):
  - Background task: transcribe + find-clips for up to 10 videos, pollable progress. Rendering is NEVER part of a batch — each video still needs a test-clip approval first.
- **UI (one page):** step indicator (1 Upload → 2 Style → 3 Transcribe → 4 Find Clips → 5 Render Test Clip → 6 Approve → 7 Render All → 8 Download), Approve/Reject buttons on every clip, Render All, Batch section, History section with Download + Delete (confirm dialog).
- **Language correction (Phase 4):** Whisper default `language="en"` + per-language initial prompts (Hinglish = Roman prompt, forces Latin-script decoding); English-first keyword banks in `clip_finder.py` (`scoring_language` recorded in clips JSON); `language_requested` / `language_note` / `transcript_script` in transcript JSON and render sidecar; Language selector in UI (default English).
- **Phase 1-3 endpoints still work:** `/health` now reports `phase: 4`

## Test (Phase 4, as run on this VM — 14 Nov 2026)

Existing Phase 3 evidence reused: upload `bf830f67…` (191.8s), 4 clips, clip_01 (49.02s) + clip_02 (53.7s) already rendered.

| Check | Result |
|---|---|
| `GET /projects` before approval | `bf830f67` status `test_rendered`, 4 clips, rendered [clip_01, clip_02], approved [] |
| Guard: `POST /render-all` BEFORE approval | ✅ **400** "Pehle 1 test clip approve karo" |
| `POST /approve-clip clip_01` + `POST /reject-clip clip_04` | ✅ 200 both; status → `approved` |
| `POST /render-all` after approval | ✅ 2m00s — 1 rendered (clip_03, 49.24s), 2 skipped (already rendered), 1 skipped (rejected), 0 error |
| ffprobe clip_03 | ✅ 1080x1920, h264, yuv420p |
| Batch: 2 English videos (64.5s + 63.1s, gTTS English speech) | ✅ batch `b241c0647fd4` → `done` 2/2, 1 clip each, `scoring_language: en`, rule-based, scores 47 + 55 |
| English transcripts | ✅ both `language_requested: en`, detected `en`, script `latin`, 166 + 154 words, **0 Arabic / 0 Devanagari chars** |
| English render (en2 clip_01) | ✅ 46.92s, 1080x1920, yuv420p, SRT English words only (0 non-Latin chars), sidecar `language_note: "Captions in English words (Roman script) as requested."`, 1m56s |
| Restore bf83 clip_01 (was overwritten by the English render — known clip_id collision, see caveat) | ✅ re-rendered 49.02s, script arabic (Hindi audio — correctly recorded) |
| History + delete | ✅ `GET /projects` showed 3 projects; DELETE removed 4 files each for both English test projects; back to 1 project (bf83, approved, renders clip_01/02/03); `/rendered/clip_01` still 200 |
| Regression | ✅ `/styles` 200, `/clips/bf83` 4 clips, `/rendered/clip_03` 200, `/rendered/clip_03/srt` 200, unknown batch/project 404 |

Caveats: (1) Rendered files are keyed by clip_id globally (`outputs/rendered/clip_01.mp4`), NOT per upload — rendering clip_01 for a second upload overwrites the first. Render-all skips only when the sidecar's upload_id matches; delete only removes renders whose sidecar matches. A per-upload render namespace is a Phase 5 fix. (2) en1 clip_01's hook_line came out as "help" — the candidate window starts mid-sentence, so the hook is a sentence fragment. Pre-existing Phase 2 behaviour (windows start at segment boundaries), not a Phase 4 regression; hook-line trimming is a Phase 5 fix. (3) Old clips files made before Phase 4 (e.g. bf83's) have no `scoring_language` field — new clips do. (4) Batch transcribe + render are synchronous/background-CPU work: ~1 min per 60s of audio (Whisper base), ~2 min per ~47s render. (5) Test audio for the English proof was English (gTTS) — the Hindi bf83 audio still transcribes to Urdu script, correctly recorded, NOT converted.

## Phase 3 includes

## Phase 1 includes

- FastAPI app launched with `python app.py` — `/health` returns `{"status":"ok","phase":1}`
- Startup FFmpeg detection, shown in the terminal and `/health`
- Dark upload page for an MP4/MOV long video and optional MP4/MOV reference video
- **ffprobe upload validation:** fake / 0-duration / non-video files are rejected with `400 — Ye valid video file nahi hai` (Phase 0 accepted a fake 4-byte MP4; Phase 1 does not)
- **Real style extraction** (`style_extractor.py`, OpenCV + FFmpeg, no paid API):
  - 1 frame sampled every 2 seconds, thumbnails saved under `outputs/thumbnails/`
  - Scene-change count via ffmpeg `select='gt(scene,0.4)'` → `cut_every_seconds`
  - Colour look measured from sampled frames (brightness / saturation / contrast / warm-cool) → FFmpeg `eq` values
  - OCR: tesseract is used only if installed; if missing, `hook_text_style="not_detected_ocr_missing"` — no text is invented
  - Output JSON under `styles/<slug>.json` with exactly 22 fields (see `style_extractor.STYLE_FIELDS`)
- **Default locked profile:** `styles/global-baatien.json` — "Global Baatien Style" (verified 14 Nov 2026): no music (`music_volume_db: -99`), no B-roll, bold ALL-CAPS glowing white captions, lower-third, dark studio, warm skin, contrast 1.15, saturation 1.10, brightness -0.02, 9:16
- **Style API:** `POST /extract-style` (file upload or `reference_id`), `GET /styles`, `GET /styles/{name}`, `POST /styles` (save/approve an edited profile)
- **Style Card UI:** extract a reference, see the profile, edit style name / caption colour / highlight / size / cut / contrast / saturation / tone, Approve & Save
- Missing files from Phase 0 now created: `utils.py`, `.env.example` (placeholder key only), `static/`, `styles/`, `outputs/`

Uploads are limited to 8 GiB per file. This build renders real 9:16 edited reels (Phase 3). A pasted social link is not accepted yet.

## Phase 5 includes — FINAL VERIFICATION (fresh end-to-end, 14 Nov 2026)

- **Download / share** (`app.py`): `GET /download/{upload_id}/{clip_id}` (per-clip ZIP), `GET /download/{upload_id}/all` (whole-project ZIP, one folder per clip), `GET /caption/{upload_id}/{clip_id}` (title + caption + hashtags JSON), `GET /share-guide` (Instagram/Facebook phone-post guide). No direct auto-post — no Meta API keys are configured; the honest flow is download → phone → post, said plainly in the guide.
- **Per-upload render namespace (Phase 5 fix, verified):** renders write to `outputs/rendered/<upload_id>/<clip_id>.*`, never the old flat `outputs/rendered/<clip_id>.*`. Namespaced endpoints exist first (`/rendered/{upload_id}/{clip_id}`), legacy flat paths are fallback only.
- **Two bugs found + fixed during this fresh verification:**
  1. **Whole-project ZIP 404:** `/download/{upload_id}/all` returned 404 "Rendered clip nahi mila: all" — FastAPI matched the literal `all` as `{clip_id}` because `/download/{upload_id}/{clip_id}` was registered first. Fix: the `/all` route is now registered BEFORE the `/{clip_id}` route (with a comment in `app.py` so nobody reorders it back).
  2. **Sidecar `.json` missing from ZIPs:** `editor.py` wrote `output_json` into the returned dict but NOT into the sidecar JSON on disk, so `/download/.../all` (which reads sidecars from disk) silently skipped every `.json`. Fix: `output_json` is now set in the sidecar BEFORE it is written; the 5 existing sidecars on disk were patched to add the key (path is real, nothing faked).
- **Face-tracking `centre_crop`: STILL UNVERIFIED on a real face.** The code exists (Haar cascade, face width ≥ 12% → centre-crop) but every test video so far is testsrc (no face), so only `blur_fill` has ever been verified live. Do NOT claim centre_crop works until a real talking-head video is rendered and the frames are eyeballed. `blur_fill` is verified.

## Test (Phase 5, fresh end-to-end — 14 Nov 2026, upload `031694cfa21f464f9cb4c14395d560ad`)

Fresh test video built for this run only: `test_make_speech_video_p5.py` (NEW English lines, not the Phase 2/4 scripts), gTTS English speech, 118.9s, 1280x720, `/tmp/vcs_p5_en.mp4` → uploaded as `vcs_p5_en.mp4` (2,827,860 bytes). Server: `.venv2/bin/python -u app.py`, `/health` phase 5, FFmpeg 8.1.2. Times are CPU times on this VM.

| Step | Result |
|---|---|
| `GET /health` | ✅ `{"status":"ok","phase":5,"ffmpeg":"8.1.2"}` |
| `POST /upload` fresh video | ✅ 200, id `031694cf…`, 118.9s, 1280x720, has_audio |
| `POST /transcribe` language=en (Whisper base, 87.7s) | ✅ 200, language `en`, script `latin`, **304 words, 0 non-Latin chars**, note "Transcript in English words (Roman script) as requested." |
| `POST /find-clips` top_n=5 | ✅ **2 clips** (118.9s / ~50s windows = max 2 non-overlapping), scores **61 + 45**, `scoring_method: rule_based`, `scoring_language: en`. Hooks are COMPLETE phrases: clip_01 "So the winners are not the smartest people, they are the ones who never…" / clip_02 "Second, work in 90 minute blocks, and never check messages in between." — the Phase 4 "help" fragment did NOT recur on this run. |
| `POST /render-clip` clip_01, Global Baatien (2m19s) | ✅ 200, **55.76s** (53.76+2), 1080x1920, 30fps, h264, **yuv420p**, 9,914,152 bytes, caption_script `latin`, font DejaVu Sans (Montserrat not installed — recorded fallback), 41 phrases / 143 words, reframe `blur_fill` (no face), grade 1.15/1.10/-0.02, music OFF. Output: `outputs/rendered/031694cf…/clip_01.*` (namespaced, incl. `.json` sidecar). |
| **Namespace non-overwrite** | ✅ Legacy bf83 `clip_01.mp4` md5 `be806e7d…` UNCHANGED (7,981,706 bytes); new clip_01 md5 `551beb6c…` (9,914,152 bytes); 78c7 clip_01 md5 `279506b1…` untouched. `GET /rendered/bf83…/clip_01` still returns the OLD clip (200, 7,981,706 bytes); namespaced new clip returns the NEW clip. |
| SRT check (both clips) | ✅ **0 non-Latin chars** in both SRTs; English words only. |
| Guard: `POST /render-all` BEFORE approval | ✅ **400** "Pehle 1 test clip approve karo" |
| `POST /approve-clip` clip_01 | ✅ 200, project status → `approved` |
| `POST /render-all` after approval (2m03s) | ✅ 1 rendered (clip_02, 48.74s, 8,493,146 bytes, 1080x1920 yuv420p, SRT 0 non-Latin), 1 skipped (already rendered), 0 error. Project status → **`done`, `all_rendered: true`**. |
| `GET /download/<new>/clip_01` | ✅ 200, 9,839,768 bytes, 7 files: MP4 + SRT + ASS + txt + caption txt + sidecar JSON + style JSON |
| `GET /download/<new>/all` | ✅ 200 (after route-order fix; was 404 before), 18,267,827 bytes, **12 files** (2 clips × 6 incl. `.json` each); extracts cleanly. Legacy bf83 all-ZIP also 200, 18 files. |
| `GET /caption/<new>/clip_01` + clip_02 | ✅ 200, English title/caption. Note: hashtags are `#rajshamani #aura #podcasts #viralreels #trendingnow` — they come from the Global Baatien style profile, not from the video content. For non-Raj-Shamani videos the user should edit the style profile's hashtags or the `_caption.txt`. |
| `GET /share-guide`, `GET /projects` | ✅ 200 both. `/projects` shows 3 projects; new project `done`, 2 clips, 2 rendered. |
| Regression | ✅ `/styles`, `/clips`, `/rendered/<old>/clip_01`, homepage all still 200 after the fixes. |

**Phase 5 caveats (honest):** (1) `centre_crop` face-tracking still unverified on a real face — see above. (2) First SRT phrase of a clip can be a mid-sentence fragment ("week of a new habit?") because the render pads 1s before the clip window — the `hook_line` itself is a complete phrase; only the padded first caption phrase is affected. (3) The 78c7 English project from Phase 4 has a namespaced render MP4 but NO sidecar JSON (pre-Phase-5 artefact), so `/download/78c7…/clip_01` 404s and `/projects` shows it as 0 rendered — its MP4 is on disk but unlisted. Not repaired (would need re-rendering). (4) Render is synchronous (~2 min per ~50s clip on CPU); transcription ~88s per 2 min audio (Whisper base). (5) Gemini path still untested live (no key) — all scoring in every phase so far was `rule_based`.

## Phase 3 includes

- **Real 9:16 rendering** (`editor.py`, FFmpeg + OpenCV, no paid API):
  - Cut `start-1s .. end+1s` (clamped to source duration)
  - Reframe: face detected (Haar cascade, face width ≥ 12% of frame) → centre-crop 9:16 on the speaker; no face → blurred-background fill (`gblur` + overlay), never black bars, never stretch
  - Zoom punch-in 110% for the first 2s (hook), then 100% (two trims + concat)
  - Colour grade from the Style Profile (`eq=contrast:saturation:brightness`) — Global Baatien: 1.15 / 1.10 / -0.02
  - Word-by-word captions burned in as ASS karaoke (2-4 words/phrase, current word in profile highlight colour), hook text top-centre for the first 2s
  - Audio: original voice only for Global Baatien (music OFF). If a style asks for music but no track exists in `static/music/`, voice-only with a note — no copyrighted music, ever
  - Export: **1080x1920, 30fps, H.264 CRF 18, yuv420p, AAC 192k, 48kHz** (`yuv420p` is mandatory for Android phones)
  - Outputs per clip under `outputs/rendered/<clip_id>.*`: `.mp4`, `.srt`, `.ass` (burned-in source), `.txt` (transcript), `_caption.txt` (title + caption + hashtags), `.json` (full render metadata sidecar)
- **Caption script decision (honest):** Whisper base renders Hindi in Urdu (Perso-Arabic) script. Captions keep the transcript text EXACTLY as Whisper returned it — no silent transliteration. Output records `caption_script` and the font actually used. Font is picked per script (DejaVu has no Arabic glyphs): Arabic → Noto Sans Arabic Bold, Devanagari → Noto Sans Devanagari Bold, Latin → DejaVu Sans Bold. Montserrat ExtraBold is the profile name but is NOT installed on this VM — the fallback is recorded in `font_fallback_note`.
- **Silence removal: SKIPPED on purpose.** `silenceremove` can cut words when its detector disagrees with Whisper's word timings; a half-cut word is worse than a 0.3s pause. Recorded as `silence_removed: false`.
- **New API:** `POST /render-clip {upload_id, clip_id, style_name}` (synchronous, 1-3 min for a ~47s clip on CPU), `GET /rendered/{clip_id}` (MP4 download), `GET /rendered/{clip_id}/srt`, `GET /rendered/{clip_id}/info`
- **UI:** "Render 1 Test Clip" section — upload id + clip id + style → render → preview video player + Download MP4 + Download SRT
- **Phase 1 + 2 endpoints still work:** `/health` now reports `phase: 3`

## Test (Phase 3, as run on this VM — 14 Nov 2026)

Test video: same Phase 2 gTTS Hindi speech video, 191.83s, 1280x720, re-created with `test_make_speech_video.py` (upload id `bf830f679e4140718915b87cd33931dd`). Transcript: Whisper base, 440 words, `hi`, real word timestamps. Clips: 4, rule-based, top clip_01 6.48-53.5s, score 44.

| Attempt | Result |
|---|---|
| 1-2 | ❌ Render fail: `concat` size/SAR mismatch — `[vbase]` was consumed by two `trim` filters. Fix: `split=2` before the trims. (Attempt 2 failed identically because the server was still running old code — `reload=False`, restart required.) |
| 3 | ❌ Render "succeeded" but file was **43.57s, not 49.02s** — output `-ss` + `trim` silently drops exactly the `-ss` seconds. Verified with isolated ffmpeg tests (12s → 6.53s in every trim variant; input `-ss` + trim → 12.00s). |
| 4 | ❌ Same 43.57s — the `setpts=PTS-STARTPTS` fix did nothing (timestamps were already 0-based; `showinfo` proved it). |
| 5 | ✅ Duration fixed: **49.02s** — cut moved entirely into the filter (`-ss` only as INPUT seek to `clip_start-2s`, `trim`/`atrim` with times relative to the seek point). But visual QA found 2 defects (see below). |
| 6 | ✅ **FINAL PASS** — duration 49.02s, font + karaoke timing fixed (below). |

**Visual QA defects found in attempt 5 (frames eyeballed, not just ffprobe):**
1. **Tofu box:** "والا" rendered as `و▯لا` in Noto Sans Arabic Bold (isolated-alef shaping bug in that weight — glyph exists in the cmap, harfbuzz shaping fails). **Fix:** Arabic font is now **Noto Naskh Arabic Bold**, which renders "والا" perfectly (verified word-by-word).
2. **Karaoke ran ahead:** `\k` windows used raw word durations, so flips landed before the spoken word when Whisper left gaps ("کیا" flipped 0.66s early). **Fix:** each word's `\k` window now stretches to the next word's start (last word: phrase end). Verified at t=0.5 (والا+ہم green, کیا+آپ white) and t=1.2 (کیا green, آپ white).
- Isolated karaoke test proved libass `\k` is a **whole-word flip** (no smooth sweep): at 0.3s ONE green/rest white; at 1.2s ONE+TWO+THREE green, FOUR white. That is the CapCut-style highlight and is correct as designed.

**Final live run (attempt 6, Global Baatien Style):**
- `/render-clip clip_01` → HTTP 200, 2m05s render, 1080x1920, 30fps, H.264, **yuv420p**, AAC 48kHz, 7,981,706 bytes, duration **49.02s** (video 49.00 / audio 49.02)
- Captions: 30 phrases, 118 words, script `arabic` (Whisper text kept exactly as returned), font **Noto Naskh Arabic**
- Reframe: `blur_fill` (test video is testsrc — no face detected, correctly), zoom punch 110% first 2s, grade contrast 1.15 / saturation 1.10 / brightness -0.02, music OFF (voice only, -3dB)
- Files: `outputs/rendered/clip_01.{mp4,srt,ass,txt,_caption.txt,json}` (+ `clip_02.mp4` rendered as a repeatability check)
- Endpoints: `GET /rendered/clip_01` 200 (7,981,706 bytes), `/rendered/clip_01/srt` 200, unknown clip 404; `/styles`, `/clips`, homepage still 200 (Phase 1/2 regression pass)
- Sample kept as evidence: `outputs/rendered/clip_01.mp4` — open it to see hook + captions

Caveats: render is synchronous (blocks the server 1-3 min per ~47s clip on CPU). `centre_crop` reframe (face tracking) is implemented but NOT yet exercised on a real talking-head video — the test video has no face, so only `blur_fill` was verified live. Silence removal is skipped on purpose (see above). Montserrat is not installed; the recorded fallback font is used.

## Phase 2 includes

- **Real Whisper transcription** (`transcriber.py`, local/offline):
  - FFmpeg extracts 16 kHz mono WAV to a temp file (deleted after use)
  - Whisper model `tiny` by default (fast, low RAM); `WHISPER_MODEL=base` env override for real use
  - Word-level timestamps, detected language, full text + segments
  - Saved per upload: `outputs/transcripts/<upload_id>.json` + `.txt`
  - If faster-whisper is not installed, `POST /transcribe` returns a clear 503 `faster-whisper install karo: pip install faster-whisper` — never a fake transcript
  - If the video has no audio, a clear 400 error (no transcript possible)
- **Real viral clip finding** (`clip_finder.py`):
  - Candidate 30-60s windows (sliding, segment boundaries, min 25s / max 65s)
  - Score 0-100: hook first 5s = 30, emotion/curiosity = 30, complete idea = 20, loop = 20
  - **Gemini FREE API only if `GEMINI_API_KEY` is set in `.env`** (and `google-generativeai` installed); otherwise rule-based fallback. Output always records `scoring_method: "gemini"` or `"rule_based"` — Gemini is never claimed when it was rule-based
  - Top 5-8 clips (dedupe: windows overlapping >40% are suppressed), sorted by score, each with `clip_id, start, end, duration, score, reason, hook_line, transcript_text`
  - Saved: `outputs/clips/<upload_id>_clips.json`
- **New API:** `POST /transcribe {upload_id}`, `GET /transcript/{upload_id}`, `POST /find-clips {upload_id, top_n=5, style_name}`, `GET /clips/{upload_id}`
  - `/find-clips` transcribes automatically first if no transcript exists
- **UI:** "Find Viral Clips" section — paste upload id, Transcribe → Find Viral Clips, clips list with score, hook line, start-end, Approve tickbox (UI only; rendering is Phase 3)
- **Phase 1 endpoints still work:** `/health` now reports `phase: 2`, `/upload`, `/extract-style`, `/styles` unchanged

## Test (Phase 2, as run on this VM)

```bash
# 1. Install (Whisper pulls torch ~800 MB — slow on flaky networks; see note below)
python -m venv .venv2
TMPDIR=$PWD/.pip-tmp .venv2/bin/pip install --resume-retries 10 -r requirements.txt
.venv2/bin/python app.py

curl http://127.0.0.1:8000/health   # {"status":"ok","phase":2,...}

# 2. Real-speech test video (gTTS Hindi speech, ~4 min) — test helper, not part of the app:
python3 -m venv .tts_venv && .tts_venv/bin/pip install -q gtts
.tts_venv/bin/python test_make_speech_video.py   # writes /tmp/vcs_test_long.mp4

# 3. Upload + transcribe + find clips:
curl -F "long_video=@/tmp/vcs_test_long.mp4" http://127.0.0.1:8000/upload
# -> note the long_video id, then:
curl -F "upload_id=<id>" http://127.0.0.1:8000/transcribe
curl -F "upload_id=<id>" -F "top_n=5" http://127.0.0.1:8000/find-clips
curl http://127.0.0.1:8000/clips/<id>
```

Install note (updated 15 Nov 2026 — torch is GONE): transcription now uses faster-whisper (CTranslate2, no torch, no CUDA). Just `pip install -r requirements.txt` — no CPU-torch dance needed. (Historical: Phase 2 used openai-whisper, whose PyPI torch pulled ~2.5 GB of NVIDIA CUDA libs and needed a CPU-only torch install first. That path is obsolete — do NOT re-add openai-whisper/torch.) If faster-whisper cannot install, the app still runs and `/transcribe` returns the clear 503 above — transcription is honestly unavailable, not faked.

Gemini note: no key was set during Phase 2 testing, so all test clips used `scoring_method: "rule_based"`. To use Gemini, copy `.env.example` to `.env` and set `GEMINI_API_KEY=<free key>` — never commit a real `.env`.

## Measured results (Phase 2 live test, 14 Nov 2026)

Test video: 191.8s, 1280x720, real Hindi speech (gTTS, 30 lines, 423 words) — built by `test_make_speech_video.py`.

| Model | Words | Time (192s audio, CPU) | Verdict |
|---|---|---|---|
| Whisper `tiny` | 283 | 5:01 | ❌ Unusable for Hindi — mostly garbage / mixed scripts |
| Whisper `base` (no prompt) | 441 | 3:27 | ✅ Best — words ~right, but Hindi rendered in **Urdu script** |
| Whisper `base` + Devanagari `initial_prompt` | 308 | ~10:00 | ⚠️ Script fixed to Devanagari, but words garbled — worse overall |

**Default model is now `base`** (was `tiny` in the plan) — measured faster AND more accurate. `initial_prompt` is supported via API/env (`WHISPER_INITIAL_PROMPT`) but NOT default, because it hurt accuracy. Urdu-script keyword banks were added to `clip_finder.py` so Hindi transcripts score properly in either script.

Final live run (base, no prompt, rule-based scoring — no Gemini key):
- `/transcribe` → HTTP 200, language `hi`, 441 words, real word timestamps, 34 segments
- `/find-clips top_n=5` → **4 clips** (191s video / ~47s clips = max ~4 non-overlapping windows; a 260s synthetic transcript gave 5), scores **44, 34, 32, 30**, durations 47.0s / 51.7s / 47.2s / 30.9s, hooks are the correct lines
- Synthetic-transcript test (260s, Roman Hinglish): 5 clips, scores 35-56
- Phase 1 regression: `/extract-style` still returns 22 fields, `/styles` lists Global Baatien Style, homepage 200
- Edge cases: `/transcribe` without Whisper → 503 `Whisper install karo`; unknown ids → 404; fake upload → 400; empty transcript → no candidates

Caveats: Whisper `base` Hindi comes out in Urdu script (words right, script wrong) — captions in Phase 3 will need a script decision (keep Urdu script, transliterate, or accept Devanagari-prompt accuracy loss). Transcription is slow on CPU (~3.5-10 min for 3 min of audio) — `small` model would be more accurate but slower still.

## Run

```bash
python -m venv .venv
# CPU-only torch FIRST (see install note below), then requirements:
TMPDIR=$PWD/.pip-tmp .venv/bin/pip install --resume-retries 10 --index-url https://download.pytorch.org/whl/cpu torch
TMPDIR=$PWD/.pip-tmp .venv/bin/pip install --resume-retries 10 -r requirements.txt
.venv/bin/python app.py
```

Open <http://127.0.0.1:8000>. Health check: <http://127.0.0.1:8000/health>.

On this VM the already-installed, verified env is `.venv2` — run with `.venv2/bin/python app.py` (no reinstall needed).

FFmpeg + ffprobe must be installed separately and available on `PATH`. The app reports its status at startup; it remains available if FFmpeg is missing, but extraction needs it.

## Test (Phase 1, as run on this VM)

```bash
# Real 12-second test reference video (FFmpeg lavfi, no copyrighted content)
ffmpeg -y -f lavfi -i testsrc=duration=12:size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=440:duration=12 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest /tmp/test_ref.mp4

curl http://127.0.0.1:8000/health
curl -F "reference_video=@/tmp/test_ref.mp4" -F "style_name=Test Style" http://127.0.0.1:8000/extract-style
curl http://127.0.0.1:8000/styles
printf 'fake' > /tmp/fake.mp4
curl -F "long_video=@/tmp/fake.mp4" http://127.0.0.1:8000/upload   # expect 400 "Ye valid video file nahi hai"
```

## Local data

- Uploaded files: `uploads/`
- Style profiles: `styles/*.json`
- Thumbnails: `outputs/thumbnails/`
- SQLite database: `viral_clip_studio.sqlite3`

These runtime files are created when the app starts and are not source files.

## Planned phases

- Phase 1: ✅ reference-video style profile extraction and approval
- Phase 2: ✅ local Whisper transcription and candidate clip scoring
- Phase 3: ✅ 9:16 clip rendering and editable text assets
- Phase 4: ✅ workflow, history, batch mode, and test-clip approval — **DEFAULT LANGUAGE: ENGLISH**
- Phase 5: download/share conveniences
- Phase 6: GitHub + Render free deploy + live link

No paid service or API key is required for Phase 1. Gemini key is Phase 2 only, and optional — rule-based scoring works without it.

## Phase 6 fix re-test — faster-whisper (15 Nov 2026)

Live `/transcribe` crashed Render Free twice (openai-whisper + torch OOM, 512 MB). Fix: faster-whisper (CTranslate2 int8, no torch) + a stdlib-`wave` WAV decode workaround (faster-whisper 1.2.1's PyAV decoder breaks on av>=14: `av.open() got an unexpected keyword argument 'metadata_errors'`).

Local re-test (same 118.9s English test video, upload `cf004e3bd8924162b832b5ca2134e1c6`):

| Check | Result |
|---|---|
| `POST /transcribe` (model cached) | ✅ 29.3s, **304 words** — identical count to old openai-whisper base (304), same opening text ("Good morning friends, today I will share the biggest secret about focus…"), 13 segments, real word timestamps, `en` / `latin`, note "Transcript in English words (Roman script) as requested." — **no quality loss** |
| Peak server RSS during transcribe | ✅ **426.9 MB** — under Render Free's 512 MB (~85 MB headroom). Old torch stack was ~2 GB+ → definite OOM |
| `POST /find-clips` | ✅ 3 clips (scores 63/49/40, rule-based, `en`) — Phase 5 had 2 clips (61/45); small difference from faster-whisper segment boundaries, not a regression |
| `POST /render-clip clip_01` | ✅ 2m20s, 49.03s, 1080x1920, h264, yuv420p, 30fps, AAC, 8,660,854 bytes, SRT 0 non-Latin chars, sidecar JSON present |
| Live re-test (1st attempt, 15 Nov 2026) | ⚠️ **Transcribe FIXED, render CRASHED**: upload OK (88s cold start), transcribe completed on-server — 304 words, engine faster-whisper, `en`/`latin`, 0 non-Latin chars — BUT the client connection was cut at ~4m30s (Render proxy) and /health hung for ~12 min because the sync transcribe blocked the event loop; render then 502'd at 56s and the instance restarted, wiping the project (ephemeral storage). Root cause: (a) event-loop blocking, (b) ffmpeg render peaked at ~520 MB alone (x264 medium lookahead) — see "Render OOM fix" below. Fixed in code (to_thread + model free + render memory diet) and re-tested locally; 2nd live test pending after push. |
| Live re-test (2nd attempt, 15 Nov 2026) | ✅ **FULL END-TO-END PASS, no crash**: upload 746b1d65… (118.9s, 3.7s); transcribe completed server-side in ~668s (~11 min, first use incl. ~150 MB model download + 0.1 CPU) — **304 words**, engine faster-whisper, `en`/`latin`, 0 non-Latin chars, real word timestamps; **/health answered 24/24 times DURING the transcribe** (to_thread fix — was 0/0, hung); client connection cut at 277s by Render proxy but transcript was saved, retrieved via `GET /transcript/{id}`; find-clips 2 clips (57/48, rule-based, `en`); render clip_01 completed server-side in ~6 min — 49.08s, 1080x1920, h264, yuv420p, 30fps, AAC, 8,281,261 bytes, SRT 0 non-Latin chars, blur_fill + zoom, frame eyeballed (blurred fill + green/white karaoke OK); project ZIP 200, 8,209,039 bytes, 6 files; project status `test_rendered`, no restart, nothing lost. **Live link now works end to end.** Remaining honest limits: first transcribe is slow (~11 min) and the proxy cuts the response — poll for the transcript; a crash would still wipe the project + model cache (ephemeral storage). |

## Render OOM fix (15 Nov 2026) — second live crash, same root cause (memory)

After the faster-whisper swap, live transcribe worked (304 words) but live **render** 502'd at 56s and the instance restarted, wiping the project. Measured locally: the ffmpeg child alone peaked at **~520 MB RSS** (preset medium, uncapped threads) — over Render Free's 512 MB before counting the Python server at all. Fixes (all measured on a 10s window of the same clip, then full-clip verified):

- **faster-whisper model freed after transcribe** (`del model` + `gc.collect()` + `malloc_trim`): RSS after transcribe 309 → **~190 MB**.
- **CPU work off the event loop** (`asyncio.to_thread` for `/transcribe`, `/find-clips` auto-transcribe, `/render-clip`, `/render-all`): /health now answers 15/15 times DURING a transcribe (was 0 — it hung for the whole transcribe).
- **Render memory diet** (`editor.py`): blur background at quarter resolution (270x480, sigma=10, upscale — same look, blur was NOT the hog: -15 MB), x264 `veryfast` + `rc-lookahead=5:ref=1`, threads=1. Bisection: medium = 520 MB, veryfast+look10 = 329 MB, + blur-downscale + threads1 = 252 MB, look5+ref1 = **220 MB**. Quality vs medium original: **SSIM 0.9956, PSNR 45.8 dB**, file 1.76 vs 1.89 MB — visually transparent at CRF 18. superfast rejected (170 MB but 3.3x file size).
- **Full local re-test (final code, same 118.9s video, upload `fd43cd59…`):** transcribe 34s, 304 words, peak 375 MB, RSS after 187 MB, /health 15/15 OK during; find-clips 2 clips (61/38); render 77s (was 147s — 2x faster), **combined peak server+ffmpeg 451 MB** (was 878 MB) — ~60 MB under the 512 MB limit; output 55.08s, 1080x1920, h264, yuv420p, 9,371,450 bytes, SRT 0 non-Latin chars. Frame eyeballed: blurred fill + sharp centre + green/white karaoke captions correct.
- **Known live limits (honest):** first live transcribe takes ~12 min wall-clock on Render Free (model download ~150 MB + 0.1 CPU) and Render's proxy cuts the client connection after ~4.5 min — the transcript IS saved, poll `GET /transcript/{id}` for it. A crash still wipes the project + model cache (ephemeral storage) — re-upload + re-transcribe needed after any restart.
