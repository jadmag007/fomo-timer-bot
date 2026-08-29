"""Fomo Timer Bot — точка входа.

Запуск: python bot.py (токен в .env, см. .env.example).
Совместимо с aiogram 3.x.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

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


async def _apply_webapp_url(bot: Bot, url: str):
    """Туннель поднялся (или адрес из .env) → обновить кнопку меню Telegram.

    Адрес меняется при каждом рестарте бота — поэтому кнопку ставим заново
    при каждом запуске; у пользователя вместо меню команд останется кнопка
    «🎯 Таймеры», открывающая мини-апп.
    """
    webapp_server.set_public_url(url)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🎯 Таймеры",
                                         web_app=WebAppInfo(url=url)))
        log.info("Мини-апп: кнопка меню указывает на %s", url)
    except Exception as e:
        log.warning("Мини-апп: не удалось поставить кнопку меню: %s", e)


def start_webapp(bot: Bot, loop: asyncio.AbstractEventLoop):
    """Мини-апп: локальный HTTP-сервер + публичный туннель.

    Сервер слушает только 127.0.0.1 (снаружи не виден), наружу отдаёт только
    cloudflared. Публичный адрес можно задать своим (WEBAPP_PUBLIC_URL в .env),
    иначе поднимаем быстрый туннель cloudflared (при первом запуске сам
    скачает ~20 МБ, без регистрации).
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
    tunnel.start(port=port, on_url=lambda u: asyncio.run_coroutine_threadsafe(
        _apply_webapp_url(bot, u), loop))


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
