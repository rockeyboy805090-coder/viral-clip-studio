"""Phase 2: local Whisper transcription — real implementation.

Free stack only: FFmpeg + openai-whisper (local, offline). No paid API,
no network call for transcription itself.

Behaviour:
- Extract audio from the video with FFmpeg to 16 kHz mono WAV in
  outputs/audio/<upload_id>.wav (temp file, deleted after transcription).
- Load Whisper model "base" by default. Measured on this VM (Nov 2026,
  192s Hindi speech): tiny = 283 words, mostly garbage, 5:01 min;
  base = 441 words vs 423 in script, 3:27 min — base is BOTH faster
  and far more accurate, so it is the default. Env override:
  WHISPER_MODEL=tiny (fast smoke test) or small/medium (better still).
  Model cache: ~/.cache/whisper (Whisper default).
- If whisper/torch is NOT installed, raise RuntimeError with the clear
  message "Whisper install karo: pip install openai-whisper" — never fake
  a transcript.
- Returns word-level timestamps: words [{word, start, end}], full text,
  segments, detected language. Saves transcript JSON + .txt per upload
  under outputs/transcripts/<upload_id>.json / .txt
- Language: DEFAULT IS ENGLISH (user correction, Phase 4).
  WHISPER_LANGUAGE env (default "en"). Options: "en" (English),
  "hinglish" (Roman Hinglish), "hi" (Hindi). Whisper is forced to
  that language — it does NOT translate: if the source audio is
  Hindi, forcing "en" makes Whisper decode it as English-like text
  (poor/approximate) or, for clearly Hindi audio, still emit Hindi
  words in Urdu script. The output records language_requested,
  language (detected) and language_note so a mismatch is VISIBLE,
  never silently "converted". For real English captions, the source
  audio must be English. Words are NEVER invented — output is
  exactly what Whisper heard.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from utils import probe_video

# Default language: ENGLISH (user order, Phase 4). Captions must be
# English words by default — never Urdu script by default.
DEFAULT_LANGUAGE = "en"
LANGUAGE_PROMPTS = {
    # Forced-English decoding + a steering prompt keeps output Latin.
    "en": "This is an English conversation about business, money, motivation, success and life.",
    # Hinglish in ROMAN script: force English decoding so Hindi words
    # come out phonetically in Latin letters, not Devanagari/Urdu.
    "hinglish": "This is a Hinglish conversation in Roman English, mixing Hindi and English words, written in Roman script.",
    # Hindi: no forced prompt (user can steer via WHISPER_INITIAL_PROMPT).
    "hi": None,
}
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def normalize_language(value: str | None) -> str:
    """en / english -> 'en', hinglish -> 'hinglish', hi / hindi -> 'hi'."""
    v = (value or "").strip().lower()
    if v in ("en", "english"):
        return "en"
    if v in ("hinglish", "hinglish-roman", "roman"):
        return "hinglish"
    if v in ("hi", "hindi"):
        return "hi"
    return DEFAULT_LANGUAGE


def _detect_script(text: str) -> str:
    if _ARABIC_RE.search(text or ""):
        return "arabic"
    if _DEVANAGARI_RE.search(text or ""):
        return "devanagari"
    return "latin"

TRANSCRIPTS_DIR = Path(__file__).resolve().parent / "outputs" / "transcripts"


def extract_audio(video_path: Path, wav_path: Path) -> None:
    """Extract 16 kHz mono WAV audio from a video with FFmpeg.

    Raises RuntimeError if ffmpeg is missing or extraction fails, or if
    the video has no audio stream.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg install karo — audio extract nahi ho sakta")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(video_path),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
        capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size == 0:
        err = (result.stderr or "").strip()
        raise RuntimeError(
            f"Audio extract nahi hua — video me audio stream nahi hai ya file kharab hai. {err}")


def _load_model(model_name: str):
    """Import whisper lazily and load the model.

    Lazy import so `import transcriber` works even when Whisper is not
    installed — the error only appears when transcription is attempted.
    """
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Whisper install karo: pip install openai-whisper"
        ) from exc
    return whisper.load_model(model_name)


def transcribe(video_path, upload_id: str, model_name: str | None = None,
               initial_prompt: str | None = None,
               language: str | None = None) -> dict:
    """Transcribe a video file with local Whisper.

    Args:
        video_path: path to the uploaded MP4/MOV.
        upload_id: upload id (used for output filenames).
        model_name: Whisper model name; defaults to WHISPER_MODEL env or "base".
        initial_prompt: optional prompt to steer Whisper's decoding.
            Defaults per language (LANGUAGE_PROMPTS) if not given.
        language: "en" (DEFAULT), "hinglish" or "hi". Defaults to
            WHISPER_LANGUAGE env or "en". This is a DECODING language,
            not a translator — Hindi audio forced to "en" will NOT
            become clean English. The mismatch is recorded in
            language_note, never hidden.

    Returns dict with keys: upload_id, model, language (detected),
    language_requested, language_note, duration_sec, text, word_count,
    words [{word, start, end}], segments [{start, end, text}],
    transcript_json, transcript_txt.

    Side effects: writes outputs/transcripts/<upload_id>.json and .txt.
    The temp WAV is deleted after transcription.
    """
    video_path = Path(video_path)
    probe = probe_video(video_path)
    if not probe["valid"]:
        raise ValueError(f"Ye valid video file nahi hai: {probe['error']}")
    if not probe["has_audio"]:
        raise ValueError("Is video me audio nahi hai — transcript nahi ban sakta")
    model_name = model_name or os.getenv("WHISPER_MODEL", "base")
    language_requested = normalize_language(
        language or os.getenv("WHISPER_LANGUAGE") or DEFAULT_LANGUAGE)
    if initial_prompt is None:
        initial_prompt = (os.getenv("WHISPER_INITIAL_PROMPT")
                          or LANGUAGE_PROMPTS.get(language_requested))
    # Whisper language code: "en" for English AND Hinglish (Roman
    # decoding is forced by language="en" + the Roman prompt above);
    # "hi" only when the user explicitly picks Hindi.
    whisper_lang = "hi" if language_requested == "hi" else "en"

    tmp_dir = Path(tempfile.gettempdir())
    wav_path = tmp_dir / f"vcs_{upload_id}.wav"
    try:
        extract_audio(video_path, wav_path)
        model = _load_model(model_name)
        # word_timestamps=True gives word-level timings (needs the model
        # to support it — tiny/base do).
        kwargs: dict = {"word_timestamps": True, "verbose": False,
                        "language": whisper_lang}
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        result = model.transcribe(str(wav_path), **kwargs)
    finally:
        wav_path.unlink(missing_ok=True)

    language = str(result.get("language") or "unknown")
    text = str(result.get("text") or "").strip()
    words: list[dict] = []
    segments: list[dict] = []
    for seg in result.get("segments") or []:
        segments.append({
            "start": round(float(seg.get("start", 0.0)), 2),
            "end": round(float(seg.get("end", 0.0)), 2),
            "text": str(seg.get("text") or "").strip(),
        })
        for w in seg.get("words") or []:
            word = str(w.get("word") or "").strip()
            if not word:
                continue
            words.append({
                "word": word,
                "start": round(float(w.get("start", 0.0)), 2),
                "end": round(float(w.get("end", 0.0)), 2),
            })
    # Fallback: if word timestamps came back empty (some builds), rebuild
    # words from segments by splitting on spaces — timestamps are then
    # segment-level, marked honestly in the output.
    word_timestamps_real = len(words) > 0
    if not word_timestamps_real:
        for seg in segments:
            if not seg["text"]:
                continue
            parts = seg["text"].split()
            if not parts:
                continue
            span = max(0.01, seg["end"] - seg["start"]) / len(parts)
            for i, part in enumerate(parts):
                words.append({
                    "word": part,
                    "start": round(seg["start"] + i * span, 2),
                    "end": round(seg["start"] + (i + 1) * span, 2),
                })

    out = {
        "upload_id": upload_id,
        "model": model_name,
        "language": language,
        "language_requested": language_requested,
        "whisper_language": whisper_lang,
        "duration_sec": round(probe["duration"], 2),
        "text": text,
        "word_count": len(words),
        "word_timestamps_real": word_timestamps_real,
        "words": words,
        "segments": segments,
    }
    # Honest language note: forcing "en" does not translate Hindi audio.
    # If the transcript script is not Latin despite an English request,
    # say so plainly — the fix is English source audio, not a fake rewrite.
    script = _detect_script(text)
    out["transcript_script"] = script
    if language_requested in ("en", "hinglish") and script != "latin":
        out["language_note"] = (
            f"Language setting: {language_requested} (English default), but transcript "
            f"came back in {script} script — the source audio is Hindi/Urdu, so Whisper "
            f"heard Hindi words. English captions need English audio; nothing was "
            f"translated or rewritten.")
    elif language_requested in ("en", "hinglish"):
        out["language_note"] = (
            "Transcript in English words (Roman script) as requested.")
    else:
        out["language_note"] = "Transcript in Hindi as requested."
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = TRANSCRIPTS_DIR / f"{upload_id}.json"
    txt_path = TRANSCRIPTS_DIR / f"{upload_id}.txt"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    out["transcript_json"] = str(json_path)
    out["transcript_txt"] = str(txt_path)
    return out


def load_transcript(upload_id: str) -> dict:
    """Load a saved transcript JSON; raises FileNotFoundError if absent."""
    path = TRANSCRIPTS_DIR / f"{upload_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Transcript nahi mila: {upload_id} — pehle POST /transcribe karo")
    return json.loads(path.read_text(encoding="utf-8"))
