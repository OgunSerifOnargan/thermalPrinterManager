# Thermal Printer Service — setup (Windows PowerShell)
# Run from project root: .\setup.ps1
#
# What it does:
#   1. Picks a Python interpreter >=3.11
#   2. Creates .venv (skips if already there)
#   3. Installs requirements.txt
#   4. Copies .env.example -> .env (skips if .env already exists)
#
# USB demos additionally need Zadig + libusb-win32 (manual, see README).

$ErrorActionPreference = "Stop"

function Write-Info($msg)  { Write-Host $msg -ForegroundColor Cyan }
function Write-Warn($msg)  { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host $msg -ForegroundColor Red }

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $projectRoot

# 1. Python check
$python = $null
foreach ($cand in @("python3.13", "python3.12", "python3.11", "python", "py")) {
    try {
        $ver = & $cand --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -ge 3 -and $min -ge 11) {
                $python = $cand
                break
            }
        }
    } catch { }
}
if (-not $python) {
    Write-Err "Python >=3.11 not found. Install from https://python.org or via Microsoft Store."
    exit 1
}
Write-Info "Using $python ($(& $python --version))"

# 2. venv
if (-not (Test-Path ".venv")) {
    Write-Info "Creating .venv"
    & $python -m venv .venv
} else {
    Write-Info ".venv already exists, skipping"
}

# 3. dependencies
$pip = ".\.venv\Scripts\pip.exe"
Write-Info "Installing dependencies"
& $pip install --quiet --upgrade pip
& $pip install --quiet -r requirements.txt

# 4. .env
if (-not (Test-Path ".env")) {
    Write-Info "Creating .env from .env.example"
    Copy-Item ".env.example" ".env"
} else {
    Write-Info ".env already exists, skipping"
}

# 5. USB note
Write-Warn ""
Write-Warn "Windows USB note: real USB connection to Cashino requires Zadig"
Write-Warn "(https://zadig.akeo.ie) to install the libusb-win32 driver. See README."
Write-Warn "LAN works out-of-the-box — set LAN_HOST in .env."

Write-Info ""
Write-Info "Setup complete."
Write-Info ""
Write-Info "Run the service:"
Write-Info "  .\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000"
Write-Info ""
Write-Info "Then open http://127.0.0.1:8000/ui/"
