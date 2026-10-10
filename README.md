# JARVIS 🎙️🤖

A voice-controlled AI assistant inspired by Iron Man's JARVIS.

Say **"Jarvis, open YouTube"** — and it opens Chrome, navigates to YouTube, and responds in your language.

## Features

- 🎤 **Voice recognition** (STT) — faster-whisper, works offline
- 🧠 **AI brain** — Gemini (free), Groq, OpenAI or Ollama Cloud, with function calling
- 🖥️ **Desktop automation** — opens apps, types, clicks, screenshots
- 🌐 **Browser automation** — opens Chrome, searches YouTube/Google
- 🔊 **Voice response** (TTS) — edge-tts, free Microsoft neural voices
- 🌍 **Multilingual** — Thai 🇹🇭, English 🇺🇸, German 🇩🇪
- 💬 **Conversation memory** — remembers previous commands
- ❓ **Ask for clarification** — if command is ambiguous, asks you back
- ☁️ **Cloud web app** — use JARVIS from any phone/browser: assistant, real-time translation, live lecture notes & summaries

## ☁️ JARVIS on the Cloud (ใช้งานผ่านมือถือ/เบราว์เซอร์ ไม่ต้องรันบนเครื่องตัวเอง)

รัน JARVIS บนเซิร์ฟเวอร์ แล้วเปิดใช้จากมือถือหรือโน้ตบุ๊กเครื่องไหนก็ได้ผ่านเบราว์เซอร์
(กด "Add to Home Screen" จะได้ไอคอนเหมือนแอป)

| แท็บ | ทำอะไร |
|------|--------|
| **ผู้ช่วย** | คุยด้วยเสียงหรือพิมพ์ — ค้นเว็บ, ข่าว, อากาศ, เวลา, จำสิ่งที่คุณบอก. โหมด Hands-free: พูด "Jarvis …" ได้เลย |
| **แปลสด** | พูดแล้วคำแปลขึ้นทันทีทีละประโยค (8 ภาษา: ไทย/อังกฤษ/เยอรมัน/ญี่ปุ่น/จีน/เกาหลี/ฝรั่งเศส/สเปน) อ่านออกเสียงได้ |
| **สรุปเลคเชอร์** | ถอดเสียงอาจารย์แบบ real-time, โน้ตอัปเดตเองระหว่างสอน, แปลซับได้, กด "สรุปเลคเชอร์" ได้สรุป + คำถามทบทวน, ถามคำถามเกี่ยวกับเลคเชอร์ได้, ดาวน์โหลดเป็น .md |

### ขั้นแรก: ขอ API key ฟรี

JARVIS ใช้ AI ได้หลายค่าย — แนะนำ **Google Gemini** (ฟรี ไม่ต้องใช้บัตรเครดิต ภาษาไทยดี):

1. เข้า https://aistudio.google.com/apikey แล้วล็อกอินด้วยบัญชี Google
2. กด **Create API key** แล้วคัดลอก key ไว้

| ค่าย | ตัวแปร | หมายเหตุ |
|------|--------|----------|
| Google Gemini | `GEMINI_API_KEY` | ฟรี, แนะนำ |
| Groq | `GROQ_API_KEY` | ฟรี, เร็วมาก (เหมาะกับแปลสด) — https://console.groq.com/keys |
| OpenAI | `OPENAI_API_KEY` | เสียเงิน |
| Ollama Cloud | `OLLAMA_API_KEY` | |

ตั้งแค่ตัวใดตัวหนึ่ง JARVIS จะเลือกค่ายให้อัตโนมัติ

> Free tier ของ Gemini: Google อาจนำข้อความที่ส่งไปใช้ปรับปรุงบริการ — อย่าใช้กับข้อมูลลับ

### Deploy ฟรีบน Render (แนะนำ)

1. สมัคร https://render.com แล้วเชื่อม GitHub
2. **New → Blueprint** → เลือก repo นี้ (Render อ่าน `render.yaml` ให้เอง)
3. ใส่ค่า 2 ตัว:
   - `GEMINI_API_KEY` — key จากขั้นแรก
   - `JARVIS_ACCESS_TOKEN` — รหัสผ่านสำหรับเข้าเว็บ (ตั้งเองให้เดายาก) ⚠️ ถ้าไม่ตั้ง ใครเจอ URL ก็ใช้โควต้า AI ของคุณได้
4. รอ deploy เสร็จ → เปิด `https://<ชื่อ>.onrender.com` บนมือถือ → ใส่รหัส → ใช้งานได้เลย

> Render แพ็กเกจฟรีจะ "หลับ" เมื่อไม่มีคนใช้ 15 นาที — เปิดครั้งแรกรอ ~1 นาที. ถ้าต้องการให้ตื่นตลอดใช้แพ็กเกจ Starter
> หรือ deploy ที่อื่นที่รัน Docker ได้ (Fly.io, Railway, Google Cloud Run, VPS)

### รันด้วย Docker (ที่ไหนก็ได้)

```bash
docker build -t jarvis .
docker run -p 8000:8000 -e GEMINI_API_KEY=... -e JARVIS_ACCESS_TOKEN=... -v jarvis-data:/data jarvis
```

### ทดสอบบนเครื่อง

```bash
pip install -e '.[web]'
jarvis --web            # เปิด http://localhost:8000
```

### ตั้งค่า (environment variables)

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|--------|-----------|----------|
| `GEMINI_API_KEY` (หรือ key ค่ายอื่น) | — | API key ของ AI (จำเป็น) |
| `JARVIS_ACCESS_TOKEN` | — | รหัสผ่านเข้าเว็บ (ควรตั้ง) |
| `JARVIS_PROVIDER` | `auto` | `gemini` / `groq` / `openai` / `ollama` |
| `JARVIS_LLM_MODEL` | ตามค่าย | โมเดลหลัก (แชท, โน้ต, สรุป) |
| `JARVIS_FAST_MODEL` | ตามค่าย | โมเดลสำหรับแปลสด — ใส่โมเดลที่เร็วจะ real-time ขึ้น |
| `JARVIS_BASE_URL` + `JARVIS_API_KEY` | — | ใช้ endpoint แบบ OpenAI-compatible ตัวไหนก็ได้ |
| `JARVIS_TIMEZONE` | `Asia/Bangkok` (ใน Docker) | เขตเวลาที่ใช้ตอบ "กี่โมงแล้ว" |
| `JARVIS_TTS_VOICE` | `th-TH-PremwadeeNeural` | เสียงพูดตอบ (เปลี่ยนภาษาให้อัตโนมัติตามคำตอบ) |
| `JARVIS_DATA_DIR` | `~/.jarvis` | ที่เก็บความจำและประวัติแชท |

### เรื่องที่ควรรู้

- **การฟังเสียง** ใช้ระบบในเบราว์เซอร์ (Chrome / Edge / Safari) — เร็วและ real-time ที่สุด.
  Firefox ไม่มีระบบนี้ → build Docker ด้วย `--build-arg WITH_WHISPER=1` เพื่อใช้ Whisper บนเซิร์ฟเวอร์ (ต้องการ RAM ~1GB)
- **ไมโครโฟนต้องใช้ HTTPS** — Render/Fly ให้ HTTPS อัตโนมัติ
- **เลคเชอร์ถูกบันทึกในเบราว์เซอร์ของคุณ** (เมนู 📚) และดาวน์โหลดเป็นไฟล์ได้ — ล้างข้อมูลเบราว์เซอร์แล้วจะหาย
- ระหว่างบันทึกเลคเชอร์ หน้าจอจะไม่ดับเอง; ถ้าล็อกจอ เบราว์เซอร์มือถือจะหยุดฟัง
- เปิด "อ่านคำแปลออกเสียง" ควรใช้หูฟัง ไม่งั้นไมค์จะได้ยินเสียงคำแปลเอง
- **การคุมคอมพิวเตอร์** (เปิดแอป, คลิก, พิมพ์, มองจอ) ทำได้เฉพาะ JARVIS ที่รันบนเครื่องตัวเอง (`jarvis`) — เซิร์ฟเวอร์บนคลาวด์มองไม่เห็นหน้าจอของคุณ

## Quick Install (desktop — คุมคอมพิวเตอร์ได้)

### macOS

#### 1. ติดตั้ง Homebrew (ถ้ายังไม่มี)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

#### 2. ติดตั้ง Python 3.10+ (ถ้ายังไม่มี)
```bash
brew install python@3.12
```

#### 3. ดาวน์โหลด JARVIS
```bash
git clone https://github.com/michoder26-cloud/JARVIS.git
cd JARVIS
```

#### 4. ติดตั้ง
```bash
bash install.sh
```

#### 5. ตั้งค่า API Key (ขอฟรีที่ https://aistudio.google.com/apikey)
```bash
echo 'export GEMINI_API_KEY=ใส่keyของคุณที่นี่' >> ~/.zshrc
source ~/.zshrc
```

#### 6. รัน
```bash
jarvis
```

> ถ้า `jarvis` ไม่พบ ให้รัน: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc`

---

### Linux / WSL

#### 1. ติดตั้ง Python 3.10+ (ถ้ายังไม่มี)
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
```

#### 2. ดาวน์โหลด JARVIS
```bash
git clone https://github.com/michoder26-cloud/JARVIS.git
cd JARVIS
```

#### 3. ติดตั้ง
```bash
bash install.sh
```

#### 4. ตั้งค่า API Key (ขอฟรีที่ https://aistudio.google.com/apikey)
```bash
echo 'export GEMINI_API_KEY=ใส่keyของคุณที่นี่' >> ~/.bashrc
source ~/.bashrc
```

#### 5. รัน
```bash
jarvis
```

> ถ้า `jarvis` ไม่พบ ให้รัน: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc`

---

### Windows

#### 1. ติดตั้ง Python 3.10+
ดาวน์โหลดจาก https://python.org และติดตั้ง (ติ๊ก "Add Python to PATH")

#### 2. ดาวน์โหลด JARVIS
เปิด PowerShell:
```powershell
git clone https://github.com/michoder26-cloud/JARVIS.git
cd JARVIS
```

#### 3. ติดตั้ง
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

#### 4. ตั้งค่า API Key
```powershell
[System.Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "ใส่keyของคุณที่นี่", "User")
```
ปิด PowerShell แล้วเปิดใหม่

#### 5. รัน
```powershell
jarvis
```

---

### ไม่มีไมโครโฟน? ใช้ text mode
```bash
jarvis --text-mode
```
พิมพ์คำสั่งแทนพูด — AI + actions ทำงานปกติ แค่ไม่ต้องพูด

## Usage

```bash
jarvis                      # start with defaults
jarvis --lang en            # force English recognition
jarvis --model small        # larger Whisper model (more accurate)
jarvis --llm-model minimax-m3   # use a different LLM
jarvis --text-mode          # text-only mode (no mic needed)
jarvis --verbose            # debug logging
jarvis --list-audio         # show audio devices
jarvis --web                # cloud web app on http://localhost:8000
```

Say **"Jarvis"** then your command:

| You say | JARVIS does |
|---------|-------------|
| "Jarvis เปิด YouTube" | Opens browser → YouTube |
| "Jarvis ค้นหาหมากเดียว" | Opens YouTube → searches |
| "Jarvis what time is it" | Gets time → speaks it |
| "Jarvis wie ist das Wetter in München" | Gets weather → speaks German |
| "Jarvis open Notepad" | Opens Notepad |
| "Jarvis type hello world" | Types at cursor position |
| "Jarvis turn down the volume" | Presses volume down key |
| "Jarvis ถ่ายภาพหน้าจอ" | Takes screenshot |

### Text Mode (no microphone)

If you don't have a mic or audio setup:
```bash
jarvis --text-mode
```
Type commands instead of speaking. Full AI + actions still work.

## Configuration

Edit `jarvis/config.yaml`:

```yaml
llm:
  provider: auto          # gemini / groq / openai / ollama

stt:
  model: base              # tiny/base/small/medium/large-v3
  language: auto            # auto/th/en/de
  wake_word: "jarvis"
  silence_threshold: 0.5

tts:
  voice: th-TH-PremwadeeNeural   # see voices below
  rate: 0                    # +50 = faster, -20 = slower
  volume: 0

hotkey:
  push_to_talk: ctrl+space
```

### Available TTS Voices

| Alias | Voice name | Language |
|-------|-----------|----------|
| `th-female` | `th-TH-PremwadeeNeural` | Thai 🇹🇭 |
| `th-male` | `th-TH-NiwatNeural` | Thai 🇹🇭 |
| `en-female` | `en-US-AriaNeural` | English 🇺🇸 |
| `en-male` | `en-US-GuyNeural` | English 🇺🇸 |
| `de-female` | `de-DE-KatjaNeural` | German 🇩🇪 |
| `de-male` | `de-DE-ConradNeural` | German 🇩🇪 |

### API Key

Set your API key in `.env` or environment (free Gemini key: https://aistudio.google.com/apikey):
```bash
export GEMINI_API_KEY=your-key-here
```

## Project Layout

```
Jarvis/
├── install.sh / install.ps1     # One-command installers
├── requirements.txt
└── jarvis/
    ├── main.py                  # CLI entry point + integration
    ├── config.yaml               # Default configuration
    ├── stt/listener.py          # Speech-to-Text (faster-whisper)
    ├── tts/speaker.py           # Text-to-Speech (edge-tts)
    ├── brain/
    │   ├── llm.py               # LLM client (OpenAI-compatible)
    │   ├── prompts.py           # JARVIS system prompt
    │   ├── tools.py             # Function calling schemas (12 tools)
    │   └── conversation.py      # Conversation history
    ├── actions/
    │   ├── browser.py           # Playwright browser automation
    │   ├── desktop.py           # pyautogui desktop control
    │   ├── system.py            # System commands + weather + time
    │   └── registry.py          # Action dispatch registry
    ├── web/                     # Cloud web app (FastAPI + browser UI)
    │   ├── server.py            # HTTP/WebSocket API, auth, TTS, Whisper
    │   ├── ai.py                # Streaming translation / notes / summaries
    │   └── static/              # index.html, app.js, app.css
    └── utils/audio.py           # Audio device helpers
```

## Architecture

```
🎤 Microphone
    │
    ▼
RealtimeSTT + faster-whisper     ← STT: speech → text (Thai/EN/DE)
    │
    ▼ (wake word "Jarvis" detected)
    │
LLM Brain (Gemini / Groq / OpenAI / Ollama)
    │
    ├──→ Text Response ──→ edge-tts ──→ 🔊 Speaker
    │                                      (Thai/EN/DE voice)
    │
    └──→ Function Call
            │
    ┌───────┴───────┐
    ▼               ▼
Playwright      pyautogui
Browser Auto    Desktop Auto
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No microphone | `jarvis --list-audio` to check; use `--text-mode` fallback |
| TTS silent | Install `ffmpeg` (installer does this on Linux) |
| Slow first start | Whisper downloads model on first use (~150MB for `base`) |
| API error | Check your API key (e.g. `GEMINI_API_KEY`) is set in `.env` or environment |
| Browser doesn't open | Run `playwright install chromium` |
| Desktop automation fails on Linux | Needs X11 display — use on Windows or Linux with desktop |

## Development

```bash
pip install -e '.[web]' pytest
python -m pytest tests
```

## License

MIT