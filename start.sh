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

# ---------- Автообновление из GitHub (тихо; без git или репо — пропускаем) ----------
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    if git fetch origin main >/dev/null 2>&1; then
        LOCALREV=$(git rev-parse HEAD 2>/dev/null)
        REMOTEREV=$(git rev-parse origin/main 2>/dev/null)
        if [ -n "$LOCALREV" ] && [ -n "$REMOTEREV" ] && [ "$LOCALREV" != "$REMOTEREV" ]; then
            echo "GitHub: есть обновление — ставлю. Правки служебных файлов уйдут в"
            echo "stash (возврат: git stash pop); личные .env и data/ не трогаются."
            git stash >/dev/null 2>&1
            if git pull --ff-only >/dev/null 2>&1; then
                echo "GitHub: обновление установлено."
            else
                echo "GitHub: обновить не удалось — запускаю локальную версию как есть."
            fi
        fi
    fi
fi

# Версия в лог — чтобы на скриншотах было видно, что реально запущено
"$PYBIN" -c "import config; print('Fomo Timer Bot', config.APP_VERSION)" 2>/dev/null

while true; do
    "$PYBIN" bot.py
    code=$?
    if [ "$code" -eq 0 ]; then
        echo "Бот остановлен."
        break
    fi
    if [ "$code" -eq 2 ]; then
        # Код 2 = ошибка конфигурации (обычно нет BOT_TOKEN в .env): цикл
        # перезапуска бессмысленен — бот падал бы каждые 5 секунд. Как в start.bat.
        echo "Ошибка конфигурации (код 2). НЕ перезапускаю: исправьте то, что"
        echo "показано выше, и запустите bash start.sh снова."
        exit 2
    fi
    echo "Бот упал (код $code). Перезапуск через 5 секунд... (Ctrl+C — остановить всё)"
    sleep 5
done
