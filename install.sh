#!/usr/bin/env bash
# Fomo Timer Bot — установка (Linux / macOS)
# Запуск: откройте терминал в папке проекта и выполните  ./install.sh
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Fomo Timer Bot — установка (Linux/macOS)"
echo "============================================"
echo

# ---------- 1. Python ----------
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
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
echo "Python: $($PY --version)"

# ---------- 2. Окружение и зависимости ----------
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
sleep 2
exec ./start.sh
