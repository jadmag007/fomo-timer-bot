"""Планировщик напоминаний (без aiogram — тестируется отдельно).

Каждую секунду смотрим в БД:
  * due_prewarn — «за час до конца осады аванпоста» (успеть отправить войска);
  * due_warn — «через минуту» (ТОЛЬКО если WARN_ENABLED=true; по умолчанию выключено);
  * due_done — время вышло, шлём «✅ готово» (в т.ч. догоняем после офлайна бота).
Таймеры живут в SQLite, поэтому перезапуск/падение бота ничего не теряет.

Тихий режим из мини-аппа (кнопка меню ☰): если группа таймера заглушена
(webapp_prefs.is_muted) — пуш НЕ отправляется вовсе, таймер тихо помечается
обработанным. Включение звука возвращает пуши для будущих таймеров; накопленное
по заглушенным группам не догоняется — всё видно в самом мини-аппе.
"""
import asyncio
import logging
import time

import config
import db
import util
import webapp_prefs

log = logging.getLogger("timers")


def warn_text(row, tz):
    return (
        "⏳ <b>Через ~1 минуту завершится:</b>\n\n"
        f"🏷 {row['label']}\n"
        f"🕐 финиш в {util.local_str(row['ends_at'], tz)}"
    )


def prewarn_text(row, tz):
    """Предупреждение за час до конца осады аванпоста.

    Остаток считаем в момент отправки: если бот был офлайн и опоздал,
    в сообщении будет честное «осталось Xмин», а не «час».
    """
    left = max(0, int(row["ends_at"] - time.time()))
    return (
        "🚩 <b>Осада аванпоста скоро закончится — пора отправлять войска!</b>\n\n"
        f"🏷 {row['label']}\n"
        f"⏳ осталось {util.fmt_delta(left)}\n"
        f"🕐 финиш в {util.local_str(row['ends_at'], tz)}"
    )


def done_text(row, tz):
    now = time.time()
    late = int(now - row["ends_at"])
    text = (
        "✅ <b>Готово!</b>\n\n"
        f"🏷 {row['label']}\n"
        f"🕐 {util.local_str(row['ends_at'], tz)}"
    )
    if late > 90:
        text += f"\n⚠️ Бот был офлайн — сообщение с опозданием {util.fmt_delta(late)}"
    return text


async def _send(bot, chat_id, text):
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:  # пользователь заблокировал бота, сеть и т.п.
        log.warning("Не доставлено chat_id=%s: %s", chat_id, e)


async def tick(bot, now=None):
    now = now if now is not None else time.time()

    for row in db.due_done(now):
        if webapp_prefs.is_muted(row["bucket"]):
            db.mark_done(row["id"])   # тихий режим: без пуша, но и без догонялок
            continue
        user = db.get_user(row["tg_id"])
        tz = util.safe_tz(user["tz"] if user else None)
        await _send(bot, row["chat_id"], done_text(row, tz))
        db.mark_done(row["id"])

    for row in db.due_prewarn(now):
        if webapp_prefs.is_muted(row["bucket"]):
            db.mark_prewarn(row["id"])
            continue
        user = db.get_user(row["tg_id"])
        tz = util.safe_tz(user["tz"] if user else None)
        await _send(bot, row["chat_id"], prewarn_text(row, tz))
        db.mark_prewarn(row["id"])

    if config.WARN_ENABLED:
        for row in db.due_warn(now):
            if webapp_prefs.is_muted(row["bucket"]):
                db.mark_warn(row["id"])
                continue
            user = db.get_user(row["tg_id"])
            tz = util.safe_tz(user["tz"] if user else None)
            await _send(bot, row["chat_id"], warn_text(row, tz))
            db.mark_warn(row["id"])


async def scheduler_loop(bot):
    log.info("Планировщик запущен (осады T-%sмин · T-1мин: %s)",
             config.SIEGE_PREWARN_SEC // 60,
             "вкл" if config.WARN_ENABLED else "выкл")
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("Ошибка в планировщике")
        await asyncio.sleep(1)
