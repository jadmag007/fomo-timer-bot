#!/usr/bin/env bash
# Fomo Timer Bot — запуск (Linux / macOS). Автоперезапуск при падении.
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "Сначала запустите ./install.sh — бот ещё не установлен."
    exit 1
fi

while true; do
    ./.venv/bin/python bot.py
    code=$?
    if [ "$code" -eq 0 ]; then
        echo "Бот остановлен."
        break
    fi
    echo "Бот упал (код $code). Перезапуск через 5 секунд... (Ctrl+C — остановить всё)"
    sleep 5
done
