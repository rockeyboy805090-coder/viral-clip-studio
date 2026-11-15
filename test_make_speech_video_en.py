"""Test helper (NOT part of the app): build TWO real-speech ENGLISH test videos.

Uses gTTS (free Google Translate TTS, needs internet) to speak English
lines with viral keywords (secret, mistake, money, never, stop, how to),
concatenates them, and muxes each with a test pattern video.
Writes:
  /tmp/vcs_test_en1.mp4  (~70s) + /tmp/vcs_test_en1_script.json
  /tmp/vcs_test_en2.mp4  (~70s) + /tmp/vcs_test_en2_script.json
Run: .tts_venv/bin/python test_make_speech_video_en.py
"""
import json
import subprocess
import sys
from pathlib import Path

from gtts import gTTS

LINES_1 = [
    "Hello friends, today I will tell you the biggest secret about saving money.",
    "Do you know that every month you waste five thousand rupees without even thinking?",
    "This is a mistake almost everyone makes, and it changes your whole life.",
    "I used to do the same, then I found a method that changed everything.",
    "First, make a list of your spending, and track every single rupee.",
    "Second, stop buying things you do not need, it will shock you but it helps.",
    "The third secret is, save money first and spend later, never do it the other way.",
    "Now let us talk about investing, and how small steps build one lakh rupees.",
    "Listen to this story, one man started with only five hundred rupees a month.",
    "Slowly it became his habit, and today he is completely tension free.",
    "Did you know ninety percent of people give up in the very first month?",
    "So the winners are the ones who never stop, who do a little every day.",
]
LINES_2 = [
    "Stop making this mistake with your money, it is costing you a fortune.",
    "How to save ten thousand rupees in one week, the secret nobody tells you.",
    "First, never spend before you save, this one rule changes everything.",
    "Second, track your money every week, it takes only five minutes.",
    "Money grows slowly, but the people who win are the ones who never quit.",
    "Here is the truth about investing, you do not need a lot of money to start.",
    "Start small, learn every day, and your future self will thank you.",
    "Never compare yourself with others, compare yourself with yesterday.",
    "The biggest secret of rich people is simple, they save first and spend later.",
    "So start today, do not wait for tomorrow, your first step is now.",
    "Follow for part two, where I show the exact plan to save one lakh rupees.",
    "Thank you friends, take care of your money, and I will see you tomorrow.",
]


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float((out.stdout or "0").strip())


def build(lines, tag):
    work = Path(f"/tmp/vcs_speech_en_{tag}")
    work.mkdir(exist_ok=True)
    mp3s, script = [], []
    t = 0.0
    for i, line in enumerate(lines):
        mp3 = work / f"line_{i:02d}.mp3"
        if not mp3.is_file():
            gTTS(text=line, lang="en", slow=False).save(str(mp3))
        dur = ffprobe_duration(mp3)
        script.append({"start": round(t, 2), "end": round(t + dur, 2), "text": line})
        mp3s.append(mp3)
        t += dur
    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{m}'\n" for m in mp3s), encoding="utf-8")
    audio = Path(f"/tmp/vcs_test_audio_en_{tag}.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", str(audio)], check=True)
    total = ffprobe_duration(audio)
    video = Path(f"/tmp/vcs_test_en{tag}.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc=duration={total:.1f}:size=1280x720:rate=30",
                    "-i", str(audio),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(video)], check=True)
    Path(f"/tmp/vcs_test_en{tag}_script.json").write_text(
        json.dumps({"total_sec": round(total, 2), "lines": script},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK {tag}: total audio {total:.1f}s, video {video}")
    return video


def main() -> None:
    build(LINES_1, "1")
    build(LINES_2, "2")


if __name__ == "__main__":
    sys.exit(main())
