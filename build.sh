#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
APP_NAME="AgentF"
mkdir -p logs

# Ensure 3.12 venv exists
if [[ ! -x .venv/bin/python ]]; then
  bash ./v-env.sh
fi
source .venv/bin/activate
PYVER=$(python -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if [[ "$PYVER" != "3.12" ]]; then
  echo "Wrong venv ($PYVER). Rebuilding…"
  deactivate || true
  rm -rf .venv
  bash ./v-env.sh
  source .venv/bin/activate
fi

# Clean
rm -rf build dist *.egg-info .eggs
: > logs/py2app.log

# Build
echo "Building with $(python -V)"
set +e
python setup.py py2app > logs/py2app.log 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  echo "Build failed (rc=$rc). See logs/py2app.log"
  exit $rc
fi

APP="dist/${APP_NAME}.app"
[[ -d "$APP" ]] && echo "✅ Built: $APP" || { echo "App missing. See logs/py2app.log"; exit 1; }
