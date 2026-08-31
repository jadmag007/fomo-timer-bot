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

# ---------- Автообновление из GitHub (ТИХО — ни одного запроса логина) ----------
# Старт бота НЕ требует GitHub: не получилось обновиться (нет сети, нет
# токена, репо приватное) — молча запускаем то, что установлено. Логин и
# пароль git здесь не спросит НИКОГДА: GIT_TERMINAL_PROMPT=0 запрещает
# интерактивный ввод, GIT_ASKPASS=echo страхует его же. Авторизация —
# только токеном GITHUB_TOKEN из .env (тем же, каким публикует
# github_push.sh). Пароль аккаунта GitHub git всё равно не принимает
# с 2021 года (только PAT) — спрашивать его бессмысленно.
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    GTOKEN=""
    if [ -f .env ]; then
        GTOKEN="$(grep -E '^GITHUB_TOKEN=' .env | head -n 1 | cut -d= -f2- | tr -d ' "' | tr -d "'")"
    fi
    GOPT=(-c http.version=HTTP/1.1)
    if [ -n "$GTOKEN" ]; then
        GOPT+=(-c "credential.helper=!f() { echo username=x-access-token; echo password=$GTOKEN; }; f")
    fi
    FETCHED=0
    if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=echo git "${GOPT[@]}" fetch origin main >/dev/null 2>&1; then
        FETCHED=1
    else
        # Репо ПУБЛИЧНЫЙ, но сохранённые креды могут быть испорчены (например,
        # в credential.helper store когда-то попал пароль вместо PAT) — GitHub
        # отвечает 401 даже на публичный fetch. Повторяем ПОЛНОСТЬЮ анонимно:
        # пустой credential.helper= сбрасывает ВСЕ хелперы (глобальные тоже).
        if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=echo git -c credential.helper= -c http.version=HTTP/1.1 fetch origin main >/dev/null 2>&1; then
            FETCHED=1
            GOPT=(-c http.version=HTTP/1.1 -c credential.helper=)
        fi
    fi
    if [ "$FETCHED" -eq 1 ]; then
        LOCALREV=$(git rev-parse HEAD 2>/dev/null)
        REMOTEREV=$(git rev-parse origin/main 2>/dev/null)
        if [ -n "$LOCALREV" ] && [ -n "$REMOTEREV" ] && [ "$LOCALREV" != "$REMOTEREV" ]; then
            echo "GitHub: есть обновление — ставлю. Правки служебных файлов уйдут в"
            echo "stash (возврат: git stash pop); личные .env и data/ не трогаются."
            git stash >/dev/null 2>&1
            if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=echo git "${GOPT[@]}" pull --ff-only origin main >/dev/null 2>&1; then
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
