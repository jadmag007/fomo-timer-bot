"""Fomo Timer Bot — точка входа.

Запуск: python bot.py (токен в .env, см. .env.example).
Совместимо с aiogram 3.x.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

import api_poller
import config
import db
import handlers
import timers

log = logging.getLogger("bot")

BOT_COMMANDS = [
    BotCommand(command="t", description="⏱ Таймер: /t 22:24 лесопилка"),
    BotCommand(command="timers", description="📋 Активные таймеры"),
    BotCommand(command="tz", description="🌍 Часовой пояс"),
    BotCommand(command="help", description="❓ Справка"),
]


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

    # Фоновые задачи: планировщик напоминаний + (опционально) автотрекинг API
    asyncio.create_task(timers.scheduler_loop(bot))
    asyncio.create_task(api_poller.poll_forever(bot))

    me = await bot.get_me()
    log.info("Бот запущен: @%s (id=%s)", me.username, me.id)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        print(f"Остановка: {e}")
