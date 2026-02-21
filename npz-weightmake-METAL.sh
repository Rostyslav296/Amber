#!/bin/bash
set -euo pipefail

# ==============================================================
# npz-weightmake.sh — Create manifest qwen.npz for Qwen3-8B-MLX-4bit
# ==============================================================

# --- Config / defaults ---
LLM_DIR="${LLM_DIR:-$HOME/Developer/llm}"
REPO="${1:-Qwen/Qwen3-8B-MLX-4bit}"      # default HF repo (MLX-optimized 8B)
ENGINE="${2:-mlx}"                        # engine: mlx for Apple Silicon
PRECISION="${3:-4bit}"                    # quantization level
OUT="${4:-qwen.npz}"                      # output filename

# Optional env vars (kept for backward compatibility)
PROMPT_TMPL="${PROMPT_TMPL:-You: {user}\nAI: }"
PREAMBLE="${PREAMBLE:-You: Hello\nAI: }"
ENABLE_THINKING="${ENABLE_THINKING:-false}"   # "true" or "false"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-You are a helpful assistant.}"

# --- Go to project & activate venv if present ---
cd "$LLM_DIR"
if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source .venv/bin/activate
fi

# --- Verify python3 available ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 not found. Install Python 3 or activate your venv." >&2
  exit 1
fi

# --- Build .npz manifest ---
python3 - <<'PY'
import os, json, re, numpy as np

def sanitize_prompt_template(t):
    if not isinstance(t, str): return "You: {user}\nAI: "
    if t.count("{") != t.count("}"): return "You: {user}\nAI: "
    if "{user}" not in t:
        t = re.sub(r"\{user[^\}]*\}", "{user}", t)
        if "{user}" not in t: t = "You: {user}\nAI: "
    return t

repo   = os.environ.get("REPO", "Qwen/Qwen3-8B-MLX-4bit")
engine = os.environ.get("ENGINE", "mlx")
prec   = os.environ.get("PRECISION", "4bit")
out    = os.environ.get("OUT", "qwen.npz")
tmpl   = sanitize_prompt_template(os.environ.get("PROMPT_TMPL", "You: {user}\nAI: "))
prem   = os.environ.get("PREAMBLE", "You: Hello\nAI: ")
enable = os.environ.get("ENABLE_THINKING", "false").lower() == "true"
sysmsg = os.environ.get("SYSTEM_PROMPT", "You are a helpful assistant.")

meta = {
  "type": "hf",
  "repo": repo,
  "engine": engine,
  "precision": prec,
  "enable_thinking": enable,
  "system_prompt": sysmsg,
  "prompt_template": tmpl,
  "chat_preamble": prem
}

np.savez(out, meta_json=json.dumps(meta).encode("utf-8"))
print(f"✅ wrote {out}")
print("   repo           :", repo)
print("   engine         :", engine)
print("   precision      :", prec)
print("   enable_thinking:", enable)
print("   system_prompt  :", repr(sysmsg))
PY

echo
echo "✅ Done. Your manifest qwen.npz points to Qwen3-8B-MLX-4bit."
echo "   Use it with ai.py like this:"
echo "     python ai.py chat --weights qwen.npz --temperature 0.7 --top-k 20 --top-p 0.8"
