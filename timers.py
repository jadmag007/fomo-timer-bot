"""Планировщик напоминаний (без жёсткой зависимости от aiogram — тестируется отдельно).

Каждую секунду смотрим в БД:
  * due_prewarn — «за час до конца осады аванпоста» (успеть отправить войска);
  * due_warn — «через минуту» (ТОЛЬКО если WARN_ENABLED=true; по умолчанию выключено);
  * due_done — время вышло, шлём «✅ готово» (в т.ч. догоняем после офлайна бота).
Таймеры живут в SQLite, поэтому перезапуск/падение бота ничего не теряет.

ДОСТАВКА (с 0.1.0-alpha): если Telegram недоступен (сеть легла, провайдер
штормит), пуш больше НЕ теряется — таймер остаётся непомеченным и досылается
в ближайший тик, пока не пройдёт RETRY_*_SEC. Раньше сбой проглатывался
и таймер молча закрывался без уведомления.

Пауза (кнопка «⏸ Пауза» в меню, pause_state.py): пуши не отправляются вовсе,
завершившиеся таймеры попадают в список пропущенного — при снятии паузы
приходит одна сводка. Таймеры при этом продолжают ставиться.

Тихий режим со страницы таймеров (в браузере, /app): если группа таймера
заглушена (webapp_prefs.is_muted) — пуш НЕ отправляется вовсе, таймер тихо
помечается обработанным. Включение звука возвращает пуши для будущих таймеров;
накопленное по заглушенным группам не догоняется — всё видно на самой странице.
"""
import asyncio
import logging
import time

import config
import db
import pause_state
import sched_push
import util
import webapp_prefs

try:
    from aiogram.exceptions import TelegramForbiddenError
except Exception:  # тесты/окружение без aiogram
    class TelegramForbiddenError(Exception):
        pass

log = logging.getLogger("timers")

# Сколько секунд после финиша ещё имеет смысл досылать недоставленный пуш:
# сеть была недоступна, но вернулась — бот должен догнать уведомление.
# По истечении окна таймер закрывается, чтобы не копить хвост из мёртвых пушей.
DONE_RETRY_SEC = 600      # «✅ Готово» — 10 минут
WARN_RETRY_SEC = 180      # «через минуту» — смысла слать позже нет
PREWARN_RETRY_SEC = 900   # «за час до осады» — 15 минут

# Небольшой backoff между попытками (сек), чтобы не долбить мёртвую сеть
# каждый тик: {timer_id: unix_время_следующей_попытки}
_RETRY_AT = {}


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


async def _send(bot, chat_id, text) -> bool:
    """Доставить сообщение. False = сеть/сервис недоступны, стоит повторить.

    Исключение составляют «перманентные» отказы (пользователь заблокировал
    бота): повторять бессмысленно — считаем доставленным.
    """
    try:
        await bot.send_message(chat_id, text)
        return True
    except TelegramForbiddenError:
        return True
    except Exception as e:
        log.warning("Не доставлено chat_id=%s: %s — повторю позже", chat_id, e)
        return False


def _can_retry(row_id, now):
    if _RETRY_AT.get(row_id, 0) > now:
        return False
    _RETRY_AT[row_id] = now + 10  # следующая попытка не раньше чем через 10 с
    return True


def _retry_done(row_id):
    _RETRY_AT.pop(row_id, None)


async def tick(bot, now=None):
    now = now if now is not None else time.time()
    paused = pause_state.is_paused()

    for row in db.due_done(now):
        if paused:
            db.mark_done(row["id"])
            pause_state.record_missed(row["label"], row["ends_at"])
            await sched_push.cancel_for(row["label"])  # запланированный дубль снимаем
            continue
        if webapp_prefs.is_muted(row["bucket"]):
            db.mark_done(row["id"])   # тихий режим: без пуша, но и без догонялок
            await sched_push.cancel_for(row["label"])
            continue
        user = db.get_user(row["tg_id"])
        tz = util.safe_tz(user["tz"] if user else None)
        if not _can_retry(row["id"], now):
            continue
        if await _send(bot, row["chat_id"], done_text(row, tz)):
            db.mark_done(row["id"])
            _retry_done(row["id"])
            # Свой пуш доставлен — запланированный на серверах дубль больше не нужен.
            # (Если доставить не удалось, запланированное СОХРАНЯЕМ: оно и есть страховка.)
            await sched_push.cancel_for(row["label"])
        elif now - row["ends_at"] > DONE_RETRY_SEC:
            # сеть лежит слишком долго — закрываем, чтобы не копить хвост
            db.mark_done(row["id"])
            _retry_done(row["id"])
            log.warning("Пуш %r не доставлен за %s с — закрываю без уведомления",
                        row["label"], DONE_RETRY_SEC)

    for row in db.due_prewarn(now):
        if paused:
            db.mark_prewarn(row["id"])
            pause_state.record_missed(row["label"], row["ends_at"])
            continue
        if webapp_prefs.is_muted(row["bucket"]):
            db.mark_prewarn(row["id"])
            continue
        user = db.get_user(row["tg_id"])
        tz = util.safe_tz(user["tz"] if user else None)
        if not _can_retry("p%s" % row["id"], now):
            continue
        if await _send(bot, row["chat_id"], prewarn_text(row, tz)):
            db.mark_prewarn(row["id"])
            _retry_done("p%s" % row["id"])
        elif now - row["ends_at"] > PREWARN_RETRY_SEC:
            db.mark_prewarn(row["id"])
            _retry_done("p%s" % row["id"])

    if config.WARN_ENABLED:
        for row in db.due_warn(now):
            if paused:
                db.mark_warn(row["id"])
                pause_state.record_missed(row["label"], row["ends_at"])
                continue
            if webapp_prefs.is_muted(row["bucket"]):
                db.mark_warn(row["id"])
                continue
            user = db.get_user(row["tg_id"])
            tz = util.safe_tz(user["tz"] if user else None)
            if not _can_retry("w%s" % row["id"], now):
                continue
            if await _send(bot, row["chat_id"], warn_text(row, tz)):
                db.mark_warn(row["id"])
                _retry_done("w%s" % row["id"])
            elif now - row["ends_at"] > WARN_RETRY_SEC:
                db.mark_warn(row["id"])
                _retry_done("w%s" % row["id"])


async def scheduler_loop(bot):
    log.info("Планировщик запущен (осады T-%sмин · T-1мин: %s)",
             config.SIEGE_PREWARN_SEC // 60,
             "вкл" if config.WARN_ENABLED else "выкл")
    last_cleanup = time.time()   # раз в сутки выметаем старые закрытые таймеры
    while True:
        try:
            await tick(bot)
            if time.time() - last_cleanup > 86400:
                last_cleanup = time.time()
                removed = db.cleanup_old(30)   # БД не должна расти вечно
                if removed:
                    log.info("Чистка БД: удалено закрытых таймеров старше 30 суток: %s",
                             removed)
        except Exception:
            log.exception("Ошибка в планировщике")
        await asyncio.sleep(1)
