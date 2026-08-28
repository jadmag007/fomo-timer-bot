#!/usr/bin/env bash
# Одноразовый вход юзербота (macOS/Linux): свежая initData навсегда
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "Python не найден — сначала ./install.sh"; exit 1; fi
if [ -f .venv/bin/python ]; then PY=".venv/bin/python"; fi

exec "$PY" login_userbot.py
