#!/usr/bin/env bash
# Fomo Timer Bot — запуск (Linux / macOS / Android-Termux). Автоперезапуск при падении.
cd "$(dirname "$0")"

# ---------- Termux? ----------
IS_TERMUX=false
if [ -n "$TERMUX_VERSION" ] || [ "${PREFIX#*com.termux}" != "$PREFIX" ]; then
    IS_TERMUX=true
fi

if $IS_TERMUX; then
    # wake-lock: без него Android замораживает фоновый процесс и бот «засыпает»
    command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
    PYBIN=python
    command -v "$PYBIN" >/dev/null 2>&1 || PYBIN=python3
    if ! command -v "$PYBIN" >/dev/null 2>&1; then
        echo "Python не найден — сначала запустите ./install.sh"
        exit 1
    fi
else
    if [ ! -x ".venv/bin/python" ]; then
        echo "Сначала запустите ./install.sh — бот ещё не установлен."
        exit 1
    fi
    PYBIN=./.venv/bin/python
fi

while true; do
    "$PYBIN" bot.py
    code=$?
    if [ "$code" -eq 0 ]; then
        echo "Бот остановлен."
        break
    fi
    echo "Бот упал (код $code). Перезапуск через 5 секунд... (Ctrl+C — остановить всё)"
    sleep 5
done
