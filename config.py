"""Конфигурация бота. Все настройки берутся из .env (см. .env.example)."""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

# .env живёт рядом с config.py (в корне папки бота) — путь детерминированный,
# не зависит от текущего каталога запуска.
_ENV_PATH = str(Path(__file__).resolve().parent / ".env")

load_dotenv(_ENV_PATH)

# --- Версия. ПРАВИЛО: бампается при КАЖДОМ изменении кода/документации ---
# 0.1.0.2 — локальный режим мини-аппа: страница работает в браузере на ПК с
# ботом без Telegram-подписи (спасение при error 1033, когда сеть режет туннель).
# 0.1.0.3 — Termux/Android: install.sh и start.sh понимают Termux (wake-lock,
# готовый aiohttp, без venv), tunnel.py качает arm64-сборку cloudflared,
# гайд TERMUX.md.
# 0.1.0.4 — фикс запуска после git clone на андроиде: git с Windows не хранит
# exec-бит -> «./install.sh: Permission denied». Везде bash install.sh /
# bash start.sh, install.sh сам чинит права .sh, в TERMUX.md вшит адрес
# репозитория.
# 0.1.0.5 — фикс установки зависимостей на Termux: в репо Termux НЕТ готовых
# aiohttp/pydantic, pydantic-core (Rust) не собирается без тулчейна (rustup
# не умеет android-таргет) -> install.sh ставит rust+binutils из репо Termux
# (умеет aarch64-linux-android), aiohttp в pure-python (AIOHTTP_NO_EXTENSIONS).
# 0.1.0.6 — Termux: pydantic-core ставится ГОТОВЫМ колесом android_24_arm64_v8a
# из зеркала TUR PyPI (--only-binary :all:) — без Rust и 10-25-минутной сборки,
# которые у 0.1.0.5 упали на телефоне (rustup не умеет android-таргет).
# Запасной путь: rust+binutils из репо Termux + maturin оттуда же +
# --no-build-isolation. В TERMUX.md — пошаговый перенос .env+data через свой
# git-репозиторий (сделать репо приватным, PAT, git add -f, git pull).
# 0.1.0.7 — Termux, найден главный виновник повторных падений: колесо ядра
# 2.41.5 вставало, но следующий шаг (pip install -r requirements.txt) ставил
# СВЕЖИЙ pydantic 2.13.x, а ему нужно ядро 2.46.x — готовой сборки под андроид
# нет, и pip снова падал в сборку Rust. Теперь ПАРА pydantic 2.12.5 +
# pydantic-core 2.41.5 (единственное ядро в TUR для cp313/cp314) ставится
# заодно и ДО aiogram; плюс pure-python флаги yarl/multidict/frozenlist/
# propcache, пропуск шага если pydantic уже стоит, баннер версии установщика
# и внятный совет (git pull / VPN) с exit 1 вместо тихого продолжения.
APP_VERSION = "0.1.0.7"

# --- Основное ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Moscow").strip()
DB_PATH = os.getenv("DB_PATH", "data/fomo_timers.db").strip()

# --- Автотрекинг через API игры (заполняется само, руками — только флаги) ---
API_ENABLED = os.getenv("API_ENABLED", "false").strip().lower() == "true"
# Полный адрес(а) эндпоинта состояния (можно несколько — через запятую)
API_STATE_URL = os.getenv("API_STATE_URL", "").strip()
API_AUTH_HEADER = os.getenv("API_AUTH_HEADER", "").strip()  # напр.: Authorization: Bearer eyJhbGci...
# Метод и тело запроса (для POST-эндпоинтов со подписями — как у Fomo Fighters)
API_METHOD = (os.getenv("API_METHOD", "GET").strip().upper() or "GET")
API_BODY = os.getenv("API_BODY", "").strip()
# Дополнительные заголовки JSON-словарём (подписи api-* и т.п.), напр.:
#   {"api-key": "…", "api-hash": "…", "api-time": "…", "api-version": "…"}
API_HEADERS_JSON = os.getenv("API_HEADERS_JSON", "").strip()
API_POLL_INTERVAL = int(os.getenv("API_POLL_INTERVAL", "45"))
# Ваш tg_id для автотрекинга (узнать: @userinfobot). Пусто — берётся первый /start боту.
API_OWNER_TG_ID = int(os.getenv("API_OWNER_TG_ID", "0") or 0)
# Спрашивать «Да/Нет» перед добавлением найденных таймеров. По умолчанию НЕТ —
# ставим молча; переключить: команда /вопросы или кнопка на экране /апи
API_ASK_BEFORE_ADD = os.getenv("API_ASK_BEFORE_ADD", "false").strip().lower() == "true"
# Трассировка сырых ответов API в data/trace.log (поиск новых типов таймеров).
# Переключается в боте: /трассировка, файл: /трейслог
API_TRACE = os.getenv("API_TRACE", "false").strip().lower() == "true"
# Папка, куда пользователь кидает .har / fomo.txt — бот сам разберёт и обновится
TOKEN_UPDATES_DIR = "token_updates"

# --- Нативный режим Fomo Fighters (бот сам подписывает и сам чинит ключ) ---
# initData мини-аппа (urlencoded, из /telegram/auth в fomo.txt или от юзербота).
# Есть это значение — подписи из HAR больше не нужны: всё считается само.
FOMO_INIT_DATA = os.getenv("FOMO_INIT_DATA", "").strip()
FOMO_API_BASE = os.getenv("FOMO_API_BASE", "https://api.fomofighters.xyz").strip().rstrip("/")
FOMO_GAME_BOT = os.getenv("FOMO_GAME_BOT", "fomo_fighters_bot").strip()
FOMO_APP_NAME = os.getenv("FOMO_APP_NAME", "game").strip()
FOMO_LANG = os.getenv("FOMO_LANG", "ru").strip()
FOMO_WEB_ORIGIN = os.getenv("FOMO_WEB_ORIGIN", "https://game.fomofighters.xyz").strip().rstrip("/")
# Превентивная реанимация ключа (auth), секунд
FOMO_REAUTH_INTERVAL = int(os.getenv("FOMO_REAUTH_INTERVAL", "21600") or 21600)
# Как часто опрашивать /user/data/all (клановые сундуки, награды аванпостов),
# секунд. Лёгкий /user/data/timers ходит по API_POLL_INTERVAL.
FOMO_ALL_INTERVAL = int(os.getenv("FOMO_ALL_INTERVAL", "300") or 300)
# За сколько секунд до конца ОСАДЫ АВАНПОСТА прислать отдельное предупреждение
# («успейте отправить войска»). По умолчанию — за час.
SIEGE_PREWARN_SEC = int(os.getenv("SIEGE_PREWARN_SEC", "3600") or 3600)

# --- Мини-приложение в боте (кнопка меню слева от поля ввода) ---
# Веб-страница со всеми таймерами сразу и кнопками управления (тихий режим
# по группам, отмена таймера, обновление). Уведомления работают как раньше.
# Для открытия из Telegram нужен публичный HTTPS-адрес: бот сам поднимает
# бесплатный туннель cloudflared (при первом запуске скачает ~20 МБ).
# URL туннеля меняется при каждом рестарте — кнопка меню обновляется сама.
WEBAPP_ENABLED = os.getenv("WEBAPP_ENABLED", "true").strip().lower() == "true"
# Локальный порт веб-сервера (слушает только 127.0.0.1). Если занят — берёт
# следующий свободный.
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080") or 8080)
# Свой публичный HTTPS-адрес (свой туннель/VPS). Пусто — бот поднимает
# cloudflared сам. Пример: https://my-timer.example.com
WEBAPP_PUBLIC_URL = os.getenv("WEBAPP_PUBLIC_URL", "").strip().rstrip("/")
# Транспорт туннеля: auto (по очереди http2/TCP и quic/UDP), http2 или quic.
# Если провайдер режет только один из портов 7844 — зафиксируйте рабочий.
TUNNEL_PROTOCOL = os.getenv("WEBAPP_TUNNEL_PROTOCOL", "auto").strip().lower()
# ЛОКАЛЬНЫЙ режим мини-аппа: страница http://127.0.0.1:PORT открывается в
# обычном браузере НА КОМПЬЮТЕРЕ С БОТОМ без Telegram-подписи (доступ как у
# владельца). Спасение, когда сеть режет туннель (Cloudflare error 1033).
# Безопасно: сервер слушает только 127.0.0.1, а у запросов из интернета через
# туннель всегда есть служебные заголовки Cloudflare — они отсекаются.
WEBAPP_LOCAL_DEBUG = os.getenv("WEBAPP_LOCAL_DEBUG", "true").strip().lower() == "true"

# --- Юзербот (свежая initData автоматически, логин один раз через login_bot.bat) ---
# По умолчанию — общедоступная пара Telegram Desktop; можно вписать свою из
# my.telegram.org -> API development tools
USERBOT_API_ID = int(os.getenv("USERBOT_API_ID", "6") or 6)
USERBOT_API_HASH = os.getenv("USERBOT_API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
USERBOT_SESSION_PATH = os.getenv("USERBOT_SESSION_PATH", "userbot.session").strip()

# --- Поведение напоминаний ---
# T-1мин выключен выбором «Только T-0»; при желании включите в .env (WARN_ENABLED=true)
WARN_ENABLED = os.getenv("WARN_ENABLED", "false").strip().lower() == "true"
WARN_BEFORE_SEC = 60
WARN_MIN_DURATION = 180

# --- Ограничения таймеров ---
MAX_TIMER_SEC = 10 * 24 * 3600   # 10 суток сверху
MIN_TIMER_SEC = 10               # снизу

# --- Кнопки быстрого таймера (секунды) ---
QUICK_PRESETS = [300, 900, 1800, 2700, 3600, 7200, 14400, 28800, 43200, 86400]


# ---------- Работа с .env ----------

def env_get(key, default="", env_path=None) -> str:
    """Прочитать значение ключа прямо из файла .env, минуя память процесса.

    Нужен, когда .env обновляется извне (login_userbot.py сохранил личные
    api-ключи), а работающий процесс ещё не перечитывал конфиг.
    Путь по умолчанию — .env РЯДОМ С config.py (не зависит от CWD: раньше
    запуск не из папки проекта молча читал чужой/несуществующий файл).
    """
    try:
        p = Path(env_path) if env_path else Path(_ENV_PATH)
        if not p.exists():
            return default
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    except Exception:
        pass
    return default


def _update_env_keys(updates: dict, env_path=".env") -> bool:
    """Записать значения ключей в .env, сохранив остальные строки и комментарии.

    Файла нет -> создаётся из .env.example (если есть). Отсутствующие ключи
    дописываются в конец. Возвращает True при успехе.
    """
    try:
        env = Path(env_path)
        if not env.exists():
            example = env.parent / ".env.example"
            if example.exists():
                shutil.copyfile(example, env)
        lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
        pending = {str(k): str(v) for k, v in updates.items()}
        out = []
        for line in lines:
            stripped = line.strip()
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in pending and stripped and not stripped.startswith("#"):
                out.append(f"{key}={pending.pop(key)}")
            else:
                out.append(line)
        for key, val in pending.items():
            out.append(f"{key}={val}")
        env.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def set_fomo_init_data(value: str, env_path=".env") -> bool:
    """Сохранить свежую initData в .env (юзербот добыл новую — запомним)."""
    global FOMO_INIT_DATA
    FOMO_INIT_DATA = (value or "").strip()
    return _update_env_keys({"FOMO_INIT_DATA": FOMO_INIT_DATA}, env_path)


def set_userbot_api(api_id, api_hash, env_path=".env") -> bool:
    """Сохранить личные api_id/api_hash юзербота в .env (после 403 RECAPTCHA
    login_userbot.py вызывает это сам)."""
    global USERBOT_API_ID, USERBOT_API_HASH
    try:
        USERBOT_API_ID = int(str(api_id).strip())
    except (ValueError, TypeError):
        return False
    USERBOT_API_HASH = (api_hash or "").strip()
    return _update_env_keys({"USERBOT_API_ID": str(USERBOT_API_ID),
                             "USERBOT_API_HASH": USERBOT_API_HASH}, env_path)


def set_ask_before_add(value: bool, env_path=".env") -> bool:
    """Переключить режим подтверждения: True — список с кнопками Да/Нет,
    False — ставить молча. Пишет в .env (команда /вопросы)."""
    global API_ASK_BEFORE_ADD
    API_ASK_BEFORE_ADD = bool(value)
    return _update_env_keys({"API_ASK_BEFORE_ADD": "true" if API_ASK_BEFORE_ADD else "false"},
                            env_path)


def set_trace(value: bool, env_path=".env") -> bool:
    """Вкл/выкл трассировку сырых ответов API (data/trace.log). /трассировка."""
    global API_TRACE
    API_TRACE = bool(value)
    return _update_env_keys({"API_TRACE": "true" if API_TRACE else "false"}, env_path)


def reload():
    """Перечитать .env с диска (watcher обновил файл — подтягиваем без рестарта).

    load_dotenv(override=True) заново читает файл и перезаписывает os.environ,
    после чего os.getenv ниже отдаёт свежие значения. Без этого reload читал бы
    только память процесса и «горячее» обновление не работало бы вовсе.
    """
    global API_ENABLED, API_STATE_URL, API_AUTH_HEADER, API_POLL_INTERVAL, API_OWNER_TG_ID
    global API_ASK_BEFORE_ADD, API_METHOD, API_BODY, API_HEADERS_JSON, API_TRACE
    global FOMO_INIT_DATA, FOMO_API_BASE, FOMO_GAME_BOT, FOMO_APP_NAME, FOMO_LANG, FOMO_REAUTH_INTERVAL
    global FOMO_WEB_ORIGIN, USERBOT_API_ID, USERBOT_API_HASH, USERBOT_SESSION_PATH
    global FOMO_ALL_INTERVAL, SIEGE_PREWARN_SEC  # без этого reload писал бы в локальную переменную
    global WEBAPP_ENABLED, WEBAPP_PORT, WEBAPP_PUBLIC_URL, TUNNEL_PROTOCOL
    global WEBAPP_LOCAL_DEBUG
    try:
        load_dotenv(_ENV_PATH, override=True)
    except Exception:
        pass
    API_ENABLED = os.getenv("API_ENABLED", "false").strip().lower() == "true"
    API_ASK_BEFORE_ADD = os.getenv("API_ASK_BEFORE_ADD", "false").strip().lower() == "true"
    API_TRACE = os.getenv("API_TRACE", "false").strip().lower() == "true"
    API_STATE_URL = os.getenv("API_STATE_URL", "").strip()
    API_AUTH_HEADER = os.getenv("API_AUTH_HEADER", "").strip()
    API_METHOD = (os.getenv("API_METHOD", "GET").strip().upper() or "GET")
    API_BODY = os.getenv("API_BODY", "").strip()
    API_HEADERS_JSON = os.getenv("API_HEADERS_JSON", "").strip()
    try:
        API_POLL_INTERVAL = int(os.getenv("API_POLL_INTERVAL", "45"))
    except ValueError:
        API_POLL_INTERVAL = 45
    try:
        API_OWNER_TG_ID = int(os.getenv("API_OWNER_TG_ID", "0") or 0)
    except ValueError:
        API_OWNER_TG_ID = 0
    FOMO_INIT_DATA = os.getenv("FOMO_INIT_DATA", "").strip()
    FOMO_API_BASE = os.getenv("FOMO_API_BASE", "https://api.fomofighters.xyz").strip().rstrip("/")
    FOMO_GAME_BOT = os.getenv("FOMO_GAME_BOT", "fomo_fighters_bot").strip()
    FOMO_APP_NAME = os.getenv("FOMO_APP_NAME", "game").strip()
    FOMO_LANG = os.getenv("FOMO_LANG", "ru").strip()
    FOMO_WEB_ORIGIN = os.getenv("FOMO_WEB_ORIGIN", "https://game.fomofighters.xyz").strip().rstrip("/")
    try:
        FOMO_REAUTH_INTERVAL = int(os.getenv("FOMO_REAUTH_INTERVAL", "21600") or 21600)
    except ValueError:
        FOMO_REAUTH_INTERVAL = 21600
    try:
        FOMO_ALL_INTERVAL = int(os.getenv("FOMO_ALL_INTERVAL", "300") or 300)
    except ValueError:
        FOMO_ALL_INTERVAL = 300
    try:
        SIEGE_PREWARN_SEC = int(os.getenv("SIEGE_PREWARN_SEC", "3600") or 3600)
    except ValueError:
        SIEGE_PREWARN_SEC = 3600
    WEBAPP_ENABLED = os.getenv("WEBAPP_ENABLED", "true").strip().lower() == "true"
    try:
        WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080") or 8080)
    except ValueError:
        WEBAPP_PORT = 8080
    WEBAPP_PUBLIC_URL = os.getenv("WEBAPP_PUBLIC_URL", "").strip().rstrip("/")
    TUNNEL_PROTOCOL = os.getenv("WEBAPP_TUNNEL_PROTOCOL", "auto").strip().lower()
    WEBAPP_LOCAL_DEBUG = os.getenv("WEBAPP_LOCAL_DEBUG", "true").strip().lower() == "true"
    try:
        USERBOT_API_ID = int(os.getenv("USERBOT_API_ID", "6") or 6)
    except ValueError:
        USERBOT_API_ID = 6
    USERBOT_API_HASH = os.getenv("USERBOT_API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
    USERBOT_SESSION_PATH = os.getenv("USERBOT_SESSION_PATH", "userbot.session").strip()
