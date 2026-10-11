# Installs the JARVIS PC agent on Windows. Run from the JARVIS folder:
#   powershell -ExecutionPolicy Bypass -File install-agent.ps1
$ErrorActionPreference = "Stop"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not installed. Get it from https://python.org (tick 'Add Python to PATH'), then run this again."
    exit 1
}
$venv = Join-Path $env:USERPROFILE ".jarvis\agent-venv"
Write-Host "Creating $venv ..."
python -m venv $venv
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements-agent.txt
& $py -m pip install --quiet --no-deps .
& $py -m jarvis.agent --setup
