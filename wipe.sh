#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

rm -rf build dist *.egg-info .eggs
mkdir -p logs
: > logs/py2app.log
echo "Cleaned build/, dist/, egg-info. Log reset."
