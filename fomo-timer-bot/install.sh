#!/usr/bin/env bash
# Fomo Timer Bot — установка (Linux / macOS / Android-Termux)
# Запуск: откройте терминал в папке проекта и выполните  bash install.sh
# (или ./install.sh, если файлу даны права на запуск)
# Подробный гайд по андроиду: TERMUX.md
set -e
cd "$(dirname "$0")"

# Git при пуше с Windows не сохраняет exec-бит: после git clone на
# linux/андроиде «./install.sh» даёт Permission denied. Чиним права всем
# скриптам при каждом запуске (себе это не нужно — мы уже запущены через bash).
chmod +x ./*.sh 2>/dev/null || true

# ---------- 0. Самообновление из git (если это клон, а не zip-распаковка) ----------
# Лечит «error: Your local changes ... would be overwritten by merge»: старые
# ручные правки служебных файлов уводятся в git stash (вернуть: git stash pop),
# код тянется свежий, и установщик перезапускает сам себя новой версией.
# Без этого телефон молча запускал СТАРЫЙ установщик — и все фиксы до него
# не доезжали (случай 0.1.0.7).
if [ -d .git ] && command -v git >/dev/null 2>&1; then
    _OLD="$(git rev-parse HEAD 2>/dev/null || echo '')"
    git stash >/dev/null 2>&1 || true
    echo "Обновляю код (git pull)..."
    if git pull --ff-only >/dev/null 2>&1; then
        _NEW="$(git rev-parse HEAD 2>/dev/null || echo '')"
        if [ -n "$_OLD" ] && [ -n "$_NEW" ] && [ "$_OLD" != "$_NEW" ]; then
            echo "Код обновился — перезапускаю установщик свежей версией."
            exec bash "$0"
        fi
        echo "Обновление не требуется."
    else
        echo "(git pull не удался — нет сети? Продолжаю с текущими файлами.)"
    fi
fi

# ---------- 0.1 Termux? ----------
IS_TERMUX=false
if [ -n "$TERMUX_VERSION" ] || [ "${PREFIX#*com.termux}" != "$PREFIX" ]; then
    IS_TERMUX=true
fi

echo "============================================"
echo "  Fomo Timer Bot — установка"
echo "============================================"
echo
# Баннер версии: на скриншоте сразу видно, какой установщик запущен.
# Правило обновления: сначала git pull, потом bash install.sh.
_VER="$(grep -m1 '^APP_VERSION' config.py | cut -d '"' -f 2)"
echo "Версия установщика: ${_VER:-?} (config.py)."
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
    # pydantic-core (ядро pydantic, зависимость aiogram) — Rust-расширение:
    # на PyPI нет сборок под андроид, а сборка на телефоне падает (rustup не
    # умеет android-таргет, проверено в 0.1.0.5). Готовое колесо есть в зеркале
    # TUR PyPI (Termux User Repository, тег android_24_arm64_v8a), но ТОЛЬКО
    # для ядра 2.41.5. Свежий pydantic 2.13.x требует ядро 2.46.x, которого под
    # андроид нет: 0.1.0.6 ставил колесо 2.41.5, а следующий шаг
    # «pip install -r requirements.txt» затирал его pydantic 2.13.x — и установка
    # снова падала в сборку Rust. Поэтому ставим ЗАРАНЕЕ зафиксированную пару
    # pydantic 2.12.5 + pydantic-core 2.41.5 — тогда aiogram принимает готовое.
    echo "Termux: ставлю python и git..."
    pkg install -y python git
    # C-расширения веб-стека переводим в pure-python — компилятор не нужен:
    export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1
    export FROZENLIST_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
    pip install --upgrade pip >/dev/null 2>&1 \
        || echo "(pip остался прежним — Termux разрешает только свой pip, это не страшно)"
    if python -c "import pydantic, pydantic_core" 2>/dev/null; then
        echo "pydantic уже установлен — не трогаю."
    elif pip install --only-binary :all: --extra-index-url https://termux-user-repository.github.io/pypi/ "pydantic-core==2.41.5" \
        && pip install --only-binary :all: "pydantic==2.12.5"; then
        echo "Пара pydantic 2.12.5 + pydantic-core 2.41.5: готовые колёса, без компиляции."
    else
        echo "Зеркало недоступно — запасной путь: сборка на месте (10-25 минут, разовая)..."
        pkg install -y rust binutils
        command -v rustc >/dev/null 2>&1 || {
            echo "Rust не поставился. Часто это VPN/сеть: выключи или смени VPN и снова запусти bash install.sh."
            exit 1
        }
        pip install --only-binary :all: --extra-index-url https://termux-user-repository.github.io/pypi/ maturin \
            || cargo install --locked --no-default-features maturin \
            || cargo install --locked maturin
        export PATH="$HOME/.cargo/bin:$PATH"
        pip install --no-build-isolation "pydantic-core==2.41.5" "pydantic==2.12.5"
    fi
    if ! python -c "import pydantic, pydantic_core" 2>/dev/null; then
        echo "============================================"
        echo "pydantic-core установить не удалось."
        echo "1) Сначала git pull — вдруг установщик старый."
        echo "2) Выключи или смени VPN — зеркало TUR живёт на github.io."
        echo "3) Повтори: bash install.sh"
        echo "============================================"
        exit 1
    fi
    echo "Ставлю остальные зависимости (готовый pydantic aiogram примет без сборки Rust)..."
    if ! pip install -r requirements.txt; then
        echo "Не собралось — доставляю компилятор и пробую ещё раз..."
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
echo "Потом можно запускать командой: bash start.sh"
if $IS_TERMUX; then
    echo "На андроиде держите Termux открытым (или настройте автозапуск — см. TERMUX.md)."
fi
sleep 2
exec bash start.sh
