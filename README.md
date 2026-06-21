# JARVIS 🎙️🤖

A voice-controlled AI assistant inspired by Iron Man's JARVIS.

Say **"Jarvis, open YouTube"** — and it opens Chrome, navigates to YouTube, and responds in your language.

## Features

- 🎤 **Voice recognition** (STT) — faster-whisper, works offline
- 🧠 **AI brain** — Ollama Cloud (glm-5.2) with function calling
- 🖥️ **Desktop automation** — opens apps, types, clicks, screenshots
- 🌐 **Browser automation** — opens Chrome, searches YouTube/Google
- 🔊 **Voice response** (TTS) — edge-tts, free Microsoft neural voices
- 🌍 **Multilingual** — Thai 🇹🇭, English 🇺🇸, German 🇩🇪
- 💬 **Conversation memory** — remembers previous commands
- ❓ **Ask for clarification** — if command is ambiguous, asks you back

## Quick Install

### Linux / WSL
```bash
git clone <repo> Jarvis && cd Jarvis
bash install.sh
```

### Windows (PowerShell)
```powershell
git clone <repo> Jarvis; cd Jarvis
powershell -ExecutionPolicy Bypass -File install.ps1
```

## Usage

```bash
jarvis                      # start with defaults
jarvis --lang en            # force English recognition
jarvis --model small        # larger Whisper model (more accurate)
jarvis --llm-model minimax-m3   # use a different LLM
jarvis --text-mode          # text-only mode (no mic needed)
jarvis --verbose            # debug logging
jarvis --list-audio         # show audio devices
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
  model: glm-5.2
  base_url: https://ollama.com/v1

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

Set your Ollama Cloud API key in `.env` or environment:
```bash
export OLLAMA_API_KEY=your-key-here
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
LLM Brain (glm-5.2 via Ollama Cloud)
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
| API error | Check `OLLAMA_API_KEY` is set in `.env` or environment |
| Browser doesn't open | Run `playwright install chromium` |
| Desktop automation fails on Linux | Needs X11 display — use on Windows or Linux with desktop |

## License

MIT