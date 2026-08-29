"""Fomo Timer Bot — точка входа.

Запуск: python bot.py (токен в .env, см. .env.example).
Совместимо с aiogram 3.x.
"""
import asyncio
import html
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (BotCommand, InlineKeyboardButton,
                           InlineKeyboardMarkup, MenuButtonDefault,
                           MenuButtonWebApp, WebAppInfo)

import api_poller
import config
import db
import handlers
import timers
import watcher
import webapp_server

log = logging.getLogger("bot")

BOT_COMMANDS = [
    BotCommand(command="t", description="⏱ Таймер: /t 22:24 лесопилка"),
    BotCommand(command="timers", description="📋 Активные таймеры"),
    BotCommand(command="app", description="🎯 Мини-апп: все таймеры и кнопки"),
    BotCommand(command="tz", description="🌍 Часовой пояс"),
    BotCommand(command="api", description="🤖 Статус автотрекинга"),
    BotCommand(command="ask", description="🔔 Подтверждение таймеров вкл/выкл"),
    BotCommand(command="trace", description="🧪 Трассировка API вкл/выкл"),
    BotCommand(command="help", description="❓ Справка"),
]

# Ссылки на фоновые задачи держим живыми (иначе сборщик мусора может их собрать)
_TASKS: list[asyncio.Task] = []

# Какой адрес мы уже анонсировали владельцу и когда (защита от спама,
# если туннель в crash-loop меняет адрес каждые несколько секунд)
ANNOUNCE_COOLDOWN = 120  # секунд между сообщениями с новой кнопкой
_LAST_ANNOUNCED = {"url": "", "at": 0.0}

# Сообщение о блокировке туннеля (7844) шлём не чаще раза в полчаса:
# tunnel.on_blocked и так срабатывает раз на серию, это вторая ступень защиты
BLOCKED_COOLDOWN = 1800
_LAST_BLOCKED = {"at": 0.0}


async def _announce_url(bot: Bot, url: str):
    """Прислать владельцу сообщение со СВЕЖЕЙ кнопкой мини-аппа.

    Зачем: кнопка меню ☰ у Telegram-клиентов может закэшироваться и вести
    на старый адрес (Cloudflare в этом случае показывает error 1033).
    Кнопка в только что присланном сообщении всегда актуальна — это
    надёжный вход в мини-апп после каждой смены адреса (рестарт бота,
    переезд туннеля).
    """
    if not url:
        return
    now = time.monotonic()
    if url == _LAST_ANNOUNCED["url"]:
        return
    if _LAST_ANNOUNCED["url"] and now - _LAST_ANNOUNCED["at"] < ANNOUNCE_COOLDOWN:
        log.info("Мини-апп: адрес сменился, но анонс подавлен (кулдаун)")
        return
    _LAST_ANNOUNCED.update(url=url, at=now)
    ids = webapp_server.allowed_user_ids()
    if not ids:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎯 Открыть таймеры",
                             web_app=WebAppInfo(url=url))]])
    try:
        await bot.send_message(
            next(iter(ids)),
            "🎯 Мини-апп готов — жмите кнопку ниже. Она всегда открывает "
            "актуальный адрес; кнопка меню ☰ может обновиться с задержкой.",
            reply_markup=kb)
        log.info("Мини-апп: владельцу отправлена свежая кнопка")
    except Exception as e:
        log.warning("Мини-апп: не вышло отправить кнопку владельцу: %s", e)


async def _apply_webapp_url(bot: Bot, url: str):
    """Туннель поднялся (url) или упал (url="") → обновить кнопку меню.

    Адрес меняется при каждом рестарте бота — поэтому кнопку ставим заново
    при каждом запуске. Когда туннель УМЕР, старую кнопку убираем совсем
    (MenuButtonDefault): иначе она остаётся вести на мёртвый адрес, и
    пользователь видит Cloudflare error 1033.
    """
    webapp_server.set_public_url(url or "")
    try:
        if url:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🎯 Таймеры",
                                             web_app=WebAppInfo(url=url)))
            log.info("Мини-апп: кнопка меню указывает на %s", url)
        else:
            await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            log.warning("Мини-апп: туннель упал — кнопка меню с мёртвым "
                        "адресом убрана, пока туннель не поднимется снова")
    except Exception as e:
        log.warning("Мини-апп: не удалось обновить кнопку меню: %s", e)
    await _announce_url(bot, url)


async def _announce_blocked(bot: Bot, reason: str):
    """Туннель не подтвердился (сеть режет 7844) — сообщить владельцу.

    Важно НЕ промолчать: раньше бот писал «туннель поднят», пользователь жал
    кнопку и получал Cloudflare error 1033 без объяснений. Теперь вместо
    мёртвой кнопки приходит понятный диагноз и что делать. Кнопку меню при
    этом не трогаем — просто ждём, пока сеть пропустит туннель (bot
    перепроверяет сам каждые несколько минут и пришлёт свежую кнопку).
    """
    now = time.monotonic()
    if now - _LAST_BLOCKED["at"] < BLOCKED_COOLDOWN:
        return
    _LAST_BLOCKED["at"] = now
    ids = webapp_server.allowed_user_ids()
    if not ids:
        return
    try:
        await bot.send_message(
            next(iter(ids)),
            "⚠️ <b>Мини-апп не открывается из вашей сети</b>\n\n"
            + html.escape(str(reason)) + "\n\n"
            "Что помогает:\n"
            "• включить VPN на компьютере, где запущен бот (VPN на телефоне "
            "не считается — туннель поднимает именно ПК);\n"
            "• или вписать свой адрес в <code>WEBAPP_PUBLIC_URL</code> в "
            ".env (VPS/свой туннель).\n\n"
            "🔄 Бот перепроверяет сам каждые несколько минут — как только "
            "туннель заработает, пришлю свежую кнопку.")
        log.warning("Мини-апп: владельцу сообщено о блокировке туннеля")
    except Exception as e:
        log.warning("Мини-апп: не вышло сообщить о блокировке туннеля: %s", e)


def start_webapp(bot: Bot, loop: asyncio.AbstractEventLoop):
    """Мини-апп: локальный HTTP-сервер + публичный туннель.

    Сервер слушает только 127.0.0.1 (снаружи не виден), наружу отдаёт только
    cloudflared. Публичный адрес можно задать своим (WEBAPP_PUBLIC_URL в .env),
    иначе поднимаем быстрый туннель cloudflared (при первом запуске сам
    скачает ~20 МБ, без регистрации). Адрес объявляется владельцу только
    после живой проверки — кнопки с неработающими адресами больше не приходят.
    """
    try:
        srv = webapp_server.start("127.0.0.1", config.WEBAPP_PORT)
        port = srv.server_address[1]
    except OSError as e:
        log.warning("Мини-апп: веб-сервер не запущен (%s) — пуши продолжат "
                    "работать, только без экрана таймеров", e)
        return
    webapp_server.set_providers(
        loop=loop,
        refresh_submit=lambda: asyncio.run_coroutine_threadsafe(
            api_poller.poll_once(bot), loop))
    if config.WEBAPP_PUBLIC_URL:
        log.info("Мини-апп: публичный адрес из .env: %s", config.WEBAPP_PUBLIC_URL)
        asyncio.run_coroutine_threadsafe(
            _apply_webapp_url(bot, config.WEBAPP_PUBLIC_URL), loop)
        return
    import tunnel
    tunnel.start(
        port=port,
        on_url=lambda u: asyncio.run_coroutine_threadsafe(
            _apply_webapp_url(bot, u), loop),
        on_down=lambda: asyncio.run_coroutine_threadsafe(
            _apply_webapp_url(bot, ""), loop),
        on_blocked=lambda reason: asyncio.run_coroutine_threadsafe(
            _announce_blocked(bot, reason), loop))


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if not config.BOT_TOKEN:
        raise SystemExit(
            "Не задан BOT_TOKEN.\n"
            "1) Скопируйте .env.example в .env\n"
            "2) Вставьте токен от @BotFather\n"
            "3) Запустите снова. Подробности — в README.md"
        )

    db.init()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(handlers.router)

    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        log.warning("set_my_commands не удался: %s", e)

    # Фоновые задачи: планировщик напоминаний + автотрекинг API + слежка за файлами
    _TASKS.append(asyncio.create_task(timers.scheduler_loop(bot)))
    _TASKS.append(asyncio.create_task(api_poller.poll_forever(bot)))
    _TASKS.append(asyncio.create_task(watcher.loop(bot)))

    # Мини-апп (кнопка меню ☰): локальный сервер + туннель. Пуши не зависят
    # от него и работают даже если туннель не поднялся.
    if config.WEBAPP_ENABLED:
        start_webapp(bot, asyncio.get_running_loop())
    else:
        log.info("Мини-апп выключен (WEBAPP_ENABLED=false)")

    me = await bot.get_me()
    log.info("Fomo Timer Bot v%s запущен: @%s (id=%s)",
             config.APP_VERSION, me.username, me.id)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        print(f"Остановка: {e}")
