"""Автотрекинг: бот сам опрашивает API игры и ставит таймеры.

Как это работает у пользователя:
  1. Один раз включает автотрекинг: юзербот (login_bot.bat) ИЛИ файл fomo.txt
     (перетащить на update_token.bat, в папку token_updates/ или просто в
     корень папки бота — watcher подхватит сам).
  2. Дальше всё автоматически: бот опрашивает /user/data/timers раз в
     API_POLL_INTERVAL секунд, точный парсер разбирает ответ, новые таймеры
     ставятся молча (режим подтверждения Да/Нет включается командой /вопросы).

Нативный режим (есть FOMO_INIT_DATA): бот сам подписывает каждый запрос
(fomo_client.py) и сам реанимирует ключ через /telegram/auth — HAR-файлы
нужны только в крайнем случае.

Запасной режим (подписи из файла): подписи api-* повторяются как есть и
умирают при переподключении игры — тогда 401, и нужен свежий fomo.txt.

Точный парсер Fomo Fighters: списки t* из ответа (tBuildings/tTroops/tSkills…)
+ ЛЮБЫЕ поля-даты вне этих списков (клановые сундуки, награды аванпостов,
кулдауны — метка «✨»). Переводы ключей — в translations.py. Неизвестные
ключи показываются английским именем; чтобы найти их и перевести, включите
трассировку (/трассировка) и пришлите себе файл лога (/трейслог).
"""
import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp

import config
import db
import fomo_client
import apitrace as trace_mod
import translations as tr
from tools import har_inspect
from util import fmt_clock, local_str, safe_tz

log = logging.getLogger("api_poller")

# Память о уже поставленных по API таймерах: {(label, минутный_бакет), ...}
_SEEN = set()
_SEEN_MAX = 20000

# Снимок состояния для команды /api
_STATE = {
    "last_poll": None,     # unix последнего ответа API
    "last_status": None,   # последний HTTP-код
    "last_error": "",      # последняя сетевая ошибка
    "token_dead": False,   # True, пока API отвечает 401/403
    "dead_notified": False,
    "added_total": 0,      # сколько авто-таймеров поставлено за всё время
    "proposed_total": 0,   # сколько таймеров предложено кнопками Да/Нет
}


def reset_state():
    """Сброс после обновления токена (вызывает watcher)."""
    _SEEN.clear()
    _STATE.update(last_poll=None, last_status=None, last_error="",
                  token_dead=False, dead_notified=False)


def state_urls():
    """API_STATE_URL -> список URL (поддерживаем несколько через запятую)."""
    return [u.strip() for u in config.API_STATE_URL.split(",") if u.strip()]


def auth_headers():
    """"Authorization: Bearer eyJ..." -> dict для запроса."""
    line = config.API_AUTH_HEADER
    name, _, value = line.partition(":")
    if value:
        return {name.strip(): value.strip()}
    if line:
        return {"Authorization": line.strip()}
    return {}


def extra_headers():
    """API_HEADERS_JSON (подписи api-* и т.п.) -> dict заголовков."""
    if not config.API_HEADERS_JSON:
        return {}
    try:
        d = json.loads(config.API_HEADERS_JSON)
    except json.JSONDecodeError:
        log.warning("API_HEADERS_JSON не разобрался — подписи не отправляются")
        return {}
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def owner():
    """Кому ставить автотаймеры: из .env (API_OWNER_TG_ID) или первый /start."""
    if config.API_OWNER_TG_ID:
        return db.get_user(config.API_OWNER_TG_ID)
    return db.first_user()


# ---------- Нативный режим Fomo Fighters (самоподпись + авто-реанимация) ----------

_FOMO = None  # fomo_client.FomoClient, живёт между опросами


def native_mode() -> bool:
    """True, если есть initData — бот сам подписывает запросы и сам чинит ключ."""
    return bool(config.FOMO_INIT_DATA)


def fomo_state():
    """Краткое состояние нативного клиента для /api."""
    if not _FOMO:
        return {"auth_hash": "", "last_auth": None}
    return _FOMO.state()


async def _poll_fomo_native(bot=None) -> int:
    """Опрос /user/data/timers в нативном режиме (initData есть).
    Ключ реанимируется сам: auth при старте, при 401 и раз в FOMO_REAUTH_INTERVAL.
    Если initData совсем истечёт — свежую добудет юзербот (userbot.py)."""
    global _FOMO
    added = 0
    if not native_mode():
        return 0
    if _FOMO is None or _FOMO.init_data != config.FOMO_INIT_DATA:
        _FOMO = fomo_client.FomoClient(config.FOMO_API_BASE, config.FOMO_INIT_DATA,
                                       lang=config.FOMO_LANG)
    async with aiohttp.ClientSession() as session:
        try:
            data = await _FOMO.get_timers(session)
        except fomo_client.FomoAuthError as e:
            _STATE.update(last_poll=time.time(), last_status=None,
                          last_error=str(e)[:200], token_dead=True)
            if not _STATE["dead_notified"]:
                _STATE["dead_notified"] = True
                log.error("FOMO нативный: %s", e)
                await notify_owner(
                    bot,
                    "🔑 <b>initData больше не принимается сервером игры.</b>\n"
                    "Запустите <code>login_bot.bat</code> (одноразовый вход — дальше "
                    "бот всё будет обновлять сам) или положите свежий "
                    "<code>fomo.txt</code> в папку бота / <code>token_updates</code>.")
            return 0
        _STATE.update(last_poll=time.time(), last_status=200, last_error="")
        if _STATE["token_dead"]:
            _STATE.update(token_dead=False, dead_notified=False)
            await notify_owner(bot, "🔑 Ключ снова работает ✅ — таймеры продолжают обновляться.")
        found = extract_fomo(data)
        if config.API_ASK_BEFORE_ADD:
            await propose_new(bot, found)
        else:
            for up in found:
                added += maybe_add(up)
        if trace_mod.enabled():
            trace_mod.log_response("native", 200, data, found=found, added=added)
    return added


# ---------- Автоматический разбор ответа (запасной режим, не-FOMO API) ----------

_NAME_KEYS = ("name", "title", "label", "building", "type", "key")
_LABEL_MAX = 40


def iter_time_hits(node, hint=None):
    """Рекурсивно собрать (метка, ключ, значение, тип) для всех полей-таймеров.

    Метка — понятное имя объекта (name/title/… из этого же или родительского
    словаря), чтобы таймер в списке выглядел как «Лесопилка · upgrade_finished_at».
    """
    if isinstance(node, dict):
        local_hint = None
        for k in _NAME_KEYS:
            v = node.get(k)
            if isinstance(v, str) and 0 < len(v) <= _LABEL_MAX and not har_inspect.JWT_RE.search(v):
                local_hint = v.strip()
                break
        cur = local_hint or hint
        for k, v in node.items():
            k = str(k)
            if har_inspect.TIME_KEY_RE.search(k):
                kind = har_inspect.classify_time_value(v)
                if kind:
                    yield (cur, k, v, kind)
            yield from iter_time_hits(v, cur)
    elif isinstance(node, list):
        for v in node[:80]:  # глубже 80 элементов массива не ходим
            yield from iter_time_hits(v, hint)


def to_ts(v, kind, now):
    """Значение поля -> unix-время окончания. None, если распознать не удалось."""
    try:
        if kind == "unix":
            return float(v)
        if kind == "ms":
            return float(v) / 1000.0
        if kind == "delta":
            return now + float(v)
        if kind == "clock":
            parts = [int(x) for x in str(v).strip().split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            h, m, s = parts[-3:]
            return now + h * 3600 + m * 60 + s
        if kind == "iso":
            dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None
    return None


def extract_upgrades(state_json, now=None):
    """АВТОМАТ: все поля ответа, похожие на время окончания -> [{label, ends_at}].

    Фильтры: не старше 5 минут (уже закончившееся молча пропускаем) и не
    дальше 60 суток. Уникальность метки не требуется — дедупликация в maybe_add.
    """
    now = now if now is not None else time.time()
    out = []
    for hint, key, v, kind in iter_time_hits(state_json):
        ts = to_ts(v, kind, now)
        if ts is None:
            continue
        if ts < now - 300 or ts > now + 60 * 24 * 3600:
            continue
        label = f"{hint} · {key}" if hint and hint != key else key
        out.append({"label": label, "ends_at": ts})
    return out


# ---------- Точный разбор ответа Fomo Fighters (/user/data/timers) ----------

_GAME_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _parse_game_date(s):
    """'2026-08-27 21:24:41' (UTC, проверено по логу) -> unix или None."""
    if not isinstance(s, str) or not _GAME_DATE_RE.match(s.strip()):
        return None
    try:
        dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _extra_from_data(d, now):
    """Поля-времена ВНЕ t*-списков: клановые сундуки (раз в час), награды
    аванпостов (раз в 4 часа), кулдауны и прочее. Раньше такие таймеры бот
    не видел вовсе — теперь ставит их с меткой «✨», а трассировка
    (/трассировка) помогает перевести их ключи в translations.py."""
    rest = {k: v for k, v in d.items()
            if not (str(k).startswith("t") and isinstance(v, list))}
    out = []
    for hint, key, v, kind in iter_time_hits(rest):
        ts = to_ts(v, kind, now)
        if ts is None:
            continue
        if ts < now - 300 or ts > now + 60 * 24 * 3600:
            continue
        label = f"✨ {hint} · {tr.pretty(key)}" if hint and hint != key else f"✨ {tr.pretty(key)}"
        out.append({"label": label, "ends_at": ts})
    return out


def extract_fomo(state_json, now=None):
    """Точный парсер Fomo Fighters: /user/data/timers -> [{label, ends_at}].

    Формат: {"success":true,"data":{"tBuildings":[{dateEnd,buildingKey,…}],
    …,"serverTime":мс}}. Дата-строки — UTC (проверено: dateStart совпадает
    с моментом клика в логе), и мы калибруем их по serverTime — так уходит
    и сдвиг пояса, и рассинхрон часов. Поля-времена вне t*-списков собирает
    _extra_from_data. Не-FOMO JSON отдаём общему автомату.
    """
    if not isinstance(state_json, dict):
        return extract_upgrades(state_json, now)
    d = state_json.get("data")
    if not isinstance(d, dict) or not any(str(k).startswith("t") for k in d):
        return extract_upgrades(state_json, now)
    now = now if now is not None else time.time()
    try:
        base = float(d.get("serverTime")) / 1000.0 if d.get("serverTime") else now
    except (TypeError, ValueError):
        base = now
    out = []
    for bucket_key, items in d.items():
        b = str(bucket_key)
        if not (b.startswith("t") and isinstance(items, list)):
            continue  # не t*-список — соберёт второй проход (_extra_from_data)
        ru = tr.bucket(b)
        for it in items[:40]:
            if not isinstance(it, dict):
                continue
            ts = _parse_game_date(it.get("dateEnd"))
            if ts is None:
                continue
            ends = now + (ts - base)
            if ends < now - 300 or ends > now + 60 * 24 * 3600:
                continue
            out.append({"label": tr.item_label(ru, it), "ends_at": ends})
    out.extend(_extra_from_data(d, now))
    return out


def extract_from_har(path, now=None):
    """HAR-файл -> находки [{label, ends_at, url}] по всем JSON-ответам.

    Эндпоинты со «говорящими» адресами (…/timers, …/rooms/…) разбираем
    первыми. Для файлов «Copy as cURL» вернёт [] (тел ответов там нет).
    """
    try:
        rows = har_inspect.parse_har(path)
    except Exception:  # не HAR (например, cURL-текст) — предложений не будет
        return []
    now = now if now is not None else time.time()
    rows.sort(key=lambda r: (
        0 if (r["time_fields"] and har_inspect.URL_PRIORITY_RE.search(r["url"] or "")) else 1,
        -len(r["time_fields"]),
    ))
    out, seen = [], set()
    for r in rows:
        if not r["parsed"] or not r["time_fields"]:
            continue
        for up in extract_upgrades(r["parsed"], now=now):
            key = (up["label"], round(float(up["ends_at"]) / 60))
            if key in seen:
                continue
            seen.add(key)
            up["url"] = r["url"]
            out.append(up)
    return out


def maybe_add(up):
    """Поставить авто-таймер с защитой от дублей. -> 1, если добавлен."""
    label, ends_at = up["label"], float(up["ends_at"])
    key = (label, round(ends_at / 60))  # бакет в минуту: мелкий дрейф не плодит ключи
    if key in _SEEN:
        return 0
    user = owner()
    if not user:
        log.warning("API: некому ставить таймер — откройте боту /start или "
                    "заполните API_OWNER_TG_ID в .env")
        return 0
    # Уже стоит почти такой же активный таймер (поле remaining «плывёт» на пару секунд)?
    for t in db.active(user["tg_id"]):
        if t["label"] == label and abs(t["ends_at"] - ends_at) <= 180:
            _SEEN.add(key)
            return 0
    db.add_timer(user["tg_id"], user["tg_id"], label, ends_at)
    _SEEN.add(key)
    _STATE["added_total"] += 1
    log.info("API: авто-таймер %r -> %s", label, ends_at)
    if len(_SEEN) > _SEEN_MAX:
        _SEEN.clear()
    return 1


# ---------- Предложения «добавить таймеры?» (Да/Нет) ----------

_PENDING = {}       # gid -> {"chat": tg_id, "entries": [...], "ts": float}
_DECLINED = set()   # (метка, минутный бакет) — на это уже сказали «нет»
_PENDING_MAX = 12   # сколько последних партий предложений помним
_PROPOSAL_CAP = 10  # максимум строк в одном предложении


def _entry_key(up):
    return (up["label"], round(float(up["ends_at"]) / 60))


def build_proposals(entries, now=None):
    """Сырые находки -> чистый список для предложения пользователю.

    Отсекаем: истёкшее (>5 мин назад), слишком далёкое (>60 суток), дубли
    (метка+минута), уже стоящие в БД (±3 мин) и ранее отклонённое.
    """
    now = now if now is not None else time.time()
    user = owner()
    active = db.active(user["tg_id"]) if user else []
    out, seen = [], set()
    for up in sorted(entries, key=lambda u: float(u["ends_at"])):
        ts = float(up["ends_at"])
        if ts < now - 300 or ts > now + 60 * 24 * 3600:
            continue
        key = _entry_key(up)
        if key in seen or key in _DECLINED or key in _SEEN:
            continue
        if any(t["label"] == up["label"] and abs(t["ends_at"] - ts) <= 180 for t in active):
            _SEEN.add(key)  # уже стоит — молча запоминаем, больше не предлагаем
            continue
        seen.add(key)
        out.append({"label": up["label"], "ends_at": ts})
        if len(out) >= _PROPOSAL_CAP * 2:
            break
    return out[:_PROPOSAL_CAP]


def register_pending(chat_id, entries):
    """Запомнить партию предложений, вернуть gid для кнопок Да/Нет.
    Все предложения помечаются «показанными», чтобы не спрашивать дважды."""
    gid = int(time.time() * 1000) % 1_000_000_000
    while gid in _PENDING:
        gid += 1
    _PENDING[gid] = {"chat": chat_id, "entries": list(entries), "ts": time.time()}
    for up in entries:
        _SEEN.add(_entry_key(up))
    if len(_PENDING) > _PENDING_MAX:
        for k in sorted(_PENDING, key=lambda k: _PENDING[k]["ts"])[:-_PENDING_MAX]:
            _PENDING.pop(k, None)
    return gid


def confirm_group(gid, now=None):
    """Кнопка «Да»: поставить предложенные таймеры. -> (сколько, [{label, ends_at}])."""
    prop = _PENDING.pop(gid, None)
    if not prop:
        return 0, []
    user = owner()
    if not user:
        return 0, []
    now = now if now is not None else time.time()
    added = []
    for up in prop["entries"]:
        ts = float(up["ends_at"])
        if ts < now - 300:
            continue
        if any(t["label"] == up["label"] and abs(t["ends_at"] - ts) <= 180
               for t in db.active(user["tg_id"])):
            continue
        db.add_timer(user["tg_id"], user["tg_id"], up["label"], ts)
        _STATE["added_total"] += 1
        added.append({"label": up["label"], "ends_at": ts})
    if added:
        log.info("API: по кнопке «Да» добавлено таймеров: %s", len(added))
    return len(added), added


def decline_group(gid):
    """Кнопка «Нет»: забыть партию и не предлагать эти таймеры снова."""
    prop = _PENDING.pop(gid, None)
    if not prop:
        return False
    for up in prop["entries"]:
        _DECLINED.add(_entry_key(up))
    if len(_DECLINED) > _SEEN_MAX:
        _DECLINED.clear()
    log.info("API: пользователь отклонил партию из %s таймеров", len(prop["entries"]))
    return True


def proposal_text(entries, tz=None):
    """Человекочитаемый список находок для сообщения с кнопками."""
    now = time.time()
    if tz is None:
        user = owner()
        tz = safe_tz(user["tz"] if user else config.DEFAULT_TZ)
    lines = [f"👀 <b>Нашёл {len(entries)} таймер(ов)</b> в данных игры:", ""]
    for up in entries:
        rem = max(0, int(up["ends_at"] - now))
        lines.append(
            f"• {html.escape(up['label'])}\n"
            f"   ⏳ осталось <code>{fmt_clock(rem)}</code> → {local_str(up['ends_at'], tz)}"
        )
    lines.append("")
    lines.append("Добавить их в бот? «Нет» — больше не спрошу про эти.")
    return "\n".join(lines)


def proposal_kb(gid):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, добавить", callback_data=f"tadd:{gid}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"tdeny:{gid}"),
    ]])


async def propose_new(bot, found):
    """Показать новые находки кнопками Да/Нет (режим подтверждения)."""
    props = build_proposals(found)
    user = owner()
    if not props or not user or not bot:
        return 0
    gid = register_pending(user["tg_id"], props)
    _STATE["proposed_total"] += len(props)
    try:
        await bot.send_message(user["tg_id"], proposal_text(props),
                               reply_markup=proposal_kb(gid))
    except Exception:
        log.exception("Не удалось отправить предложение таймеров")
        return 0
    return len(props)


async def notify_owner(bot, text):
    if not bot:
        return
    user = owner()
    if not user:
        return
    try:
        await bot.send_message(user["tg_id"], text)
    except Exception:
        log.exception("Не удалось отправить сообщение владельцу")


# ---------- Опрос ----------

async def poll_once(bot=None):
    """Один опрос API: новые улучшения -> авто-таймеры в БД.
    Нативный режим (есть FOMO_INIT_DATA) — самоподпись, HAR не нужен."""
    if native_mode():
        return await _poll_fomo_native(bot)
    added = 0
    method = (config.API_METHOD or "GET").upper()
    data_body = config.API_BODY.encode("utf-8") if config.API_BODY else None
    headers = {**extra_headers(), **auth_headers()}
    if data_body and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    async with aiohttp.ClientSession() as session:
        for url in state_urls():
            try:
                async with session.request(
                    method, url, headers=headers, data=data_body,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    status = resp.status
                    text = await resp.text()
            except Exception as e:
                _STATE.update(last_status=None, last_error=str(e)[:200])
                log.warning("Сетевая ошибка при опросе %s: %s", _host(url), e)
                continue

            _STATE.update(last_poll=time.time(), last_status=status, last_error="")

            if status in (401, 403):
                if not _STATE["token_dead"]:
                    _STATE.update(token_dead=True, dead_notified=True)
                    log.warning("API: токен не принят (HTTP %s) — жду файл в token_updates/", status)
                    await notify_owner(
                        bot,
                        "🔑 <b>Токен автотрекинга устарел</b> (HTTP %s).\n"
                        "Положите свежий <code>fomo.txt</code> в папку бота или в "
                        "<code>token_updates</code> — обновлю всё сам." % status,
                    )
                continue

            if status != 200:
                log.warning("API: HTTP %s от %s", status, _host(url))
                continue

            if _STATE["token_dead"]:
                _STATE.update(token_dead=False)
                await notify_owner(bot, "🔑 Токен снова работает ✅ — автотрекинг продолжает ставить таймеры.")

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                log.error("API: ответ не JSON от %s: %.120s", _host(url), text)
                continue

            found = extract_fomo(data)
            if config.API_ASK_BEFORE_ADD:
                await propose_new(bot, found)   # спросим Да/Нет
            else:
                for up in found:
                    added += maybe_add(up)      # ставим молча
            if trace_mod.enabled():
                trace_mod.log_response("poll", status, data, found=found, added=added)
    return added


def _host(url):
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def status():
    """Снимок для команды /api."""
    urls = state_urls()
    native = native_mode()
    return {
        "enabled": bool(config.API_ENABLED),
        "configured": native or bool(urls and config.API_AUTH_HEADER),
        "native": native,
        "hosts": [config.FOMO_API_BASE.replace("https://", "")] if native else [_host(u) for u in urls],
        "auth_preview": (config.API_AUTH_HEADER[:34] + "…") if len(config.API_AUTH_HEADER) > 34 else (config.API_AUTH_HEADER or "—"),
        "interval": max(20, config.API_POLL_INTERVAL),
        "last_poll": _STATE["last_poll"],
        "last_status": _STATE["last_status"],
        "token_dead": _STATE["token_dead"],
        "added_total": _STATE["added_total"],
        "proposed_total": _STATE["proposed_total"],
        "ask_mode": bool(config.API_ASK_BEFORE_ADD),
        "trace": bool(config.API_TRACE),
        "last_error": _STATE["last_error"],
    }


async def poll_forever(_bot=None):
    """Вечный цикл. Пока настройки нет — тихо ждёт (файл в token_updates/ или
    в корне папки включит всё сам)."""
    bot = _bot
    if not config.API_ENABLED:
        log.info("Автотрекинг выключен — модуль ждёт fomo.txt (корень папки "
                 "или %s/), тогда включится сам.", config.TOKEN_UPDATES_DIR)
    else:
        log.info("Автотрекинг запущен (интервал %ss)", max(20, config.API_POLL_INTERVAL))
    while True:
        try:
            if not config.API_ENABLED:
                await asyncio.sleep(5)
                continue
            if not (native_mode() or (state_urls() and config.API_AUTH_HEADER)):
                await asyncio.sleep(5)
                continue
            try:
                await poll_once(bot)
            except Exception:
                log.exception("Ошибка опроса API (продолжаю по расписанию)")
            # Пока ключ мёртв — опрашиваем редко (раз в 5 мин), чтобы поймать
            # момент, когда пользователь положил свежий fomo.txt.
            await asyncio.sleep(300 if _STATE["token_dead"] else max(20, config.API_POLL_INTERVAL))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Неожиданная ошибка в цикле автотрекинга")
            await asyncio.sleep(10)
