# JARVIS installer for Windows
# Usage (PowerShell):
#   irm https://raw.githubusercontent.com/.../install.ps1 | iex
#   or:  powershell -ExecutionPolicy Bypass -File install.ps1
#
<#
.SYNOPSIS
    JARVIS installer for Windows.
.DESCRIPTION
    Checks Python 3.10+, creates a venv at %USERPROFILE%\.jarvis\venv,
    installs requirements, installs Playwright Chromium, and adds the
    venv Scripts dir to the user PATH.
#>
param(
    [string]$InstallDir = $PSScriptRoot,
    [string]$VenvDir    = "$env:USERPROFILE\.jarvis\venv"
)

$ErrorActionPreference = "Stop"
function Info($m){ Write-Host "[JARVIS] $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "[JARVIS] $m" -ForegroundColor Yellow }
function Err($m){ Write-Host "[JARVIS] $m" -ForegroundColor Red; exit 1 }

# --- Python check -----------------------------------------------------------
Info "Checking Python 3.10+ ..."
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) { Err "Python not found. Install Python 3.10+ from https://python.org" }

$pyVer = & $py.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$parts = $pyVer.Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
    Err "Python $pyVer is too old. Need 3.10+."
}
Info "Found Python $pyVer OK"

# --- Virtualenv -------------------------------------------------------------
Info "Creating virtualenv at $VenvDir ..."
& $py.Source -m venv $VenvDir
$pip  = "$VenvDir\Scripts\pip.exe"
$pyEx = "$VenvDir\Scripts\python.exe"
& $pip install --upgrade pip -q

# --- Requirements ------------------------------------------------------------
$reqPath = Join-Path $InstallDir "requirements.txt"
if (Test-Path $reqPath) {
    Info "Installing Python requirements ..."
    & $pip install -r $reqPath -q
} else {
    Warn "requirements.txt not found at $reqPath — skipping."
}

# --- Playwright -------------------------------------------------------------
Info "Installing Playwright Chromium ..."
& $pyEx -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Warn "Playwright install failed (non-fatal for STT/TTS)." }

# --- Launcher + PATH --------------------------------------------------------
$binDir = "$VenvDir\Scripts"
$jarvisBat = "$binDir\jarvis.bat"
@"
@echo off
"$pyEx" -m jarvis.main %*
"@ | Set-Content -Path $jarvisBat -Encoding ASCII

Info "Created jarvis.bat at $jarvisBat"

# Add Scripts dir to user PATH if not already present
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable(
        "Path", "$binDir;$userPath", "User")
    Info "Added $binDir to user PATH (restart terminal to apply)."
}

# --- Done -------------------------------------------------------------------
@"
   =================================================
     JARVIS installed successfully!  🎙️  🤖
   =================================================

   Open a NEW PowerShell window and run:
     jarvis --help
   or:
     python -m jarvis.main

   Say "Jarvis" then give a command.
"@ | Write-Host -ForegroundColor Green