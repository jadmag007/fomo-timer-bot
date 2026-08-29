"""Все хендлеры бота.

Схема callback_data:
  menu            — главное меню (чипы длительностей)
  q:{sec}         — таймер на N секунд
  cust            — «своё время» (ввод текстом, формат как в игре)
  list            — активные таймеры
  cancel:{id}     — отменить таймер
  tz / tz:{name}  — выбор часового пояса
  tadd:{gid}      — «Да»: добавить предложенные авто-таймеры
  tdeny:{gid}     — «Нет»: не добавлять (и больше не предлагать эти)
  ask             — переключить режим подтверждения (кнопка в /апи)
  trace           — переключить трассировку (кнопка в /апи)
  pause           — пауза/продолжить: остановить и вернуть пуши
  help            — справка

Команды:
  /т 22:24 лесопилка   — таймер в формате игры (мм:сс / чч:мм:сс)
  /т 45м  /т 1ч 30м    — таймер с суффиксами
  /пауза               — то же, что кнопка: вкл/выкл пуши
  /таймеры /пояс /апи /вопросы /трассировка /трейслог /help
"""
import html
import logging
import time

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import api_poller
import config
import db
import apitrace as trace_mod
import pause_state
import tunnel
import webapp_server
from util import fmt_clock, fmt_delta, local_str, parse_duration, safe_tz

router = Router()
log = logging.getLogger("handlers")

# «Ждём ввод своего времени / пояса» (in-memory; после рестарта просто вводим ещё раз)
_PENDING_CUSTOM = set()   # tg_id
_PENDING_TZ = set()       # tg_id


@router.message(lambda m: bool(m.text) and m.text.startswith("/"))
async def _command_while_pending(message: Message):
    """Команда во время режима «введите время» — снять ожидание и выполнить
    команду. Раньше флаг ожидания оставался, и следующее случайное сообщение
    (не команда) молча ставило таймер."""
    if message.from_user is not None:
        _PENDING_CUSTOM.discard(message.from_user.id)
    raise SkipHandler  # команда продолжит обычную обработку дальше по роутеру

TZ_LIST = [
    "Europe/Moscow", "Europe/Kyiv", "Europe/Minsk", "Europe/Berlin",
    "Asia/Almaty", "Asia/Tashkent", "UTC",
]

MENU_TEXT = (
    "⏱ <b>Fomo Timer</b> — напоминания для Fomo Fighters\n\n"
    "Запустили улучшение в игре → тапните длительность (или пришлите "
    "<code>/т 22:24</code>) — и в момент финиша придёт «Готово ✅».\n"
    "Формат как в игре: <code>22:24</code> = 22 мин 24 с, "
    "<code>1:28:10</code> = 1 ч 28 м 10 с.\n"
    "🎯 Все таймеры на одном экране: кнопка меню ☰ или <code>/app</code>.\n\n"
    f"🧪 <i>Fomo Timer Bot v{config.APP_VERSION}</i>"
)


def menu_text():
    """Меню с баннером паузы (если бот сейчас на паузе)."""
    if pause_state.is_paused():
        mins = _paused_mins()
        return (
            f"⏸ <b>БОТ НА ПАУЗЕ</b> — пуши не отправляются ({mins}).\n"
            "Таймеры продолжают ставиться; всё завершённое придёт одной "
            "сводкой после «Продолжить».\n\n" + MENU_TEXT
        )
    return MENU_TEXT


def _paused_mins():
    at = pause_state.paused_at()
    if not at:
        return "меньше минуты"
    return fmt_delta(max(60, int(time.time() - at) // 60 * 60))


def resume_summary_text(missed, paused_at):
    """Одна сводка вместо кучи «догоняющих» пушей после снятия паузы."""
    tz = safe_tz(config.DEFAULT_TZ)
    mins = ""
    if paused_at:
        secs = max(60, int(time.time() - paused_at) // 60 * 60)
        mins = f" (пауза длилась {fmt_delta(secs)})"
    head = (f"▶️ <b>Пауза снята</b> — пуши снова работают{mins}.\n"
            f"Пока вас не было, завершилось: {len(missed)}\n")
    show = missed[:12]
    # метки в БД хранятся УЖЕ экранированными (create_timer_reply) —
    # второй html.escape давал на экране «&amp;» вместо символа
    lines = [f"  ✅ {m['label']} — {local_str(m['ends_at'], tz)}"
             for m in show]
    extra = len(missed) - len(show)
    if extra > 0:
        lines.append(f"  …и ещё {extra}")
    return head + "\n".join(lines)


def pause_btn():
    """Кнопка паузы для главного меню: ⏸ Пауза ↔ ▶️ Продолжить."""
    if pause_state.is_paused():
        return InlineKeyboardButton(text="▶️ Продолжить", callback_data="pause")
    return InlineKeyboardButton(text="⏸ Пауза", callback_data="pause")


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
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return  # перерисовали то же самое — не надо дублировать сообщение
    except Exception as e:
        log.warning("edit failed: %s", e)
    try:
        if m is not None:
            await m.answer(text, reply_markup=kb)
    except Exception as e:
        log.warning("edit fallback failed: %s", e)


def clamp_seconds(sec):
    return max(config.MIN_TIMER_SEC, min(int(sec), config.MAX_TIMER_SEC))


def miniapp_button():
    """Кнопка «🎯 Мини-апп» (web_app), если публичный адрес уже известен.

    Адрес туннеля появляется не мгновенно (cloudflared поднимается) — тогда
    кнопки просто нет, а /app объяснит, что происходит.
    """
    url = webapp_server.current_url()
    if not url:
        return None
    return InlineKeyboardButton(text="🎯 Мини-апп: все таймеры",
                                web_app=WebAppInfo(url=url))


def kb_main(tg_id):
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
    app_btn = miniapp_button()
    if app_btn:
        rows.append([app_btn])
    rows.append([
        InlineKeyboardButton(text=f"🌍 {user_tz(tg_id).key}",
                             callback_data="tz"),
        InlineKeyboardButton(text="❓ Справка", callback_data="help"),
    ])
    rows.append([pause_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def kb_api():
    """Переключатели на экране /апи: подтверждение Да/Нет и трассировка."""
    ask_label = ("🔔 Подтверждение Да/Нет: ВКЛ — выключить" if config.API_ASK_BEFORE_ADD
                 else "🔔 Подтверждение Да/Нет: выкл — включить")
    trace_label = ("🧪 Трассировка API: ВКЛ — выключить" if config.API_TRACE
                   else "🧪 Трассировка API: выкл — включить")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ask_label, callback_data="ask")],
        [InlineKeyboardButton(text=trace_label, callback_data="trace")],
    ])


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


async def create_timer_reply(msg: Message, tg_id, chat_id, label, seconds):
    """Создать таймер в БД и подтвердить пользователю."""
    now = time.time()
    seconds = clamp_seconds(seconds)
    ends = now + seconds
    timer_id = db.add_timer(tg_id, chat_id, html.escape(label), ends, now)
    tz = user_tz(tg_id)
    await msg.answer(
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
        "3. Когда время выйдет — придёт уведомление. Таймеры хранятся в "
        "базе на компьютере, телефон можно выключать.\n\n"
        "<b>Форматы времени</b> (как в игре):\n"
        "• <code>22:24</code> — 22 мин 24 с (мм:сс)\n"
        "• <code>1:28:10</code> — 1 ч 28 м 10 с (чч:мм:сс)\n"
        "• <code>1ч 30м</code>, <code>45м</code>, <code>30с</code>, <code>8h</code>\n"
        "• голое число (<code>90</code>) — минуты\n\n"
        "<b>Команды:</b>\n"
        "<code>/т 22:24 лесопилка</code> — таймер (подпись необязательна)\n"
        "<code>/таймеры</code> — список активных · <code>/пояс</code> — часовой пояс\n"
        "<code>/апи</code> — автотрекинг · <code>/вопросы</code> — Да/Нет вкл/выкл\n"
        "<code>/пауза</code> — остановить/вернуть пуши (уходя от компа)\n"
        "<code>/трассировка</code> — лог сырых ответов API вкл/выкл\n"
        "<code>/трейслог</code> — прислать файл trace.log · "
        "<code>/help</code> — справка\n\n"
        "<b>🎯 Мини-апп</b>: кнопка меню (☰) слева от поля ввода или <code>/app</code> — "
        "все таймеры сразу, живые отсчёты, тихий режим по группам, отмена. "
        "Уведомления в чат приходят как раньше.\n\n"
        "<b>Автотрекинг:</b> положите <code>fomo.txt</code> в папку бота или в "
        "<code>token_updates</code> — таймеры будут ставиться сами, без "
        "вопросов. Не отображаются клановые сундуки/награды аванпостов? "
        "Включите /трассировка — по логу добавим переводы. Подробно: README.\n\n"
        f"🧪 <i>Fomo Timer Bot v{config.APP_VERSION}</i>"
    )


def api_status_text():
    st = api_poller.status()
    body = _api_status_body(st)
    if pause_state.is_paused():
        return (
            "⏸ <b>БОТ НА ПАУЗЕ</b> — пуши не отправляются ({}).\n"
            "Трекинг работает, таймеры ставятся; завершённое придёт одной "
            "сводкой после «Продолжить» (кнопка в меню или /пауза).\n\n"
        ).format(_paused_mins()) + body
    return body


def _api_status_body(st):
    if not st["enabled"] or not st["configured"]:
        return (
            "🤖 <b>Автотрекинг</b>\n"
            "Статус: выключен (ещё нет настроек)\n\n"
            "Два способа включить:\n\n"
            "<b>А. Юзербот — максимум автоматизма (рекомендую)</b>\n"
            "Запустите <code>login_bot.bat</code> рядом с ботом, введите телефон "
            "и код из Telegram — всё. Бот сам будет получать свежие ключи игры, "
            "никакие файлы больше не понадобятся.\n\n"
            "<b>Б. Быстрый старт по файлу fomo.txt</b>\n"
            "Снимите трафик игры в браузере (web.telegram.org → F12 → Network → "
            "правый клик → «Copy all as HAR» → сохранить как <code>fomo.txt</code>) "
            "и положите файл в папку бота или в <code>token_updates</code> — бот "
            "сам всё настроит и добавит таймеры. Пошагово: README → «Файл fomo.txt»."
        )
    if st["token_dead"]:
        head = ("🔑 <b>Автотрекинг — initData не принимается</b>\n"
                "Запустите <code>login_bot.bat</code> или положите свежий "
                "<code>fomo.txt</code> в папку бота / <code>token_updates</code>.")
    elif st.get("native"):
        head = "🤖 <b>Автотрекинг</b> — работает ✅ (нативный режим: сам подписываю и сам продлеваю ключ)"
    else:
        head = "🤖 <b>Автотрекинг</b> — работает ✅"
    lines = [head]
    if st["hosts"]:
        lines.append("API: " + html.escape(", ".join(st["hosts"])))
    lines.append(f"Интервал опроса: {st['interval']} с · добавлено таймеров: {st['added_total']}"
                 + (f" · предложено: {st['proposed_total']}" if st.get("proposed_total") else ""))
    if st.get("ask_mode"):
        lines.append("Режим: новые таймеры сначала показываю списком — добавлю после «Да»")
    else:
        lines.append("Режим: добавляю молча, без вопросов (переключить — кнопка ниже)")
    if st.get("trace"):
        lines.append("🧪 Трассировка: ВКЛ — пишу сырые ответы API (файл: /трейслог)")
    if st["last_poll"]:
        lines.append(f"Последний опрос: {int(time.time() - st['last_poll'])} с назад · HTTP {st['last_status']}")
    # Клановые сундуки / награды аванпостов (/user/data/all) — отдельный статус:
    # именно по этому опросу ставятся «🎁 Клановый сундук» и «📦 Награда аванпоста»
    if st.get("last_all_poll"):
        all_line = (f"🎁 Сундуки/аванпосты (раз в {st['all_interval']} с): "
                    f"{int(time.time() - st['last_all_poll'])} с назад · HTTP {st['last_all_status']}"
                    f" · нашёл {st['last_all_found']}, новых {st['last_all_added']}")
        lines.append(all_line)
    elif st.get("native"):
        lines.append(f"🎁 Сундуки/аванпосты (раз в {st['all_interval']} с): ещё не опрашивал")
    if st.get("last_all_error"):
        lines.append("⚠️ Сундуки/аванпосты, ошибка: " + html.escape(st["last_all_error"]))
    if st["last_error"]:
        lines.append("Сеть: " + html.escape(st["last_error"]))
    # Мини-апп: где сейчас живёт и подтверждён ли; статус туннеля показывает
    # падения/переезды не только в логе (см. error 1033 в FAQ)
    if not config.WEBAPP_ENABLED:
        lines.append("🎯 Мини-апп: выключен (WEBAPP_ENABLED=false в .env)")
    else:
        turl = webapp_server.current_url()
        tst = tunnel.status()
        if turl:
            extra = ""
            if tst.get("since"):
                age = int(time.time() - tst["since"])
                extra += f" · жив {age // 60} мин" if age >= 60 else f" · жив {age} с"
            if tst.get("restarts"):
                extra += f" · перезапусков туннеля: {tst['restarts']}"
            lines.append("🎯 Мини-апп: работает — кнопка меню ☰ или /app" + extra)
        elif tst.get("blocked"):
            lines.append("🎯 Мини-апп: сеть блокирует туннель (порты 7844) — "
                         "помогает VPN на ПК. Перепроверяю сам каждые несколько "
                         "минут; как заработает — пришлю кнопку (лог: "
                         "data/tunnel.log)")
        else:
            down = ""
            if tst.get("down_at"):
                down = f" (последний упал {int(time.time() - tst['down_at'])} с назад)"
            lines.append("🎯 Мини-апп: поднимаю/перезапускаю туннель…" + down
                         + " (лог: data/tunnel.log)")
    lines.append("")
    if st.get("native"):
        lines.append("Ключ продлевается автоматически. Если сервер перестанет "
                     "принимать initData — свежую добудет юзербот (если вы "
                     "запускали <code>login_bot.bat</code>) или напишу, что нужно.")
    else:
        lines.append("Когда подписи устареют — положите свежий fomo.txt в папку "
                     "бота, обновлюсь сам и напишу в личку. А чтобы это делалось "
                     "без вас — запустите <code>login_bot.bat</code> один раз.")
    return "\n".join(lines)


def trace_on_text():
    return (
        "🧪 <b>Трассировка включена.</b>\n\n"
        "Теперь каждый ответ API игры пишется в файл "
        "<code>data/trace.log</code> (папка бота): все ключи, поля с датами "
        "и полный JSON. Пришлите <code>/трейслог</code> через пару минут "
        "(лучше после запуска улучшения в игре) — заберёте файл, по нему "
        "добавляются переводы новых таймеров.\n"
        "Выключить: <code>/трассировка</code> ещё раз."
    )


# ---------- Команды ----------

@router.message(CommandStart())
async def cmd_start(message: Message):
    db.upsert_user(message.from_user.id, config.DEFAULT_TZ)
    _PENDING_CUSTOM.discard(message.from_user.id)
    _PENDING_TZ.discard(message.from_user.id)
    await message.answer(menu_text(), reply_markup=kb_main(message.from_user.id))


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
    await create_timer_reply(message, message.from_user.id, message.chat.id, label, sec)


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
    await message.answer(menu_text(), reply_markup=kb_main(message.from_user.id))


@router.message(Command("app"))
async def cmd_app(message: Message):
    """Мини-апп: все таймеры на одном экране + кнопки управления."""
    await _send_app(message)


async def _send_app(message: Message):
    db.upsert_user(message.from_user.id, config.DEFAULT_TZ)
    if not config.WEBAPP_ENABLED:
        await message.answer(
            "🎯 Мини-апп выключен (WEBAPP_ENABLED=false в .env). "
            "Включите и перезапустите <code>start.bat</code>.")
        return
    url = webapp_server.current_url()
    if not url:
        if tunnel.status().get("blocked"):
            await message.answer(
                "⚠️ <b>Мини-апп не открывается из вашей сети</b>: провайдер/"
                "файрвол блокирует порты Cloudflare-туннеля (TCP/UDP 7844).\n\n"
                "Что помогает:\n"
                "• включить VPN на компьютере, где запущен бот (VPN на телефоне "
                "не считается — туннель поднимает именно ПК);\n"
                "• или вписать свой адрес в <code>WEBAPP_PUBLIC_URL</code> в .env.\n\n"
                "🔄 Бот перепроверяет сам каждые несколько минут — как только "
                "туннель заработает, пришлю свежую кнопку.")
        else:
            await message.answer(
                "🎯 Мини-апп поднимается: бот ищет/скачивает <code>cloudflared</code> "
                "и открывает туннель (до минуты; лог — <code>data/tunnel.log</code>). "
                "Напишите /app ещё раз через минуту — появится кнопка, а также она "
                "появится слева от поля ввода (кнопка меню ☰).")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎯 Открыть все таймеры",
                             web_app=WebAppInfo(url=url))]])
    await message.answer(
        "🎯 <b>Мини-апп со всеми таймерами</b>\n"
        "Кнопка ниже открывает панель: живые отсчёты, тихий режим по группам, "
        "отмена таймеров, кнопка «Обновить». Уведомления в чат приходят "
        "как раньше.\n\n"
        "Тот же экран открывает кнопка меню ☰ слева от поля ввода.",
        reply_markup=kb)


@router.message(Command("ask"))
async def cmd_ask(message: Message):
    """Переключить режим подтверждения (Да/Нет ↔ молча), с записью в .env."""
    new = not bool(config.API_ASK_BEFORE_ADD)
    config.set_ask_before_add(new)
    if new:
        await message.answer("🔔 Буду <b>показывать новые таймеры списком</b> и добавлю "
                             "после вашего «Да». Экран: /апи")
    else:
        await message.answer("⚡️ Режим: <b>добавляю таймеры молча</b>, без вопросов. "
                             "Список: /таймеры · Экран: /апи")


@router.message(Command("trace"))
async def cmd_trace(message: Message):
    """Вкл/выкл трассировку сырых ответов API (data/trace.log)."""
    new = not trace_mod.enabled()
    config.set_trace(new)
    if new:
        await message.answer(trace_on_text())
    else:
        await message.answer("🧪 Трассировка выключена. Лог остался в "
                             "<code>data/trace.log</code> — забрать: /трейслог")


@router.message(Command("tracelog"))
async def cmd_tracelog(message: Message):
    """Прислать файл trace.log (по нему добавляются новые таймеры/переводы)."""
    p = trace_mod.LOG_PATH
    if not p.exists() or p.stat().st_size == 0:
        await message.answer(
            "Лог трассировки пока пуст.\n"
            "Включите <code>/трассировка</code>, запустите в игре улучшение "
            "(или откройте клановый сундук / заберите награду аванпоста), "
            "подождите пару минут и пришлите <code>/трейслог</code> снова."
        )
        return
    try:
        await message.answer_document(
            FSInputFile(p),
            caption="🧪 Лог трассировки. Пришлите его разработчику/в чат — по "
                    "ключам из этого файла добавляются переводы таймеров.")
    except Exception as e:
        log.warning("Не удалось отправить trace.log: %s", e)
        await message.answer(f"Не удалось отправить файл: {e}\n"
                             f"Он лежит в папке бота: <code>{p}</code>")


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
    await create_timer_reply(message, message.from_user.id, message.chat.id, label, sec)


@router.message(F.text.regexp(r"^/(таймеры|список)(@\w+)?$"))
async def cmd_timers_cyr(message: Message):
    await cmd_timers(message)


@router.message(F.text.regexp(r"^/пояс(@\w+)?$"))
async def cmd_tz_cyr(message: Message):
    await cmd_tz(message)


@router.message(F.text.regexp(r"^/(помощь|справка)(@\w+)?$"))
async def cmd_help_cyr(message: Message):
    await cmd_help(message)


@router.message(F.text.regexp(r"^/(апи|автотрекинг)(@\w+)?$"))
async def cmd_api_cyr(message: Message):
    await message.answer(api_status_text(), reply_markup=kb_api())


@router.message(F.text.regexp(r"^/(вопросы|подтверждение|ask)(@\w+)?$"))
async def cmd_ask_cyr(message: Message):
    await cmd_ask(message)


@router.message(F.text.regexp(r"^/(трассировка|trace)(@\w+)?$"))
async def cmd_trace_cyr(message: Message):
    await cmd_trace(message)


@router.message(F.text.regexp(r"^/(трейслог|tracelog)(@\w+)?$"))
async def cmd_tracelog_cyr(message: Message):
    await cmd_tracelog(message)


@router.message(F.text.regexp(r"^/(приложение|миниапп)(@\w+)?$"))
async def cmd_app_cyr(message: Message):
    await _send_app(message)


# ---------- Callbacks ----------

@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    db.upsert_user(cb.from_user.id, config.DEFAULT_TZ)
    _PENDING_CUSTOM.discard(cb.from_user.id)
    _PENDING_TZ.discard(cb.from_user.id)
    await edit(cb, menu_text(), kb_main(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data.startswith("q:"))
async def cb_quick(cb: CallbackQuery):
    try:
        sec = int(cb.data.split(":")[1])
    except (IndexError, ValueError):
        await cb.answer("Не понял длительность")
        return
    if cb.message is not None:
        await create_timer_reply(cb.message, cb.from_user.id, cb.message.chat.id,
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
    try:
        tid = int(cb.data.split(":")[1])
    except (IndexError, ValueError):
        await cb.answer("Не понял таймер")
        return
    ok = db.cancel(cb.from_user.id, tid)
    text, kb = render_list(cb.from_user.id)
    await edit(cb, text, kb)
    await cb.answer("🗑 Отменено" if ok else "Уже неактуально")


@router.callback_query(F.data == "tz")
async def cb_tz(cb: CallbackQuery):
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
    await edit(cb, menu_text(), kb_main(cb.from_user.id))
    await cb.answer("Сохранено")


@router.callback_query(F.data.startswith("tadd:"))
async def cb_auto_add(cb: CallbackQuery):
    """«Да»: поставить все таймеры из предложенной партии."""
    try:
        gid = int(cb.data.split(":", 1)[1])
    except (IndexError, ValueError):
        gid = -1
    n, added = api_poller.confirm_group(gid)
    if n:
        tz = user_tz(cb.from_user.id)
        lines = [f"✅ <b>Добавлено таймеров: {n}</b>", ""]
        for up in added:
            lines.append(f"• {html.escape(up['label'])} → {local_str(up['ends_at'], tz)}")
        lines.append("")
        lines.append("🔔 В момент финиша пришлю «Готово». Список: /таймеры")
        await edit(cb, "\n".join(lines), kb_back_menu())
        await cb.answer("Добавлено")
    else:
        await edit(cb, "👌 Это предложение уже обработано или устарело.", kb_back_menu())
        await cb.answer("Уже неактуально")


@router.callback_query(F.data.startswith("tdeny:"))
async def cb_auto_deny(cb: CallbackQuery):
    """«Нет»: не ставить и не предлагать эти таймеры снова."""
    try:
        gid = int(cb.data.split(":", 1)[1])
    except (IndexError, ValueError):
        gid = -1
    api_poller.decline_group(gid)
    await edit(cb, "👌 Хорошо, эти не ставлю. Продолжаю следить: заметлю новые "
                   "улучшения — покажу отдельным вопросом.", kb_back_menu())
    await cb.answer("Ок")


@router.callback_query(F.data == "ask")
async def cb_ask_toggle(cb: CallbackQuery):
    """Кнопка на экране /апи: вкл/выкл подтверждение Да/Нет (сохраняется в .env)."""
    new = not bool(config.API_ASK_BEFORE_ADD)
    config.set_ask_before_add(new)
    await edit(cb, api_status_text(), kb_api())
    await cb.answer("Буду спрашивать подтверждение" if new else "Добавляю молча")


@router.callback_query(F.data == "trace")
async def cb_trace_toggle(cb: CallbackQuery):
    """Кнопка на экране /апи: вкл/выкл трассировку (сохраняется в .env)."""
    new = not trace_mod.enabled()
    config.set_trace(new)
    await edit(cb, api_status_text(), kb_api())
    await cb.answer("Трассировка включена (/трейслог)" if new else "Трассировка выключена")


@router.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await edit(cb, help_text(), kb_back_menu())
    await cb.answer()


# ---------- Пауза (кнопка в меню и /пауза) ----------

@router.callback_query(F.data == "pause")
async def cb_pause(cb: CallbackQuery):
    db.upsert_user(cb.from_user.id, config.DEFAULT_TZ)
    was = pause_state.is_paused()
    snap = pause_state.set_paused(not was)
    log.info("Пауза %s (владелец)", "снята" if was else "включена")
    await edit(cb, menu_text(), kb_main(cb.from_user.id))
    if was:
        await cb.answer("▶️ Пуши снова работают")
        missed = pause_state.take_missed()
        if missed and cb.message is not None:
            try:
                await cb.message.answer(
                    resume_summary_text(missed, snap.get("paused_at")))
            except Exception:
                log.exception("Не удалось отправить сводку после паузы")
    else:
        await cb.answer("⏸ Пуши остановлены — пока не нажмёте «Продолжить»")


@router.message(Command("pause", "пауза"))
async def cmd_pause(message: Message):
    """Пауза/продолжить: то же, что кнопка в меню."""
    db.upsert_user(message.from_user.id, config.DEFAULT_TZ)
    was = pause_state.is_paused()
    snap = pause_state.set_paused(not was)
    log.info("Пауза %s (команда)", "снята" if was else "включена")
    if not was:
        await message.answer(
            "⏸ <b>Бот поставлен на паузу.</b>\n\n"
            "Пуши («Готово ✅», предупреждения, предложения Да/Нет) больше "
            "не приходят, пока не нажмёте ▶️ <b>Продолжить</b> в меню или "
            "не пришлёте <code>/пауза</code> ещё раз.\n\n"
            "Таймеры продолжают ставиться, автотрекинг работает, всё "
            "завершённое придёт одной сводкой после возобновления.",
            reply_markup=kb_main(message.from_user.id),
        )
        return
    await message.answer(menu_text(), reply_markup=kb_main(message.from_user.id))
    missed = pause_state.take_missed()
    if missed:
        await message.answer(resume_summary_text(missed, snap.get("paused_at")))


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
    await create_timer_reply(message, message.from_user.id, message.chat.id, label, sec)


@router.message(lambda m: m.from_user is not None and m.from_user.id in _PENDING_TZ)
async def on_pending_tz(message: Message):
    _PENDING_TZ.discard(message.from_user.id)
    tz = safe_tz((message.text or "").strip())
    db.set_tz(message.from_user.id, tz.key)
    await message.answer(f"🌍 Часовой пояс: <code>{tz.key}</code>.",
                         reply_markup=kb_main(message.from_user.id))
