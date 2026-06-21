#!/usr/bin/env bash
# JARVIS installer for Linux / macOS / WSL
# Usage:  curl -sSL https://raw.githubusercontent.com/.../install.sh | bash
#   or:   bash install.sh
set -euo pipefail

JARVIS_DIR="${JARVIS_DIR:-$(pwd)}"
VENV_DIR="${HOME}/.jarvis/venv"
BIN_DIR="${HOME}/.local/bin"

info()  { echo -e "\033[1;34m[JARVIS]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[JARVIS]\033[0m $*"; }
error() { echo -e "\033[1;31m[JARVIS]\033[0m $*" >&2; exit 1; }

# --- OS detection -----------------------------------------------------------
OS_TYPE="$(uname -s)"
info "Detected OS: ${OS_TYPE}"

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

# --- System deps ------------------------------------------------------------
if [ "$OS_TYPE" = "Darwin" ]; then
    # --- macOS ---
    if ! command -v brew &>/dev/null; then
        warn "Homebrew not found. Install from https://brew.sh"
        warn "After installing Homebrew, run: brew install portaudio ffmpeg"
    else
        info "Installing system dependencies via Homebrew ..."
        brew install portaudio ffmpeg || warn "Could not install some packages — STT/TTS may fail."
    fi
elif command -v apt-get &>/dev/null; then
    # --- Debian/Ubuntu/WSL ---
    info "Installing system audio dependencies (portaudio) ..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq portaudio19-dev ffmpeg libsndfile1 || \
        warn "Could not install audio packages — STT/TTS may fail."
elif command -v dnf &>/dev/null; then
    # --- Fedora/RHEL ---
    info "Installing system audio dependencies ..."
    sudo dnf install -y portaudio-devel ffmpeg || warn "Could not install audio packages."
elif command -v pacman &>/dev/null; then
    # --- Arch Linux ---
    info "Installing system audio dependencies ..."
    sudo pacman -S --noconfirm portaudio ffmpeg || warn "Could not install audio packages."
else
    warn "Unknown package manager. Please install portaudio and ffmpeg manually."
fi

# --- Virtualenv -------------------------------------------------------------
info "Creating virtualenv at ${VENV_DIR} ..."
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip -q

# --- Requirements ------------------------------------------------------------
info "Installing JARVIS package ..."

# Use pyproject.toml with platform-specific extras
if [ "$OS_TYPE" = "Darwin" ]; then
    # macOS: skip pywinauto (Windows-only)
    pip install -e "${JARVIS_DIR}" -q || \
        warn "Some packages failed to install (non-fatal)."
elif [ "$OS_TYPE" = "Linux" ]; then
    # Linux: also skip pywinauto
    pip install -e "${JARVIS_DIR}" -q || \
        warn "Some packages failed to install (non-fatal)."
else
    # Windows (Git Bash / WSL): include pywinauto
    pip install -e "${JARVIS_DIR}[windows]" -q || \
        warn "Some packages failed to install (non-fatal)."
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
    *":${BIN_DIR}:") ;;
    *)
        # On macOS, suggest adding to .zshrc
        if [ "$OS_TYPE" = "Darwin" ]; then
            SHELL_RC="${HOME}/.zshrc"
            warn "Add ${BIN_DIR} to your PATH:"
            warn "  echo 'export PATH=\"${BIN_DIR}:\$PATH\"' >> ${SHELL_RC}"
            warn "  source ${SHELL_RC}"
        else
            warn "Add ${BIN_DIR} to your PATH:  export PATH=\"${BIN_DIR}:\$PATH\""
        fi
        ;;
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