/* JARVIS cloud web app — assistant, live translation and lecture notes. */
"use strict";

// --------------------------------------------------------------------------
// Constants & helpers
// --------------------------------------------------------------------------
const LANGS = [
  ["th", "ไทย", "th-TH", "th-TH-PremwadeeNeural"],
  ["en", "English", "en-US", "en-US-AriaNeural"],
  ["de", "Deutsch", "de-DE", "de-DE-KatjaNeural"],
  ["ja", "日本語", "ja-JP", "ja-JP-NanamiNeural"],
  ["zh", "中文", "zh-CN", "zh-CN-XiaoxiaoNeural"],
  ["ko", "한국어", "ko-KR", "ko-KR-SunHiNeural"],
  ["fr", "Français", "fr-FR", "fr-FR-DeniseNeural"],
  ["es", "Español", "es-ES", "es-ES-ElviraNeural"],
];
const LANG = Object.fromEntries(LANGS.map(([code, label, locale, voice]) => [code, { code, label, locale, voice }]));
const WAKE_WORDS = /^\s*(hey\s+)?(jarvis|จาร์วิส|จาวิส|จาร์วิด|จาวิด|จารวิส)[\s,.!?]*/i;
const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("jarvis_" + key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("jarvis_" + key, JSON.stringify(value)); } catch { /* storage full or blocked */ }
  },
};

let health = {};
const settings = { engine: store.get("engine", "auto"), tts: store.get("tts", "server") };


function showBanner(text) {
  const b = $("banner");
  if (!text) { b.hidden = true; return; }
  b.textContent = text; b.hidden = false;
}

let toastTimer = null;
function flash(text) {
  showBanner(text);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { if (!health.missingKey) showBanner(""); else showBanner(health.missingKey); }, 6000);
}

// Minimal, safe Markdown renderer (headings, lists, bold, italics, code).
function renderMarkdown(src) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  let html = "", list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of String(src || "").split("\n")) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      close();
      const level = Math.min(m[1].length, 3);
      html += `<h${level}>${inline(m[2])}</h${level}>`;
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      if (list !== "ul") { close(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(m[1])}</li>`;
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { close(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(m[1])}</li>`;
    } else if (!line.trim()) {
      close();
    } else {
      close();
      html += `<p>${inline(line)}</p>`;
    }
  }
  close();
  return html;
}

function scrollToEnd(el) {
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  if (nearBottom) el.scrollTop = el.scrollHeight;
}

// --------------------------------------------------------------------------
// Speech recognition: browser (Web Speech API) or server (Whisper)
// --------------------------------------------------------------------------
class BrowserRecognizer {
  constructor(opts) { Object.assign(this, opts); this.active = false; this.lastFinal = ""; this.lastFinalAt = 0; }

  start() { this.active = true; this._spawn(); this.onState(true); }

  _spawn() {
    const r = new SpeechRec();
    r.lang = this.locale;
    r.continuous = this.continuous;
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res[0].transcript.trim();
        if (res.isFinal) {
          // Android Chrome sometimes repeats the same final result.
          const now = Date.now();
          if (text && !(text === this.lastFinal && now - this.lastFinalAt < 3000)) {
            this.lastFinal = text; this.lastFinalAt = now;
            this.onFinal(text);
          }
        } else {
          interim += res[0].transcript;
        }
      }
      this.onInterim(interim);
    };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        this.active = false;
        this.onError("ไม่ได้รับอนุญาตให้ใช้ไมโครโฟน — เปิดสิทธิ์ไมค์ในเบราว์เซอร์");
      } else if (e.error === "network") {
        this.onError("ระบบฟังเสียงของเบราว์เซอร์ต้องใช้อินเทอร์เน็ต");
      } else if (e.error === "language-not-supported") {
        this.active = false;
        this.onError("เบราว์เซอร์นี้ไม่รองรับภาษานี้ — ลองเปลี่ยนเป็น Whisper ในตั้งค่า");
      }
    };
    r.onend = () => {
      this.onInterim("");
      if (this.active && this.continuous) {
        // Chrome stops after silence or ~60 s; restart to keep listening.
        setTimeout(() => { if (this.active) this._spawn(); }, 200);
      } else {
        this.active = false;
        this.onState(false);
      }
    };
    this.rec = r;
    try { r.start(); } catch { /* already started */ }
  }

  stop() {
    const wasActive = this.active;
    this.active = false;
    try { this.rec && this.rec.stop(); } catch { /* ignore */ }
    if (wasActive && this.continuous) this.onState(false);
  }
}

class ServerRecognizer {
  constructor(opts) { Object.assign(this, opts); this.active = false; this.queue = Promise.resolve(); }

  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      this.onError("ไม่ได้รับอนุญาตให้ใช้ไมโครโฟน");
      this.onState(false);
      return;
    }
    this.active = true;
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    src.connect(this.analyser);
    this.buf = new Float32Array(this.analyser.fftSize);
    this.mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
    this._segment();
    this._tick();
    this.onState(true);
  }

  _segment() {
    const chunks = [];
    const rec = new MediaRecorder(this.stream, this.mime ? { mimeType: this.mime } : undefined);
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      const hadSpeech = this.segSpeech;
      if (hadSpeech) this._upload(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
      if (this.active && (this.continuous || !hadSpeech)) this._segment();
      else if (this.active) this.stop();
    };
    rec.start();
    this.rec = rec;
    this.segStart = Date.now();
    this.lastVoice = Date.now();
    this.segSpeech = false;
  }

  _tick() {
    if (!this.active) return;
    this.analyser.getFloatTimeDomainData(this.buf);
    let sum = 0;
    for (const v of this.buf) sum += v * v;
    const rms = Math.sqrt(sum / this.buf.length);
    const now = Date.now();
    if (rms > 0.02) { this.segSpeech = true; this.lastVoice = now; this.onInterim("🎙️ …"); }
    const dur = now - this.segStart;
    const pauseEnded = this.segSpeech && now - this.lastVoice > 800 && dur > 1200;
    if (this.rec.state === "recording" && (pauseEnded || dur > 15000 || (!this.segSpeech && dur > 8000))) {
      this.rec.stop();
    }
    setTimeout(() => this._tick(), 60);
  }

  _upload(blob) {
    const lang = this.locale.split("-")[0];
    this.queue = this.queue.then(async () => {
      this.onInterim("…กำลังถอดเสียง");
      try {
        const r = await fetch(`/api/transcribe?lang=${encodeURIComponent(lang)}`, {
          method: "POST", headers: { "Content-Type": blob.type }, body: blob,
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || r.statusText);
        if (data.text) this.onFinal(data.text.trim());
      } catch (err) {
        this.onError("ถอดเสียงไม่สำเร็จ: " + err.message);
      } finally {
        this.onInterim("");
      }
    });
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    try { if (this.rec && this.rec.state === "recording") this.rec.stop(); } catch { /* ignore */ }
    this.stream && this.stream.getTracks().forEach((t) => t.stop());
    this.ctx && this.ctx.close().catch(() => {});
    this.onState(false);
  }
}

function chooseEngine() {
  if (settings.engine === "browser") return SpeechRec ? "browser" : null;
  if (settings.engine === "server") return health.server_stt ? "server" : null;
  if (SpeechRec) return "browser";
  return health.server_stt ? "server" : null;
}

let activeRecognizer = null;
let activeOwner = null;

/** Start listening. Only one recognizer runs at a time. */
function listen(owner, opts) {
  stopListening();
  const engine = chooseEngine();
  if (!engine) {
    flash("เบราว์เซอร์นี้ไม่รองรับการฟังเสียง — ใช้ Chrome/Edge/Safari หรือเปิด Whisper บนเซิร์ฟเวอร์");
    return null;
  }
  const options = {
    continuous: true, onInterim() {}, onFinal() {}, onState() {},
    onError: (m) => flash(m), ...opts,
  };
  const userOnState = options.onState;
  options.onState = (on) => {
    if (!on && activeRecognizer === rec) { activeRecognizer = null; activeOwner = null; }
    userOnState(on);
  };
  const rec = engine === "browser" ? new BrowserRecognizer(options) : new ServerRecognizer(options);
  activeRecognizer = rec;
  activeOwner = owner;
  rec.start();
  return rec;
}

function stopListening() {
  if (activeRecognizer) {
    const rec = activeRecognizer;
    activeRecognizer = null; activeOwner = null;
    rec.stop();
  }
}

// --------------------------------------------------------------------------
// Speech output
// --------------------------------------------------------------------------
const speaker = {
  audio: new Audio(),
  queue: [],
  busy: false,
  unlocked: false,

  // Mobile browsers only allow audio that starts from a tap; play a silent
  // clip on the first tap so later replies can play automatically.
  unlock() {
    if (this.unlocked) return;
    this.unlocked = true;
    this.audio.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
    this.audio.play().catch(() => {});
    if (window.speechSynthesis) speechSynthesis.cancel();
  },

  speak(text, langCode) {
    if (!text) return;
    this.queue.push({ text, langCode });
    if (!this.busy) this._next();
  },

  stop() {
    this.queue = [];
    this.audio.pause();
    if (window.speechSynthesis) speechSynthesis.cancel();
    this.busy = false;
  },

  async _next() {
    const item = this.queue.shift();
    if (!item) { this.busy = false; return; }
    this.busy = true;
    try {
      if (settings.tts === "server") await this._server(item);
      else await this._browser(item);
    } catch {
      try { await this._browser(item); } catch { /* give up on this one */ }
    }
    this._next();
  },

  _server({ text, langCode }) {
    const params = new URLSearchParams({ text });
    if (langCode && LANG[langCode]) params.set("voice", LANG[langCode].voice);
    return new Promise((resolve, reject) => {
      const a = this.audio;
      a.onended = resolve;
      a.onerror = () => reject(new Error("tts"));
      a.src = "/api/tts?" + params;
      a.play().catch(reject);
    });
  },

  _browser({ text, langCode }) {
    return new Promise((resolve, reject) => {
      if (!window.speechSynthesis) return reject(new Error("no speechSynthesis"));
      const u = new SpeechSynthesisUtterance(text);
      if (langCode && LANG[langCode]) u.lang = LANG[langCode].locale;
      u.onend = resolve;
      u.onerror = resolve;
      speechSynthesis.speak(u);
    });
  },
};

// --------------------------------------------------------------------------
// WebSocket connection (auto-reconnects)
// --------------------------------------------------------------------------
const conn = {
  sock: null,
  outbox: [],
  retry: 0,
  handlers: {},
  pingTimer: null,

  connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;
    const sock = new WebSocket(url);
    this.sock = sock;
    sock.onopen = () => {
      this.retry = 0;
      setConnState(true);
      this.onReconnect && this.onReconnect();
      const pending = this.outbox; this.outbox = [];
      pending.forEach((m) => this.send(m));
      clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => this.send({ type: "ping" }), 25000);
    };
    sock.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      const h = this.handlers[msg.type];
      if (h) h(msg);
    };
    sock.onclose = (e) => {
      setConnState(false);
      clearInterval(this.pingTimer);
      if (e.code === 4401) { showLogin("รหัสเข้าใช้งานไม่ถูกต้อง"); return; }
      const delay = Math.min(1000 * 2 ** this.retry++, 15000);
      setTimeout(() => this.connect(), delay);
    };
  },

  send(msg) {
    if (this.sock && this.sock.readyState === WebSocket.OPEN) this.sock.send(JSON.stringify(msg));
    else this.outbox.push(msg);
  },

  on(type, fn) { this.handlers[type] = fn; },
};

function setConnState(on) {
  const el = $("conn");
  el.classList.toggle("on", on);
  el.querySelector("b").textContent = on ? "online" : "offline";
}

// Streams: {type: stream_start|stream|stream_end, channel, id, delta|text}
const streamHandlers = {};
conn.on("stream_start", (m) => streamHandlers[m.channel]?.start?.(m));
conn.on("stream", (m) => streamHandlers[m.channel]?.delta?.(m));
conn.on("stream_end", (m) => streamHandlers[m.channel]?.end?.(m));
conn.on("error", (m) => {
  if (m.channel && streamHandlers[m.channel]?.error) streamHandlers[m.channel].error(m);
  else flash(m.message);
});

// --------------------------------------------------------------------------
// Tabs
// --------------------------------------------------------------------------
function selectTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.dataset.panel !== name; });
  store.set("tab", name);
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));

function fillLangSelect(sel, value) {
  sel.innerHTML = LANGS.map(([code, label]) => `<option value="${code}">${label}</option>`).join("");
  sel.value = value;
}

// --------------------------------------------------------------------------
// Assistant tab
// --------------------------------------------------------------------------
const chat = {
  log: $("chat-log"),
  thinkingEl: null,

  add(role, text, extra = "") {
    const hint = this.log.querySelector(".empty-hint");
    if (hint) hint.remove();
    const el = document.createElement("div");
    el.className = `msg ${role} ${extra}`.trim();
    el.textContent = text;
    this.log.appendChild(el);
    this.log.scrollTop = this.log.scrollHeight;
    return el;
  },

  send(text) {
    text = text.trim();
    if (!text) return;
    this.add("user", text);
    conn.send({ type: "chat", text });
  },
};

conn.on("hello", (m) => {
  health.missingKey = m.llm_configured ? "" : `⚠ เซิร์ฟเวอร์ยังไม่ได้ตั้งค่า ${m.key_env || "API key"} — ฟีเจอร์ AI จะยังใช้ไม่ได้`;
  showBanner(health.missingKey);
  if (!chat.log.querySelector(".msg") && m.history && m.history.length) {
    m.history.forEach((h) => { if (h.role === "user" || h.role === "assistant") chat.add(h.role, h.content); });
  }
});
conn.on("chat_status", () => {
  if (!chat.thinkingEl) chat.thinkingEl = chat.add("assistant", "กำลังคิด…", "thinking");
});
conn.on("chat_reply", (m) => {
  const el = chat.thinkingEl || chat.add("assistant", "");
  chat.thinkingEl = null;
  el.classList.remove("thinking");
  el.classList.toggle("err", !!m.error);
  el.textContent = m.text || "(ไม่มีคำตอบ)";
  if (m.tools && m.tools.length) {
    const t = document.createElement("span");
    t.className = "tools";
    t.textContent = "🛠 " + [...new Set(m.tools)].join(", ");
    el.appendChild(t);
  }
  chat.log.scrollTop = chat.log.scrollHeight;
  if ($("chat-voice").checked && m.text) speaker.speak(m.text);
});
conn.on("chat_reset", () => {
  chat.log.innerHTML = "";
  chat.add("assistant", "ล้างประวัติแชทแล้วครับ");
});

$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("chat-input");
  chat.send(input.value);
  input.value = "";
});

function setMicUi(on) {
  $("chat-mic").classList.toggle("on", on);
  document.querySelector(".brand .arc").classList.toggle("active", on);
}

$("chat-mic").addEventListener("click", () => {
  if (activeOwner === "chat-ptt") { stopListening(); return; }
  $("handsfree").checked = false;
  speaker.stop();
  listen("chat-ptt", {
    locale: LANG.th.locale,
    continuous: false,
    onInterim: (t) => { $("chat-interim").textContent = t; },
    onFinal: (t) => { $("chat-interim").textContent = ""; chat.send(t); },
    onState: setMicUi,
  });
});

function startHandsFree() {
  listen("chat-handsfree", {
    locale: LANG.th.locale,
    continuous: true,
    onInterim: (t) => { $("chat-interim").textContent = t; },
    onFinal: (t) => {
      $("chat-interim").textContent = "";
      if (speaker.busy) return; // don't react to our own voice
      const m = t.match(WAKE_WORDS);
      if (!m) return;
      const command = t.slice(m[0].length).trim();
      if (command) chat.send(command);
      else speaker.speak("ครับ?");
    },
    onState: (on) => {
      setMicUi(on);
      if (!on) $("handsfree").checked = false;
    },
  });
}

$("handsfree").addEventListener("change", (e) => {
  if (e.target.checked) startHandsFree();
  else if (activeOwner === "chat-handsfree") stopListening();
});
$("chat-reset").addEventListener("click", () => conn.send({ type: "chat_reset" }));

// --------------------------------------------------------------------------
// Live translate tab
// --------------------------------------------------------------------------
const translateTargets = {}; // id -> {el, langCode, onDone}
let captionSeq = 0;

streamHandlers.translation = {
  delta(m) {
    const t = translateTargets[m.id];
    if (t) { t.el.textContent += m.delta; t.scroll && scrollToEnd(t.scroll); }
  },
  end(m) {
    const t = translateTargets[m.id];
    if (!t) return;
    delete translateTargets[m.id];
    if (m.text) t.el.textContent = m.text;
    t.onDone && t.onDone(m.text);
  },
  error(m) {
    const t = translateTargets[m.id];
    if (t) t.el.textContent = "⚠ " + m.message;
  },
};

function requestTranslation(text, target, el, scroll, onDone) {
  const id = "t" + ++captionSeq;
  translateTargets[id] = { el, scroll, onDone };
  conn.send({ type: "translate", id, text, target });
}

const tr = { running: false };

function setTranslateUi(on) {
  tr.running = on;
  const btn = $("tr-start");
  btn.textContent = on ? "■ หยุดแปล" : "▶ เริ่มแปล";
  btn.classList.toggle("recording", on);
  if (!on) $("tr-interim").textContent = "";
}

function addCaption(text) {
  const log = $("tr-log");
  log.querySelector(".empty-hint")?.remove();
  const cap = document.createElement("div");
  cap.className = "cap";
  cap.innerHTML = `<div class="src"></div><div class="dst"></div>`;
  cap.querySelector(".src").textContent = text;
  log.appendChild(cap);
  log.scrollTop = log.scrollHeight;
  const target = $("tr-target").value;
  requestTranslation(text, target, cap.querySelector(".dst"), log, (out) => {
    if ($("tr-speak").checked && out) speaker.speak(out, target);
  });
}

$("tr-start").addEventListener("click", () => {
  if (tr.running) { stopListening(); return; }
  listen("translate", {
    locale: LANG[$("tr-source").value].locale,
    continuous: true,
    onInterim: (t) => { $("tr-interim").textContent = t; },
    onFinal: addCaption,
    onState: setTranslateUi,
  });
});

function restartTranslateIfRunning() {
  if (tr.running) { stopListening(); setTimeout(() => $("tr-start").click(), 300); }
}
$("tr-swap").addEventListener("click", () => {
  const s = $("tr-source"), t = $("tr-target");
  [s.value, t.value] = [t.value, s.value];
  saveTranslatePrefs();
  restartTranslateIfRunning();
});
function saveTranslatePrefs() { store.set("tr", { source: $("tr-source").value, target: $("tr-target").value }); }
$("tr-source").addEventListener("change", () => { saveTranslatePrefs(); restartTranslateIfRunning(); });
$("tr-target").addEventListener("change", saveTranslatePrefs);
$("tr-big").addEventListener("change", (e) => $("tr-log").classList.toggle("big", e.target.checked));
$("tr-clear").addEventListener("click", () => { $("tr-log").innerHTML = ""; });

// --------------------------------------------------------------------------
// Lecture tab
// --------------------------------------------------------------------------
const MAX_SAVED_LECTURES = 30;
let lecture = null;
let lectureRecording = false;
let timerHandle = null;
let wakeLock = null;

function newLecture() {
  return {
    id: "lec-" + Date.now(),
    title: $("lec-title").value.trim(),
    lang: $("lec-lang").value,
    notesLang: $("lec-notes-lang").value,
    createdAt: new Date().toISOString(),
    elapsed: 0,
    segments: [],
    translations: {},
    notes: "",
    summary: "",
  };
}

function lectureStartMessage() {
  return { type: "lecture_start", lang: lecture.notesLang, segments: lecture.segments, notes: lecture.notes };
}

let saveTimer = null;
function saveLecture(now = false) {
  if (!lecture || !lecture.segments.length) return;
  clearTimeout(saveTimer);
  const doSave = () => {
    lecture.title = $("lec-title").value.trim() || lecture.title;
    const all = store.get("lectures", []).filter((l) => l.id !== lecture.id);
    all.unshift(lecture);
    store.set("lectures", all.slice(0, MAX_SAVED_LECTURES));
    refreshHistory();
  };
  if (now) doSave(); else saveTimer = setTimeout(doSave, 2000);
}

function refreshHistory() {
  const sel = $("lec-history");
  const items = store.get("lectures", []);
  sel.innerHTML = `<option value="">📚 เลคเชอร์ที่บันทึกไว้ (${items.length})</option>` + items.map((l) => {
    const d = new Date(l.createdAt);
    const label = `${l.title || "ไม่มีชื่อ"} — ${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    return `<option value="${esc(l.id)}">${esc(label)}</option>`;
  }).join("");
}

function fmtTime(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function updateTimer() { $("lec-timer").textContent = fmtTime(lecture ? lecture.elapsed : 0); }

function renderTranscriptSegment(i) {
  const box = $("lec-transcript");
  const cap = document.createElement("div");
  cap.className = "cap";
  cap.innerHTML = `<div class="text"></div>`;
  cap.querySelector(".text").textContent = lecture.segments[i];
  const tr = lecture.translations[i];
  if (tr !== undefined) {
    const dst = document.createElement("div");
    dst.className = "dst";
    dst.textContent = tr;
    cap.appendChild(dst);
  }
  box.appendChild(cap);
  $("lec-count").textContent = `(${lecture.segments.length})`;
  return cap;
}

function renderLecture() {
  $("lec-transcript").innerHTML = "";
  $("lec-count").textContent = "";
  $("lec-notes").innerHTML = '<p class="muted">โน้ตจะอัปเดตอัตโนมัติระหว่างที่อาจารย์สอน</p>';
  $("lec-summary").innerHTML = "";
  $("lec-summary-card").hidden = true;
  $("lec-answer").innerHTML = "";
  if (!lecture) { updateTimer(); $("lec-finish").disabled = true; return; }
  $("lec-title").value = lecture.title || "";
  $("lec-lang").value = lecture.lang;
  $("lec-notes-lang").value = lecture.notesLang;
  lecture.segments.forEach((_, i) => renderTranscriptSegment(i));
  if (lecture.notes) $("lec-notes").innerHTML = renderMarkdown(lecture.notes);
  if (lecture.summary) { $("lec-summary").innerHTML = renderMarkdown(lecture.summary); $("lec-summary-card").hidden = false; }
  $("lec-transcript").scrollTop = $("lec-transcript").scrollHeight;
  $("lec-finish").disabled = !lecture.segments.length;
  updateTimer();
}

function onLectureFinal(text) {
  const i = lecture.segments.length;
  lecture.segments.push(text);
  const cap = renderTranscriptSegment(i);
  scrollToEnd($("lec-transcript"));
  conn.send({ type: "lecture_segment", text });
  $("lec-finish").disabled = false;
  if ($("lec-translate").checked && lecture.notesLang !== lecture.lang) {
    const dst = document.createElement("div");
    dst.className = "dst";
    cap.appendChild(dst);
    requestTranslation(text, lecture.notesLang, dst, $("lec-transcript"), (out) => {
      lecture.translations[i] = out; saveLecture();
    });
  }
  saveLecture();
}

async function requestWakeLock() {
  try { if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen"); } catch { /* not allowed */ }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && lectureRecording) requestWakeLock();
});

function setLectureRecordingUi(on) {
  lectureRecording = on;
  const btn = $("lec-start");
  btn.textContent = on ? "⏸ หยุดชั่วคราว" : (lecture && lecture.segments.length ? "● บันทึกต่อ" : "● เริ่มบันทึก");
  btn.classList.toggle("recording", on);
  $("lec-timer").classList.toggle("live", on);
  clearInterval(timerHandle);
  if (on) {
    let last = Date.now();
    timerHandle = setInterval(() => {
      const now = Date.now();
      lecture.elapsed += (now - last) / 1000; last = now;
      updateTimer();
    }, 1000);
    requestWakeLock();
  } else {
    $("lec-interim").textContent = "";
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    saveLecture(true);
  }
}

$("lec-start").addEventListener("click", () => {
  if (lectureRecording) { stopListening(); return; }
  if (!lecture) {
    lecture = newLecture();
    renderLecture();
    conn.send(lectureStartMessage());
  }
  lecture.lang = $("lec-lang").value;
  listen("lecture", {
    locale: LANG[lecture.lang].locale,
    continuous: true,
    onInterim: (t) => { $("lec-interim").textContent = t; scrollToEnd($("lec-transcript")); },
    onFinal: onLectureFinal,
    onState: setLectureRecordingUi,
  });
});

$("lec-finish").addEventListener("click", () => {
  if (!lecture || !lecture.segments.length) return;
  if (lectureRecording) stopListening();
  $("lec-summary-card").hidden = false;
  conn.send({ type: "lecture_finish" });
  $("lec-summary-card").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("lec-new").addEventListener("click", () => {
  if (lecture && lecture.segments.length && !confirm("เริ่มเลคเชอร์ใหม่? (อันเดิมถูกบันทึกไว้แล้ว)")) return;
  if (lectureRecording) stopListening();
  saveLecture(true);
  lecture = null;
  $("lec-title").value = "";
  $("lec-history").value = "";
  renderLecture();
  setLectureRecordingUi(false);
});

$("lec-history").addEventListener("change", (e) => {
  const found = store.get("lectures", []).find((l) => l.id === e.target.value);
  if (!found) return;
  if (lectureRecording) stopListening();
  saveLecture(true);
  lecture = found;
  renderLecture();
  setLectureRecordingUi(false);
  conn.send(lectureStartMessage()); // so questions about this lecture work
});

$("lec-notes-lang").addEventListener("change", (e) => {
  if (!lecture) return;
  lecture.notesLang = e.target.value;
  conn.send(lectureStartMessage());
});
$("lec-title").addEventListener("change", () => saveLecture());

$("lec-ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("lec-ask").value.trim();
  if (!q) return;
  if (!lecture || !lecture.segments.length) { flash("ยังไม่มี transcript ของเลคเชอร์"); return; }
  answerQuestion = q;
  conn.send({ type: "lecture_ask", question: q });
  $("lec-ask").value = "";
});

function lectureMarkdown() {
  const d = new Date(lecture.createdAt);
  const parts = [`# ${lecture.title || "Lecture"}`, `_${d.toLocaleString()} · ${fmtTime(lecture.elapsed)}_`];
  if (lecture.summary) parts.push("", lecture.summary);
  if (lecture.notes) parts.push("", "---", "", "## Live notes", "", lecture.notes);
  parts.push("", "---", "", "## Transcript", "");
  lecture.segments.forEach((s, i) => {
    parts.push(s);
    if (lecture.translations[i]) parts.push(`> ${lecture.translations[i]}`);
    parts.push("");
  });
  return parts.join("\n");
}

$("lec-download").addEventListener("click", () => {
  if (!lecture) return;
  const blob = new Blob([lectureMarkdown()], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (lecture.title || "lecture").replace(/[\\/:*?"<>|]+/g, "_") + ".md";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
$("lec-copy").addEventListener("click", async () => {
  if (!lecture) return;
  try { await navigator.clipboard.writeText(lecture.summary || lectureMarkdown()); flash("คัดลอกแล้ว"); }
  catch { flash("คัดลอกไม่สำเร็จ"); }
});

// Streams for notes / summary / answers.
function markdownStream(el, busyEl, onEnd) {
  let buf = "", raf = null;
  const paint = () => { raf = null; el.innerHTML = renderMarkdown(buf); };
  return {
    start() { buf = ""; if (busyEl) busyEl.hidden = false; },
    delta(m) { buf += m.delta; if (!raf) raf = setTimeout(paint, 120); },
    end(m) {
      clearTimeout(raf); raf = null;
      if (busyEl) busyEl.hidden = true;
      if (m.text) { buf = m.text; el.innerHTML = renderMarkdown(buf); }
      onEnd && onEnd(m.text);
    },
    error(m) { if (busyEl) busyEl.hidden = true; flash(m.message); },
  };
}

streamHandlers.notes = markdownStream($("lec-notes"), $("notes-busy"), (text) => {
  if (text && lecture) { lecture.notes = text; saveLecture(); }
});
streamHandlers.summary = markdownStream($("lec-summary"), $("summary-busy"), (text) => {
  if (text && lecture) { lecture.summary = text; saveLecture(true); }
});

let answerQuestion = "";
const answerEl = document.createElement("div");
const answerStream = markdownStream(answerEl, null, null);
streamHandlers.answer = {
  start(m) {
    const box = $("lec-answer");
    box.innerHTML = `<p><strong>Q:</strong> ${esc(answerQuestion)}</p>`;
    box.appendChild(answerEl);
    answerEl.innerHTML = '<p class="muted">กำลังคิด…</p>';
    answerStream.start(m);
  },
  delta: (m) => answerStream.delta(m),
  end: (m) => answerStream.end(m),
  error: (m) => { answerEl.innerHTML = `<p class="error">${esc(m.message)}</p>`; },
};

conn.onReconnect = () => {
  // The server forgets lecture state when the connection drops; resend it.
  if (lecture) conn.send(lectureStartMessage());
};

// --------------------------------------------------------------------------
// Settings, login & boot
// --------------------------------------------------------------------------
function updateEngineNote() {
  const parts = [];
  parts.push(SpeechRec ? "✓ เบราว์เซอร์นี้รองรับการฟังเสียงแบบ real-time" : "✗ เบราว์เซอร์นี้ไม่มีระบบฟังเสียงในตัว");
  parts.push(health.server_stt ? "✓ เซิร์ฟเวอร์มี Whisper" : "✗ เซิร์ฟเวอร์ไม่ได้ติดตั้ง Whisper");
  $("engine-note").textContent = parts.join(" · ");
  $("server-info").textContent = `AI: ${health.provider || "?"} / ${health.model || "?"} · key: ${health.llm_configured ? "✓" : "✗ (ยังไม่ตั้ง " + (health.key_env || "") + ")"}`;
}

$("open-settings").addEventListener("click", () => { updateEngineNote(); $("settings").showModal(); });
$("set-engine").addEventListener("change", (e) => { settings.engine = e.target.value; store.set("engine", settings.engine); updateEngineNote(); });
$("set-tts").addEventListener("change", (e) => { settings.tts = e.target.value; store.set("tts", settings.tts); });
$("logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" }).catch(() => {});
  location.reload();
});

function showLogin(error) {
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-error").hidden = !error;
  $("login-error").textContent = error || "";
  $("login-token").focus();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const candidate = $("login-token").value.trim();
  const r = await fetch("/api/auth", { method: "POST", headers: { Authorization: "Bearer " + candidate } });
  if (r.status === 429) { showLogin("ใส่รหัสผิดหลายครั้งเกินไป — รอ 15 นาที"); return; }
  if (!r.ok) { showLogin("รหัสไม่ถูกต้อง"); return; }
  $("login-token").value = "";
  startApp();
});

let started = false;
function startApp() {
  $("login").hidden = true;
  $("app").hidden = false;
  if (started) return;
  started = true;
  conn.connect();
}

document.addEventListener("pointerdown", () => speaker.unlock(), { once: true });

async function boot() {
  const trPrefs = store.get("tr", { source: "en", target: "th" });
  fillLangSelect($("tr-source"), trPrefs.source);
  fillLangSelect($("tr-target"), trPrefs.target);
  fillLangSelect($("lec-lang"), "th");
  fillLangSelect($("lec-notes-lang"), "th");
  $("set-engine").value = settings.engine;
  $("set-tts").value = settings.tts;
  selectTab(store.get("tab", "assistant"));
  refreshHistory();
  renderLecture();

  try { health = await (await fetch("/api/health")).json(); } catch { health = {}; }
  if (health.auth_required) {
    // Older versions kept the password in localStorage; trade it for a cookie once.
    const legacy = store.get("token", "");
    if (legacy) {
      try { localStorage.removeItem("jarvis_token"); } catch { /* ignore */ }
      await fetch("/api/auth", { method: "POST", headers: { Authorization: "Bearer " + legacy } }).catch(() => {});
    }
    const ok = (await fetch("/api/auth", { method: "POST" }).catch(() => ({ ok: false }))).ok;
    if (!ok) { showLogin(); return; }
  }
  startApp();
}

boot();
