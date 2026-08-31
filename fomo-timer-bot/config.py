"""Конфигурация бота. Все настройки берутся из .env (см. .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Основное ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Moscow").strip()
DB_PATH = os.getenv("DB_PATH", "data/fomo_timers.db").strip()

# --- Вариант 2: автотрекинг через API игры (пока выключен) ---
API_ENABLED = os.getenv("API_ENABLED", "false").strip().lower() == "true"
API_BASE_URL = os.getenv("API_BASE_URL", "").strip().rstrip("/")
API_AUTH_HEADER = os.getenv("API_AUTH_HEADER", "").strip()  # напр.: Authorization: Bearer eyJhbGci...
API_POLL_INTERVAL = int(os.getenv("API_POLL_INTERVAL", "45"))
# Ваш tg_id для автотрекинга (узнать: @userinfobot). Пусто — берётся первый /start боту.
API_OWNER_TG_ID = int(os.getenv("API_OWNER_TG_ID", "0") or 0)

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
