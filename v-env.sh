#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="AgentF"
echo "=== [$APP_NAME] Virtual environment setup ==="

# -------------------------------
#  Locate Python 3.12
# -------------------------------
PY_BIN=""
for p in "/opt/homebrew/bin/python3.12" "$(command -v python3.12 || true)"; do
  if [[ -n "${p:-}" && -x "$p" ]]; then
    PY_BIN="$p"; break
  fi
done

if [[ -z "$PY_BIN" ]]; then
  echo "❌ ERROR: python3.12 not found."
  echo "→ Install with: brew install python@3.12"
  exit 1
fi

echo "🔍 Using Python: $("$PY_BIN" -V)"

# -------------------------------
#  Remove old .venv if wrong version
# -------------------------------
if [[ -x .venv/bin/python ]]; then
  V=$(.venv/bin/python -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")' || echo "0")
  if [[ "$V" != "3.12" ]]; then
    echo "🧹 Existing .venv is Python $V → removing..."
    rm -rf .venv
  else
    echo "✅ Existing .venv already uses Python 3.12"
  fi
fi

# -------------------------------
#  Create and activate venv
# -------------------------------
if [[ ! -d .venv ]]; then
  echo "📦 Creating new Python 3.12 venv..."
  "$PY_BIN" -m venv .venv
fi

# Activate in this script for installs
# shellcheck disable=SC1091
source .venv/bin/activate

# Verify correct version
PYVER=$(python -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if [[ "$PYVER" != "3.12" ]]; then
  echo "❌ ERROR: .venv is not Python 3.12 (got $PYVER)"
  deactivate || true
  rm -rf .venv
  exit 1
fi

# -------------------------------
#  Install / upgrade dependencies
# -------------------------------
echo "⬆️  Upgrading pip/setuptools/wheel..."
python -m pip install --upgrade pip wheel setuptools

echo "📦 Installing build dependencies..."
python -m pip install \
  "py2app>=0.28.8" \
  "PySide6>=6.7" \
  altgraph \
  modulegraph \
  macholib \
  packaging

# -------------------------------
#  Confirmation
# -------------------------------
echo "✅ Build venv ready: .venv ($(python -V))"
echo "Location: $(python -c 'import sys;print(sys.executable)')"
echo
echo "🚀 Entering virtual environment..."
echo "(type 'deactivate' or 'exit' to leave)"

# -------------------------------
#  Enter interactive shell (zsh/bash aware)
# -------------------------------
SHELL_BIN="${SHELL:-/bin/zsh}"
SHELL_NAME="$(basename "$SHELL_BIN")"

case "$SHELL_NAME" in
  zsh)
    # Start an interactive zsh, source venv in a command, then exec a fresh zsh -i
    exec zsh -i -c 'source .venv/bin/activate; exec zsh -i'
    ;;
  bash)
    # Start an interactive bash, source venv in a command, then exec a fresh bash -i
    exec bash -i -c 'source .venv/bin/activate; exec bash -i'
    ;;
  *)
    # Generic fallback: try POSIX-ish shell
    exec "$SHELL_BIN" -i -c '. .venv/bin/activate; exec '"$SHELL_BIN"' -i'
    ;;
esac
