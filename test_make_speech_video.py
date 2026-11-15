"""Test helper (NOT part of the app): build a real-speech test video.

Uses gTTS (free Google Translate TTS, needs internet) to speak Hindi
lines, concatenates them, and muxes with a test pattern video.
Writes:
  /tmp/vcs_test_long.mp4  (video + real Hindi speech)
  /tmp/vcs_test_script.json (the exact lines + measured start/end times)
Run: .tts_venv/bin/python test_make_speech_video.py
"""
import json
import subprocess
import sys
from pathlib import Path

from gtts import gTTS

LINES = [
    "नमस्ते दोस्तों, आज मैं आपको पैसे बचाने का सबसे बड़ा सीक्रेट बताने वाला हूँ।",
    "क्या आप जानते हैं कि हर महीने आप पाँच हज़ार रुपये बिना सोचे खर्च कर देते हैं?",
    "यह गलती लगभग हर कोई करता है, और इसका असर आपकी ज़िंदगी पर बहुत बड़ा होता है।",
    "पहले मैं भी ऐसा ही करता था, फिर मुझे एक तरीका मिला जिसने सब कुछ बदल दिया।",
    "सबसे पहले अपनी खर्च की लिस्ट बनाओ, हर एक रुपये का हिसाब रखो।",
    "दूसरा, जो चीज़ ज़रूरी नहीं है उसको तुरंत बंद करो, शॉक लगेगा लेकिन फायदा होगा।",
    "तीसरा सीक्रेट यह है कि पैसे को पहले बचाओ, फिर खर्च करो, उल्टा मत करो।",
    "अब बात करते हैं इन्वेस्टमेंट की, लाख रुपये कैसे बनते हैं छोटे छोटे कदमों से।",
    "एक कहानी सुनो, एक आदमी ने सिर्फ पाँच सौ रुपये महीने से शुरुआत की थी।",
    "धीरे धीरे उसकी आदत बन गई, और आज वह बिल्कुल टेंशन फ्री है।",
    "क्या आपको पता है कि नब्बे प्रतिशत लोग पहले महीने में ही हार मान लेते हैं?",
    "इसलिए जीत उनकी होती है जो रुकते नहीं, जो हर दिन थोड़ा थोड़ा करते रहते हैं।",
    "अब एक और राज़, जो कोई नहीं बताता, वह यह है कि अपने दोस्तों से सीखो।",
    "जो लोग आपसे आगे हैं उनसे सवाल पूछो, शर्माना मत, यही असली सीक्रेट है।",
    "और हाँ, यह वीडियो खत्म होने से पहले एक और बात, कल का वीडियो मत भूलना।",
    "कल मैं बताऊँगा कि एक हफ्ते में दस हज़ार रुपये कैसे बचाए जा सकते हैं।",
    "तो दोस्तों, आज का लेसन याद रखना, पैसे बचाना मुश्किल नहीं है।",
    "बस शुरुआत करनी है, छोटी शुरुआत, बड़ी जीत की तरफ पहला कदम।",
    "अगर यह बात पसंद आई तो फॉलो करो, और कमेंट में लिखो पैसा।",
    "मिलते हैं कल, तब तक के लिए अपना ख्याल रखना, अलविदा दोस्तों।",
    "एक मिनट रुकिए, एक बोनस टिप भी है, यह सबसे इम्पोर्टेन्ट है।",
    "अपने खर्च पर नज़र रखो, हर हफ्ते एक बार हिसाब करो, बस इतना ही।",
    "यह आदत आपकी ज़िंदगी बदल देगी, मेरा वादा है, आप खुद देखोगे।",
    "शुक्रिया दोस्तों, आप सबका बहुत बहुत धन्यवाद, फिर मिलेंगे।",
    "अलविदा, और हाँ, यह सीक्रेट किसी को मत बताना, सिर्फ अपने तक रखो।",
    "और एक बात, क्या आपको पता है कि छोटे शहरों में भी लोग लाख रुपये कमा रहे हैं?",
    "उनका सीक्रेट सिम्पल है, वह रोज़ सुबह एक घंटा सीखने में लगाते हैं।",
    "सीखना कभी बंद मत करो, यही असली जीत है, यही असली ज़िंदगी है।",
    "तो दोस्तों, आज से शुरू करो, कल पर मत छोड़ो, अभी करो।",
    "मिलते हैं अगले वीडियो में, तब तक खुश रहो, मेहनत करो, जीत पक्की है।",
]


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float((out.stdout or "0").strip())


def main() -> None:
    work = Path("/tmp/vcs_speech")
    work.mkdir(exist_ok=True)
    mp3s, script = [], []
    t = 0.0
    for i, line in enumerate(LINES):
        mp3 = work / f"line_{i:02d}.mp3"
        if not mp3.is_file():
            gTTS(text=line, lang="hi", slow=False).save(str(mp3))
        dur = ffprobe_duration(mp3)
        script.append({"start": round(t, 2), "end": round(t + dur, 2), "text": line})
        mp3s.append(mp3)
        t += dur
    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{m}'\n" for m in mp3s), encoding="utf-8")
    audio = Path("/tmp/vcs_test_audio.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", str(audio)], check=True)
    total = ffprobe_duration(audio)
    video = Path("/tmp/vcs_test_long.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc=duration={total:.1f}:size=1280x720:rate=30",
                    "-i", str(audio),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(video)], check=True)
    Path("/tmp/vcs_test_script.json").write_text(
        json.dumps({"total_sec": round(total, 2), "lines": script},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK: total audio {total:.1f}s, video {video}, script /tmp/vcs_test_script.json")


if __name__ == "__main__":
    sys.exit(main())
