# VIRAL CLIP STUDIO — AI Se Banwane Ka Master Prompt
### Long Video → Viral Clips → Global Baatien Style Edit → Instagram / Facebook

**App ka naam:** Viral Clip Studio
**User:** Me Ai Roy — Hinglish user, laptop par pehle version chahiye, FREE stack only, NO card, NO paid API
**Kaam:** Main long video dunga, app usme se viral clips nikaalega, reference video jaisi editing karega, color grading karega, captions lagayega, 9:16 me export karega.

---

## PROMPT 1 — MASTER PROMPT (Ye ek hi prompt AI ko pehle do)
> Copy-paste this full block into Lovable / Bolt / Replit / Claude Code / Cursor / ChatGPT / Antigravity:

```
You are an expert full-stack Python developer. Build me a complete working web app called "Viral Clip Studio" — step by step, in phases. Do NOT build everything in one go. Follow the LOOP RULE below strictly.

GOAL:
User uploads a LONG video (10 min to 2 hours, MP4/MOV) OR pastes a YouTube/Instagram link.
User also uploads/pastes a REFERENCE reel video they like (example: https://www.instagram.com/global.baatien/).
The app must:
1. Extract the editing STYLE from the reference video and save it as a reusable "Style Profile"
2. Find the most viral 30-60 second moments from the long video
3. Edit ALL clips in EXACTLY that reference style — same captions, same zoom, same cuts, same color grading
4. Export ready-to-post Reels for Instagram and Facebook (1080x1920, 9:16)
5. Give EVERYTHING also as editable TEXT (transcript, captions, hook, title, hashtags)

FREE STACK ONLY — NO PAID API, NO CARD:
- Backend: Python + FastAPI
- Video processing: FFmpeg (must be used, no paid editor)
- Speech-to-text: OpenAI Whisper LOCAL model "base" (offline, free) — Hindi, Hinglish, English
- Viral scoring: Google Gemini FREE API (user will paste key in .env as GEMINI_API_KEY) — if key missing, use rule-based scoring, NEVER crash
- Face tracking / reframing: OpenCV (free)
- Frontend: Simple clean HTML + Tailwind, dark theme, big buttons, Hinglish labels where possible
- Database: SQLite (local file, no setup)
- No OpenAI paid API, no Stripe, no paid hosting required. Must run on a normal laptop with `python app.py`.

FEATURES — PHASE WISE:

PHASE 0 — SETUP:
Project structure:
  /app.py (FastAPI entry)
  /style_extractor.py
  /clip_finder.py
  /editor.py
  /transcriber.py
  /utils.py
  /templates/index.html
  /static/
  /uploads/  /outputs/  /styles/
  /requirements.txt  /.env.example  /README.md
Install FFmpeg check on startup. If FFmpeg missing, show clear error "FFmpeg install karo".

PHASE 1 — STYLE EXTRACTOR (Reference Video → Style Profile):
Input: reference video file OR link.
Do this:
- Sample 1 frame every 2 seconds, save thumbnails
- Detect caption text with OCR: font size, color (white/yellow?), position (% from top), ALL CAPS or not, outline/shadow
- Detect cut speed: count scene changes per minute using PySceneDetect logic / frame difference
- Detect zoom: check if face scale changes 100% → 115% on important lines
- Extract color look: average brightness, contrast, saturation, warm/cool tone → save as FFmpeg eq values + a .cube LUT file
- Detect music tempo and voice loudness
- Detect hook text in first 2 seconds and CTA text in last 3 seconds
- Output: a JSON file /styles/<style-name>.json with EXACTLY these 15 fields:
  style_name, caption_font, caption_size_px, caption_color, highlight_color, caption_position_y_percent, caption_all_caps, outline_px, hook_text_style, cut_every_seconds, zoom_percent, zoom_duration_sec, contrast, saturation, brightness, color_tone, music_volume_db, voice_volume_db, broll_enabled, transition_type, end_cta_text, aspect_ratio
- Show this as a "Style Card" in UI for user to Approve/Edit before saving. User can save many styles: "Global Baatien Style", "Podcast Style", etc.

PHASE 2 — CLIP FINDER (Long Video → Viral Clips):
- Extract audio, transcribe with Whisper with word-level timestamps
- Split transcript into 30-60 sec candidate segments
- Score EACH segment 0-100 with Gemini (or rule-based fallback):
  Hook strength (first 5 sec has question/shock/secret/mistake/money) = 30 pts
  Emotion / curiosity / controversy = 30 pts
  Complete idea in 60 sec = 20 pts
  Loop potential (end connects to start) = 20 pts
- Return TOP 5-8 clips, sorted by score, with start_time, end_time, score, reason, hook_line, transcript_text
- Show list in UI with Play preview + Approve/Reject tickbox

PHASE 3 — AUTO EDITOR (Style Profile → Edited Reel):
For EACH approved clip, apply the selected Style Profile EXACTLY:
- Cut from start-1 sec to end+1 sec, remove silence >0.25 sec and filler words
- Reframe to 1080x1920 (9:16): detect face with OpenCV, keep speaker in center, fill side with blurred background, NO black bars
- Add word-by-word captions from Whisper timestamps: 2-4 words at a time, style from profile, highlight current word
- Add hook text in first 2 seconds (big, top-center)
- Add zoom punch-in 110-115% on important lines only
- Apply color grading from profile: FFmpeg eq=contrast=X:saturation=Y:brightness=Z + LUT
- Add background music at -20dB, voice at -3dB (use copyright-free music from /static/music/)
- Add end CTA text in last 20% of clip
- Export: 1080x1920, 30fps, H.264, CRF 18, pix_fmt yuv420p, AAC 192k, 48kHz — yuv420p is MANDATORY for Android phones
- Save .srt caption file + transcript .txt + title + caption + hashtags .txt for EVERY clip

PHASE 4 — WEB UI + BATCH + ANY VIDEO TYPE:
- Page 1: Upload Long Video + Upload/Paste Reference Video + Choose Style → Generate
- Page 2: Style Library (all saved styles, Create/Edit/Delete)
- Page 3: My Clips (history, download, re-edit with different style)
- Auto-detect video type and adjust: Podcast/Interview = speaker tracking, Talking Head = more zoom, Vlog/Travel = more B-roll + music, Tutorial/Screen = captions on top so screen is clear, Motivational = slow zoom + big hook
- Batch: user can upload 3-4 long videos at once, same style or different style per video
- TEST MODE FIRST: Generate ONLY 1 test clip first, show preview, wait for user Approve, THEN generate remaining clips. Never generate all at once without approval.

PHASE 5 — UPLOAD / SHARE:
- Download button for each clip (MP4 + SRT + Caption txt)
- Copy Caption + Hashtags button
- Direct upload buttons (optional, if Meta API keys given, else show "Download karke phone se post karo" guide)
- Export specs shown clearly: Instagram Reels 1080x1920, Facebook Reels same

QUALITY RULES:
- Never change user's words, never invent dialogue, never change face
- Never use copyrighted music
- Captions spelling must match Whisper transcript exactly
- Every clip must be watchable on phone, no black bars, no pixelation
- If a step fails, show the EXACT error in plain language, do not silently skip it

LOOP RULE — MOST IMPORTANT:
Build ONLY Phase 0 first. Then STOP, TEST it, and report.
After EVERY phase:
1. Run the app and test with a real sample video
2. List: What Works / What Failed / What is Missing
3. If ANY test fails, FIX it and re-test. Repeat this fix → test loop max 5 times for that phase.
4. DO NOT move to the next phase until the current phase passes ALL tests.
5. After each phase, ask me: "Phase X complete, test pass. Aage badhu ya kuch badlu?"
6. Never say "done" if you did not actually run and test the code. Show the test output.
7. If you are stuck for 2 attempts, tell me the exact error and the 2 options to fix it, do not guess.
Keep looping this way until Phase 5 is complete and the full flow — Upload Long Video → Select Style → 1 Test Clip → Approve → All Clips Download — works end-to-end without error.
```

---

## PROMPT 2 — LOOP PROMPT (Har phase ke baad ye paste karo)
> Is prompt ko AI se har baar kaam karwane ke baad paste karo, ye loop chalata rahega:

```
LOOP CHECK — Abhi ruko, aage mat badho.

1. Jo tumne abhi banaya hai, use RUN karke dikhao. Sirf code likhna kaafi nahi hai.
2. Is phase ka TEST karo:
   - App start hoti hai?
   - Sample long video (2 min) upload hoti hai?
   - Reference video se Style Profile banta hai?
   - 1 test clip banti hai aur 1080x1920 me download hoti hai?
   - Captions sahi time par aate hain?
3. Result is format me do:
   ✅ PASS: [kya chal raha hai]
   ❌ FAIL: [kya nahi chal raha, exact error kya hai]
   ⏳ MISSING: [kya abhi baki hai]
4. Agar koi FAIL hai, to use FIX karo aur dobara TEST karo. Ye loop tab tak chalao jab tak saare FAIL, PASS na ban jayein. Max 5 try.
5. Jab sab PASS ho jaye, tabhi mujhse puchho: "Agle phase par jau?"
6. Agli baar bhi yahi LOOP follow karna — Banao → Chalao → Test karo → Fix karo → Approve lo → Agla phase.

Abhi isi phase ka loop shuru karo.
```

---

## PROMPT 3 — AGAR AI PEHLE HI GALAT BANAYE / ATK JAYE
```
Ye code run nahi ho raha / ye error aa raha hai: [yahan error paste karo]

Tum ye karo:
1. Error ko line-by-line samjhao, Hinglish me simple bhasha me.
2. Sirf usi file ko fix karo jisme error hai, poora project dobara mat likho.
3. Fix karke dobara RUN karo aur output dikhao.
4. Agar 2 baar fix karke bhi nahi chala, to mujhe 2 alag options do, main ek choose karunga.
5. Aage tabhi badhna jab ye error fix ho jaye. Loop yahi rahega.
```

---

## PROMPT 4 — LOVABLE / BOLT / REPLIT KE LIYE (Short Version)
> Agar tu Lovable, Bolt ya Replit use kar raha hai, to ye chhota prompt use kar — wahan poora Python backend nahi chalega, isliye web version ke liye ye do:

```
Build a web app "Viral Clip Studio" in React + Tailwind, dark theme.

Features:
1. Upload long video, show its duration and transcript preview (use Whisper API mock for demo, real Whisper in backend later)
2. Upload reference reel, extract and show Style Card: font, caption color, cut speed, color tone, zoom, music
3. Save Style Profiles in a library, user can select one
4. Show Top 5 viral clips list with score 0-100, hook line, start-end time, Approve tickbox
5. "Generate 1 Test Clip" button first, preview player, Approve button, then "Generate All Clips"
6. Each clip card: 9:16 preview, Download MP4, Download SRT, Copy Caption + Hashtags
7. All text editable: transcript, captions, hook, title, hashtags in text boxes

Design: Mobile-first, Instagram-style, big buttons, Hinglish labels.
Loop: Build homepage first, test it, then add Style Extractor, test it, then add Clip Finder, test it. Do not jump to next step until previous step works. After each step show What Works / What Failed.
```

---

## KIS AI SE BANWAYE? — Tera Best Option

**Best: Claude Code / Cursor / Antigravity — PROMPT 1 use kar**
Ye poora Python + FFmpeg wala real software banayega. Tere paas jo Mini Antigravity + Video DNA Extractor hai, usi pattern par banega.

**Fast Demo ke liye: Lovable / Replit — PROMPT 4 use kar**
Ye 30 min me website jaisa demo de dega, par real video editing (FFmpeg/Whisper) usme poori nahi hogi. Demo approve karke phir Claude se real version banwana.

**ChatGPT Browser se: PROMPT 1 ko Phase 0 se shuru karke ek-ek phase paste kar**
Ek saath poora mat do. Phase 0 complete → Loop Prompt do → Approve → Phase 1 do. Yehi tera pehle wala "ChatGPT = drafting engine, Rockey = verifier" wala rule hai.

---

## TU ABHI KYA KAREGA — EXACT STEPS

1. AI tool kholo (Claude / Lovable / ChatGPT)
2. PROMPT 1 paste karo
3. AI jab Phase 0 de, to PROMPT 2 (LOOP PROMPT) paste karo
4. Test PASS ho jaye, tabhi "Haan, Phase 1 banao" bolo
5. Aise hi Phase 5 tak jao — kabhi bhi ek saath "sab bana do" mat bolo
6. Pehla reference ye do: https://www.instagram.com/global.baatien/ — iska Style Profile pehle banao

**Yaad rakh:** AI ko loop me rakhna hai to har baar PROMPT 2 dena hai. Warna AI pehli baar me "ho gaya" bol dega aur code chalega nahi.
```


---

## LOCKED STYLE PROFILE #1 — GLOBAL BAATIEN (Verified 14 Nov 2026, live check)
**Account:** @global.baatien — Varun Sharma, "Global Baatien" — Bio: "Bringing powerful conversations to the world." — London, UK — 118 posts, 20,018 followers, 18 following — Links: globalbaatien.com, figurr.in
**Content type:** Almost only Raj Shamani "Figuring Out" podcast clips — motivational, business/tech, geopolitics, curiosity/true-crime — plus occasional other podcast clips.

**Top 6 verified reels (use as reference):**
1. 12.4M views, 1M likes, 15 Oct 2026 — Barbara Bucher death investigator — https://www.instagram.com/reel/DehUJL_zzfk
2. 7.1M views, 458K likes, 14 Oct 2026 — Barbara Bucher teaser — https://www.instagram.com/reel/Dee-LQZzoCc
3. 3.4M views, 302K likes, 23 Oct 2026 — David Goggins — https://www.instagram.com/reel/De0t-Oxz7bH
4. 1.7M views, 169K likes, 22 Oct 2026 — Ello teaser, B-roll runner — https://www.instagram.com/reel/DeynkY0TXEa
5. 1.4M views, 130K likes, 7 Nov 2026 — Raj Shamani Pakistan/IMF — https://www.instagram.com/reel/DfchhlaPMEt
6. 823K views, 55K likes, 3 Nov 2026 — Sam Altman — https://www.instagram.com/reel/DfSP00mTBD9

**LOCKED EDITING DNA — put this EXACTLY in style_extractor / default profile "Global Baatien Style":**
- Hook: First 2 sec, bold claim/question, guest name or shocking number. Examples pattern: "WHAT WILL PEOPLE LEARN BY THE END OF THIS PODCAST??" / "HE ASKED WHAT MILLIONS OF INDIANS WANTED TO KNOW"
- Captions: Bold ALL-CAPS, word-by-word, lower-third, white base with key words highlighted in green / yellow / red / cyan / blue, glowing/neon effect. Teaser variant: black bar + italic white keyword (e.g. "NEVER") + serif subtitle, then glowing top text.
- Cuts: Tight close-ups, jump/zoom cuts between speakers, mostly static wide two-shot for long explanation, close-up for punch lines. Almost NO B-roll — exception is teaser #4 with drone B-roll + fast radial-blur zooms.
- Audio: Original podcast audio ONLY, no added music in 5/6 top reels. Exception #4: slowed phonk track, no speech. Voice is king, music -inf by default for this style.
- Color: Dark/black studio background, warm skin tones, high contrast, saturated caption colours.
- Length: 30-60 sec mostly, top clip 60-90 sec. Teaser 30 sec.
- Written caption (post caption): Very short 1-2 lines, emoji-heavy, hashtags #rajshamani #aura #podcasts #viralreels, teasers add "Stay Tuned Tomorrow/Today at 9:09 PM".
- Motif: Recurring skull emoji 💀, crowned skull graphic in some clips.
- FFmpeg values for this profile: contrast 1.15, saturation 1.10, brightness -0.02, warm skin, dark background preserved. Captions: Montserrat ExtraBold equivalent, white #FFFFFF, highlight green/yellow/red/cyan/blue, glow shadow, lower-third, 2-4 words per phrase.

**IMPORTANT CORRECTION to earlier generic template:** This style does NOT use background music, does NOT use heavy B-roll, does NOT use yellow-only captions. Default music OFF for this profile. B-roll only for teaser mode.

---

## UPDATE — 14 Nov 20:43 IST — PHASE 6: GITHUB + LIVE LINK (User order)
**User ne bola: Full app banane ke baad agent GitHub par host kare aur mujhe Live Link de.**

### PROMPT 5 — FULL APP + GITHUB + LIVE LINK (Ye final prompt agent ko do)
> Is prompt ko Phase 0-5 wale master prompt ke SAATH do, ya akela do agar Phase 0 already pass hai:

```
Tum Viral Clip Studio ka FULL app banao — Phase 0 se Phase 5 tak, ek-ek phase, LOOP RULE ke saath.
Phase 0 already VERIFIED hai: /health ok, FFmpeg 8.1.2, homepage 200, upload works.
Phase 1 abhi baki hai — pehle Phase 1 banao, test karo, approve lo, phir Phase 2, 3, 4, 5.

Jab Phase 5 complete ho jaye aur local par full flow — Upload Long Video → Select Style (Global Baatien Style default) → 1 Test Clip → Approve → All Clips Download — bina error ke chal jaye, TABHI Phase 6 shuru karna. Phase 6 ke bina kaam adhura mana jayega.

PHASE 6 — GITHUB + LIVE HOSTING + LIVE LINK:
1. GITHUB REPO BANAO:
   - Repo name: viral-clip-studio
   - Public repo banao, description: "Long video se viral clips nikaalne wala AI app — Global Baatien style edit, captions, color grading"
   - Saara code push karo: app.py, style_extractor.py, clip_finder.py, editor.py, transcriber.py, utils.py, templates/, static/, styles/, requirements.txt, README.md, .env.example, render.yaml
   - .gitignore me ye DALO, kabhi push mat karo: uploads/, outputs/, *.sqlite3, .venv/, __pycache__/, .env (real key kabhi GitHub par mat daalo)
   - README me likho: kaise run karein, live link kya hai, Phase 0-5 kya karta hai

2. LIVE DEPLOY KARO — RENDER FREE (NO CARD):
   - Render.com par FREE Web Service banao, GitHub repo se connect karo
   - render.yaml file repo me pehle se banao:
     services:
       - type: web
         name: viral-clip-studio
         env: python
         buildCommand: "pip install -r requirements.txt"
         startCommand: "uvicorn app:app --host 0.0.0.0 --port $PORT"
         plan: free
   - Environment variables Render dashboard me daalo: GEMINI_API_KEY (user dega, code me hardcode mat karo)
   - FFmpeg Render par available hai, check karo — agar build me FFmpeg missing ho to apt install ya build command me add karo
   - Deploy hone ke baad live link test karo

3. LIVE LINK DO — YE FINAL DELIVERY HAI:
   - Mujhe ye 3 cheezein ek saath do:
     a) GitHub repo link: https://github.com/<username>/viral-clip-studio
     b) LIVE LINK: https://viral-clip-studio.onrender.com (ya jo Render link mile)
     c) Test proof: Live link kholo, /health par {"status":"ok"} dikhao, homepage ka screenshot/description do
   - Live link par ye test karke dikhao: homepage khulti hai? /health ok hai? Upload page dikhta hai?
   - Agar live link par upload slow ho ya Render free sleep kare (15 min idle ke baad), to mujhe saaf batao — ye Render Free ki limit hai, app ki galti nahi. Pehli baar khulne me 1 min lag sakta hai.

4. LOOP RULE — PHASE 6 KE LIYE BHI:
   - GitHub push fail ho → error dikhao, fix karo, dobara push karo
   - Render deploy fail ho → Render logs padho, exact error batao, fix karo, dobara deploy karo, max 5 try
   - Live link 404 / 500 de → tab tak "done" mat bolo, jab tak live link par homepage + /health dono na chal jayein
   - Jab live link chal jaye, tabhi bolo "DONE — Live link ye hai: ..."

IMPORTANT — LIVE LINK KI SACCHAI (mujhe ye pehle se bata do):
- GitHub Pages par ye Python app LIVE nahi hogi — GitHub sirf code ke liye hai, live chalane ke liye Render chahiye. Isliye GitHub + Render dono karo.
- Render Free par heavy video (1 ghante ki) slow chalegi ya crash ho sakti hai — 512 MB RAM limit hai. Live link DEMO ke liye hai, heavy editing ke liye LOCAL laptop version hi main hoga.
- Real .env / API key kabhi GitHub par mat daalo, kabhi chat me mat dikhao.
```

### AGENT KO GITHUB KE LIYE KYA CHAHIYE — User se ye ek baar puchhna hai
Agent khud GitHub account nahi bana sakta. Uske liye ye chahiye:
1. GitHub username / repo link — user khud https://github.com/new par `viral-clip-studio` naam se repo bana de, ya agent ko bolo "mere GitHub account me repo banao" aur login karwaye
2. GEMINI_API_KEY — Render par daalne ke liye, user .env me dega
3. Render account — free, no card, user khud GitHub se login karke connect karega, ya agent ko access dega

Agar agent ke paas GitHub push ka access nahi hai, to wo ye kare:
- Poora code ZIP karke de + step-by-step likh ke de "GitHub par kaise push karein, Render par kaise deploy karein — 5 minute me"
- Phir bhi LIVE LINK tabhi milega jab user khud 2 click karke deploy karega

### TU ABHI KYA KAREGA — EXACT ORDER
1. Agent ko PROMPT 5 (upar wala) paste karo
2. Agent Phase 1 → 2 → 3 → 4 → 5 banaye, har phase ke baad LOOP PROMPT do
3. Local test PASS ho jaye, tabhi bolo "Ab Phase 6 karo — GitHub + Live Link do"
4. Agent se ye 3 link lo: GitHub repo link + Live Render link + /health proof
5. Wo 3 link mujhe bhej — main live link khol ke khud check karunga

---

## LANGUAGE RULE — LOCKED 14 Nov 22:32 IST (User correction, 2 baar bola)
**User: "Urdu word sab likhkar aa rahe hain, main chahta hun English word likhkar aaye. English per focus karna hai aur English words per."**

**Cause:** Phase 2 test video Hindi speech (gTTS) tha, Whisper `base` ne Hindi ko Urdu script me likha. Captions ne wahi script copy kiya. Ye test audio ki wajah se tha, par user ko English chahiye.

**LOCKED RULE — sab phases me:**
- DEFAULT LANGUAGE: English (en). Whisper language="en", WHISPER_LANGUAGE=en.
- Transcript, captions, hook, title, hashtags — sab ENGLISH WORDS, Roman script me aayenge.
- Caption font: English ke liye Montserrat ExtraBold / DejaVuSans-Bold, Urdu/Arabic font (Noto Naskh Arabic) default NAHI hoga.
- Clip scoring keywords: English PRIMARY (secret, mistake, money, how to, never, stop, free, etc.). Hindi/Urdu banks sirf fallback jab language English na ho.
- UI me Language selector hoga: English (default), Hinglish (Roman), Hindi. Default English.
- Agar source audio Hindi/Urdu hai, to bhi default English output do — aur sidecar me likho `caption_script` + `original_language`. Hindi audio se English transcript fake mat karo — test me English audio use karke verify karo.
- Phase 4 test me kam se kam ek clip ki SRT English words me verify karni hai.
