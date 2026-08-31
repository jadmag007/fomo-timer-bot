"""Все хендлеры бота — минимальный ручной режим: только быстрые таймеры.

Схема callback_data:
  menu            — главное меню (чипы длительностей)
  q:{sec}         — таймер на N секунд
  cust            — «своё время» (ввод текстом, формат как в игре)
  list            — активные таймеры
  cancel:{id}     — отменить таймер
  tz / tz:{name}  — выбор часового пояса
  help            — справка

Команды:
  /т 22:24 лесопилка   — таймер в формате игры (мм:сс / чч:мм:сс)
  /т 45м  /т 1ч 30м    — таймер с суффиксами
  /таймеры /пояс /help
"""
import html
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
from util import fmt_clock, fmt_delta, local_str, parse_duration, safe_tz

router = Router()
log = logging.getLogger("handlers")

# «Ждём ввод своего времени / пояса» (in-memory; после рестарта просто вводим ещё раз)
_PENDING_CUSTOM = set()   # tg_id
_PENDING_TZ = set()       # tg_id

TZ_LIST = [
    "Europe/Moscow", "Europe/Kyiv", "Europe/Minsk", "Europe/Berlin",
    "Asia/Almaty", "Asia/Tashkent", "UTC",
]

MENU_TEXT = (
    "⏱ <b>Fomo Timer</b> — напоминания для Fomo Fighters\n\n"
    "Запустили улучшение в игре → тапните длительность (или пришлите "
    "<code>/т 22:24</code>) — и в момент финиша придёт «Готово ✅».\n"
    "Формат как в игре: <code>22:24</code> = 22 мин 24 с, "
    "<code>1:28:10</code> = 1 ч 28 м 10 с."
)


# ---------- Помощники ----------

def user_tz(tg_id):
    row = db.get_user(tg_id)
    return safe_tz(row["tz"] if row else config.DEFAULT_TZ)


def dur_label(sec):
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}ч" + (f" {m}м" if m else "")
    if m:
        return f"{m}м" + (f" {s}с" if s else "")
    return f"{s}с"


async def edit(cb: CallbackQuery, text, kb=None):
    """Безопасно отредактировать сообщение меню (или отправить новое)."""
    m = cb.message
    try:
        if m is not None:
            await m.edit_text(text, reply_markup=kb)
        else:
            await cb.bot.send_message(cb.from_user.id, text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass
    try:
        if m is not None:
            await m.answer(text, reply_markup=kb)
    except Exception as e:
        log.warning("edit fallback failed: %s", e)


def clamp_seconds(sec):
    return max(config.MIN_TIMER_SEC, min(int(sec), config.MAX_TIMER_SEC))


def kb_main():
    """Главное меню = просто чипы длительностей."""
    rows = []
    q = config.QUICK_PRESETS
    for k in range(0, len(q), 4):
        rows.append([InlineKeyboardButton(
            text=dur_label(sec), callback_data=f"q:{sec}",
        ) for sec in q[k:k + 4]])
    rows.append([
        InlineKeyboardButton(text="✍️ Своё время", callback_data="cust"),
        InlineKeyboardButton(text="📋 Таймеры", callback_data="list"),
    ])
    rows.append([
        InlineKeyboardButton(text=f"🌍 {user_tz(_viewer[0]).key if _viewer[0] else 'Пояс'}",
                             callback_data="tz"),
        InlineKeyboardButton(text="❓ Справка", callback_data="help"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Небольшой хак: kb_main() вызывается и из колбэков, где from_user доступен;
# запоминаем последнего активного пользователя для подписи кнопки пояса.
_viewer = [0]


def kb_created():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Мои таймеры", callback_data="list"),
        InlineKeyboardButton(text="⬅️ В меню", callback_data="menu"),
    ]])


def kb_back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]])


def kb_tz():
    rows = [[InlineKeyboardButton(text=name, callback_data=f"tz:{name}")]
            for name in TZ_LIST]
    rows.append([InlineKeyboardButton(text="✍️ Ввести вручную (IANA)", callback_data="tz:custom")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_list(tg_id):
    rows = db.active(tg_id)
    tz = user_tz(tg_id)
    now = time.time()
    if not rows:
        return (
            "📋 <b>Активных таймеров нет.</b>\n\n"
            "Тапните длительность в меню или пришлите:\n"
            "<code>/т 22:24 лесопилка</code>",
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⏱ Меню", callback_data="menu"),
            ]]),
        )
    lines = [f"📋 <b>Активные таймеры:</b> {len(rows)}\n"]
    cbtns = []
    for r in rows:
        rem = int(r["ends_at"] - now)
        lines.append(
            f"• {r['label']}\n"
            f"   ⏳ осталось <code>{fmt_clock(rem)}</code> → {local_str(r['ends_at'], tz)}"
        )
        short = r["label"][:24] + ("…" if len(r["label"]) > 24 else "")
        cbtns.append([InlineKeyboardButton(text=f"❌ {short}", callback_data=f"cancel:{r['id']}")])
    cbtns.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="list"),
        InlineKeyboardButton(text="⬅️ В меню", callback_data="menu"),
    ])
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=cbtns)


def create_timer_reply(msg: Message, tg_id, chat_id, label, seconds):
    """Создать таймер в БД и подтвердить пользователю."""
    now = time.time()
    seconds = clamp_seconds(seconds)
    ends = now + seconds
    timer_id = db.add_timer(tg_id, chat_id, html.escape(label), ends, now)
    tz = user_tz(tg_id)
    msg.answer(
        "✅ <b>Таймер поставлен</b>\n\n"
        f"🏷 {html.escape(label)}\n"
        f"⏰ Финиш: {local_str(ends, tz)}\n"
        f"⏳ Через {fmt_delta(seconds)}\n\n"
        "🔔 В момент финиша пришлю «Готово».",
        reply_markup=kb_created(),
    )
    log.info("Таймер #%s: uid=%s label=%r sec=%s", timer_id, tg_id, label, seconds)


def parse_quick_args(args):
    """/т 22:24 лесопилка -> (1344, 'лесопилка') или (None, None)."""
    if not args or not args.strip():
        return None, None
    tokens = args.strip().split(None, 1)
    sec = parse_duration(tokens[0])
    if not sec:
        return None, None
    label = tokens[1].strip() if len(tokens) > 1 else f"Таймер {dur_label(sec)}"
    return sec, label


def help_text():
    return (
        "❓ <b>Справка Fomo Timer</b>\n\n"
        "Мини-апп Fomo Fighters не умеет присылать уведомления — этот бот "
        "делает это за него: в момент окончания таймера приходит обычный "
        "пуш Telegram «Готово ✅», мгновенно и без опозданий.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Запустили улучшение в игре → посмотрите таймер на кнопке.\n"
        "2. В боте: тапните чип длительности или пришлите время текстом.\n"
        "3. Когда время выйдет — придёт уведомление. Таймеры хранятся на "
        "сервере, телефон можно выключать.\n\n"
        "<b>Форматы времени</b> (как в игре):\n"
        "• <code>22:24</code> — 22 мин 24 с (мм:сс)\n"
        "• <code>1:28:10</code> — 1 ч 28 м 10 с (чч:мм:сс)\n"
        "• <code>1ч 30м</code>, <code>45м</code>, <code>30с</code>, <code>8h</code>\n"
        "• голое число (<code>90</code>) — минуты\n\n"
        "<b>Команды:</b>\n"
        "<code>/т 22:24 лесопилка</code> — таймер (подпись необязательна)\n"
        "<code>/таймеры</code> — список активных · <code>/пояс</code> — часовой пояс · "
        "<code>/help</code> — справка\n\n"
        "ℹ️ Позже бот сможет ставить таймеры автоматически — по данным API игры "
        "(см. README на сервере, раздел «Автотрекинг»)."
    )


# ---------- Команды ----------

@router.message(CommandStart())
async def cmd_start(message: Message):
    db.upsert_user(message.from_user.id, config.DEFAULT_TZ)
    _viewer[0] = message.from_user.id
    _PENDING_CUSTOM.discard(message.from_user.id)
    _PENDING_TZ.discard(message.from_user.id)
    await message.answer(MENU_TEXT, reply_markup=kb_main())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(help_text(), reply_markup=kb_back_menu())


@router.message(Command("t"))
async def cmd_t(message: Message, command: CommandObject):
    sec, label = parse_quick_args(command.args)
    if not sec:
        await message.answer(
            "Использование: <code>/т 22:24 лесопилка</code>\n"
            "Форматы: мм:сс, чч:мм:сс, 1ч 30м, 45м, 30с, 90 (минут)."
        )
        return
    create_timer_reply(message, message.from_user.id, message.chat.id, label, sec)


@router.message(Command("timers", "list"))
async def cmd_timers(message: Message):
    text, kb = render_list(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(Command("tz"))
async def cmd_tz(message: Message):
    await message.answer(
        f"🌍 <b>Часовой пояс</b>\nТекущий: <code>{user_tz(message.from_user.id).key}</code>",
        reply_markup=kb_tz(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    db.upsert_user(message.from_user.id, config.DEFAULT_TZ)
    _viewer[0] = message.from_user.id
    await message.answer(MENU_TEXT, reply_markup=kb_main())


# Кириллические алиасы (в официальном меню команд ТГ кириллицу не вставить)

@router.message(F.text.regexp(r"^/т(@\w+)?(\s|$)"))
async def cmd_t_cyr(message: Message):
    parts = (message.text or "").split(None, 1)
    sec, label = parse_quick_args(parts[1] if len(parts) > 1 else None)
    if not sec:
        await message.answer(
            "Использование: <code>/т 22:24 лесопилка</code>\n"
            "Форматы: мм:сс, чч:мм:сс, 1ч 30м, 45м, 30с, 90 (минут)."
        )
        return
    create_timer_reply(message, message.from_user.id, message.chat.id, label, sec)


@router.message(F.text.regexp(r"^/(таймеры|список)(@\w+)?$"))
async def cmd_timers_cyr(message: Message):
    await cmd_timers(message)


@router.message(F.text.regexp(r"^/пояс(@\w+)?$"))
async def cmd_tz_cyr(message: Message):
    await cmd_tz(message)


@router.message(F.text.regexp(r"^/(помощь|справка)(@\w+)?$"))
async def cmd_help_cyr(message: Message):
    await cmd_help(message)


# ---------- Callbacks ----------

@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    db.upsert_user(cb.from_user.id, config.DEFAULT_TZ)
    _viewer[0] = cb.from_user.id
    _PENDING_CUSTOM.discard(cb.from_user.id)
    _PENDING_TZ.discard(cb.from_user.id)
    await edit(cb, MENU_TEXT, kb_main())
    await cb.answer()


@router.callback_query(F.data.startswith("q:"))
async def cb_quick(cb: CallbackQuery):
    sec = int(cb.data.split(":")[1])
    if cb.message is not None:
        create_timer_reply(cb.message, cb.from_user.id, cb.message.chat.id,
                           f"Таймер {dur_label(sec)}", sec)
    await cb.answer()


@router.callback_query(F.data == "cust")
async def cb_custom(cb: CallbackQuery):
    _PENDING_CUSTOM.add(cb.from_user.id)
    await cb.message.answer(
        "✍️ Введите время (например <code>22:24</code>, <code>1:28:10</code>, "
        "<code>1ч 30м</code>) и необязательную подпись:\n"
        "<code>22:24 лесопилка</code>"
    )
    await cb.answer()


@router.callback_query(F.data == "list")
async def cb_list(cb: CallbackQuery):
    text, kb = render_list(cb.from_user.id)
    await edit(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(cb: CallbackQuery):
    tid = int(cb.data.split(":")[1])
    ok = db.cancel(cb.from_user.id, tid)
    text, kb = render_list(cb.from_user.id)
    await edit(cb, text, kb)
    await cb.answer("🗑 Отменено" if ok else "Уже неактуально")


@router.callback_query(F.data == "tz")
async def cb_tz(cb: CallbackQuery):
    _viewer[0] = cb.from_user.id
    await edit(cb, f"🌍 <b>Часовой пояс</b>\nТекущий: <code>{user_tz(cb.from_user.id).key}</code>",
               kb_tz())
    await cb.answer()


@router.callback_query(F.data.startswith("tz:"))
async def cb_tz_set(cb: CallbackQuery):
    name = cb.data.split(":", 1)[1]
    if name == "custom":
        _PENDING_TZ.add(cb.from_user.id)
        await cb.message.answer("✍️ Введите имя пояса IANA, например <code>Europe/Moscow</code>:")
        await cb.answer()
        return
    db.set_tz(cb.from_user.id, name)
    _viewer[0] = cb.from_user.id
    await edit(cb, MENU_TEXT, kb_main())
    await cb.answer("Сохранено")


@router.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await edit(cb, help_text(), kb_back_menu())
    await cb.answer()


# ---------- Ввод текстом (должны быть ПОСЛЕДними — ловят любой текст) ----------

@router.message(lambda m: m.from_user is not None and m.from_user.id in _PENDING_CUSTOM)
async def on_pending_custom(message: Message):
    _PENDING_CUSTOM.discard(message.from_user.id)
    sec, label = parse_quick_args(message.text)
    if not sec:
        await message.answer(
            "Не понял время 🤔 Примеры: <code>22:24</code>, <code>1:28:10</code>, "
            "<code>1ч 30м</code>, <code>45м лесопилка</code>."
        )
        _PENDING_CUSTOM.add(message.from_user.id)  # даём ещё попытку
        return
    create_timer_reply(message, message.from_user.id, message.chat.id, label, sec)


@router.message(lambda m: m.from_user is not None and m.from_user.id in _PENDING_TZ)
async def on_pending_tz(message: Message):
    _PENDING_TZ.discard(message.from_user.id)
    tz = safe_tz((message.text or "").strip())
    db.set_tz(message.from_user.id, tz.key)
    _viewer[0] = message.from_user.id
    await message.answer(f"🌍 Часовой пояс: <code>{tz.key}</code>.",
                         reply_markup=kb_main())
