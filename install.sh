#!/usr/bin/env bash
# JARVIS installer for Linux / WSL
# Usage:  curl -sSL https://raw.githubusercontent.com/.../install.sh | bash
#   or:   bash install.sh
set -euo pipefail

JARVIS_DIR="${JARVIS_DIR:-$(pwd)}"
VENV_DIR="${HOME}/.jarvis/venv"
BIN_DIR="${HOME}/.local/bin"

info()  { echo -e "\033[1;34m[JARVIS]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[JARVIS]\033[0m $*"; }
error() { echo -e "\033[1;31m[JARVIS]\033[0m $*" >&2; exit 1; }

# --- Python check -----------------------------------------------------------
info "Checking Python 3.10+ ..."
if ! command -v python3 &>/dev/null; then
    error "python3 not found. Install Python 3.10+ first."
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    error "Python ${PY_VERSION} is too old. Need 3.10+."
fi
info "Found Python ${PY_VERSION} ✓"

# --- System deps (apt-based only) -------------------------------------------
if command -v apt-get &>/dev/null; then
    info "Installing system audio dependencies (portaudio) ..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq portaudio19-dev ffmpeg libsndfile1 || \
        warn "Could not install audio packages — STT/TTS may fail."
fi

# --- Virtualenv -------------------------------------------------------------
info "Creating virtualenv at ${VENV_DIR} ..."
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip -q

# --- Requirements ------------------------------------------------------------
if [ -f "${JARVIS_DIR}/requirements.txt" ]; then
    info "Installing Python requirements ..."
    pip install -r "${JARVIS_DIR}/requirements.txt" -q || \
        warn "Some packages failed to install (non-fatal)."
else
    warn "requirements.txt not found in ${JARVIS_DIR} — skipping."
fi

# --- Playwright -------------------------------------------------------------
info "Installing Playwright Chromium ..."
python -m playwright install chromium || warn "Playwright install failed (non-fatal for STT/TTS)."

# --- Command symlink --------------------------------------------------------
mkdir -p "$BIN_DIR"
cat > "${BIN_DIR}/jarvis" <<EOF
#!/usr/bin/env bash
source "${VENV_DIR}/bin/activate"
exec python -m jarvis.main "\$@"
EOF
chmod +x "${BIN_DIR}/jarvis"
info "Created 'jarvis' command at ${BIN_DIR}/jarvis"

# Ensure ~/.local/bin is on PATH
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) warn "Add ${BIN_DIR} to your PATH:  export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac

# --- Done -------------------------------------------------------------------
cat <<'BANNER'

   ╔══════════════════════════════════════════════╗
   ║   JARVIS installed successfully!  🎙️  🤖     ║
   ╚══════════════════════════════════════════════╝

   Run:  jarvis --help
   Or:   python -m jarvis.main

   Say "Jarvis" then give a command.
BANNER