$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

py -m venv (Join-Path $ProjectRoot ".venv")
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Host "Environment ready. Run: .\.venv\Scripts\python.exe vision_gesture_control.py"
