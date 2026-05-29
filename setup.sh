#!/usr/bin/env bash
# Thermal Printer Service — setup (Linux + macOS)
# Idempotent: safe to re-run.
#
# What it does:
#   1. Picks a Python interpreter >=3.11
#   2. Creates .venv (skips if already there)
#   3. Installs requirements.txt
#   4. Copies .env.example → .env (skips if .env already exists)
#   5. On Linux: optionally installs udev rule for USB access

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

color() { printf "\033[1;36m%s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m%s\033[0m\n" "$*" >&2; }
err()   { printf "\033[1;31m%s\033[0m\n" "$*" >&2; }

# ----- 1. Python check -----
need_python() {
  local cands=("python3.13" "python3.12" "python3.11" "python3")
  for c in "${cands[@]}"; do
    if command -v "$c" >/dev/null; then
      local ver
      ver=$("$c" -c "import sys; print('%d.%d' % sys.version_info[:2])")
      local major minor
      major=$(echo "$ver" | cut -d. -f1)
      minor=$(echo "$ver" | cut -d. -f2)
      if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
        echo "$c"
        return 0
      fi
    fi
  done
  err "Python ≥3.11 not found. Install via: brew install python@3.11 (mac) or apt install python3.11 (linux)"
  exit 1
}
PYTHON="$(need_python)"
color "→ Using $PYTHON ($($PYTHON --version))"

# ----- 2. venv -----
if [ ! -d ".venv" ]; then
  color "→ Creating .venv"
  "$PYTHON" -m venv .venv
else
  color "→ .venv already exists, skipping"
fi

# ----- 3. dependencies -----
color "→ Installing dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# ----- 4. .env -----
if [ ! -f .env ]; then
  color "→ Creating .env from .env.example"
  cp .env.example .env
else
  color "→ .env already exists, skipping"
fi

# ----- 5. Optional: install udev rule for USB on Linux -----
OS="$(uname -s)"
if [ "$OS" = "Linux" ]; then
  RULES_SRC="$PROJECT_DIR/scripts/udev/99-escpos.rules"
  RULES_DST="/etc/udev/rules.d/99-escpos.rules"
  if [ -f "$RULES_SRC" ] && [ ! -f "$RULES_DST" ]; then
    warn ""
    warn "Optional: install USB udev rule (requires sudo)?"
    warn "  This grants the 'plugdev' group access to the Cashino printer."
    warn "  Skip with: just press Enter."
    read -p "  Install [y/N]? " yn
    if [[ "$yn" =~ ^[Yy]$ ]]; then
      sudo install -m 0644 "$RULES_SRC" "$RULES_DST"
      sudo udevadm control --reload-rules
      sudo udevadm trigger
      color "✓ udev rule installed; you may need to add your user to plugdev:"
      color "  sudo usermod -aG plugdev \$USER && newgrp plugdev"
    fi
  fi
elif [ "$OS" = "Darwin" ]; then
  if ! brew list libusb >/dev/null 2>&1; then
    warn ""
    warn "macOS USB support needs libusb:  brew install libusb"
  fi
fi

color ""
color "✓ Setup complete."
color ""
color "Run the service:"
color "  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000"
color ""
color "Then open: http://127.0.0.1:8000/ui/"
