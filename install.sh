#!/usr/bin/env bash
# Fomo Timer Bot — установка (Linux / macOS / Android-Termux)
# Запуск: откройте терминал в папке проекта и выполните  ./install.sh
# Подробный гайд по андроиду: TERMUX.md
set -e
cd "$(dirname "$0")"

# ---------- 0. Termux? ----------
IS_TERMUX=false
if [ -n "$TERMUX_VERSION" ] || [ "${PREFIX#*com.termux}" != "$PREFIX" ]; then
    IS_TERMUX=true
fi

echo "============================================"
echo "  Fomo Timer Bot — установка"
echo "============================================"
echo

# ---------- 1. Python ----------
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    if $IS_TERMUX; then
        echo "Python не найден — ставлю через pkg..."
        pkg install -y python git
        PY=python
    else
        echo "Python 3 не найден. Пробую установить автоматически..."
        if command -v apt-get >/dev/null 2>&1; then
            sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y python3
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -S --noconfirm python
        elif command -v brew >/dev/null 2>&1; then
            brew install python@3.12
        else
            echo "Не нашёл пакетный менеджер. Установите Python 3.11+ с https://python.org и запустите install.sh снова."
            exit 1
        fi
    fi
fi
echo "Python: $($PY --version)"

# ---------- 2. Окружение и зависимости ----------
if $IS_TERMUX; then
    # В Termux venv ненадёжен, а aiohttp берём ГОТОВЫЙ из репозитория
    # Termux (python-aiohttp) — иначе pip будет компилировать его минут десять.
    echo "Termux: ставлю python-aiohttp из репозитория (без долгой компиляции)..."
    pkg install -y python-aiohttp || true
    echo "Ставлю зависимости..."
    pip install --upgrade pip || true
    if ! pip install -r requirements.txt; then
        echo "Не собралось — ставлю компилятор и пробую ещё раз..."
        pkg install -y build-essential
        pip install -r requirements.txt
    fi
else
    if [ ! -x ".venv/bin/python" ]; then
        echo "Создаю виртуальное окружение..."
        if ! "$PY" -m venv .venv 2>/dev/null; then
            echo "Модуль venv отсутствует (Debian/Ubuntu?) — ставлю python3-venv..."
            sudo apt-get update && sudo apt-get install -y python3-venv
            "$PY" -m venv .venv
        fi
    fi
    echo "Ставлю зависимости (1-2 минуты)..."
    ./.venv/bin/pip install -q --upgrade pip
    ./.venv/bin/pip install -q -r requirements.txt
fi
echo "Зависимости установлены."
echo

# ---------- 3. Токен бота ----------
if [ -f .env ] && grep -q "^BOT_TOKEN=..*" .env; then
    echo "Токен найден: OK"
else
    echo "Нужен токен бота:"
    echo "  1. Откройте в Telegram @BotFather"
    echo "  2. Отправьте /newbot, придумайте имя и username"
    echo "  3. Скопируйте токен вида 7712345678:AAF3xQ..."
    echo
    read -r -p "Вставьте токен сюда и нажмите Enter: " TOKEN
    if [ -z "$TOKEN" ]; then
        echo "Токен пустой. Запустите install.sh ещё раз."
        exit 1
    fi
    printf 'BOT_TOKEN=%s\nDEFAULT_TZ=Europe/Moscow\nWARN_ENABLED=false\n' "$TOKEN" > .env
    echo "Токен сохранён."
fi
echo

# ---------- 4. Запуск ----------
echo "Готово! Запускаю бота (остановка: Ctrl+C)."
echo "Потом можно запускать командой: ./start.sh"
if $IS_TERMUX; then
    echo "На андроиде держите Termux открытым (или настройте автозапуск — см. TERMUX.md)."
fi
sleep 2
exec ./start.sh
