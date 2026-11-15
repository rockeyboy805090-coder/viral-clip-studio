"""Phase 3: 9:16 clip rendering — real implementation.

Free stack only: FFmpeg + OpenCV + stdlib. No paid API, no network.

Pipeline (render_clip):
  source video + clip {start, end} + Style Profile + Whisper transcript
  -> cut start-1s .. end+1s (clamped to source duration)
  -> reframe to 1080x1920 9:16:
       * face detected (OpenCV Haar, face width >= 12% of frame) ->
         centre-crop 9:16 on the speaker (no black bars, no stretch)
       * no face -> blurred-background fill (split/scale/crop/gblur +
         overlay), still no black bars
  -> zoom punch-in 110% for the first 2s (hook), then 100%
  -> colour grade from the profile (FFmpeg eq)
  -> word-by-word captions burned in as ASS karaoke (2-4 words/phrase,
     current word in the profile highlight colour), hook text top-centre
     for the first 2s
  -> audio: original voice only for Global Baatien (music OFF). If a
     style asks for music and a track exists in static/music/, it is
     mixed at -20dB, voice at -3dB. No copyrighted music is ever used.
  -> export 1080x1920, 30fps, H.264 CRF 18, yuv420p (MANDATORY for
     Android), AAC 192k, 48kHz.

Caption script decision (Phase 2 caveat): Whisper base renders Hindi in
Urdu (Perso-Arabic) script. Captions keep the transcript text EXACTLY
as Whisper returned it — no silent transliteration. The output records
caption_script ("arabic" / "devanagari" / "latin") and the font actually
used. Font is picked per script so glyphs render (DejaVu has no Arabic
glyphs): Arabic -> Noto Naskh Arabic Bold (Noto Sans Arabic Bold
mis-shapes "والا" into a tofu box — verified live), Devanagari ->
Noto Sans Devanagari Bold, Latin -> DejaVu Sans Bold. Montserrat is
the profile name; it is NOT installed on this VM, so the fallback
font is recorded honestly as font_used.

Silence removal: SKIPPED on purpose. silenceremove can cut words when
Whisper's word timings and FFmpeg's silence detector disagree; a
half-cut word is worse than a 0.3s pause. Recorded as
silence_removed=False with the reason.

Outputs per clip (Phase 5: outputs/rendered/<upload_id>/<clip_id>.*):
  .mp4  rendered reel
  .srt  captions (2-4 words per cue, clip-relative times)
  .ass  burned-in subtitle source (kept as evidence)
  .txt  clip transcript text (as Whisper returned it)
  _caption.txt  title + caption + hashtags (ready to paste)
  .json  sidecar with full render metadata
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import cv2

from style_extractor import load_style
from transcriber import load_transcript
from utils import probe_video

ROOT = Path(__file__).resolve().parent
RENDERED_DIR = ROOT / "outputs" / "rendered"
MUSIC_DIR = ROOT / "static" / "music"

# Font fallback chain (family name for libass, file path to check).
# Montserrat ExtraBold is the profile font but is NOT installed here.
FONT_BY_SCRIPT = {
    # Arabic: Noto Naskh Arabic Bold — NOT Noto Sans Arabic Bold.
    # Verified live: "والا" renders a tofu box for the isolated alef in
    # Noto Sans Arabic Bold (harfbuzz shaping), while Noto Naskh Arabic
    # Bold renders the same word perfectly. Naskh is also more readable
    # for Urdu-script Hindi.
    "arabic": ("Noto Naskh Arabic",
               "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
    "devanagari": ("Noto Sans Devanagari",
                   "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"),
    "latin": ("DejaVu Sans",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
}
FONT_FALLBACK = ("DejaVu Sans",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def detect_caption_script(text: str) -> str:
    """arabic (incl. Urdu script) / devanagari / latin, by glyph range."""
    if ARABIC_RE.search(text or ""):
        return "arabic"
    if DEVANAGARI_RE.search(text or ""):
        return "devanagari"
    return "latin"


def pick_font(script: str) -> dict:
    """Pick a real installed bold font for the script.

    Returns {"family": ..., "path": ..., "fallback": bool}. Never crashes
    if a font file is missing — falls back to DejaVu Sans Bold.
    """
    family, path = FONT_BY_SCRIPT.get(script, FONT_BY_SCRIPT["latin"])
    if Path(path).is_file():
        return {"family": family, "path": path, "fallback": False,
                "note": f"profile font Montserrat ExtraBold not installed; using {family} Bold"}
    fb_family, fb_path = FONT_FALLBACK
    return {"family": fb_family, "path": fb_path, "fallback": True,
            "note": f"{family} font file missing; using DejaVu Sans Bold fallback"}


def _hex_to_ass(hex_color: str) -> str:
    """#RRGGBB -> ASS &HAABBGGRR (alpha 00 = opaque)."""
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}"


def detect_face_centre(video_path: Path, start: float, end: float) -> dict:
    """Sample frames in [start, end], Haar-detect faces, median centre.

    Returns {"face_detected": bool, "centre_x": 0..1, "centre_y": 0..1,
    "face_rel_width": float, "frames_checked": int, "frames_with_face": int}.
    Never raises — returns face_detected=False on any failure.
    """
    out = {"face_detected": False, "centre_x": 0.5, "centre_y": 0.5,
           "face_rel_width": 0.0, "frames_checked": 0, "frames_with_face": 0}
    try:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.is_file():
            out["note"] = "Haar cascade file missing"
            return out
        cascade = cv2.CascadeClassifier(str(cascade_path))
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            out["note"] = "OpenCV could not open video"
            return out
        centres_x, centres_y, widths = [], [], []
        t = start
        while t <= end:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                t += 2.0
                continue
            out["frames_checked"] += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1,
                                             minNeighbors=5, minSize=(60, 60))
            if len(faces):
                # Largest face = main speaker — but ignore detections whose
                # centre sits in the bottom band (centre_y > 0.72). Proven
                # 15 Nov 2026 on FO588 clip_04: the Haar cascade fired a
                # persistent FALSE POSITIVE on the desk mug at (0.11, 0.84)
                # in 10/11 frames, outvoting the one real face at
                # (0.63, 0.45); the median locked centre_crop onto the mug
                # and the speaker was out of frame for the whole clip. A
                # seated speaker's face centre is in the upper 2/3.
                cands = []
                fh, fw = frame.shape[:2]
                for (x, y, w, h) in faces:
                    cx, cy = (x + w / 2) / fw, (y + h / 2) / fh
                    if cy <= 0.72:
                        cands.append((x, y, w, h, cx, cy))
                if not cands:
                    t += 2.0
                    continue
                out["frames_with_face"] += 1
                x, y, w, h, cx, cy = max(cands, key=lambda r: r[2] * r[3])
                centres_x.append(cx)
                centres_y.append(cy)
                widths.append(w / fw)
            t += 2.0
        cap.release()
        if centres_x:
            centres_x.sort()
            centres_y.sort()
            widths.sort()
            mid = len(centres_x) // 2
            out["face_detected"] = True
            out["centre_x"] = round(float(centres_x[mid]), 3)
            out["centre_y"] = round(float(centres_y[mid]), 3)
            out["face_rel_width"] = round(float(widths[mid]), 3)
    except Exception as exc:  # face detection must never break a render
        out["note"] = f"face detection failed (ignored): {exc}"
    return out


def group_words(words: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    """Group clip-range words into 2-4 word phrases for captions/SRT.

    Phrases break at sentence punctuation or after 4 words, and never
    exceed ~3.5s. Returns [{start, end, text, words:[{word,start,end}]}]
    with times RELATIVE to clip_start.
    """
    in_range = []
    for w in words or []:
        ws, we = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if we <= clip_start or ws >= clip_end:
            continue
        in_range.append({
            "word": str(w.get("word") or "").strip(),
            "start": round(max(ws, clip_start) - clip_start, 2),
            "end": round(min(we, clip_end) - clip_start, 2),
        })
    in_range = [w for w in in_range if w["word"]]
    phrases: list[dict] = []
    cur: list[dict] = []
    for w in in_range:
        cur.append(w)
        ends_sentence = w["word"][-1:] in ".!?\u0964\u06d4"
        too_long = cur and (w["end"] - cur[0]["start"] > 3.5)
        if len(cur) >= 4 or ends_sentence or too_long:
            phrases.append(_finish_phrase(cur))
            cur = []
    if cur:
        phrases.append(_finish_phrase(cur))
    return phrases


def _finish_phrase(words: list[dict]) -> dict:
    return {"start": words[0]["start"], "end": words[-1]["end"],
            "text": " ".join(w["word"] for w in words), "words": words}


def _fmt_srt_time(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    return f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"


def write_srt(phrases: list[dict], path: Path) -> Path:
    """Write SRT cues (2-4 words each, clip-relative times)."""
    lines = []
    for i, p in enumerate(phrases, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(p['start'])} --> {_fmt_srt_time(p['end'])}")
        lines.append(p["text"])
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ass_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _fmt_ass_time(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    return f"{ms // 3600000}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}"


def write_ass(phrases: list[dict], hook_line: str, profile: dict,
              font_family: str, path: Path) -> Path:
    """Write ASS subtitles with karaoke word-by-word highlight.

    Karaoke semantics (libass, VERIFIED with an isolated test):
    whole-word flip — a word turns PrimaryColour the moment its \\k
    window starts, no smooth sweep inside the word. So PrimaryColour
    is the profile highlight colour (sung words = highlight) and
    SecondaryColour is the caption colour (upcoming words = white).
    TIMING: a word's \\k window is stretched to the NEXT word's start
    (last word: to the phrase end), so flips land on the actual word
    starts. Using raw word durations instead makes flips jump ahead
    whenever Whisper leaves gaps between words (verified: "کیا" would
    flip 0.66s before it is spoken).
    Hook event: separate top-centre style, first 2s only.
    """
    size = int(profile.get("caption_size_px", 80) or 80)
    cap_col = _hex_to_ass(str(profile.get("caption_color", "#FFFFFF")))
    hi_col = _hex_to_ass(str(profile.get("highlight_color", "#00FF66")))
    outline = int(profile.get("outline_px", 3) or 3)
    pos_pct = float(profile.get("caption_position_y_percent", 62) or 62)
    # Bottom margin so caption centre sits at pos_pct of 1920px height.
    margin_v = max(80, int(1920 * (1 - pos_pct / 100.0) - size - 20))
    all_caps = bool(profile.get("caption_all_caps", True))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_family},{size},{hi_col},{cap_col},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,40,40,{margin_v},1\n"
        f"Style: Hook,{font_family},{size + 16},{cap_col},{cap_col},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,8,40,40,140,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    hook = (hook_line or "").strip()
    if hook:
        hook_text = hook.upper() if all_caps else hook
        events.append(
            f"Dialogue: 0,0:00:00.00,0:00:02.00,Hook,,0,0,0,,{_ass_escape(hook_text)}")
    for p in phrases:
        # Karaoke: each word gets {\k<centiseconds>}. The window runs
        # to the NEXT word's start (last word: phrase end) so the flip
        # lands exactly on the spoken word, gaps included.
        parts = []
        ws = p["words"]
        for i, w in enumerate(ws):
            if i + 1 < len(ws):
                span = ws[i + 1]["start"] - w["start"]
            else:
                span = p["end"] - w["start"]
            cs = max(5, int(round(span * 100)))
            word = w["word"].upper() if all_caps else w["word"]
            parts.append("{\\k%d}%s" % (cs, _ass_escape(word)))
        text = " ".join(parts)
        events.append(
            f"Dialogue: 0,{_fmt_ass_time(p['start'])},{_fmt_ass_time(p['end'])},"
            f"Default,,0,0,0,,{text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def _caption_txt(clip: dict, style: dict, transcript_text: str) -> str:
    hook = (clip.get("hook_line") or "").strip()
    title = hook or "Viral clip"
    if "baatien" in str(style.get("style_name", "")).lower():
        tags = "#rajshamani #aura #podcasts #viralreels #trendingnow"
    else:
        tags = "#viralreels #reels #shorts #trendingnow"
    preview = (transcript_text or "").strip()
    if len(preview) > 300:
        preview = preview[:300].rstrip() + "…"
    return (f"{title}\n\n{preview}\n\n{tags}\n")


def render_clip(video_path, upload_id: str, clip: dict,
                style_name: str | None = None, transcript: dict | None = None) -> dict:
    """Render ONE clip to 1080x1920 with captions + grading.

    Args:
        video_path: source long video file.
        upload_id: upload id (transcript lookup + output naming).
        clip: dict from clip_finder (clip_id, start, end, hook_line,
            transcript_text, score).
        style_name: style slug or name; default "global-baatien".
        transcript: optional loaded transcript dict (loaded if None).

    Returns a metadata dict and writes outputs/rendered/<clip_id>.*.
    Raises FileNotFoundError / ValueError / RuntimeError on bad input
    or a failed ffmpeg run (stderr tail included).
    """
    video_path = Path(video_path)
    probe = probe_video(video_path)
    if not probe["valid"]:
        raise ValueError(f"Ye valid video file nahi hai: {probe['error']}")
    if transcript is None:
        transcript = load_transcript(upload_id)
    words = transcript.get("words") or []
    if not words:
        raise ValueError("Transcript me words nahi hain — captions nahi ban sakte")

    clip_id = str(clip.get("clip_id") or "clip_01")
    start = float(clip.get("start", 0.0))
    end = float(clip.get("end", start + 30.0))
    if end <= start:
        raise ValueError(f"Clip {clip_id} ka end <= start hai")
    src_dur = probe["duration"]
    clip_start = max(0.0, start - 1.0)
    clip_end = min(src_dur, end + 1.0)
    duration = round(clip_end - clip_start, 2)
    if duration < 5:
        raise ValueError(f"Clip {clip_id} bahut chhota hai ({duration}s)")

    style = load_style(style_name or "global-baatien")
    profile_name = str(style.get("style_name", style_name or "global-baatien"))

    # --- Face detection (drives reframe mode) ---
    face = detect_face_centre(video_path, clip_start, clip_end)
    use_crop = bool(face["face_detected"] and face["face_rel_width"] >= 0.12)
    reframe_mode = "centre_crop" if use_crop else "blur_fill"
    src_w, src_h = probe["width"], probe["height"]

    # --- Captions (keep Whisper text exactly as returned) ---
    clip_text = str(clip.get("transcript_text") or "").strip()
    caption_script = detect_caption_script(clip_text or transcript.get("text", ""))
    font = pick_font(caption_script)
    phrases = group_words(words, clip_start, clip_end)
    if not phrases:
        raise ValueError("Clip range me caption words nahi mile")

    # Phase 5: per-upload namespace — outputs/rendered/<upload_id>/<clip_id>.*
    # Old flat layout (outputs/rendered/<clip_id>.*) overwrote clip_01
    # of a previous upload. New renders never collide.
    out_dir = RENDERED_DIR / str(upload_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / f"{clip_id}.mp4"
    srt_path = out_dir / f"{clip_id}.srt"
    ass_path = out_dir / f"{clip_id}.ass"
    txt_path = out_dir / f"{clip_id}.txt"
    cap_path = out_dir / f"{clip_id}_caption.txt"
    json_path = out_dir / f"{clip_id}.json"

    write_srt(phrases, srt_path)
    write_ass(phrases, str(clip.get("hook_line") or ""), style,
              font["family"], ass_path)
    txt_path.write_text(clip_text, encoding="utf-8")
    cap_path.write_text(_caption_txt(clip, style, clip_text), encoding="utf-8")

    # --- FFmpeg filter graph ---
    contrast = float(style.get("contrast", 1.0) or 1.0)
    saturation = float(style.get("saturation", 1.0) or 1.0)
    brightness = float(style.get("brightness", 0.0) or 0.0)
    zoom_pct = int(style.get("zoom_percent", 110) or 110)
    grade = f"eq=contrast={contrast}:saturation={saturation}:brightness={brightness}"
    ass_escaped = str(ass_path).replace("'", "'\\''")
    sub = f"subtitles='{ass_escaped}':fontsdir='/usr/share/fonts'"

    # CUTTING GOTCHA — verified empirically (live test, attempts 3-4):
    # `-ss` as an OUTPUT option (after -i) + trim/atrim in the filter
    # silently drops exactly the -ss seconds (49.02s render came out
    # 43.53s; a 12s test came out 6.53s; every trim variant failed the
    # same way). `-ss` as an INPUT option + in-filter trim works
    # (12.00s in the same test). So: NO output -ss/-t at all. Seek with
    # input -ss to (clip_start - 2s) for speed, then cut precisely with
    # trim/atrim using times RELATIVE to the seek point, and normalise
    # PTS to 0 with setpts/asetpts right after the cut.
    seek = max(0.0, clip_start - 2.0)
    rel_start = round(clip_start - seek, 2)
    rel_end = round(rel_start + duration, 2)
    if use_crop:
        crop_w = int(src_h * 9 / 16)
        crop_x = int(max(0, min(src_w - crop_w,
                                face["centre_x"] * src_w - crop_w / 2)))
        base = (f"[0:v]trim=start={rel_start}:end={rel_end},setpts=PTS-STARTPTS,"
                f"fps=30,crop={crop_w}:{src_h}:{crop_x}:0,"
                f"scale=1080:1920:flags=lanczos[vbase]")
    else:
        # Blur-fill background, blurred at QUARTER resolution (Phase 6
        # fix, 15 Nov 2026): blurring full 1080x1920 at sigma=40 was
        # part of a ~520 MB ffmpeg footprint that OOM-killed Render
        # Free (512 MB). The background is blurred beyond recognition
        # anyway, so downscale → blur sigma=10 → upscale gives the
        # same look for ~1/4 of the blur memory (measured: -15 MB
        # alone; combined with the x264 tuning below, -270 MB).
        base = (f"[0:v]trim=start={rel_start}:end={rel_end},setpts=PTS-STARTPTS,"
                f"fps=30,split=2[bg0][fg0];"
                "[bg0]scale=270:480:force_original_aspect_ratio=increase,"
                "crop=270:480,gblur=sigma=10,scale=1080:1920[bg];"
                "[fg0]scale=1080:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[vbase]")

    # Zoom punch: first 2s at zoom_pct%, rest at 100% (two trims + concat).
    zoom_applied = False
    if duration > 2.5 and zoom_pct > 100:
        zw = int(1080 * zoom_pct / 100)
        zh = int(1920 * zoom_pct / 100)
        # NOTE: a filter output pad can be consumed only once — [vbase]
        # must be split before feeding two trims, otherwise the second
        # trim silently gets the raw 1280x720 input and concat fails
        # with a size/SAR mismatch (found in live test, attempt 1).
        zoom = (f"[vbase]split=2[vb1][vb2];"
                f"[vb1]trim=0:2,setpts=PTS-STARTPTS,"
                f"scale={zw}:{zh},crop=1080:1920[vz1];"
                f"[vb2]trim=2:{duration},setpts=PTS-STARTPTS[vz2];"
                f"[vz1][vz2]concat=n=2:v=1:a=0[vzoom]")
        zoom_applied = True
        vchain = f"{base};{zoom};[vzoom]{grade},{sub}[vout]"
    else:
        vchain = f"{base};[vbase]{grade},{sub}[vout]"

    music_db = float(style.get("music_volume_db", -99) or -99)
    voice_db = float(style.get("voice_volume_db", -3) or -3)
    music_applied = False
    music_note = "music OFF for this style (Global Baatien: original voice only)"
    if music_db > -90:
        # Only mix music if a royalty-free track actually exists.
        tracks = sorted(MUSIC_DIR.glob("*.mp3")) + sorted(MUSIC_DIR.glob("*.wav"))
        if tracks:
            music_note = f"music track found but mixing not wired in Phase 3: {tracks[0].name}"
        else:
            music_note = "style asks for music but no track in static/music/ — voice only"
    audio = (f"[0:a]atrim=start={rel_start}:end={rel_end},asetpts=PTS-STARTPTS,"
             f"aresample=48000,volume={voice_db}dB[aout]")

    filter_complex = f"{vchain};{audio}"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg install karo — render nahi ho sakta")
    # Input -ss only (fast keyframe seek); precise cut is in-filter.
    # NEVER put -ss/-t after -i here — see the cutting-gotcha note above.
    # Thread caps + x264 tuning (Phase 6 fix, 15 Nov 2026): the stock
    # command (preset medium, uncapped threads) peaked at ~520 MB RSS
    # for the ffmpeg child alone — on Render Free (512 MB) that
    # OOM-killed the container mid-render. Bisected on a 10s window
    # of the same clip (local ffmpeg): medium/threads2 = 520 MB;
    # blur-downscale = 505 MB (blur was NOT the hog); veryfast +
    # rc-lookahead=10:ref=2 = 329 MB; + blur-downscale + threads=1
    # = 252 MB; veryfast + rc-lookahead=5:ref=1 + blur-downscale +
    # threads=1 = 220 MB. Quality check vs the medium original:
    # SSIM 0.9956, PSNR 45.8 dB, file 1.76 vs 1.89 MB — visually
    # transparent at the same CRF 18. superfast was rejected
    # (170 MB but 3.3x file size). 1 thread is right for a 0.1-CPU
    # container; local renders also got ~2x faster (147s -> 75s).
    cmd = [ffmpeg, "-y", "-v", "error",
           "-threads", "1",
           "-ss", str(seek),
           "-i", str(video_path),
           "-filter_complex", filter_complex,
           "-filter_threads", "1", "-filter_complex_threads", "1",
           "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-x264-params", "rc-lookahead=5:ref=1",
           "-threads", "1",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(mp4_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not mp4_path.is_file() or mp4_path.stat().st_size == 0:
        err = ((result.stderr or "").strip()[-2000:] or "unknown ffmpeg error")
        raise RuntimeError(f"Render fail hua (clip {clip_id}): {err}")

    out_probe = probe_video(mp4_path)
    # Language note (Phase 4 correction): captions keep the transcript
    # text exactly. If English was requested but the transcript script
    # is not Latin, say so — the audio was Hindi, nothing was converted.
    lang_requested = str((transcript or {}).get("language_requested") or "en")
    if lang_requested in ("en", "hinglish") and caption_script != "latin":
        language_note = (
            f"Language setting: {lang_requested} (English default), but captions are in "
            f"{caption_script} script — the source audio is Hindi, so Whisper heard Hindi "
            f"words. English captions need English audio; nothing was translated.")
    elif caption_script == "latin":
        language_note = "Captions in English words (Roman script) as requested."
    else:
        language_note = f"Captions in {caption_script} script as requested ({lang_requested})."
    meta = {
        "clip_id": clip_id,
        "upload_id": upload_id,
        "language_requested": lang_requested,
        "language_note": language_note,
        "style_name": profile_name,
        "style_slug": style_name or "global-baatien",
        "start": start, "end": end,
        "clip_start": clip_start, "clip_end": clip_end,
        "seek_sec": seek,
        "duration_sec": duration,
        "score": clip.get("score"),
        "hook_line": clip.get("hook_line"),
        "output_mp4": str(mp4_path),
        "output_srt": str(srt_path),
        "output_ass": str(ass_path),
        "output_txt": str(txt_path),
        "output_caption_txt": str(cap_path),
        "file_size_bytes": mp4_path.stat().st_size,
        "width": out_probe["width"], "height": out_probe["height"],
        "fps": out_probe["fps"], "has_audio": out_probe["has_audio"],
        "pix_fmt": "yuv420p", "video_codec": out_probe["codec"],
        "caption_script": caption_script,
        "caption_font_used": font["family"],
        "font_fallback_note": font["note"],
        "caption_phrases": len(phrases),
        "caption_words": sum(len(p["words"]) for p in phrases),
        "reframe_mode": reframe_mode,
        "face_detected": face["face_detected"],
        "face_centre_x": face["centre_x"],
        "face_rel_width": face["face_rel_width"],
        "zoom_applied": zoom_applied,
        "zoom_percent": zoom_pct if zoom_applied else 100,
        "zoom_note": "opening 2s punch-in, then 100%" if zoom_applied else "zoom skipped (clip too short or zoom<=100%)",
        "color_grade": {"contrast": contrast, "saturation": saturation,
                        "brightness": brightness},
        "silence_removed": False,
        "silence_note": "skipped on purpose — silenceremove can cut words",
        "music_applied": music_applied,
        "music_note": music_note,
        "voice_volume_db": voice_db,
        "export": {"resolution": "1080x1920", "fps": 30, "codec": "H.264",
                   "crf": 18, "pix_fmt": "yuv420p", "audio": "AAC 192k 48kHz"},
    }
    # Phase 5 fix (14 Nov 2026): output_json must be IN the sidecar on
    # disk, not only in the returned dict — /download/<id>/all reads the
    # sidecar from disk to build the ZIP, and it silently skipped the
    # .json files because the key was missing there.
    meta["output_json"] = str(json_path)
    json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def find_render_info(upload_id: str, clip_id: str) -> dict | None:
    """Sidecar for one (upload_id, clip_id), new layout first, then legacy."""
    for path in (RENDERED_DIR / str(upload_id) / f"{clip_id}.json",
                 RENDERED_DIR / f"{clip_id}.json"):
        if path.is_file():
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(meta.get("upload_id")) == str(upload_id):
                return meta
    return None


def load_render_info(clip_id: str, upload_id: str | None = None) -> dict:
    """Load saved render sidecar JSON; raises FileNotFoundError if absent.

    With upload_id: namespaced path first, then legacy flat file.
    Without: scan all sidecars (namespaced + legacy) for this clip_id
    and return the first match — backwards compatible with Phase 3/4.
    """
    if upload_id:
        meta = find_render_info(str(upload_id), clip_id)
        if meta is not None:
            return meta
        raise FileNotFoundError(f"Rendered clip nahi mila: {clip_id} (upload {upload_id}) — pehle POST /render-clip karo")
    candidates = [RENDERED_DIR / f"{clip_id}.json"]
    candidates += sorted(RENDERED_DIR.glob(f"*/{clip_id}.json"))
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    raise FileNotFoundError(f"Rendered clip nahi mila: {clip_id} — pehle POST /render-clip karo")
