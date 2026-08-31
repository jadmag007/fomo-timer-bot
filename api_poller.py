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
+ ЛЮБЫЕ поля-даты вне этих списков (кулдауны — метка «✨»). Переводы ключей —
в translations.py. Неизвестные ключи показываются английским именем; чтобы
найти их и перевести, включите трассировку (/трассировка) и пришлите себе
файл лога (/трейслог).

Клановые сундуки и награды аванпостов в /user/data/timers НЕ приходят — они
в /user/data/all (списки stClanRewards/stOutpostRewards), этот эндпоинт бот
опрашивает раз в FOMO_ALL_INTERVAL секунд (extract_all_timers).
"""
import asyncio
import html
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp

import config
import db
import fomo_client
import pause_state
import pollbrain
import apitrace as trace_mod
import sched_push
import translations as tr
from tools import har_inspect
from util import fmt_clock, local_str, safe_tz

log = logging.getLogger("api_poller")

# Память о уже поставленных по API таймерах: {(label, минутный_бакет), ...}
_MAX_SEEN = 20000


class _SeenSet(set):
    """set с порядком вставки: при переполнении вытесняются СТАРЕЙШИЕ ключи.

    Прежний вариант делал _SEEN.clear(): вместе с мусором стирались и
    ready_key созревших наград («пора забрать») — а награда висит в ответе
    API, пока её не заберут, и на каждом цикле уходил повторный пуш.
    """

    def __init__(self):
        super().__init__()
        self._order = []

    def add(self, key):
        if key in self:
            return
        super().add(key)
        self._order.append(key)
        while len(self._order) > _MAX_SEEN:
            super().discard(self._order.pop(0))

    def clear(self):
        super().clear()
        self._order.clear()


_SEEN = _SeenSet()
_SEEN_MAX = _MAX_SEEN  # совместимость со старыми тестами/скриптами

# Снимок состояния для команды /api
_STATE = {
    "last_poll": None,     # unix последнего ответа API
    "last_all_poll": 0.0,  # unix последнего /user/data/all (клановые сундуки)
    "last_all_status": None,  # HTTP последнего /user/data/all (None — не был)
    "last_all_found": 0,   # сколько наград увидел в последнем all-ответе
    "last_all_added": 0,   # сколько из них поставлено
    "last_all_error": "",  # последняя ошибка all-опроса (пусто — всё ок)
    "last_status": None,   # последний HTTP-код
    "last_error": "",      # последняя сетевая ошибка
    "token_dead": False,   # True, пока API отвечает 401/403
    "dead_notified": False,
    "added_total": 0,      # сколько авто-таймеров поставлено за всё время
    "proposed_total": 0,   # сколько таймеров предложено кнопками Да/Нет
    "reauth_seen": 0.0,    # unix последней РЕАНИМАЦИИ ключа, уже показанной владельцу
    "quiet": False,        # тихий режим: опросы остановлены (3 опроса без нового таймера)
    "quiet_strikes": 0,    # сколько опросов подряд без изменений в таймерах
}


def reset_state():
    """Сброс после обновления токена (вызывает watcher)."""
    global _LAST_SNAPSHOT
    _SEEN.clear()
    _LAST_SNAPSHOT = None
    _STATE.update(last_poll=None, last_all_poll=0.0, last_all_status=None,
                  last_all_found=0, last_all_added=0, last_all_error="",
                  last_status=None, last_error="", token_dead=False,
                  dead_notified=False, quiet=False, quiet_strikes=0)


def state_urls():
    """API_STATE_URL -> список URL (поддерживаем несколько через запятую)."""
    return [u.strip() for u in config.API_STATE_URL.split(",") if u.strip()]


# ---------- Тихий режим (скрытность + экономия батареи) ----------
# Если 3 опроса подряд таймеры НЕ изменились — игрок явно не в игре, и каждый
# следующий опрос оставляет след на сервере игры и будит сеть телефона
# без пользы. Опросник переходит на АВТОПУЛЬС: одна проверка раз в случайные
# 30–55 минут (POLL_PULSE_MIN/MAX) — сам находит новые таймеры, тапать
# ничего не нужно. Пуши уже поставленных таймеров и отложенные пуши на
# серверах Telegram продолжают работать как ни в чём не бывало.
# НОЧЬ (pollbrain.is_night) перекрывает всё: в окне ночи запросов нет
# вовсе (либо 1–2 микротика, если включены в меню).

QUIET_STRIKES_MAX = 3   # сколько опросов подряд без изменений -> засыпаем
_WAKE = False           # флаг явного «разбудить» (страница/команда/файл)
_LAST_SNAPSHOT = None   # frozenset(label, ends_at) последнего опроса

# --- Будильники без тапков (0.1.1.3, мозг опросника — pollbrain.py) ---
_WAKE_AT = 0.0          # запланированное пробуждение после сообщения владельца
_PUSH_POLL_AT = 0.0     # контрольный опрос после доставленного «⏰ Готово»
_LAST_USER_TS = 0.0     # когда владелец В ПОСЛЕДНИЙ РАЗ писал боту («занят»)
_LAST_USER_ID = 0
_OWNER_ID_CACHE = (0.0, 0)   # кэш id владельца (60 с), чтобы не дёргать БД


def _owner_id_cached():
    now = time.time()
    ts, oid = _OWNER_ID_CACHE
    if now - ts < 60:
        return oid
    oid = 0
    try:
        if config.API_OWNER_TG_ID:
            oid = int(config.API_OWNER_TG_ID)
        else:
            u = db.first_user()
            oid = int(u["tg_id"]) if u else 0
    except Exception:
        oid = 0
    globals()["_OWNER_ID_CACHE"] = (now, oid)
    return oid


def note_user_seen(tg_id=0):
    """Владелец проявился (сообщение/кнопка бота) — планируем пробуждение.

    Работает на ЛЮБОМ действии в боте, ничего специально тапать не нужно.
    Пробуждение через случайные WAKE_DELAY 3–8 минут: игрок часто пишет
    прямо из игры, ранний опрос мог бы поймать переавторизацию и выбить
    сессию. Если будильник уже стоит — берём более ранний из двух.
    """
    global _WAKE_AT, _LAST_USER_TS, _LAST_USER_ID
    try:
        now = time.time()
        owner = _owner_id_cached()
        if owner and tg_id and int(tg_id) != owner:
            return  # чужой пользователь — не будим и «занятым» не считаем
        _LAST_USER_TS = now
        _LAST_USER_ID = int(tg_id or 0)
        cand = now + pollbrain.wake_delay()
        _WAKE_AT = cand if _WAKE_AT <= now else min(_WAKE_AT, cand)
        log.info("Игрок проявился в боте — опрос через %s мин",
                 max(1, int(round((_WAKE_AT - now) / 60))))
    except Exception:
        pass


def note_push_delivered():
    """«⏰ Готово» доставлено — контрольный опрос через 30–120 с.

    Игрок обычно идёт собирать сразу после напоминания: наш опрос в этот
    момент выглядит естественно и ловит таймеры, поставленные при сборе.
    """
    global _PUSH_POLL_AT
    try:
        _PUSH_POLL_AT = time.time() + pollbrain.control_delay()
    except Exception:
        pass


def user_busy(now=None):
    """Владелец недавно писал боту — скорее всего прямо сейчас в игре."""
    now = now if now is not None else time.time()
    return _LAST_USER_TS > 0 and now - _LAST_USER_TS < config.OWNER_BUSY_WINDOW


def reauth_cooldown(now=None):
    """Только что была реанимация ключа — даём игре успокоиться."""
    now = now if now is not None else time.time()
    last = float(getattr(fomo_client, "last_reauth_ts", 0.0) or 0.0)
    return last > 0 and now - last < config.REAUTH_COOLDOWN


def request_wake(reason: str = "") -> None:
    """Разбудить опросчик из тихого режима (безопасно из любого потока)."""
    global _WAKE
    _WAKE = True
    if reason:
        log.info("Тихий режим: запрос на пробуждение (%s)", reason)


def _snapshot(found) -> frozenset:
    """Отпечаток списка таймеров: (метка, конец, округлённый до 5 с).
    ends_at между опросами дрейфует на десятые доли секунды — округление
    убирает ложные «изменения» при полностью неподвижной игре."""
    return frozenset((str(u.get("label")), int(float(u.get("ends_at", 0)) // 5))
                     for u in found or [])


def _quiet_account(found, bot) -> None:
    """Учёт «новизны» после успешного опроса: считать промахи и входить/выходить
    из тихого режима. Список таймеров изменился = игрок активен = не спим."""
    global _LAST_SNAPSHOT
    snap = _snapshot(found)
    changed = snap != _LAST_SNAPSHOT
    _LAST_SNAPSHOT = snap
    if changed:
        if _STATE["quiet"]:
            _STATE.update(quiet=False, quiet_strikes=0)
            log.info("Тихий режим: таймеры изменились — просыпаюсь, опросы вернулись")
            _quiet_note(bot, awake=True)
        else:
            _STATE["quiet_strikes"] = 0
        return
    if _STATE["quiet"]:
        return
    _STATE["quiet_strikes"] = int(_STATE.get("quiet_strikes", 0)) + 1
    if _STATE["quiet_strikes"] >= QUIET_STRIKES_MAX:
        _STATE["quiet"] = True
        log.info("Тихий режим: %s опроса без нового таймера — опросы остановлены "
                 "(пуши поставленных таймеров и отложенные на серверах Telegram "
                 "продолжают работать)", QUIET_STRIKES_MAX)
        _quiet_note(bot, awake=False)


def _quiet_note(bot, awake: bool) -> None:
    """Одно уведомление при входе в тишину и при выходе из неё."""
    if awake:
        text = ("☀️ <b>Опросы вернулись</b> — таймеры обновлены, слежу дальше.")
    else:
        text = ("🌙 <b>Тихий режим</b> — 3 опроса подряд без нового таймера. "
                "Теперь только автопульс (раз в 30–55 мин) — запущенное "
                "уловлю сам, ничего нажимать не нужно.\n\n"
                "Напоминания работают как всегда: поставленные таймеры и "
                "отложенные пуши на серверах Telegram никуда не денутся.\n\n"
                "Заигрался? Просто напиши боту что угодно — проснусь через "
                "несколько минут.")
    import asyncio as _aio
    try:
        _aio.get_running_loop().create_task(notify_owner(bot, text))
    except RuntimeError:
        pass  # нет живого цикла (тесты) — уведомление не нужно


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
    """Кому ставить автотаймеры: из .env (API_OWNER_TG_ID) или первый /start.

    ID из .env — приоритет, а не тупик: если пользователя с таким ID в базе
    нет (свежая установка, база пустая), берём как раньше — первого /start.
    """
    if config.API_OWNER_TG_ID:
        u = db.get_user(config.API_OWNER_TG_ID)
        if u:
            return u
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
    Ключ реанимируется сам: auth при старте и при 401; initData совсем истечёт —
    свежую добудет юзербот (userbot.py)."""
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
                log.error("FOMO нативный: %s", e)
                # Пометку «уведомлено» ставим только при ДОСТАВКЕ: раньше
                # флаг выставлялся до отправки, и при обрыве сети алерт
                # терялся навсегда.
                if await notify_owner(
                        bot,
                        "🔑 <b>initData больше не принимается сервером игры.</b>\n"
                        "Запустите <code>login_bot.bat</code> (одноразовый вход — дальше "
                        "бот всё будет обновлять сам) или положите свежий "
                        "<code>fomo.txt</code> в папку бота / <code>token_updates</code>."):
                    _STATE["dead_notified"] = True
            return 0
        except fomo_client.FomoNetworkError as e:
            # Сеть легла — это НЕ мёртвый ключ: ключ не трогаем, тихо ждём тика
            _STATE.update(last_status=None, last_error=str(e)[:200])
            log.warning("FOMO: %s", str(e)[:160])
            return 0
        _STATE.update(last_poll=time.time(), last_status=200, last_error="")
        await _reauth_note_tick(bot)
        if _STATE["token_dead"]:
            _STATE.update(token_dead=False, dead_notified=False)
            await notify_owner(bot, "🔑 Ключ снова работает ✅ — таймеры продолжают обновляться.")
        found = extract_fomo(data)
        if config.API_ASK_BEFORE_ADD:
            await propose_new(bot, found)
        else:
            for up in found:
                added += maybe_add(up)
        # Сундуки аутпостов, готовые к забору (outpostClaimableCountByOutpostId):
        # это напоминание-ДЕЙСТВИЕ («забери сейчас»), а не новый таймер из игры,
        # поэтому ставим молча и в режиме вопросов тоже — без Да/Нет.
        claim = extract_claimable(data)
        for up in claim_ready_timers(claim):
            added += maybe_add(up)
        # Забранные сундуки (исчезли из ответа игры) — снять их карточки со страницы
        try:
            sync_claim_sticky(claim)
        except Exception:
            log.exception("sync_claim_sticky ошибка")
        _quiet_account(found, bot)  # тихий режим: 3 опроса без изменений = спим
        if trace_mod.enabled():
            trace_mod.log_response("native", 200, data, found=found, added=added)

        # Раз в FOMO_ALL_INTERVAL секунд: /user/data/all — клановые сундуки
        # и награды аванпостов (в /user/data/timers их нет).
        if time.time() - _STATE["last_all_poll"] >= max(60, config.FOMO_ALL_INTERVAL):
            _STATE["last_all_poll"] = time.time()
            try:
                alld = await _FOMO.get_all(session)
                found_all = extract_all_timers(alld)
                all_added = 0   # счётчик именно all-находок (added — весь опрос)
                if config.API_ASK_BEFORE_ADD:
                    await propose_new(bot, found_all)
                else:
                    for up in found_all:
                        all_added += maybe_add(up)
                # Собранные клановые сундуки/награды — снять карточки со страницы.
                # Только после УСПЕШНОГО ответа: сбой сети карточки не трогает.
                try:
                    sync_all_sticky(found_all)
                except Exception:
                    log.exception("sync_all_sticky ошибка")
                _STATE.update(last_all_status=200, last_all_error="",
                              last_all_found=len(found_all), last_all_added=all_added)
                log.info("FOMO /user/data/all: наград в ответе %s, из них новых: %s",
                         len(found_all), all_added)
                if trace_mod.enabled():
                    trace_mod.log_response("all", 200, alld,
                                           found=found_all, added=all_added)
            except fomo_client.FomoAuthError as e:
                # ключ жив (timers только что ответил) — значит беда именно с all
                _STATE.update(last_all_status=None, last_all_error=str(e)[:200])
                log.warning("FOMO /user/data/all не удался: %s", str(e)[:120])
                if trace_mod.enabled():
                    trace_mod.log_event("all", "ОШИБКА /user/data/all: " + str(e)[:400])
            except Exception as e:
                # сеть/JSON — тоже показываем в /апи и в трассировке, чтобы
                # «сундуки не появились» можно было диагностировать без гаданий
                _STATE.update(last_all_status=None, last_all_error=str(e)[:200])
                log.warning("FOMO /user/data/all ошибка: %s", str(e)[:200])
                if trace_mod.enabled():
                    trace_mod.log_event("all", "ОШИБКА /user/data/all: " + str(e)[:400])
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
            out.append({"label": tr.item_label(ru, it), "ends_at": ends, "bucket": b})
    out.extend(_extra_from_data(d, now))
    return out


def extract_all_timers(all_json, now=None):
    """Разбор /user/data/all: клановые сундуки и награды аванпостов.

    В data есть списки stClanRewards/stOutpostRewards — объекты {key,
    dateStart, dateEnd} в том же UTC-формате, что и t*-списки; калибровка
    по serverTime.

    Два случая:
    * dateEnd в будущем — награда на перезарядке: обычный таймер, пуш придёт
      к моменту готовности;
    * dateEnd в ПРОШЛОМ — награда УЖЕ созрела и ждёт забора (запись висит
      в ответе, пока её не заберут). Раньше такие записи молча отбрасывались
      фильтром «не старше 5 минут» — и «пора забрать сундук» не приходило
      никогда (жалоба: «таймер найден, но не работает»). Теперь созревшая
      награда даёт мгновенный таймер с пушем и стабильным dedup-ключом
      ready_key — без повторов на каждый all-цикл.

    t*-списки из этого ответа НЕ разбираем — их каждый цикл отдаёт лёгкий
    /user/data/timers (дедупликация всё равно защитила бы от дублей).
    """
    if not isinstance(all_json, dict):
        return []
    d = all_json.get("data")
    if not isinstance(d, dict):
        return []
    now = now if now is not None else time.time()
    try:
        base = float(d.get("serverTime")) / 1000.0 if d.get("serverTime") else now
    except (TypeError, ValueError):
        base = now
    out = []
    for list_name in ("stClanRewards", "stOutpostRewards"):
        items = d.get(list_name)
        if not isinstance(items, list):
            continue
        for it in items[:60]:
            if not isinstance(it, dict):
                continue
            ts = _parse_game_date(it.get("dateEnd"))
            if ts is None:
                continue
            ends = now + (ts - base)
            if ends > now + 60 * 24 * 3600:
                continue
            label = tr.reward_label(list_name, it)
            if ends >= now - 300:
                out.append({"label": label, "ends_at": ends, "bucket": list_name})
            else:
                # созревшая награда ждёт забора: мгновенный пуш + готовность
                # к дедупликации по содержимому записи, а не по минуте now
                out.append({"label": label, "ends_at": now, "bucket": list_name,
                            "ready_key": ready_key(list_name, it)})
    return out


def ready_key(list_name, item):
    """Стабильный ключ созревшей награды: список + ключ/id/дата записи.

    dateEnd включаем обязательно: у кланового сундука key (clan_N) один и
    тот же в каждом цикле, а вот дата созревания меняется. Ключ должен быть
    уникален НА СОЗРЕВАНИЕ, иначе повторное «пора забрать» после сбора и
    новой перезарядки заглушится старым ключом (до рестарта бота).
    """
    ident = (item.get("key") or item.get("id") or item.get("dateEnd") or "")
    de = item.get("dateEnd") or ""
    if de and ident != de:
        return f"{list_name}:{ident}:{de}"
    return f"{list_name}:{ident}"


def extract_claimable(data):
    """outpostClaimableCountByOutpostId из /user/data/timers -> {outpostId: count}.

    Игра присылает этот словарь ВСЕГДА (в т.ч. когда таймеров нет): сколько
    сундуков аутпостов прямо сейчас можно забрать. Поле-число, не дата, —
    поэтому обычный парсер таймеров его никогда не видел.
    """
    d = data.get("data") if isinstance(data, dict) else None
    m = d.get("outpostClaimableCountByOutpostId") if isinstance(d, dict) else None
    if not isinstance(m, dict):
        return {}
    out = {}
    for k, v in m.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out[str(k)] = n
    return out


_CLAIM_LAST = {}  # outpostId -> счётчик, по которому уже отчитались


def claim_ready_timers(claim, now=None):
    """Готовые к забору сундуки аутпостов -> таймеры с мгновенным пушем.

    Пуш только на ИЗМЕНЕНИЕ счётчика (появился/вырос): 0->1, 1->2. Забрал —
    счётчик обнулился, следующий сундук отчитается как новый. Дедуп —
    через ready_key (claim:outpostId:count): повторы на каждый опрос не приходят.
    """
    now = now if now is not None else time.time()
    out = []
    base = tr.bucket("tOutpostClaimable")
    for oid, n in sorted(claim.items()):
        if _CLAIM_LAST.get(oid) == n:
            continue
        _CLAIM_LAST[oid] = n
        out.append({
            "label": base + (f" · готов к забору ×{n}" if n > 1 else " · готов к забору"),
            "ends_at": now,
            "bucket": "tOutpostClaimable",
            "ready_key": f"claim:{oid}:{n}",
        })
    # исчезнувшие из ответа (забрал) — сброс, чтобы следующий сундук отчитался
    for oid in list(_CLAIM_LAST):
        if oid not in claim:
            _CLAIM_LAST.pop(oid, None)
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


_OWNER_WARN_TS = 0.0  # последнее предупреждение «некому ставить таймер» (анти-спам)
_OWNER_WARN_EVERY = 600.0  # секунды между повторами предупреждения

# Бакеты «ждёт забора» из /user/data/all (клановые сундуки и награды аванпостов)
_ALL_STICKY_BUCKETS = ("stClanRewards", "stOutpostRewards")


def sync_claim_sticky(claim):
    """Забранные сундуки аутпостов убрать со страницы (0.1.1.8).

    Липкая карточка tOutpostClaimable живёт, пока её outpostId числится в
    outpostClaimableCountByOutpostId. Исчез из ответа — сундук забран,
    карточку снимаем. Рост счётчика (1->2) даёт НОВЫЙ ready_key, старая
    карточка «×1» тоже снимается — на странице остаётся актуальная.
    """
    u = owner()
    if not u:
        return 0
    # Актуальные ключи СЕЙЧАС: рост счётчика (1->2) даёт новый ключ —
    # старая карточка «×1» уходит, остаётся только «×2».
    current = {"claim:%s:%s" % (oid, n) for oid, n in claim.items()}
    removed = 0
    for r in db.sticky_rows(u["tg_id"]):
        if r["bucket"] != "tOutpostClaimable":
            continue
        rk = r["ready_key"] or ""
        if rk.startswith("claim:") and rk not in current:
            removed += 1 if db.cancel(u["tg_id"], r["id"]) else 0
    if removed:
        log.info("API: сундуков аутпоста забрано, карточек убрано: %s", removed)
    return removed


def sync_all_sticky(found_all):
    """Собранные клановые сундуки/награды аванпостов убрать со страницы (0.1.1.8).

    Запись st* висит в ответе /user/data/all, пока награда не забрана;
    после сбора она исчезает ИЛИ переходит в перезарядку (dateEnd в будущем).
    В обоих случаях готовый ключ ready_key больше не приходит — липкую
    карточку снимаем (при перезарядке вместо неё появится обычный таймер).
    Вызывается ТОЛЬКО после УСПЕШНОГО all-опроса — сбой сети карточки не трогает.
    """
    u = owner()
    if not u:
        return 0
    matured = {up.get("ready_key") for up in (found_all or [])
               if up.get("ready_key")}
    removed = 0
    for r in db.sticky_rows(u["tg_id"]):
        if r["bucket"] not in _ALL_STICKY_BUCKETS:
            continue
        if (r["ready_key"] or "") not in matured:
            removed += 1 if db.cancel(u["tg_id"], r["id"]) else 0
    if removed:
        log.info("API: клановых/аванпостных наград забрано, карточек убрано: %s",
                 removed)
    return removed


def maybe_add(up):
    """Поставить авто-таймер с защитой от дублей. -> 1, если добавлен."""
    global _OWNER_WARN_TS
    label, ends_at = up["label"], float(up["ends_at"])
    rk = up.get("ready_key")
    if rk:
        # созревшая награда/claimable-сундук: дедуп по содержимому записи,
        # а не по минутному бакету ends_at (он у таких всегда «сейчас»)
        key = ("ready", rk)
    else:
        key = (label, round(ends_at / 60))  # бакет в минуту: мелкий дрейф не плодит ключи
    if key in _SEEN:
        return 0
    user = owner()
    if not user:
        # Десяток наград = десяток одинаковых предупреждений за цикл —
        # предупреждаем не чаще раза в 10 минут, таймеры ждут владельца.
        import time as _t
        now = _t.monotonic()
        if now - _OWNER_WARN_TS >= _OWNER_WARN_EVERY:
            _OWNER_WARN_TS = now
            log.warning("API: некому ставить таймер — откройте боту /start или "
                        "заполните API_OWNER_TG_ID в .env")
        return 0
    # Уже стоит почти такой же активный таймер (поле remaining «плывёт» на пару секунд)?
    for t in db.active(user["tg_id"]):
        if t["label"] == label and (abs(t["ends_at"] - ends_at) <= 180 or rk):
            _SEEN.add(key)
            return 0
    db.add_timer(user["tg_id"], user["tg_id"], label, ends_at,
                 bucket=up.get("bucket", ""),
                 sticky=bool(rk), ready_key=rk or "")
    _SEEN.add(key)
    _STATE["added_total"] += 1
    log.info("API: авто-таймер %r -> %s", label, ends_at)
    sched_push.kick_schedule(label, ends_at)  # отложенный пуш на серверах Telegram
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
        out.append({"label": up["label"], "ends_at": ts,
                    "bucket": up.get("bucket", "")})
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
        db.add_timer(user["tg_id"], user["tg_id"], up["label"], ts,
                     bucket=up.get("bucket", ""))
        _STATE["added_total"] += 1
        added.append({"label": up["label"], "ends_at": ts})
    if added:
        log.info("API: по кнопке «Да» добавлено таймеров: %s", len(added))
        for it in added:
            sched_push.kick_schedule(it["label"], it["ends_at"])
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
    """Показать новые находки кнопками Да/Нет (режим подтверждения).
    На паузе бот молчит: предложение будет показано после снятия паузы."""
    if pause_state.is_paused():
        return 0
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


async def notify_owner(bot, text) -> bool:
    """Системное сообщение владельцу. -> True, если ДОСТАВЛЕНО (без бота,
    владельца или при сетевом сбое — False: вызывающий решает, повторять ли)."""
    if not bot:
        return False
    user = owner()
    if not user:
        return False
    try:
        await bot.send_message(user["tg_id"], text)
        return True
    except Exception:
        log.exception("Не удалось отправить сообщение владельцу")
        return False


_REAUTH_NOTE_TS = 0.0  # анти-спам уведомления о реанимации ключа (раз в 30 минут)


async def _reauth_note_tick(bot) -> None:
    """Ключ реанимирован? Предупредить владельца: игра могла выкинуть его.

    Бот и игра делят одну сессию: когда бот перелогинивается (/telegram/auth),
    игра может попросить перезайти. Без этого пуша пользователь думал, что
    бот сломался. Не чаще раза в 30 минут (иначе качель 401 замусорит чат).
    """
    global _REAUTH_NOTE_TS
    fc = _FOMO
    if not fc or not fc.last_reauth_ts:
        return
    if fc.last_reauth_ts <= _STATE.get("reauth_seen", 0.0):
        return
    _STATE["reauth_seen"] = fc.last_reauth_ts
    now = time.time()
    if now - _REAUTH_NOTE_TS < 1800:
        return
    _REAUTH_NOTE_TS = now
    await notify_owner(
        bot,
        "🔄 <b>Бот обновил ключ игры</b> (переавторизация).\n\n"
        "Если ты сейчас в игре — она могла попросить перезайти: бот и игра "
        "делят одну сессию, это нормально и не поломка.")


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
                    _STATE.update(token_dead=True)
                    log.warning("API: токен не принят (HTTP %s) — жду файл в token_updates/", status)
                    # dead_notified — только при доставке: при упавшей сети
                    # алерт уйдёт со следующей попытки, а не потеряется.
                    if await notify_owner(
                            bot,
                            "🔑 <b>Токен автотрекинга устарел</b> (HTTP %s).\n"
                            "Положите свежий <code>fomo.txt</code> в папку бота или в "
                            "<code>token_updates</code> — обновлю всё сам." % status):
                        _STATE["dead_notified"] = True
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
            _quiet_account(found, bot)  # тихий режим: 3 опроса без изменений = спим
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
        "interval": max(60, config.API_POLL_INTERVAL),
        "last_poll": _STATE["last_poll"],
        "last_status": _STATE["last_status"],
        "token_dead": _STATE["token_dead"],
        "added_total": _STATE["added_total"],
        "proposed_total": _STATE["proposed_total"],
        "ask_mode": bool(config.API_ASK_BEFORE_ADD),
        "trace": bool(config.API_TRACE),
        "last_error": _STATE["last_error"],
        # /user/data/all (клановые сундуки, награды аванпостов) — чтобы «сундуки
        # не появились» диагностировались на экране /апи без консоли и гаданий
        "last_all_poll": _STATE["last_all_poll"] or None,
        "all_interval": max(60, config.FOMO_ALL_INTERVAL),
        "last_all_status": _STATE["last_all_status"],
        "last_all_found": _STATE["last_all_found"],
        "last_all_added": _STATE["last_all_added"],
        "last_all_error": _STATE["last_all_error"],
        "quiet": bool(_STATE.get("quiet")),
        "quiet_strikes": int(_STATE.get("quiet_strikes", 0)),
        # Мозг опросника: режим, ночное окно, будильники (экран /апи, страница)
        "mode": _mode(),
        "night": _night_info(),
        "wake_at": _WAKE_AT if _WAKE_AT > time.time() else None,
        "push_poll_at": _PUSH_POLL_AT if _PUSH_POLL_AT > time.time() else None,
    }


def _mode():
    """Текущий режим опросника: night / quiet / active / off."""
    if not config.API_ENABLED:
        return "off"
    try:
        if pollbrain.is_night():
            return "night"
    except Exception:
        pass
    if _STATE.get("quiet"):
        return "quiet"
    return "active"


def _night_info():
    """Снимок ночного режима для отображения (строка/страница)."""
    try:
        st = pollbrain.settings()
        tz = pollbrain.night_tz()
        return {
            "start": st["night_start"],
            "end": st["night_end"],
            "silent": bool(st["night_silent"]),
            "microticks": bool(st["night_microticks"]),
            "tz": getattr(tz, "key", str(tz)),
            "is_night": bool(pollbrain.is_night(st=st)),
        }
    except Exception:
        return {}


# --- Самовключение автотрекинга (юзербот залогинен -> fomo.txt не нужен) ---
_SELF_NEXT = 0.0     # когда можно снова заглянуть в .env на диске (сек)
_SELF_TRIED = False  # добычу ключа юзерботом пробуем ОДИН раз за запуск


async def _selfenable_tick(bot=None) -> None:
    """Пока автотрекинг выключен (API_ENABLED=false), раз в 15 секунд читаем
    .env ПРЯМО С ДИСКА: там мог появиться ключ (login_userbot.py сохраняет
    initData и ставит API_ENABLED=true). Появился — включаемся сами, без
    рестарта бота и без fomo.txt. Если ключа нет, но есть сессия юзербота —
    один раз добываем ключ сами (как login_userbot.py, только молча)."""
    global _SELF_NEXT, _SELF_TRIED, _FOMO
    import os
    now = time.time()
    if now < _SELF_NEXT:
        return
    _SELF_NEXT = now + 15
    disk_enabled = config.env_get("API_ENABLED", "false").strip().lower() == "true"
    disk_init = config.env_get("FOMO_INIT_DATA", "").strip()
    if disk_init:
        # Ключ появился в .env (login_bot.bat / руками): включаемся и
        # перечитываем весь конфиг, клиент пересоздастся с новым ключом.
        if not disk_enabled:
            config.set_api_enabled(True)
        config.reload()
        _FOMO = None
        log.info("Автотрекинг включён сам: в .env появился initData-ключ — "
                 "fomo.txt не нужен.")
        await notify_owner(bot, "🤖 <b>Автотрекинг включён сам</b>: ключ игры "
                                "появился в .env — fomo.txt не нужен, таймеры "
                                "пойдут без ваших действий.")
        return
    if disk_enabled:
        config.reload()  # флаг уже true на диске — подтягиваем остальные поля
        return
    if _SELF_TRIED:
        return
    _SELF_TRIED = True
    spath = config.USERBOT_SESSION_PATH
    sess = spath if spath.endswith(".session") else spath + ".session"
    if not os.path.exists(sess):
        return
    log.info("Юзербот-сессия найдена, а initData ещё нет — пробую добыть ключ "
             "сам (один раз; ваши действия не нужны)…")
    try:
        import userbot
        fresh = await userbot.refresh_init_data()
    except Exception as e:
        log.warning("Юзербот: самостоятельная добыча ключа не удалась: %s",
                    str(e)[:150])
        fresh = ""
    if fresh:
        config.set_fomo_init_data(fresh)
        config.set_api_enabled(True)
        config.reload()
        _FOMO = None
        log.info("Юзербот добыл ключ сам — автотрекинг включён.")
        await notify_owner(bot, "🤖 <b>Всё настроилось само</b>: юзербот добыл "
                                "свежий ключ, автотрекинг включён — fomo.txt "
                                "не нужен.")
    else:
        log.warning("Юзербот-сессия не дала ключ (сессия могла устареть) — "
                    "запустите login_bot.bat ещё раз, либо положите fomo.txt.")


async def _do_poll(bot) -> None:
    """Один опрос с обработкой ошибки (цикл не должен умирать)."""
    try:
        await poll_once(bot)
    except Exception:
        log.exception("Ошибка опроса API (продолжаю по расписанию)")


async def _sleep_until(target_ts) -> None:
    """Спать до момента, но будильники прерывают раньше срока.

    Без этого «проснусь через 3–8 минут» не сработало бы: цикл досыпал бы
    остаток длинного сна (пульс до 55 мин, ночь до 10 мин). Проснувшись,
    цикл сам увидит будильник и опросит игру. Куски по 5 с без сети —
    дешевле будильника не бывает.
    """
    while True:
        now = time.time()
        if now >= target_ts:
            return
        if _WAKE:
            return
        if _WAKE_AT and now >= _WAKE_AT:
            return
        if _PUSH_POLL_AT and now >= _PUSH_POLL_AT:
            return
        await asyncio.sleep(min(5.0, target_ts - now))


async def _sleep_wake() -> None:
    """Пауза после опроса (продолжительность решает _next_sleep)."""
    await _sleep_until(time.time() + _next_sleep())


def _next_sleep() -> float:
    """Пауза после опроса: база ± джиттер; мёртвый ключ — 300 с.

    Если опрос только что утихомирил нас в тихий режим — следующий цикл
    сам уйдёт на автопульс, здесь длинную паузу не задаём.
    """
    if _STATE["token_dead"]:
        return 300.0
    if _STATE.get("quiet"):
        return min(30.0, pollbrain.pulse_delay())
    return pollbrain.jittered(config.API_POLL_INTERVAL)


async def poll_forever(_bot=None):
    """Вечный цикл — мозг опросника (pollbrain.py, BRAIN.md).

    Режимы: АКТИВНЫЙ (база ± случайность) -> ТИХИЙ (автопульс 30–55 мин) ->
    НОЧЬ (штиль или 1–2 микротика). Пробуждение без тапков: сообщение
    владельца (через 3–8 мин), контрольный опрос после «⏰ Готово» (30–120 с),
    /обновить и «Обновить» на странице. Пока настроек нет — тихо ждёт.
    """
    global _WAKE, _WAKE_AT, _PUSH_POLL_AT
    bot = _bot
    if not config.API_ENABLED:
        log.info("Автотрекинг выключен — жду ключ (fomo.txt в корне или %s/, "
                 "либо вход юзербота login_bot.bat): включусь сам, без "
                 "рестарта.", config.TOKEN_UPDATES_DIR)
    else:
        log.info("Автотрекинг запущен (интервал ~%s с ±%d%%, автопульс %s–%s с, "
                 "ночь %s–%s)",
                 max(60, config.API_POLL_INTERVAL), int(config.POLL_JITTER * 100),
                 config.POLL_PULSE_MIN, config.POLL_PULSE_MAX,
                 config.NIGHT_START, config.NIGHT_END)
    while True:
        try:
            if not config.API_ENABLED:
                await _selfenable_tick(bot)
                await asyncio.sleep(5)
                continue
            if not (native_mode() or (state_urls() and config.API_AUTH_HEADER)):
                await asyncio.sleep(5)
                continue

            now = time.time()
            nst = pollbrain.settings()
            night = pollbrain.is_night(now, st=nst)
            micro = bool(nst["night_microticks"])
            silent = bool(nst["night_silent"])

            # 1) Явные будильники работают всегда, даже ночью и на паузе:
            #    /обновить, «Обновить» на странице, свежий fomo.txt.
            if _WAKE:
                _WAKE = False
                await _do_poll(bot)
                await _sleep_wake()
                continue

            # 2) Запланированное пробуждение после сообщения владельца.
            if _WAKE_AT and now >= _WAKE_AT:
                _WAKE_AT = 0.0
                await _do_poll(bot)
                await _sleep_wake()
                continue

            # 3) Контрольный опрос после доставленного «⏰ Готово».
            if _PUSH_POLL_AT and now >= _PUSH_POLL_AT:
                _PUSH_POLL_AT = 0.0
                if not (night and silent and not micro):
                    await _do_poll(bot)
                    await _sleep_wake()
                    continue

            # 4) НОЧЬ: полный штиль либо микротики (1–2 за окно).
            if night:
                _PUSH_POLL_AT = 0.0   # ночной контрольный опрос отменяем
                if silent and not micro:
                    await _sleep_until(now + pollbrain.morning_slumber(now))
                    continue
                if micro:
                    ticks = pollbrain.night_ticks(now, st=nst)
                    if ticks and ticks[0] <= now:
                        # Микротик настал: один опрос и убираем его из списка.
                        pollbrain.night_ticks_consume(now)
                        await _do_poll(bot)
                        await _sleep_wake()
                        continue
                    if not ticks:
                        await _sleep_until(now + pollbrain.morning_slumber(now))
                        continue
                    await _sleep_until(min(now + 600.0, ticks[0]))
                    continue   # спим до микротика, настройки применяются живьём

            # 5) Игрок занят: писал боту в последние OWNER_BUSY_WINDOW секунд —
            #    скорее всего прямо сейчас в игре, не лезем (переавторизация!).
            if user_busy(now):
                await asyncio.sleep(random.uniform(60, 150))
                continue

            # 6) Только что реанимировали ключ — даём игре успокоиться.
            if reauth_cooldown(now):
                await asyncio.sleep(random.uniform(90, 240))
                continue

            # 7) ТИХИЙ РЕЖИМ: автопульс раз в случайные 30–55 минут.
            if _STATE["quiet"]:
                await _sleep_until(time.time() + pollbrain.pulse_delay())
                await _do_poll(bot)
                await _sleep_wake()
                continue

            # 8) АКТИВНЫЙ опрос.
            await _do_poll(bot)
            await _sleep_wake()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Неожиданная ошибка в цикле автотрекинга")
            await asyncio.sleep(10)
