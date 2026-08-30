#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""login_userbot.py — ОДНОРАЗОВЫЙ вход юзербота (свежая initData навсегда).

Что делает:
  1. Спросит телефон (в международном формате: +7…), затем код, который
     придёт в Telegram, и пароль 2FA — если он включён.
  2. Создаст файл userbot.session рядом с ботом — это ваш вход в Telegram.
  3. Сразу проверит: откроет мини-апп Fomo Fighters и получит initData.
     Увидите «ОК: initData получена» — больше бот ничего у вас не спросит.

Если сервер ответит «403 RECAPTCHA_CHECK…» — это анти-спас Telegram:
встроенные «публичные» api-ключи он считает подозрительными и просит
капчу. Скрипт сам предложит ввести ваши личные api_id/api_hash из
my.telegram.org (бесплатно, ~2 минуты) и сохранит их в .env.

Запуск: двойной клик по login_bot.bat (Windows) или ./login_bot.sh
Зачем: когда ключ игры устареет, бот сам откроет мини-апп этой сессией и
продолжит работу без ваших действий.
"""
import asyncio
import pathlib
import sys
import warnings

OK = "\n" + "=" * 62

# Публичные api-пары официальных клиентов Telegram (запасные). На них давно
# давит анти-спас Telegram (403 RECAPTCHA_CHECK) — надёжнее свои из my.telegram.org.
PUBLIC_PAIRS = [
    (6, "eb06d4abfb49dc3eeb1aeb98ae0f581e"),       # Telegram Desktop
    (2040, "b18441a1ff607e10a989891a5462e627"),    # Telegram Android
]


def _quiet_policy() -> None:
    """Selector-цикл для Telethon на Windows — без DeprecationWarning (Python 3.14+)."""
    try:
        if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


def _drop_session(spath: str) -> None:
    """Полу-сессия после неудачного входа бесполезна — убираем её и журнал."""
    for suffix in (".session", ".session-journal"):
        try:
            p = pathlib.Path(spath + suffix)
            if p.exists():
                p.unlink()
        except OSError:
            pass


def _print_own_creds_hint() -> None:
    print()
    print("Как получить свои api-ключи (бесплатно, ~2 минуты):")
    print("  1. В браузере откройте https://my.telegram.org")
    print("  2. Войдите по своему номеру — код придёт В TELEGRAM (в чат Telegram)")
    print("  3. Откройте «API development tools»")
    print("  4. App title: любое (напр. fomotimer), Short name: fomotimer,")
    print("     Platform: Desktop -> Create Application")
    print("  5. Скопируйте app_api_id (число) и app_api_hash (32 символа)")
    print("     Если вместо формы видна «ERROR» — просто обновите страницу.")
    print()


def _ask_own_creds() -> tuple[int, str] | None:
    """Попросить личные api_id/api_hash. None — пользователь отменил."""
    _print_own_creds_hint()
    while True:
        try:
            raw = input("Вставьте app_api_id (или Enter — отмена): ").strip()
            if not raw:
                return None
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not digits:
                print("api_id — только число, напр. 1234567. Попробуйте ещё раз.")
                continue
            api_hash = input("Вставьте app_api_hash: ").strip()
            if len(api_hash) < 30:
                print("api_hash — длинная строка из 32 символов. Попробуйте ещё раз.")
                continue
            return int(digits), api_hash
        except (KeyboardInterrupt, EOFError):
            print()
            return None


async def _menu_after_captcha(err_text: str, used: tuple[int, str]) -> tuple[int, str] | None:
    """Выбор после 403 RECAPTCHA: свои ключи / другая публичная пара / выход."""
    print()
    print("=" * 62)
    print("Telegram отклонил запрос кода:")
    print("  " + err_text.strip())
    print()
    print("Это анти-спас Telegram, а не поломка: встроенные «публичные» api-ключи")
    print("(в том числе от официальных приложений) он просит подтвердить капчей,")
    print("потому что ими массово пользуются боты. Ваш аккаунт и ПК ни при чём.")
    print()
    print("  1 - ввести СВОИ api_id/api_hash из my.telegram.org (надёжно, рекомендую)")
    print("  2 - попробовать другую публичную пару (может снова упереться в капчу)")
    print("  Enter - выйти")
    try:
        choice = input("Выбор: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if choice == "1":
        return _ask_own_creds()
    if choice == "2":
        for pair in PUBLIC_PAIRS:
            if pair != used:
                print(f"Пробую запасную пару api_id={pair[0]}…")
                return pair
        print("Запасных публичных пар больше нет.")
    return None


async def _login_once(spath: str, api_id: int, api_hash: str) -> bool:
    """Один заход: вход в Telegram + проверка мини-аппа игры.
    True — всё готово; False — вошёл, но initData не найдена;
    исключение (напр. RECAPTCHA) — пробрасывается наверх."""
    from telethon import TelegramClient
    import config

    client = TelegramClient(spath, api_id, api_hash,
                            device_model="FomoTimerBot", system_version="Windows",
                            app_version="1.0")
    try:
        await client.start()
        me = await client.get_me()
        print(OK)
        uname = f"@{me.username}" if me.username else "без username"
        print(f"Вход выполнен: {me.first_name} ({uname}) — сессия: {spath}.session")

        print("Проверяю доступ к мини-аппу игры…")
        from telethon.tl.functions.messages import RequestAppWebViewRequest
        from telethon.tl.types import InputBotAppShortName

        entity = await client.get_entity(config.FOMO_GAME_BOT)
        app = InputBotAppShortName(id=entity.id, access_hash=entity.access_hash,
                                   short_name=config.FOMO_APP_NAME)
        res = await client(RequestAppWebViewRequest(
            peer=entity, app=app, platform="android", write_allowed=False))
        url = getattr(res, "url", "") or ""
        from urllib.parse import parse_qs, urlsplit
        init = (parse_qs(urlsplit(url).fragment, keep_blank_values=True)
                .get("tgWebAppData") or [None])[0]
        if init:
            import fomo_client
            print("ОК: мини-апп открылся, initData получена "
                  f"({fomo_client.preview_init_data(init)})")
            if config.set_fomo_init_data(init):
                print("Ключ сохранён в .env (FOMO_INIT_DATA) — fomo.txt не "
                      "нужен и не понадобится.")
            print(OK)
            print("Готово! Больше ничего не требуется: бот сам будет")
            print("обновлять initData этой сессией, когда понадобится.")
            return True
        print("Мини-апп открылся, но initData в ссылке не найдена.")
        print("Проверьте FOMO_GAME_BOT / FOMO_APP_NAME в .env.")
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main() -> int:
    try:
        import telethon  # noqa: F401
    except ImportError:
        print("Не установлен telethon. Выполните:")
        print("    .venv\\Scripts\\pip install telethon")
        print("(или запустите install.bat заново — он поставит всё сам)")
        return 1

    import config

    spath = config.USERBOT_SESSION_PATH
    if spath.endswith(".session"):
        spath = spath[:-len(".session")]

    print("Одноразовый вход юзербота Fomo Timer Bot")
    print("-" * 62)
    print("Понадобится: телефон аккаунта Telegram, код из чата Telegram")
    print("(и пароль 2FA, если включён). Данные НЕ отправляются никуда,")
    print("кроме серверов Telegram.")
    print("-" * 62)

    orig = (int(config.USERBOT_API_ID), config.USERBOT_API_HASH.strip())
    api_id, api_hash = orig
    for _attempt in range(4):
        try:
            ok = await _login_once(spath, api_id, api_hash)
            if ok and (api_id, api_hash) != orig:
                if config.set_userbot_api(str(api_id), api_hash):
                    print(f"Ваши api-ключи сохранены в .env (USERBOT_API_ID={api_id}) —"
                          " бот будет пользоваться ими сам.")
            if ok and not config.API_ENABLED:
                if config.set_api_enabled(True):
                    print("Автотрекинг включён (API_ENABLED=true в .env): "
                          "таймеры будут ставиться сами, fomo.txt не нужен.")
            if ok:
                print("Если бот сейчас запущен — он заметит это сам за минуту "
                      "(перезапуск не обязателен).")
            return 0 if ok else 1
        except Exception as e:
            name, text = type(e).__name__, str(e)
            _drop_session(spath)
            if name == "ForbiddenError" and "RECAPTCHA" in text.upper():
                nxt = await _menu_after_captcha(text, (api_id, api_hash))
                if nxt is None:
                    print()
                    print("Выход без входа. Ничего не сломано: бот продолжает работать;")
                    print("повторить вход можно в любой момент (login_bot.bat).")
                    return 1
                api_id, api_hash = nxt
                print("Пробую ещё раз — снова спросит телефон и код из Telegram.")
                continue
            if name == "FloodWaitError":
                print(f"Telegram просит подождать ({text}).")
                print("Запустите login_bot.bat через указанное время — всё получится.")
                return 1
            print(f"Ошибка входа: {name}: {text}")
            print("Сессия не создана. Подсказки: проверьте интернет; номер —")
            print("в формате +7…; если повторится — пришлите скрин этого окна.")
            return 1
    return 1


if __name__ == "__main__":
    _quiet_policy()
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nОтменено.")
        sys.exit(130)
