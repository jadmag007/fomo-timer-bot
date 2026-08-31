"""sched_push.py — отложенные пуши силами СЕРВЕРОВ Telegram (MTProto schedule).

Зачем: обычный пуш шлёт сам бот — бот выключен или телефон спит, пуши ждут
его запуска. Запланированное сообщение живёт НА СЕРВЕРАХ Telegram: бот через
юзербота (telethon) отдаёт его с schedule= на момент финиша таймера, и
Telegram доставит его в срок даже при выключенном боте.

КУДА: в чат с самим ботом (@myftimer_bot — имя бот записывает в .env при
старте, BOT_USERNAME). Сообщение появится там вовремя, в правильном
контексте, рядом с ответами бота.

ЧЕСТНО ПРО УВЕДОМЛЕНИЯ: юзербот — это ВАШ собственный аккаунт, поэтому
отложка всегда пишется «от вас». Telegram НИКОГДА не уведомляет о своих
собственных сообщениях — в любом чате, включая чат с ботом (звук/вибрация
бывают только от другой стороны: онлайн-бота, людей, каналов). Отложка —
это вовремя и наглядно; а УВЕДОМЛЕНИЕ приносит сам бот: обычные пуши, пока
он работает, и «догоняющий» пуш, когда вернулся (таймеры живут в SQLite,
ничего не теряется).

Дубли: когда бот онлайн и сам доставляет пуш, запланированный дубль
снимается (cancel_for). Пауза: cancel_all() снимает всё наше запланированное,
после возобновления reschedule_unfinished() распланирует недоставленное
заново. Если бот не смог доставить пуш долго (сеть лежала), запланированное
СОЗНАТЕЛЬНО остаётся — оно и есть страховка.

Маркер: текст начинается с «⏰ Готово: » — только такие сообщения трогают
cancel_*; если у вас есть личные отложенные сообщения в чате с ботом, они
не пострадают.

Включение: USERBOT_SCHEDULE=true (по умолчанию включено) и живая сессия
userbot.session (login_bot.bat). Нет сессии или имени бота — функции тихо
возвращают неуспех, бот работает как раньше.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import config

log = logging.getLogger("schedpush")

MARK = "⏰ Готово: "            # маркер наших запланированных сообщений
MIN_DELAY = 45.0                # раньше чем через 45 с — Telegram всё равно не даст
MAX_DELAY = 360 * 24 * 3600.0   # дальше года вперёд не планируем


def session_path() -> str:
    """Путь к файлу сессии юзербота (с расширением .session)."""
    p = config.USERBOT_SESSION_PATH
    if p.endswith(".session"):
        p = p[:-len(".session")]
    return p + ".session"


def peer_name() -> str | None:
    """Куда планировать: чат с ботом. None — бот ещё не записал своё имя."""
    u = (getattr(config, "BOT_USERNAME", "") or "").strip()
    return f"@{u}" if u else None


def available() -> bool:
    """Есть ли смысл пробовать: флаг включён и файл сессии на месте."""
    if not config.USERBOT_SCHEDULE:
        return False
    return os.path.exists(session_path())


async def _with_client(fn):
    """Открыть юзербота, выполнить fn(client, peer), закрыть. None при неудаче."""
    peer = peer_name()
    if not peer:
        log.debug("BOT_USERNAME не записан (бот ещё не стартовал) — отложенного "
                  "пуша не будет")
        return None
    try:
        from telethon import TelegramClient
        from userbot import _userbot_creds
        spath = session_path()[:-len(".session")]
        api_id, api_hash = _userbot_creds()
        client = TelegramClient(spath, api_id, api_hash,
                                device_model="FomoTimerBot", system_version="Windows",
                                app_version="1.0")
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log.warning("Сессия юзербота не авторизована — отложенный пуш недоступен")
                return None
            entity = await client.get_entity(peer)
            return await fn(client, entity)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
    except Exception as e:
        log.warning("Отложенный пуш: %s: %s", type(e).__name__, e)
        return None


async def _our_scheduled(client, entity):
    """Наши запланированные сообщения в чате с ботом (строго по маркеру)."""
    from telethon.tl.functions.messages import GetScheduledHistoryRequest
    hist = await client(GetScheduledHistoryRequest(peer=entity, hash=0))
    return [m for m in getattr(hist, "messages", [])
            if (getattr(m, "message", "") or "").startswith(MARK)]


async def schedule(label, ends_at) -> bool:
    """Запланировать «⏰ Готово: label» на момент ends_at. -> True, если вышло."""
    delay = float(ends_at) - time.time()
    if delay < MIN_DELAY or delay > MAX_DELAY:
        return False
    if not available():
        return False

    async def _go(client, entity):
        when = datetime.fromtimestamp(float(ends_at), tz=timezone.utc)
        await client.send_message(entity, MARK + str(label),
                                  schedule=when, parse_mode=None)
        return True

    ok = await _with_client(_go)
    if ok:
        log.info("Отложенный пуш запланирован в чат с ботом: %r (финиш через %s мин)",
                 label, round(delay / 60))
    return bool(ok)


async def _delete(client, entity, msgs) -> int:
    from telethon.tl.functions.messages import DeleteScheduledMessagesRequest
    await client(DeleteScheduledMessagesRequest(peer=entity, id=[m.id for m in msgs]))
    return len(msgs)


async def cancel_for(label) -> int:
    """Бот доставил свой пуш — снять запланированный дубль. -> сколько снято."""
    if not available():
        return 0
    frag = MARK + str(label)

    async def _go(client, entity):
        msgs = [m for m in await _our_scheduled(client, entity)
                if (m.message or "") == frag]
        return await _delete(client, entity, msgs) if msgs else 0

    return int(await _with_client(_go) or 0)


async def cancel_all() -> int:
    """Пауза: снять ВСЁ запланированное НАМИ (чужие сообщения не трогаем)."""
    if not available():
        return 0

    async def _go(client, entity):
        msgs = await _our_scheduled(client, entity)
        return await _delete(client, entity, msgs) if msgs else 0

    n = int(await _with_client(_go) or 0)
    if n:
        log.info("Пауза: снято отложенных пушей: %s", n)
    return n


async def reschedule_unfinished() -> int:
    """После возобновления: распланировать заново все недоставленные таймеры."""
    import db
    now = time.time()
    n = 0
    for row in db.unfinished():
        if float(row["ends_at"]) > now + MIN_DELAY:
            if await schedule(row["label"], row["ends_at"]):
                n += 1
    if n:
        log.info("Возобновление: отложенных пушей распланировано: %s", n)
    return n


def kick_schedule(label, ends_at) -> None:
    """Запланировать в фоне (fire-and-forget). Безопасно из sync-кода и тестов:
    нет живого цикла событий — просто ничего не делает."""
    try:
        asyncio.get_running_loop().create_task(schedule(label, ends_at))
    except RuntimeError:
        pass
