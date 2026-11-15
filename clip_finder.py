"""Phase 2: viral clip finding — real implementation.

Free stack only. Scoring is rule-based by default; Gemini FREE API is
used ONLY if GEMINI_API_KEY is set in .env and google-generativeai is
installed. If there is no key, or the API fails, scoring falls back to
the rule-based scorer and the output honestly records
scoring_method="rule_based" — Gemini is NEVER claimed when it was not used.

Pipeline:
  transcript (words + segments) -> candidate 30-60s windows (sliding,
  sentence/segment boundaries, min 25s / max 65s) -> score 0-100 each:
    Hook strength, first 5 sec (question/shock/secret/mistake/money/
    number/guest name) = 30
    Emotion / curiosity = 30
    Complete idea in 60s = 20
    Loop potential (end connects to start / ends on a tease) = 20
  -> dedupe overlapping windows -> TOP 5-8 clips, sorted by score.

Output is saved to outputs/clips/<upload_id>_clips.json.
Words are never invented — hook_line and transcript_text come straight
from the transcript.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CLIPS_DIR = Path(__file__).resolve().parent / "outputs" / "clips"

MIN_CLIP_SEC = 25.0
TARGET_CLIP_SEC = 45.0
MAX_CLIP_SEC = 65.0

# --- Keyword banks ---
# DEFAULT LANGUAGE IS ENGLISH (user correction, Phase 4).
# English banks are PRIMARY: when language == "en" only the English
# banks score (no Hindi/Urdu words can inflate a score). "hinglish"
# adds the Roman (ASCII) Hindi words; "hi" uses all banks as before.
# The old all-language banks are kept below as fallback for "hi".
HOOK_WORDS_EN = {
    "secret", "mistake", "money", "never", "stop", "free", "warning",
    "exposed", "banned", "reveal", "revealed", "truth", "shocking",
    "shock", "danger", "million", "billion", "lakh", "crore",
    "save", "earn", "rich", "fail", "failure", "win", "first", "last",
    "nobody", "everyone", "wrong", "right", "lie", "lies", "scam",
    "how", "why", "what", "when", "where", "who", "which",
    "how to", "do not", "dont", "don't", "must", "always",
}
EMOTION_WORDS_EN = {
    "fear", "happy", "happiness", "angry", "anger", "cry", "crying",
    "laugh", "laughing", "shock", "shocked", "amazing", "unbelievable",
    "incredible", "crazy", "surprised", "surprise", "pain", "dream",
    "dreams", "win", "winning", "lose", "losing", "success", "fail",
    "failure", "struggle", "hard work", "luck", "emotional", "heart",
    "life", "death", "love", "hate", "happy", "sad", "proud",
}
CURIOSITY_WORDS_EN = {
    "secret", "first time", "never before", "nobody knows", "finally",
    "why", "how", "real", "truth", "hidden", "reason", "because",
    "imagine", "what happens", "what if", "did you know",
}
TEASE_END_WORDS_EN = {"and", "but", "then", "next", "part", "wait",
                      "coming", "tomorrow", "stay tuned", "watch"}
QUESTION_WORDS_EN = {"what", "why", "how", "when", "where", "who", "which"}
QUESTION_WORDS_HINGLISH = {"kya", "kaise", "kyun", "kyon", "kab", "kahan", "kaun"}
QUESTION_WORDS_HINDI = {"क्या", "कैसे", "क्यों", "कब", "कहाँ", "कौन",
                         "کیا", "کیسے", "کیوں", "کب", "کہاں", "کون"}


def _roman_words(bank) -> set:
    """ASCII-only words of a bank (Roman Hinglish subset)."""
    return {w for w in bank if w.isascii()}


def _banks_for(language: str) -> tuple:
    """(hook, emotion, curiosity, tease, question_words) for a language."""
    lang = (language or "en").lower()
    if lang == "en":
        return (HOOK_WORDS_EN, EMOTION_WORDS_EN, CURIOSITY_WORDS_EN,
                TEASE_END_WORDS_EN, QUESTION_WORDS_EN)
    if lang == "hinglish":
        return (HOOK_WORDS_EN | _roman_words(HOOK_WORDS),
                EMOTION_WORDS_EN | _roman_words(EMOTION_WORDS),
                CURIOSITY_WORDS_EN | _roman_words(CURIOSITY_WORDS),
                TEASE_END_WORDS_EN | _roman_words(TEASE_END_WORDS),
                QUESTION_WORDS_EN | QUESTION_WORDS_HINGLISH)
    return (HOOK_WORDS, EMOTION_WORDS, CURIOSITY_WORDS,
            TEASE_END_WORDS,
            QUESTION_WORDS_EN | QUESTION_WORDS_HINGLISH | QUESTION_WORDS_HINDI)


# --- Keyword banks (Roman Hinglish + Devanagari + Urdu script + English) --
# Urdu script is included because Whisper base often renders Hindi in
# Perso-Arabic script (words right, script Urdu) — without these, Hindi
# transcripts score near zero on hook/emotion. Measured Nov 2026.
# These banks are now used ONLY when language == "hi".
HOOK_WORDS = {
    "kya", "kaise", "kyun", "kyon", "kab", "kahan", "kaun", "kitna", "kitne",
    "क्या", "कैसे", "क्यों", "कब", "कहाँ", "कौन", "कितना", "कितने",
    "کیا", "کیسے", "کیوں", "کب", "کہاں", "کون", "کتنا",
    "secret", "raaz", "रहस्य", "राज़", "galti", "गलती", "mistake", "paisa",
    "पैसा", "पैसे", "पैसों", "सीक्रेट", "money", "lakh", "लाख", "crore", "करोड़",
    "ہزار", "हजार", "हज़ार", "free", "muft", "मुफ्त",
    "پیسے", "پیسہ", "سیکریٹ", "غلطی", "لاکھ", "کروڑ", "سچ", "راز",
    "pehli", "पहली", "baar", "never", "kabhi", "shock", "shocking", "sach",
    "सच", "jhooth", "झूठ", "danger", "khatra", "खतरा", "banned", "exposed",
    "reveal", "khulasa", "खुलासा", "stop", "ruko", "रुको", "dekho", "देखो",
    "suno", "सुनो", "warning", "chetawani", "चेतावनी", "viral", "trend",
}
EMOTION_WORDS = {
    "darr", "डर", "khushi", "खुशी", "gussa", "गुस्सा", "rona", "रोना",
    "hasna", "हँसना", "shock", "shocked", "amazing", "unbelievable",
    "incredible", "pagal", "पागल", "hairan", "हैरान", "maza", "मज़ा",
    "dard", "दर्द", "sapna", "सपना", "dream", "jeet", "जीत", "haar", "हार",
    "success", "fail", "failure", "struggle", "mehnat", "मेहनत", "kismat",
    "किस्मत", "emotional", "dil", "दिल", "jaan", "जान", "zindagi", "जिंदगी",
    "life", "death", "maut", "मौत", "love", "pyaar", "प्यार", "hate",
    "ڈر", "خوشی", "غصہ", "رونا", "ہنسنا", "پاگل", "حیران", "مزہ",
    "درد", "سپنا", "جیت", "ہار", "محنت", "قسمت", "دل", "جان", "زندگی",
    "موت", "پیار",
}
CURIOSITY_WORDS = {
    "raaz", "secret", "pehli baar", "kabhi nahi", "koi nahi janta", "aakhir",
    "akhir", "kyun", "kaise", "asli", "real", "truth", "sach", "hidden",
    "chhupa", "छुपा", "wajah", "reason", "kyunki", "agar", "socho", "imagine",
    "kya hoga", "kya hota", "dekhiye", "janiye", "जानिए",
}
TEASE_END_WORDS = {"aur", "phir", "lekin", "magar", "aage", "next", "part",
                   "aage kya", "dekhte", "rukiye", "wait", "aakhir me"}
NUMBER_RE = re.compile(r"\d+")
QUESTION_RE = re.compile(r"[?؟]")


def _norm(text: str) -> str:
    return (text or "").lower()


def _first_seconds_text(words: list[dict], start: float, seconds: float = 5.0) -> str:
    return " ".join(w["word"] for w in words
                    if start <= w["start"] < start + seconds)


def _keyword_hits(text: str, keywords) -> int:
    """Count keywords present as substrings.

    Substring matching is deliberate: (a) Hindi inflects (पैसा/पैसे/पैसों,
    सीक्रेट/secret) and Whisper output varies, so exact-token matching
    misses most real hits; (b) Python's \\w splits Devanagari words at
    vowel signs (matras), so regex tokenisation is broken for Hindi —
    "जीत" tokenises as ["ज", "त"]. Substring matching avoids both.
    False positives on short words are acceptable for a 0-100 heuristic.
    """
    t = _norm(text)
    return sum(1 for kw in keywords if kw in t)


def _split_words(text: str) -> list[str]:
    """Whitespace split with punctuation stripped — works for Hindi and
    English (regex \\w breaks Devanagari at matras, so do not use it)."""
    return [w.strip(".,?!।\"'()[]{}:;—–-") for w in (text or "").split() if w.strip(".,?!।\"'()[]{}:;—–-")]


def _score_hook(first_text: str, language: str = "en") -> int:
    """0-30. Question / shock / secret / mistake / money / number in first 5s."""
    hook_words, _, _, _, question_words = _banks_for(language)
    t = _norm(first_text)
    score = 0
    if QUESTION_RE.search(first_text) or "?" in first_text:
        score += 12
    hits = _keyword_hits(first_text, hook_words)
    score += min(12, hits * 4)
    # Whisper rarely emits "?" — a question-word opening counts too.
    first_tokens = _split_words(first_text)[:4]
    if any(w in question_words for w in first_tokens):
        score += 8
    if NUMBER_RE.search(first_text):
        score += 6
    return min(30, score)


def _score_emotion(text: str, language: str = "en") -> int:
    """0-30. Emotion + curiosity words across the whole segment."""
    _, emotion_words, curiosity_words, _, _ = _banks_for(language)
    t = _norm(text)
    emo = _keyword_hits(text, emotion_words)
    cur = sum(1 for phrase in curiosity_words if phrase in t)
    score = min(18, emo * 3) + min(12, cur * 4)
    if "!" in text:
        score += 2
    return min(30, score)


def _score_complete(duration: float, text: str) -> int:
    """0-20. Complete idea: right length + ends on sentence end."""
    score = 0
    if 30 <= duration <= 60:
        score += 12
    elif MIN_CLIP_SEC <= duration < 30 or 60 < duration <= MAX_CLIP_SEC:
        score += 7
    else:
        score += 3
    stripped = (text or "").strip()
    if stripped and stripped[-1] in ".?!।":
        score += 8
    elif stripped and len(stripped.split()) >= 20:
        score += 4
    return min(20, score)


def _score_loop(text: str, language: str = "en") -> int:
    """0-20. End connects to start (repeated word) or ends on a tease."""
    _, _, _, tease_words, _ = _banks_for(language)
    words = _split_words(_norm(text))
    if len(words) < 10:
        return 5
    first_set = set(words[:8])
    last_set = set(words[-8:])
    stop = {"aur", "hai", "hain", "ka", "ki", "ke", "to", "jo",
            "the", "and", "a", "an", "of", "to", "is", "are", "you"}
    overlap = len(first_set & last_set - stop)
    score = min(12, overlap * 4)
    t = _norm(text)
    if any(w in t[-60:] for w in tease_words):
        score += 8
    return min(20, score)


def score_candidate(text: str, first_text: str, duration: float,
                    language: str = "en") -> tuple[int, dict]:
    """Score one candidate; returns (total 0-100, breakdown dict)."""
    hook = _score_hook(first_text, language)
    emo = _score_emotion(text, language)
    comp = _score_complete(duration, text)
    loop = _score_loop(text, language)
    return hook + emo + comp + loop, {
        "hook": hook, "emotion": emo, "complete": comp, "loop": loop}


def build_candidates(transcript: dict, language: str = "en") -> list[dict]:
    """Sliding windows over transcript segments: 25-65s each.

    Windows start at every segment boundary and extend until >=30s
    (TARGET 45s), closing at a segment end, never past 65s. Short
    videos (<25s of speech) yield one window covering all speech.
    """
    words = transcript.get("words") or []
    segments = transcript.get("segments") or []
    if not segments and words:
        # No segments: synthesise one segment per ~10s of words.
        segments = []
        cur = [words[0]]
        for w in words[1:]:
            if w["start"] - cur[0]["start"] >= 10:
                segments.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                                 "text": " ".join(x["word"] for x in cur)})
                cur = [w]
            else:
                cur.append(w)
        if cur:
            segments.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                             "text": " ".join(x["word"] for x in cur)})
    if not segments:
        return []

    total_end = segments[-1]["end"]
    candidates = []
    for i, seg in enumerate(segments):
        start = float(seg["start"])
        text_parts, end = [], start
        for j in range(i, len(segments)):
            s = segments[j]
            if float(s["start"]) - start > MAX_CLIP_SEC:
                break
            text_parts.append(str(s.get("text") or "").strip())
            end = float(s["end"])
            if end - start >= 30 and j > i:
                # Close at a sentence/segment boundary once >=30s.
                if end - start >= TARGET_CLIP_SEC or j == len(segments) - 1:
                    break
        duration = end - start
        if duration < 5:
            continue
        text = " ".join(p for p in text_parts if p)
        if not text:
            continue
        # Keep windows that are long enough, or the single full-speech
        # window when the whole video is short.
        if duration < MIN_CLIP_SEC and len(segments) > 1:
            continue
        first_text = _first_seconds_text(words, start) if words else text[:80]
        score, breakdown = score_candidate(text, first_text, duration, language)
        candidates.append({
            "start": round(start, 2), "end": round(end, 2),
            "duration": round(duration, 2), "score": score,
            "breakdown": breakdown, "text": text,
            "first_text": first_text,
        })
    # If greedy windows produced nothing (very short video), one window.
    if not candidates and segments:
        text = " ".join(str(s.get("text") or "") for s in segments).strip()
        start, end = float(segments[0]["start"]), float(segments[-1]["end"])
        first_text = _first_seconds_text(words, start) if words else text[:80]
        score, breakdown = score_candidate(text, first_text, end - start, language)
        candidates.append({"start": round(start, 2), "end": round(end, 2),
                           "duration": round(end - start, 2), "score": score,
                           "breakdown": breakdown, "text": text,
                           "first_text": first_text})
    return candidates


def _hook_line(text: str) -> str:
    """First COMPLETE sentence (or first ~12 words) — the on-screen hook.

    Phase 5 fix: candidate windows can start mid-sentence, so the old
    "first sentence" was often a fragment (e.g. just "help"). Now pick
    the first sentence that actually ends with . ? ! or । AND has at
    least 4 words. If no complete sentence exists, take the first
    12 words (never a 1-word hook unless the whole text is 1 word).
    """
    text = (text or "").strip()
    if not text:
        return ""
    # Complete sentences: run of non-terminator chars + a terminator.
    for m in re.finditer(r"[^.!?\u0964\u06d4]+[.!?\u0964\u06d4]", text):
        sent = m.group(0).strip()
        if len(sent.split()) >= 4:
            words = sent.split()
            if len(words) > 14:
                sent = " ".join(words[:14]) + "…"
            return sent
    words = text.split()
    if len(words) > 14:
        return " ".join(words[:14]) + "…"
    return text


def _reason(breakdown: dict, score: int, language: str = "en") -> str:
    if (language or "en").lower() == "en":
        parts = []
        if breakdown["hook"] >= 18:
            parts.append("strong hook (question/secret/number in first 5 sec)")
        elif breakdown["hook"] >= 10:
            parts.append("decent hook")
        if breakdown["emotion"] >= 18:
            parts.append("high emotion/curiosity")
        elif breakdown["emotion"] >= 10:
            parts.append("some emotion/curiosity")
        if breakdown["complete"] >= 15:
            parts.append("complete idea, right length")
        if breakdown["loop"] >= 12:
            parts.append("loop/tease ending")
        if not parts:
            parts.append("average segment")
        return f"Score {score}/100 — " + ", ".join(parts)
    parts = []
    if breakdown["hook"] >= 18:
        parts.append("strong hook (sawaal/secret/number pehle 5 sec me)")
    elif breakdown["hook"] >= 10:
        parts.append("decent hook")
    if breakdown["emotion"] >= 18:
        parts.append("high emotion/curiosity")
    elif breakdown["emotion"] >= 10:
        parts.append("some emotion/curiosity")
    if breakdown["complete"] >= 15:
        parts.append("complete idea, sahi length")
    if breakdown["loop"] >= 12:
        parts.append("loop/tease ending")
    if not parts:
        parts.append("average segment")
    return f"Score {score}/100 — " + ", ".join(parts)


def _dedupe_rank(candidates: list[dict], top_n: int) -> list[dict]:
    """Rank by score, suppress windows overlapping >40% with a picked one."""
    ranked = sorted(candidates, key=lambda c: (-c["score"], c["start"]))
    picked: list[dict] = []
    for cand in ranked:
        overlap = False
        for p in picked:
            inter = max(0.0, min(cand["end"], p["end"]) - max(cand["start"], p["start"]))
            shorter = min(cand["duration"], p["duration"])
            if shorter > 0 and inter / shorter > 0.4:
                overlap = True
                break
        if not overlap:
            picked.append(cand)
        if len(picked) >= top_n:
            break
    return picked


def _gemini_api_key() -> str:
    """Read GEMINI_API_KEY from env or .env (never log the key)."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _gemini_rescore(candidates: list[dict]) -> list[dict] | None:
    """Ask Gemini FREE API to re-score/rank candidates.

    Returns the rescored candidate list, or None on any failure
    (no key, no library, API error) — caller falls back to rule-based.
    """
    key = _gemini_api_key()
    if not key:
        return None
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        return None
    try:
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        brief = "\n".join(
            f"[{i}] {c['start']:.0f}-{c['end']:.0f}s | {c['text'][:300]}"
            for i, c in enumerate(candidates))
        prompt = (
            "You rank short video clips for Instagram Reels virality. "
            "Score each candidate 0-100 (hook in first 5 sec = 30, emotion/curiosity = 30, "
            "complete idea = 20, loop potential = 20). Reply ONLY as a JSON array, "
            "no other text, no markdown fences, each item: "
            '{"id": <index>, "score": <0-100>, "reason": "<short, in Hinglish>", '
            '"hook_line": "<first striking line, copied from the text>"}.\n\n'
            f"Candidates:\n{brief}")
        resp = model.generate_content(prompt)
        raw = (getattr(resp, "text", "") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        by_id = {int(d["id"]): d for d in data if isinstance(d, dict) and "id" in d}
        out = []
        for i, cand in enumerate(candidates):
            if i in by_id:
                d = by_id[i]
                cand = dict(cand)
                cand["score"] = max(0, min(100, int(d.get("score", cand["score"]))))
                cand["gemini_reason"] = str(d.get("reason", ""))[:200]
                cand["gemini_hook"] = str(d.get("hook_line", ""))[:160]
            out.append(cand)
        return out
    except Exception:
        return None


def find_clips(transcript: dict, upload_id: str, top_n: int = 5,
               style_name: str | None = None, use_gemini: bool = True,
               language: str | None = None) -> dict:
    """Find top viral clips from a transcript dict.

    Returns {"upload_id", "scoring_method", "scoring_language",
    "style_name", "clips": [...]}
    and saves outputs/clips/<upload_id>_clips.json.
    scoring_method is "gemini" only when Gemini actually rescored,
    otherwise "rule_based".
    Language: "en" by DEFAULT (English keyword banks are primary).
    Falls back to the transcript's language_requested, then "en".
    """
    top_n = max(1, min(8, int(top_n or 5)))
    lang = (language or transcript.get("language_requested") or "en").lower()
    if lang not in ("en", "hinglish", "hi"):
        lang = "en"
    candidates = build_candidates(transcript, lang)
    scoring_method = "rule_based"
    if use_gemini and candidates:
        rescored = _gemini_rescore(candidates)
        if rescored is not None:
            candidates = rescored
            scoring_method = "gemini"
    picked = _dedupe_rank(candidates, top_n)
    clips = []
    for idx, cand in enumerate(picked, start=1):
        hook = cand.get("gemini_hook") or _hook_line(cand["text"])
        reason = cand.get("gemini_reason") or _reason(cand["breakdown"], cand["score"], lang)
        clips.append({
            "clip_id": f"clip_{idx:02d}",
            "start": cand["start"], "end": cand["end"],
            "duration": cand["duration"],
            "score": int(cand["score"]),
            "reason": reason,
            "hook_line": hook,
            "transcript_text": cand["text"],
            "scoring_method": scoring_method,
            "scoring_language": lang,
            "breakdown": cand.get("breakdown"),
        })
    out = {
        "upload_id": upload_id,
        "scoring_method": scoring_method,
        "scoring_language": lang,
        "style_name": style_name,
        "candidates_count": len(candidates),
        "clips": clips,
    }
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    (CLIPS_DIR / f"{upload_id}_clips.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def load_clips(upload_id: str) -> dict:
    """Load saved clips JSON; raises FileNotFoundError if absent.

    Phase 5 backfill: clips files written before Phase 4 have no
    scoring_language — default it to "en" (and per-clip too) instead
    of crashing, and persist the backfill to disk.
    """
    path = CLIPS_DIR / f"{upload_id}_clips.json"
    if not path.is_file():
        raise FileNotFoundError(f"Clips nahi mile: {upload_id} — pehle POST /find-clips karo")
    data = json.loads(path.read_text(encoding="utf-8"))
    lang = data.get("scoring_language") or "en"
    changed = False
    if data.get("scoring_language") != lang:
        data["scoring_language"] = lang
        changed = True
    for clip in data.get("clips", []):
        if clip.get("scoring_language") != lang:
            clip["scoring_language"] = lang
            changed = True
        if "scoring_method" not in clip:
            clip["scoring_method"] = data.get("scoring_method", "rule_based")
            changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data
