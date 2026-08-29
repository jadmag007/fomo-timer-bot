"""fomo_client.py — нативный клиент Fomo Fighters: бот сам подписывает запросы
и сам поддерживает ключ жизни. Больше не нужно повторять «замороженные»
подписи из HAR и снимать трафик заново после каждого переподключения игры.

Алгоритмы сняты из кода фронта игры (game.fomofighters.xyz, бандл index-*.js)
и проверены на реальных запросах пользователя и живыми тестами сервера:

  Api-Key    = hash-параметр initData (Telegram подписывает initData при
               каждом открытии мини-аппа; игра использует этот hash как ключ)
  Api-Time   = unixtime в секундах
  Api-Hash   = md5(encodeURIComponent("{api-time}_{тело-запроса}"))
               (в коде игры: tM = XE(encodeURIComponent(`${i}_${t}`)), XE — MD5)
  Is-Beta-Server = строка "null" (cookie отсутствует, fetch сериализует null)

  POST /telegram/auth  {"data":{"initData":…,"photoUrl":"","platform":"weba",
                        "chatId":"","chatType":"sender","chatInstance":"…"}}
               — шлётся с Api-Key: "empty"; сервер проверяет initData у
               Telegram и активирует hash initData как рабочий ключ.
               Повторный auth со СТАРОЙ initData реанимирует ключ после 401
               (проверено: initData 3-дневной давности -> timers HTTP 200).

  POST /user/data/timers {"data":{"lang":"ru"}}
               -> {"success":true,"data":{tBuildings:…,serverTime}}
               (заголовок Api-Version, который игра выводит из hero.version,
                для /user/data/timers необязателен — проверено живьём).

  POST /user/data/all  — тот же формат, но больше данных: те же t*-списки
               + stClanRewards (клановые сундуки на перезарядке),
               stOutpostRewards (награды аванпостов), hero, clan, boxes…
               Опрашивается реже (FOMO_ALL_INTERVAL) — ради наград.

Свежая initData, когда старая окончательно перестанет приниматься, добывается
юзерботом (userbot.py, Telethon): он открывает мини-апп t.me/fomo_fighters_bot/game
от вашего имени и берёт initData из ссылки, которую выдаёт Telegram.
"""
import asyncio
import base64
import hashlib
import json
import logging
import time
from urllib.parse import quote

import aiohttp

import config

log = logging.getLogger("fomo_client")


class FomoAuthError(Exception):
    """initData не принята даже после auth (и юзербот не помог/не настроен)."""


class FomoNetworkError(RuntimeError):
    """Сетевой сбой (нет связи с api.fomofighters.xyz).

    ВАЖНО отличать от FomoAuthError: раньше обрыв сети возвращал status=None,
    и _signed_call трактовал это как «мёртвый ключ» — дёргал auth, юзербота
    (Telethon) и слал владельцу ложное «initData больше не принимается».
    Теперь сеть — отдельное исключение: цикл опроса просто подождёт следующего
    тика, ключ остаётся рабочим.
    """


# ---------- Точный порт подписи из JS ----------

def js_encode_uri_component(s: str) -> str:
    """Аналог JS encodeURIComponent: НЕ кодирует A-Za-z0-9 и -_.!~*'()."""
    return quote(s, safe="-_.!~*'()")


def api_hash(api_time: int, body: str) -> str:
    """Api-Hash = md5(encodeURIComponent('{time}_{body}'))."""
    raw = f"{api_time}_{body}"
    return hashlib.md5(js_encode_uri_component(raw).encode("utf-8")).hexdigest()


def init_data_hash(init_data: str) -> str:
    """hash-параметр из строки initData (без декодирования значений)."""
    for part in (init_data or "").split("&"):
        name, _, value = part.partition("=")
        if name == "hash":
            return value
    return ""


def build_auth_body(init_data: str, chat_instance: str = "") -> dict:
    """Тело POST /telegram/auth — та же форма, что у игры (проверено)."""
    return {"data": {
        "initData": init_data,
        "photoUrl": "",
        "platform": "weba",
        "chatId": "",
        "chatType": "sender",
        "chatInstance": chat_instance or "",
    }}


def _fnv1a(s: str) -> int:
    """FNV-1a 32-bit (порт rM из кода игры)."""
    h = 2166136261
    for ch in s:
        h ^= ord(ch) & 255
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _xorshift(e: int) -> int:
    """xorshift32 (порт iM из кода игры)."""
    e &= 0xFFFFFFFF
    e ^= (e << 13) & 0xFFFFFFFF
    e ^= e >> 17
    e ^= (e << 5) & 0xFFFFFFFF
    return e & 0xFFFFFFFF


def decrypt_api_version(version_b64: str, update_date: str, hero_id: str) -> str:
    """Api-Version: расшифровка hero.version (порт aM/nM из кода игры).

    Игра берёт hero.version (base64url), XOR-ит байты ключом
    xorshift(FNV-1a('updateDate|id')), ключ обновляется каждые 4 байта.
    Проверено живым сервером: полученная строка принимается как Api-Version.
    По умолчанию НЕ используется: /user/data/timers отвечает и без него.
    """
    if not (version_b64 and update_date and hero_id):
        return ""
    t = version_b64.replace("-", "+").replace("_", "/")
    t += "=" * (-len(t) % 4)
    raw = base64.b64decode(t)
    h = _fnv1a(f"{update_date}|{hero_id}")
    out, ks, key4 = bytearray(), 4, b""
    for byte in raw:
        if ks >= 4:
            h = _xorshift(h)
            key4 = bytes([h & 255, (h >> 8) & 255, (h >> 16) & 255, (h >> 24) & 255])
            ks = 0
        out.append(byte ^ key4[ks])
        ks += 1
    try:
        return out.decode("ascii")
    except UnicodeDecodeError:
        return ""


# ---------- Клиент ----------

class FomoClient:
    """Держит initData, сам делает auth и подписывает каждый запрос.

    Порядок действий при проблеме ключа (401 / invalid_hash):
      1. auth() с сохранённой initData (реанимация — работает почти всегда).
      2. Если auth не принят — юзербот (Telethon) добывает свежую initData,
         затем снова auth().
      3. Не помогло — FomoAuthError (владельцу придёт понятное сообщение).
    """

    def __init__(self, base_url: str, init_data: str, lang: str = "ru"):
        self.base = (base_url or "https://api.fomofighters.xyz").rstrip("/")
        self.init_data = (init_data or "").strip()
        self.lang = lang or "ru"
        self._auth_hash = ""       # Api-Key (hash из initData)
        self._last_auth = 0.0      # unix последнего auth
        self._lock = asyncio.Lock()

    # -- низкий уровень --

    def _headers(self, body: str) -> dict:
        t = int(time.time())
        return {
            "Content-Type": "application/json",
            "Api-Key": self._auth_hash or "empty",
            "Api-Time": str(t),
            "Api-Hash": api_hash(t, body),
            "Is-Beta-Server": "null",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            "Origin": getattr(config, "FOMO_WEB_ORIGIN", "") or "https://game.fomofighters.xyz",
            "Referer": (getattr(config, "FOMO_WEB_ORIGIN", "") or "https://game.fomofighters.xyz") + "/",
        }

    async def _post(self, session: aiohttp.ClientSession, path: str, body_obj: dict):
        body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False)
        try:
            async with session.post(self.base + path, json=None, data=body.encode("utf-8"),
                                    headers=self._headers(body),
                                    timeout=aiohttp.ClientTimeout(total=25)) as resp:
                text = await resp.text()
                status = resp.status
        except Exception as e:  # сеть
            log.warning("FOMO %s: сетевая ошибка: %s", path, e)
            return None, {"success": False, "error": str(e)}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"success": False, "error": f"non-JSON: {text[:120]}"}
        return status, data

    # -- auth / реанимация --

    async def auth(self, session) -> bool:
        """Активировать/реанимировать ключ через POST /telegram/auth."""
        if not self.init_data:
            return False
        saved_key, saved_time = self._auth_hash, self._last_auth
        self._auth_hash = ""  # как у игры при первом запросе: "empty"
        status, data = await self._post(session, "/telegram/auth",
                                        build_auth_body(self.init_data))
        ok = bool(data.get("success")) if isinstance(data, dict) else False
        if ok:
            self._auth_hash = init_data_hash(self.init_data)
            self._last_auth = time.time()
            log.info("FOMO auth ok (HTTP %s), ключ …%s активен",
                     status, self._auth_hash[-8:])
            return True
        self._auth_hash, self._last_auth = saved_key, saved_time
        log.warning("FOMO auth не принят: HTTP %s %s", status, data.get("error"))
        return False

    async def _fresh_init_data(self) -> str:
        """Свежая initData через юзербота (Telethon); '' — если не вышло."""
        try:
            import userbot
        except Exception:  # telethon не установлен — это не ошибка
            return ""
        try:
            return await userbot.refresh_init_data() or ""
        except Exception:
            log.exception("Юзербот: не удалось получить свежую initData")
            return ""

    def state(self) -> dict:
        """Снимок для /апи: маска ключа и время последнего auth (без приватных полей)."""
        return {
            "auth_hash": (f"…{self._auth_hash[-8:]}" if self._auth_hash else "—"),
            "last_auth": self._last_auth or None,
        }

    # -- основной ход --

    @staticmethod
    def _raise_if_net(status, data, path):
        """status is None = сети нет (ответа сервера не было вовсе).

        Раньше этот случай проваливался в цепочку «реанимации ключа» с
        ложным FomoAuthError и пушем «ключ мёртв» при каждом обрыве сети.
        """
        if status is None:
            raise FomoNetworkError(
                "FOMO %s: сеть недоступна (%s)"
                % (path, (data or {}).get("error", "?")))

    async def _signed_call(self, session, path: str, body_obj: dict) -> dict:
        """Подписанный POST с само-лечением ключа (общий ход для всех
        эндпоинтов): превентивный auth, повтор после 401, юзербот в крайнем
        случае. Возвращает JSON; сетевой сбой — FomoNetworkError,
        мёртвый ключ — FomoAuthError."""
        async with self._lock:
            # Превентивная реанимация ключа раз в FOMO_REAUTH_INTERVAL секунд
            # (сервер может инвалидировать ключ при переподключении игры).
            if not self._auth_hash or (time.time() - self._last_auth
                                       > max(600, config.FOMO_REAUTH_INTERVAL)):
                await self.auth(session)

            status, data = await self._post(session, path, body_obj)
            self._raise_if_net(status, data, path)
            if status == 200 and isinstance(data, dict) and data.get("success"):
                return data

            err_code = (data or {}).get("error_code")
            log.warning("FOMO %s: HTTP %s %s — реанимирую ключ",
                        path, status, err_code)
            if await self.auth(session):
                status, data = await self._post(session, path, body_obj)
                self._raise_if_net(status, data, path)
                if status == 200 and isinstance(data, dict) and data.get("success"):
                    return data

            # Ключ не ожил — initData, похоже, совсем истекла. Юзербот!
            fresh = await self._fresh_init_data()
            if fresh and fresh != self.init_data:
                self.init_data = fresh
                config.set_fomo_init_data(fresh)   # сохранить в .env на будущее
                if await self.auth(session):
                    status, data = await self._post(session, path, body_obj)
                    self._raise_if_net(status, data, path)
                    if status == 200 and isinstance(data, dict) and data.get("success"):
                        return data
            raise FomoAuthError(
                "initData не принята сервером игры даже после auth "
                "(ошибка %s/%s). Нужна свежая initData: запустите "
                "login_bot.bat (юзербот) или положите свежий fomo.txt в "
                "папку бота / token_updates." % (status, err_code or "—"))

    async def get_timers(self, session) -> dict:
        """POST /user/data/timers с самоподписью; при 401 — само-лечение.
        Возвращает распарсенный JSON ответа или бросает FomoAuthError."""
        return await self._signed_call(
            session, "/user/data/timers", {"data": {"lang": self.lang}})

    async def get_all(self, session) -> dict:
        """POST /user/data/all: t*-списки + stClanRewards (клановые сундуки)
        + stOutpostRewards (награды аванпостов). Само-лечение ключа то же."""
        return await self._signed_call(
            session, "/user/data/all", {"data": {"lang": self.lang}})


def preview_init_data(init_data: str) -> str:
    """initData -> короткая маска для отчётов (без личных данных)."""
    h = init_data_hash(init_data)
    return f"hash …{h[-10:]}" if h else "(без hash)"
