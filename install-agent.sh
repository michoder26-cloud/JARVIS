#!/usr/bin/env bash
# Installs the JARVIS PC agent on macOS / Linux. Run from the JARVIS folder:
#   bash install-agent.sh
set -euo pipefail
command -v python3 >/dev/null || { echo "Python 3 is required (macOS: brew install python)."; exit 1; }
VENV="$HOME/.jarvis/agent-venv"
echo "Creating $VENV ..."
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements-agent.txt
"$VENV/bin/python" -m pip install --quiet --no-deps .
if [ "$(uname)" = "Darwin" ]; then
  echo "macOS: allow your terminal under System Settings > Privacy & Security >"
  echo "       Accessibility and Screen Recording, or clicks and screenshots will not work."
fi
exec "$VENV/bin/python" -m jarvis.agent --setup
