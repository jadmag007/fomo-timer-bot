"""ВАРИАНТ 2 — автотрекинг таймеров через API игры (заготовка).

Идея: мини-апп Fomo Fighters — это веб-приложение. Его трафик видно в
Telegram Desktop (правый клик по окну игры → Inspect → вкладка Network).
Экспортировав HAR и прогнав его через tools/har_inspect.py, находим
эндпоинт состояния города и токен авторизации. После этого бот сам
замечает запущенные улучшения и ставит таймеры без ручного ввода.

Включается в .env (значения берутся из анализа HAR):
    API_ENABLED=true
    API_BASE_URL=https://…            # базовый адрес API игры
    API_AUTH_HEADER=Authorization: Bearer eyJ…   # строка заголовка целиком
    API_POLL_INTERVAL=45              # сек между опросами (не меньше 30!)
    API_OWNER_TG_ID=123456789         # ваш tg_id (@userinfobot)

Пока API_ENABLED=false — модуль ничего не делает, бот работает в ручном режиме.
Пошаговая инструкция — README.md, раздел «Автотрекинг».
"""
import asyncio
import json
import logging

import aiohttp

import config
import db

log = logging.getLogger("api_poller")

# Память о уже поставленных по API таймерах: {(label, ends_at), ...}
_SEEN = set()


def auth_headers():
    """"Authorization: Bearer eyJ..." -> dict для aiohttp."""
    line = config.API_AUTH_HEADER
    name, _, value = line.partition(":")
    if value:
        return {name.strip(): value.strip()}
    return {"Authorization": line.strip()}


async def fetch_state(session):
    """GET-запрос к API игры. TODO(В2): путь эндпоинта — из har_report.txt."""
    url = config.API_BASE_URL + "/TODO_city_state_endpoint"
    async with session.get(
        url, headers=auth_headers(), timeout=aiohttp.ClientTimeout(total=20)
    ) as resp:
        resp.raise_for_status()
        text = await resp.text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.error("Ответ не JSON (проверьте API_BASE_URL): %.200s", text)
        return None


def extract_upgrades(state_json):
    """TODO(В2): вытащить из JSON список улучшений (label, ends_at).

    Заполняется по вашему реальному har_report.txt (шаг 3 в README).
    Что искать в ответе:
      * поля finished_at / ends_at / complete_at / upgrade_end — обычно
        unix-секунды, иногда миллисекунды или ISO-строка;
      * либо remaining / time_left (секунды): ends_at = time.time() + remaining.
    Вернуть список: [{"label": "Лесопилка 11 → 12", "ends_at": 1756277000.0}, …]
    """
    raise NotImplementedError(
        "Заполните extract_upgrades() по har_report.txt (README → «Автотрекинг, шаг 3»)"
    )


def owner():
    """Кому ставить автотаймеры: из .env (API_OWNER_TG_ID) или первый /start."""
    if config.API_OWNER_TG_ID:
        return db.get_user(config.API_OWNER_TG_ID)
    rows = db.first_user()
    return rows


async def poll_once():
    """Один опрос API: новые улучшения -> таймеры в БД (уведомит планировщик)."""
    async with aiohttp.ClientSession() as session:
        state = await fetch_state(session)
    if not state:
        return 0

    added = 0
    for up in extract_upgrades(state) or []:
        label, ends_at = up["label"], float(up["ends_at"])
        key = (label, round(ends_at))
        if key in _SEEN:
            continue
        user = owner()
        if not user:
            log.warning("API: некому ставить таймер — откройте бота (/start) или "
                        "заполните API_OWNER_TG_ID в .env")
            continue
        db.add_timer(user["tg_id"], user["tg_id"], label, ends_at)
        _SEEN.add(key)
        added += 1
        log.info("API: добавлен таймер %r -> %s", label, ends_at)
    return added


async def poll_forever(_bot=None):
    if not config.API_ENABLED:
        log.info("API-автотрекинг выключен (API_ENABLED=false) — ручной режим.")
        return
    if not config.API_BASE_URL or not config.API_AUTH_HEADER:
        log.error("API_ENABLED=true, но в .env пусты API_BASE_URL/API_AUTH_HEADER — "
                  "автотрекинг не запущен.")
        return
    log.info("API-автотрекинг запущен (интервал %ss)", config.API_POLL_INTERVAL)
    while True:
        try:
            await poll_once()
        except NotImplementedError:
            log.error("extract_upgrades() не заполнен — пришлите har_report.txt "
                      "(README → «Автотрекинг, шаг 3»). Останавливаю опрос.")
            return
        except Exception:
            log.exception("Ошибка опроса API (продолжаю по расписанию)")
        await asyncio.sleep(max(20, config.API_POLL_INTERVAL))
