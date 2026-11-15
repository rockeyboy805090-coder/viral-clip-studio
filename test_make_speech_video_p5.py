"""Phase 5 fresh E2E test helper (NOT part of the app): build ONE new
2-minute English test video with NEW lines (not the Phase 2/4 scripts).
Uses gTTS (needs internet). Writes /tmp/vcs_p5_en.mp4 + script JSON.
Run: .venv2/bin/python test_make_speech_video_p5.py
"""
import json
import subprocess
import sys
from pathlib import Path

from gtts import gTTS

LINES = [
    "Good morning friends, today I will share the biggest secret about focus and deep work.",
    "Do you know that most people waste three hours every single day on their phone without noticing?",
    "This is the mistake that silently destroys your goals, your money, and your future.",
    "I made the same mistake for years, until I found a simple method that changed everything.",
    "First, write down your top three tasks every morning, before you touch your phone.",
    "Second, work in ninety minute blocks, and never check messages in between.",
    "The third secret is, protect your mornings, because your best energy lives there.",
    "Now let me tell you a story about a student who studied only two hours a day.",
    "He was consistent, he never missed a single day, and slowly his results changed completely.",
    "Did you know that ninety percent of people quit in the very first week of a new habit?",
    "So the winners are not the smartest people, they are the ones who never stop showing up.",
    "Start small today, save your focus first, and your future self will thank you.",
    "Stop waiting for motivation, because action creates motivation, not the other way around.",
    "How to build a habit that lasts, the secret is to make it so easy you cannot fail.",
    "Thank you friends, take care of your time, and I will see you in the next video.",
    "But wait, here is one more secret nobody tells you about saving money every month.",
    "First, never spend before you save, this one rule alone can change your whole life.",
    "Second, track every rupee you spend, it takes only five minutes every single week.",
    "Money grows slowly, but the people who win are the ones who never quit trying.",
    "Start today, do not wait for tomorrow, because your first small step is right now.",
]


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float((out.stdout or "0").strip())


def main() -> None:
    work = Path("/tmp/vcs_speech_p5")
    work.mkdir(exist_ok=True)
    mp3s, script = [], []
    t = 0.0
    for i, line in enumerate(LINES):
        mp3 = work / f"line_{i:02d}.mp3"
        if not mp3.is_file():
            gTTS(text=line, lang="en", slow=False).save(str(mp3))
        dur = ffprobe_duration(mp3)
        script.append({"start": round(t, 2), "end": round(t + dur, 2), "text": line})
        mp3s.append(mp3)
        t += dur
    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{m}'\n" for m in mp3s), encoding="utf-8")
    audio = Path("/tmp/vcs_test_audio_p5.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", str(audio)], check=True)
    total = ffprobe_duration(audio)
    video = Path("/tmp/vcs_p5_en.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc=duration={total:.1f}:size=1280x720:rate=30",
                    "-i", str(audio),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(video)], check=True)
    Path("/tmp/vcs_p5_en_script.json").write_text(
        json.dumps({"total_sec": round(total, 2), "lines": script},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK p5: total audio {total:.1f}s, video {video}")


if __name__ == "__main__":
    sys.exit(main())
