"""Fomo Timer Bot — точка входа.

Запуск: python bot.py (токен в .env, см. .env.example).
Совместимо с aiogram 3.x.
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonDefault

import api_poller
import config
import db
import handlers
import pause_state
import timers
import watcher
import webapp_server

log = logging.getLogger("bot")

BOT_COMMANDS = [
    BotCommand(command="t", description="⏱ Таймер: /t 22:24 лесопилка"),
    BotCommand(command="timers", description="📋 Активные таймеры"),
    BotCommand(command="tz", description="🌍 Часовой пояс"),
    BotCommand(command="api", description="🤖 Статус автотрекинга и ночь"),
    BotCommand(command="ask", description="🔔 Подтверждение таймеров вкл/выкл"),
    BotCommand(command="pause", description="⏸ Пауза: остановить/вернуть пуши"),
    BotCommand(command="trace", description="🧪 Трассировка API вкл/выкл"),
    BotCommand(command="help", description="❓ Справка"),
]

# Ссылки на фоновые задачи держим живыми (иначе сборщик мусора может их собрать)
_TASKS: list[asyncio.Task] = []


def start_webapp(bot: Bot, loop: asyncio.AbstractEventLoop):
    """Страница таймеров в браузере: локальный HTTP-сервер.

    Сервер слушает только 127.0.0.1 — страница открывается в обычном браузере
    на том устройстве, где запущен бот (на ПК — браузер ПК; на телефоне в
    Termux — браузер телефона): http://127.0.0.1:PORT. Наружу ничем не торчит
    (внешний доступ через туннель убран до лучших времён — он останется в
    истории git). Пуши не зависят от страницы и работают даже если порт занят.
    """
    try:
        srv = webapp_server.start("127.0.0.1", config.WEBAPP_PORT)
        port = srv.server_address[1]
        log.info("Страница таймеров работает в браузере: http://127.0.0.1:%d", port)
    except OSError as e:
        log.warning("Страница таймеров не запущена (%s) — пуши продолжат "
                    "работать, только без экрана таймеров", e)
        return
    webapp_server.set_providers(
        loop=loop,
        refresh_submit=lambda: asyncio.run_coroutine_threadsafe(
            api_poller.poll_once(bot), loop))


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if not config.BOT_TOKEN:
        # Код 2 = ошибка конфигурации: start.bat отличает её от падения и НЕ
        # устраивает бесконечный цикл «перезапуск через 5 секунд».
        print(
            "Не задан BOT_TOKEN.\n"
            "1) Скопируйте .env.example в .env\n"
            "2) Вставьте токен от @BotFather\n"
            "3) Запустите снова. Подробности — в README.md",
            file=sys.stderr,
        )
        raise SystemExit(2)

    db.init()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(handlers.router)

    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        log.warning("set_my_commands не удался: %s", e)

    # Кнопка мини-аппа у поля ввода (чат-меню) была включена в старых версиях,
    # и этот выбор ХРАНИТСЯ НА СЕРВЕРАХ TELEGRAM: удаление кода мини-аппа её
    # не убирает — надо явно вернуть обычное меню. Сбрасываем при каждом
    # старте (идемпотентно, несколько миллисекунд): у всех, кто обновился,
    # кнопка «мини-апп» исчезает из чата с ботом.
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        log.info("Кнопка мини-аппа у поля ввода сброшена — чат-меню обычное")
    except Exception as e:
        log.warning("Не удалось сбросить кнопку мини-аппа (чат-меню): %s", e)

    # Фоновые задачи: планировщик напоминаний + автотрекинг API + слежка за файлами
    _TASKS.append(asyncio.create_task(timers.scheduler_loop(bot)))
    _TASKS.append(asyncio.create_task(api_poller.poll_forever(bot)))
    _TASKS.append(asyncio.create_task(watcher.loop(bot)))

    # Страница таймеров в браузере: локальный сервер. Пуши не зависят
    # от неё и работают даже если порт занят.
    if config.WEBAPP_ENABLED:
        start_webapp(bot, asyncio.get_running_loop())
    else:
        log.info("Страница таймеров выключена (WEBAPP_ENABLED=false)")

    me = await bot.get_me()
    log.info("Fomo Timer Bot v%s запущен: @%s (id=%s)",
             config.APP_VERSION, me.username, me.id)
    if me.username and config.BOT_USERNAME != me.username:
        if config.set_bot_username(me.username):
            log.info("Имя бота записано в .env (BOT_USERNAME=@%s) — отложенные "
                     "пуши планируются в чат с ботом", me.username)
    if pause_state.is_paused():
        log.warning("Бот запущен НА ПАУЗЕ (data/pause.json) — пуши не отправляются, "
                    "снять: кнопка «Продолжить» в меню или /пауза")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        print(f"Остановка: {e}")
