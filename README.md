# Viral Clip Studio

Local-first video clipping app, built in phases. **Current status: Phase 6 COMPLETE — live on Render Free since 15 Nov 2026 (https://viral-clip-studio-iy6g.onrender.com, `/health` = `{"status":"ok","phase":5}`). GitHub: https://github.com/rockeyboy805090-coder/viral-clip-studio (public).**

## Language rule — DEFAULT: ENGLISH (user correction, Phase 4)

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
  - If Whisper is not installed, `POST /transcribe` returns a clear 503 `Whisper install karo: pip install openai-whisper` — never a fake transcript
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

Install note (important, learned on this VM): `pip install -r requirements.txt` straight from PyPI tries to download torch **with ~2.5 GB of NVIDIA CUDA libraries** (torch 539 MB + cudnn 519 MB + more) at ~1 MB/s — and the first attempt also failed downloading `triton` (285 MB, connection interrupted after 167 MB). **Do this instead — CPU-only torch first:**
```bash
python -m venv .venv2
TMPDIR=$PWD/.pip-tmp .venv2/bin/pip install --resume-retries 10 --index-url https://download.pytorch.org/whl/cpu torch
TMPDIR=$PWD/.pip-tmp .venv2/bin/pip install --resume-retries 10 -r requirements.txt
```
`TMPDIR` must point to a folder on disk (not `/tmp`, which is a 512 MB tmpfs). If Whisper/torch cannot install, the app still runs and `/transcribe` returns the clear 503 above — transcription is honestly unavailable, not faked.

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
