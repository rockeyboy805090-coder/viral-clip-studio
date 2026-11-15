"""Phase 2: local Whisper transcription — real implementation.

Free stack only: FFmpeg + faster-whisper (CTranslate2, NO torch).
No paid API, no network call for transcription itself (except the
one-time model download from HuggingFace on first use).

WHY faster-whisper (Phase 6 fix, 15 Nov 2026): openai-whisper + torch
needs ~2 GB RAM and CRASHED the Render Free instance (512 MB) on
POST /transcribe — the service restarted and the uploaded project was
lost (ephemeral storage). faster-whisper runs the same Whisper models
via CTranslate2 in int8 on CPU: ~150 MB model, a few hundred MB RAM,
no torch, no CUDA. Same model names (tiny/base/small), same word
timestamps, same output format.

Behaviour:
- Extract audio from the video with FFmpeg to 16 kHz mono WAV in
  a temp file (deleted after transcription).
- Load faster-whisper model "base" by default. Env override:
  WHISPER_MODEL=tiny (fast smoke test) or small/medium (better still).
  Model cache: ~/.cache/huggingface (faster-whisper default).
- If faster-whisper is NOT installed, raise RuntimeError with the
  clear message "faster-whisper install karo: pip install
  faster-whisper" — never fake a transcript.
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

import gc
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

# faster-whisper model names match openai-whisper names (tiny/base/
# small/medium/large-v3, plus .en variants). Anything else is passed
# through and faster-whisper raises a clear error for unknown names.
MODEL_ALIASES = {
    "tiny.en": "tiny.en",
    "base.en": "base.en",
    "small.en": "small.en",
    "medium.en": "medium.en",
}


def normalize_model(model_name: str | None) -> str:
    """Map WHISPER_MODEL env / argument to a faster-whisper model name."""
    name = (model_name or os.getenv("WHISPER_MODEL", "base") or "base").strip().lower()
    return MODEL_ALIASES.get(name, name)


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
    """Import faster-whisper lazily and load the model (CPU, int8).

    Lazy import so `import transcriber` works even when faster-whisper
    is not installed — the error only appears when transcription is
    attempted. int8 keeps RAM low enough for a 512 MB container.
    First call downloads the model (~150 MB for base) from
    HuggingFace into ~/.cache/huggingface.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper install karo: pip install faster-whisper"
        ) from exc
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _read_wav_mono16(wav_path: Path):
    """Read our FFmpeg-extracted 16 kHz mono PCM16 WAV as float32 samples.

    faster-whisper's own audio decoder (PyAV) breaks on newer av
    releases (`av.open() got an unexpected keyword argument
    'metadata_errors'` with av>=14) — so we decode the WAV ourselves
    with stdlib `wave` and hand faster-whisper a numpy array, which
    skips its av path entirely. The WAV is always 16 kHz mono PCM16
    because extract_audio() makes it so (asserted here).
    """
    import wave

    import numpy as np  # noqa: F401  (present via faster-whisper deps)

    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
            raise RuntimeError(
                "Audio WAV format unexpected (16 kHz mono PCM16 chahiye) — "
                f"channels={wf.getnchannels()}, width={wf.getsampwidth()}, "
                f"rate={wf.getframerate()}")
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(video_path, upload_id: str, model_name: str | None = None,
               initial_prompt: str | None = None,
               language: str | None = None) -> dict:
    """Transcribe a video file with local faster-whisper.

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

    Returns dict with keys: upload_id, model, engine, language
    (detected), language_requested, language_note, duration_sec, text,
    word_count, words [{word, start, end}], segments
    [{start, end, text}], transcript_json, transcript_txt.

    Side effects: writes outputs/transcripts/<upload_id>.json and .txt.
    The temp WAV is deleted after transcription.
    """
    video_path = Path(video_path)
    probe = probe_video(video_path)
    if not probe["valid"]:
        raise ValueError(f"Ye valid video file nahi hai: {probe['error']}")
    if not probe["has_audio"]:
        raise ValueError("Is video me audio nahi hai — transcript nahi ban sakta")
    model_name = normalize_model(model_name)
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
    model = None
    try:
        extract_audio(video_path, wav_path)
        model = _load_model(model_name)
        # Decode the WAV ourselves (see _read_wav_mono16) and pass
        # float32 samples — faster-whisper's PyAV decoder breaks on
        # newer av releases. word_timestamps=True gives word timings.
        audio = _read_wav_mono16(wav_path)
        segments_gen, info = model.transcribe(
            audio, language=whisper_lang,
            initial_prompt=initial_prompt, word_timestamps=True)
        # Consume the generator fully while the WAV still exists.
        segments: list[dict] = []
        words: list[dict] = []
        for seg in segments_gen:
            segments.append({
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": str(seg.text or "").strip(),
            })
            for w in (seg.words or []):
                word = str(w.word or "").strip()
                if not word:
                    continue
                words.append({
                    "word": word,
                    "start": round(float(w.start), 2),
                    "end": round(float(w.end), 2),
                })
        detected_language = str(getattr(info, "language", None) or "unknown")
        text = " ".join(s["text"] for s in segments).strip()
    finally:
        wav_path.unlink(missing_ok=True)
        # Free the model NOW (Phase 6 fix, 15 Nov 2026): the
        # CTranslate2 model holds ~150 MB + inference arenas. On a
        # 512 MB Render instance the next step (render = FFmpeg child
        # + opencv) OOM-killed the container when this memory was
        # still held. del + gc.collect() drops RSS back near baseline
        # before the caller renders.
        if model is not None:
            del model
            gc.collect()
            # Return freed arenas to the OS (Linux/glibc only,
            # best-effort): without this, CPython keeps the model's
            # freed blocks in-process and RSS barely drops.
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass

    # Fallback: if word timestamps came back empty, rebuild words from
    # segments by splitting on spaces — timestamps are then
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
        "engine": "faster-whisper",
        "language": detected_language,
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
