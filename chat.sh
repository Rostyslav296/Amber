#!/usr/bin/env bash
set -euo pipefail

# ===============================================
# AgentF Chat Launcher (MLX-only)
# - Uses Python from .venv if available, otherwise system Python 3.12
# - Defaults to MLX manifest qwen.npz
# - STREAM=1 enables JSON stream mode for GUI
# ===============================================

ROOT="${ROOT:-$HOME/Developer/llm}"
VENV_DIR="$ROOT/.venv"
PROJ_PY="$VENV_DIR/bin/python3"
SYS_PY="/opt/homebrew/bin/python3.12"
AI="$ROOT/ai.py"
AGENT="$ROOT/agent.py"
WEIGHTS="$ROOT/qwen.npz"

# --- pick Python ---
if [ -x "$PROJ_PY" ]; then
  PY="$PROJ_PY"
elif [ -x "$SYS_PY" ]; then
  PY="$SYS_PY"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "❌ No usable Python found."
  exit 1
fi

# --- sanity checks ---
[ -f "$AI" ] || { echo "❌ Missing ai.py at $AI"; exit 1; }
[ -f "$WEIGHTS" ] || { echo "❌ Missing weights manifest ($WEIGHTS)"; exit 1; }

# --- summary ---
echo "🔹 Launching AgentF (MLX)"
echo "   Python: $PY"
echo "   Weights: $WEIGHTS"
echo "   Agent: $AGENT"
echo

# --- run mode ---
if [ "${STREAM:-0}" = "1" ]; then
  echo "🌀 Stream mode enabled (JSON stdin)"
  exec "$PY" "$AI" chat --weights "$WEIGHTS" --agent "$AGENT" --stream
else
  exec "$PY" "$AI" chat --weights "$WEIGHTS" --agent "$AGENT"
fi
