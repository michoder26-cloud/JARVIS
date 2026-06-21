# JARVIS — Voice-Controlled AI Assistant
## แผนการสร้างระบบ

---

## 🎯 ภาพรวม

สร้าง **Jarvis** — AI assistant แบบ voice-driven เหมือน Jarvis ใน Iron Man:
- ฟังเสียงคำสั่ง → แปลงเป็นข้อความ → AI เข้าใจและตัดสินใจ → ทำงานอัตโนมัติ → ตอบกลับด้วยเสียง
- รองรับ **ภาษาไทย + อังกฤษ**
- ทำงานบน **Windows PC** (เครื่องหลักของคุณ)
- ติดตั้งง่ายเหมือน Hermes Agent (one-command installer)

---

## 🏗️ Architecture

```
[🎤 Microphone]
       │
       ▼
[RealtimeSTT + faster-whisper]  ← STT: แปลงเสียง→ข้อความ (ภาษาไทย+อังกฤษ)
       │
       ▼
[LLM: glm-5.2 / minimax-m3]    ← AI Brain: เข้าใจคำสั่ง + ตัดสินใจ + function calling
       │                    (ใช้ Ollama Cloud ที่มีอยู่แล้ว)
       ├──→ [Text Response] ──→ [edge-tts] ──→ [🔊 Speaker]
       │                                      (ตอบกลับด้วยเสียงไทย)
       │
       └──→ [Action JSON]
               │
       ┌───────┴───────┐
       ▼               ▼
  [Playwright]    [pyautogui/pywinauto]
  Browser Auto    Desktop Auto
  - เปิด Chrome    - เปิดโปรแกรม
  - ค้น YouTube    - คลิก/พิมพ์
  - นำทางเว็บ      - ควบคุมหน้าต่าง
```

---

## 📦 Tech Stack (ตรวจสอบแล้ว ✅)

| Component | Tool | Thai? | License | สถานะ |
|-----------|------|-------|---------|-------|
| STT | `faster-whisper` + `RealtimeSTT` | ✅ | MIT (free) | ทดสอบแล้ว |
| LLM | `ollama` client → Ollama Cloud | ✅ (ผ่าน model) | Free (มีอยู่) | ใช้ได้เลย |
| Browser Auto | `playwright` | N/A | Apache-2.0 | ทดสอบแล้ว |
| Desktop Auto | `pyautogui` + `pywinauto` | N/A | MIT | ทดสอบแล้ว |
| TTS | `edge-tts` | ✅ (2 เสียงไทย) | MIT (free) | ทดสอบแล้ว |
| Audio I/O | `sounddevice` | N/A | MIT | ทดสอบแล้ว |

**Thai Voices ที่มี:**
- `th-TH-NiwatNeural` (เสียงชาย)
- `th-TH-PremwadeeNeural` (เสียงหญิง)

---

## 📁 โครงสร้างโปรเจกต์

```
Jarvis/
├── install.sh / install.ps1     ← One-command installer (เหมือน Hermes)
├── requirements.txt             ← Python dependencies
├── jarvis/
│   ├── __init__.py
│   ├── main.py                  ← Entry point + CLI
│   ├── config.yaml               ← Configuration (model, voice, language)
│   ├── stt/
│   │   ├── __init__.py
│   │   └── listener.py          ← RealtimeSTT + faster-whisper wrapper
│   ├── brain/
│   │   ├── __init__.py
│   │   ├── llm.py                ← Ollama LLM client + function calling
│   │   └── prompts.py           ← System prompt สำหรับ JARVIS
│   ├── actions/
│   │   ├── __init__.py
│   │   ├── browser.py            ← Playwright: เปิดเว็บ, ค้น YouTube, ฯลฯ
│   │   ├── desktop.py            ← pyautogui: คลิก, พิมพ์, เปิดโปรแกรม
│   │   ├── system.py             ← เปิด/ปิดแอป, ปรับ volume, ฯลฯ
│   │   └── registry.py           ← Action registry (LLM เรียกผ่าน function call)
│   ├── tts/
│   │   ├── __init__.py
│   │   └── speaker.py            ← edge-tts wrapper (พูดกลับเป็นเสียงไทย)
│   └── utils/
│       ├── __init__.py
│       └── audio.py               ← Audio device management
├── scripts/
│   ├── start_jarvis.sh           ← Linux launcher
│   └── start_jarvis.ps1           ← Windows launcher
├── README.md
└── PLAN.md                        ← (ไฟล์นี้)
```

---

## 🔧 ขั้นตอนการสร้าง (Phases)

### Phase 1: Core Foundation (พื้นฐาน)
- [ ] สร้างโครงสร้างโฟลเดอร์ + `requirements.txt`
- [ ] `config.yaml` — model, voice, language, hotkey settings
- [ ] `install.sh` / `install.ps1` — installer แบบ one-command
- [ ] `main.py` — CLI entry point (เหมือน `hermes` command)

### Phase 2: STT (ฟังเสียง)
- [ ] `stt/listener.py` — RealtimeSTT + faster-whisper
- [ ] รองรับภาษาไทย + อังกฤษ (auto-detect)
- [ ] Wake word: "Jarvis" (เหมือน "Hey Siri")
- [ ] โหมด push-to-talk (กดปุ่มพูด) สำรองไว้

### Phase 3: LLM Brain (AI คิด)
- [ ] `brain/llm.py` — Ollama client → Ollama Cloud (glm-5.2/minimax-m3)
- [ ] `brain/prompts.py` — System prompt: JARVIS personality + function calling
- [ ] Function calling schema: open_browser, search_youtube, type_text, click, open_app, system_command
- [ ] Context memory: จำคำสั่งก่อนหน้าได้ (conversation history)

### Phase 4: Actions (ทำงานอัตโนมัติ)
- [ ] `actions/browser.py` — Playwright: เปิด Chrome, ค้น YouTube, นำทางเว็บ
- [ ] `actions/desktop.py` — pyautogui: คลิก, พิมพ์, เปิดโปรแกรม
- [ ] `actions/system.py` — เปิด/ปิดแอป, ปรับ volume, screenshot
- [ ] `actions/registry.py` — ลงทะเบียน actions ให้ LLM เรียกใช้

### Phase 5: TTS (ตอบกลับเป็นเสียง)
- [ ] `tts/speaker.py` — edge-tts พูดภาษาไทย
- [ ] เลือกเสียงได้ (ชาย/หญิง) จาก config
- [ ] ทำงาน async (พูดได้ขณะทำงานอื่น)

### Phase 6: Integration + Polish
- [ ] เชื่อมทุกส่วนเข้าด้วยกัน: ฟัง→คิด→ทำ→พูด
- [ ] Error handling: ถ้าทำไม่ได้ → ถามกลับ (เหมือน JARVIS จริง)
- [ ] Logging + debug mode
- [ ] README.md พร้อมคู่มือติดตั้งและใช้งาน

---

## ⚙️ Configuration (`config.yaml`)

```yaml
# JARVIS Configuration
llm:
  model: glm-5.2
  # หรือ minimax-m3 สำหรับคำสั่งซับซ้อน
  
stt:
  model: base          # tiny/base/small/medium/large-v3
  language: auto       # auto-detect ไทย/อังกฤษ
  wake_word: "jarvis"  # คำปลุก
  
tts:
  voice: th-TH-PremwadeeNeural   # หญิง
  # หรือ th-TH-NiwatNeural        # ชาย
  
automation:
  browser: playwright
  desktop: pyautogui
  
hotkey:
  push_to_talk: ctrl+space
```

---

## 🚀 การติดตั้ง (เป้าหมาย)

### Windows (เครื่องหลัก)
```powershell
# One command — เหมือน Hermes Agent
irm https://your-repo/install.ps1 | iex
jarvis
```

### Linux
```bash
curl -fsSL https://your-repo/install.sh | bash
jarvis
```

---

## ⚠️ Constraints & Gotchas

| # | เรื่อง | รายละเอียด |
|---|-------|-----------|
| 1 | **TTS ต้องเชื่อมต่ออินเทอร์เน็ต** | edge-tts ใช้ Microsoft cloud (ไม่มี offline ภาษาไทย) |
| 2 | **Desktop automation ทำงานบน Windows ได้** | pyautogui ต้องมี display (VPS headless ใช้ไม่ได้) |
| 3 | **faster-whisper ดาวน์โหลดโมเดลรอบแรก** | ~150MB (base) ถึง ~3GB (large) |
| 4 | **Ollama Cloud ใช้ได้เลย** | มี glm-5.2, minimax-m3 อยู่แล้ว |
| 5 | **Playwright ต้อง install chromium** | `playwright install chromium` หลัง pip install |

---

## 📊 งานที่ JARVIS ทำได้ (ตัวอย่าง)

| คำสั่งเสียง | JARVIS ทำอะไร |
|------------|---------------|
| "Jarvis เปิด YouTube" | เปิด Chrome → ไป youtube.com |
| "Jarvis ค้นหาวิดีโอหมากเดียว" | เปิด YouTube → พิมพ์คำค้น → กด Enter |
| "Jarvis เปิด Notepad แล้วพิมพ์สวัสดี" | เปิด Notepad → พิมพ์ข้อความ |
| "Jarvis ปรับ volume ลง" | กด volume down key |
| "Jarvis ถ่ายภาพหน้าจอ" | Screenshot + save |
| "Jarvis น้อย วันนี้อากาศเป็นไง" | ค้นเว็บ → สรุป → พูดตอบ |

---

## 🤖 การใช้ Agent ช่วยงาน

| Phase | Agent | เหตุผล |
|-------|-------|--------|
| Phase 1-2 (Core + STT) | **coder** agent (delegate) | เขียน Python structure + STT integration |
| Phase 3-4 (LLM + Actions) | **coder** agent (delegate) | LLM function calling + Playwright/pyautogui |
| Phase 5-6 (TTS + Polish) | **coder** agent (delegate) | TTS + integration + error handling |
| ตรวจสอบทุก Phase | **default** (ผม) | รับผล → ตรวจ → สรุปให้คุณ |

---

## 📌 สถานะ
- **รออนุมัติแผน** ก่อนเริ่มลงมือ
- หลังอนุมัติ → delegate ไป coder agent ทำ Phase 1 ทันที