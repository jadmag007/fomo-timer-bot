"""userbot.py — добыча свежей initData через собственную Telegram-сессию.

Зачем: initData, которую игра принимает как ключ, Telegram выдаёт при
открытии мини-аппа. Когда старая initData окончательно истечёт (сервер игры
перестанет её принимать), бот откроет мини-апп t.me/fomo_fighters_bot/game
от вашего имени через MTProto (Telethon) и заберёт новую initData из
ссылки, которую вернёт Telegram. Никаких HAR и ручных действий.

Сессия создаётся ОДИН РАЗ: запустите login_bot.bat (или python login_userbot.py)
и введите телефон + код из Telegram (+ пароль 2FA, если включён). Рядом с ботом
появится файл userbot.session — это ваш личный вход в Telegram, не передавайте
его никому.

Ключи API_ID/API_HASH: по умолчанию — публичная пара Telegram Desktop; на такие
ключи Telegram часто отвечает «403 RECAPTCHA_CHECK» (анти-спас), поэтому после
первого входа в .env обычно лежат ЛИЧНЫЕ ключи из my.telegram.org. Они читаются
из .env свежими при каждом открытии мини-аппа (USERBOT_API_ID / USERBOT_API_HASH).
"""
import logging
import os
from urllib.parse import parse_qs, urlsplit

import config

log = logging.getLogger("userbot")

_FP_HINT = (
    "Не удалось открыть мини-апп через юзербота. Проверьте, что сессия жива "
    "(снова запустите login_bot.bat), что интернет доступен, а логин в .env "
    "(FOMO_GAME_BOT) соответствует боту игры."
)


def session_ready() -> bool:
    """Есть ли файл сессии юзербота (login_bot.bat уже запускался)."""
    p = config.USERBOT_SESSION_PATH
    if p.endswith(".session"):
        p = p[:-len(".session")]
    return os.path.exists(p + ".session")


def _userbot_creds() -> tuple[int, str]:
    """Свежие api-ключи юзербота. Файл .env в приоритете: login_userbot.py
    мог сохранить туда личные ключи (после 403 RECAPTCHA), а память config
    у работающего бота могла не обновиться."""
    uid = int(config.env_get("USERBOT_API_ID", str(config.USERBOT_API_ID)) or config.USERBOT_API_ID)
    uhash = config.env_get("USERBOT_API_HASH", config.USERBOT_API_HASH)
    if len(uhash) < 30:
        uhash = config.USERBOT_API_HASH
    return uid, uhash


async def refresh_init_data() -> str:
    """Открыть мини-апп игры юзерботом -> свежая строка initData ('' при неудаче).

    Telegram возвращает ссылку вида
        https://game.fomofighters.xyz/#tgWebAppData=<urlencoded initData>
    параметр tgWebAppData — это и есть initData (urlencoded), именно в таком
    виде игра шлёт её в /telegram/auth.
    """
    try:
        from telethon import TelegramClient
        from telethon.tl.functions.messages import RequestAppWebViewRequest
        from telethon.tl.types import InputBotAppShortName
    except ImportError:
        log.warning("telethon не установлен (pip install telethon) — юзербот недоступен")
        return ""

    spath = config.USERBOT_SESSION_PATH
    if spath.endswith(".session"):
        spath = spath[:-len(".session")]
    if not os.path.exists(spath + ".session"):
        log.warning("Сессии юзербота нет (%s.session) — запустите login_bot.bat", spath)
        return ""

    api_id, api_hash = _userbot_creds()
    client = TelegramClient(spath, api_id, api_hash,
                            device_model="FomoTimerBot", system_version="Windows",
                            app_version="1.0")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            log.warning("Сессия юзербота не авторизована — запустите login_bot.bat")
            return ""
        entity = await client.get_entity(config.FOMO_GAME_BOT)
        app = InputBotAppShortName(id=entity.id, access_hash=entity.access_hash,
                                   short_name=config.FOMO_APP_NAME)
        res = await client(RequestAppWebViewRequest(
            peer=entity, app=app, platform="android", write_allowed=False))
        url = getattr(res, "url", "") or ""
        frag = urlsplit(url).fragment
        qs = parse_qs(frag, keep_blank_values=True)
        init = (qs.get("tgWebAppData") or [None])[0]
        if not init:
            log.warning("В ссылке мини-аппа нет tgWebAppData: %.200s", url)
            return ""
        log.info("Юзербот: свежая initData получена (%s симв.)", len(init))
        return init
    except Exception as e:
        log.error("Юзербот: %s: %s", type(e).__name__, e)
        log.debug("Подсказка: %s", _FP_HINT)
        return ""
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
