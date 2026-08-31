"""Мозг опросника: режимы АКТИВ/ТИХИЙ/НОЧЬ, случайные интервалы, ночной сон.

Дизайн утверждён пользователем (полное описание — BRAIN.md):
  * АКТИВНЫЙ — штатное слежение, пауза = база * (1 ± POLL_JITTER), каждый раз
    заново: двух одинаковых интервалов подряд не бывает (идеальная
    периодичность — главная сигнатура бота).
  * ТИХИЙ — после 3 опросов без нового таймера: автопульс раз в случайные
    30–55 минут. Пульс находит новые таймеры сам, тапать ничего не нужно.
  * НОЧЬ — в окне ночи (по часовой зоне владельца, по умолчанию 00:00–08:00)
    игровые запросы прекращаются: полная тишина либо 1–2 микротика за ночь
    (на случай ночной игры). Утро — со случайным сдвигом, не ровно в 08:00.
  * Пробуждение без тапков: любое сообщение владельца боту (через случайные
    3–8 минут — рано дёргаться, игрок часто пишет прямо из игры),
    контрольный опрос через 30–120 с после «⏰ Готово» (игрок пошёл
    собирать — наша активность совпадает с его).

Ночное окно/тишина/микротики меняются кнопками в боте и на странице —
хранятся в БД (db.settings), поверх дефолтов из .env (config).
"""
import random
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import db

# Кэши: БД/пояс не дёргаем на каждый чих цикла (цикл просыпается каждые ~5 с)
_SETTINGS_TTL = 15.0
_SETTINGS_CACHE = (0.0, {})
_TZ_TTL = 60.0
_TZ_CACHE = (0.0, None)
_TICKS_CACHE = (None, [])   # (ключ окна (start_ts, end_ts), моменты микротиков)


# ---------- Настройки ночного режима (БД поверх .env) ----------

def settings(now=None):
    """Эффективные настройки ночного режима: .env -> поверх kv из БД.

    Ключи: night_start, night_end ("HH:MM"), night_silent (bool),
    night_microticks (bool), tz (имя IANA для ночного окна).
    """
    now = now if now is not None else time.time()
    ts, cached = _SETTINGS_CACHE
    if now - ts < _SETTINGS_TTL:
        return cached
    st = {
        "night_start": config.NIGHT_START or "00:00",
        "night_end": config.NIGHT_END or "08:00",
        "night_silent": bool(config.NIGHT_SILENT),
        "night_microticks": bool(config.NIGHT_MICROTICKS),
        "tz": config.BOT_TZ or "",   # пусто = пояс владельца из бота
    }
    try:
        kv = db.all_settings()
    except Exception:
        kv = {}
    for key in ("night_start", "night_end", "night_silent",
                "night_microticks", "tz"):
        if key in kv and str(kv[key]).strip():
            st[key] = str(kv[key]).strip()
    st["night_silent"] = str(st["night_silent"]).lower() in ("1", "true", "yes", "вкл")
    st["night_microticks"] = str(st["night_microticks"]).lower() in ("1", "true", "yes", "вкл")
    globals()["_SETTINGS_CACHE"] = (now, st)
    return st


def invalidate_cache():
    """Сбросить кэш настроек/пояса (после правки кнопками в боте/на странице)."""
    globals()["_SETTINGS_CACHE"] = (0.0, {})
    globals()["_TZ_CACHE"] = (0.0, None)


def set_night(key, value):
    """Сохранить настройку ночного режима в БД (меню бота, страница).

    Ключи: night_start, night_end ("HH:MM"), night_silent,
    night_microticks (bool-подобные), tz ("" — пояс владельца).
    Возвращает True при успехе.
    """
    if key not in ("night_start", "night_end", "night_silent",
                   "night_microticks", "tz"):
        return False
    if key in ("night_start", "night_end"):
        if parse_hhmm(value) is None:
            return False
        db.set_setting(key, parse_hhmm(value) and str(value).strip())
    elif key == "tz":
        v = str(value).strip()
        try:
            ZoneInfo(v or "Europe/Moscow")
        except Exception:
            return False
        if v:
            db.set_setting("tz", v)
        else:
            db.del_setting("tz")
    else:
        db.set_setting(key, "true" if str(value).lower() in
                       ("1", "true", "yes", "вкл", "on") else "false")
    invalidate_cache()
    return True


def reset_night():
    """Вернуть ночной режим к дефолтам .env (удалить kv-правки)."""
    for key in ("night_start", "night_end", "night_silent",
                "night_microticks", "tz"):
        try:
            db.del_setting(key)
        except Exception:
            pass
    invalidate_cache()


# ---------- Часовой пояс и окно ночи ----------

def night_tz(now=None):
    """ZoneInfo для ночного окна: BOT_TZ/tz из БД -> пояс владельца -> дефолт."""
    now = now if now is not None else time.time()
    ts, cached = _TZ_CACHE
    if now - ts < _TZ_TTL and cached is not None:
        return cached
    st = settings(now)
    name = st.get("tz") or ""
    if not name:
        name = _owner_tz_name()
    try:
        tz = ZoneInfo(name or config.DEFAULT_TZ)
    except Exception:
        tz = ZoneInfo(config.DEFAULT_TZ)
    globals()["_TZ_CACHE"] = (now, tz)
    return tz


def _owner_tz_name():
    """Пояс владельца (тот, что он выбрал командой /tz в боте)."""
    try:
        row = None
        if config.API_OWNER_TG_ID:
            row = db.get_user(config.API_OWNER_TG_ID)
        if row is None:
            row = db.first_user()
        return (row["tz"] or "") if row else ""
    except Exception:
        return ""


def parse_hhmm(s):
    """'07:30' -> (7, 30); мусор -> None."""
    try:
        parts = str(s).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)
    except Exception:
        pass
    return None


def _window_seconds(st):
    """(секунды от полуночи начала, конца) ночного окна."""
    sh, sm = parse_hhmm(st["night_start"]) or (0, 0)
    eh, em = parse_hhmm(st["night_end"]) or (8, 0)
    return sh * 3600 + sm * 60, eh * 3600 + em * 60


def is_night(now=None, tz=None, st=None):
    """Сейчас ночное окно? Понимает переход через полночь (23:00->07:00)."""
    now = now if now is not None else time.time()
    tz = tz or night_tz(now)
    st = st or settings(now)
    start_s, end_s = _window_seconds(st)
    local = datetime.fromtimestamp(now, tz)
    secs = local.hour * 3600 + local.minute * 60 + local.second
    if start_s == end_s:
        return False  # нулевое окно = ночной режим выключен
    if start_s < end_s:
        return start_s <= secs < end_s
    return secs >= start_s or secs < end_s


def night_end_ts(now=None, tz=None, st=None):
    """Unix-время конца текущего/следующего ночного окна (после now)."""
    now = now if now is not None else time.time()
    tz = tz or night_tz(now)
    st = st or settings(now)
    start_s, end_s = _window_seconds(st)
    local = datetime.fromtimestamp(now, tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if start_s == end_s:
        return now + 86400
    if start_s < end_s:
        end_dt = midnight + timedelta(seconds=end_s)
        if local.timestamp() < end_dt.timestamp():
            return end_dt.timestamp()
        # окно сегодня уже закончилось — вернём конец ЗАВТРАШНЕГО окна
        return (midnight + timedelta(days=1, seconds=end_s)).timestamp()
    # переход через полночь: окно [вчера start .. сегодня end] или [сегодня start .. завтра end]
    end_dt = midnight + timedelta(seconds=end_s)
    if local.timestamp() < end_dt.timestamp():
        return end_dt.timestamp()
    return (midnight + timedelta(days=1, seconds=end_s)).timestamp()


# ---------- Случайные интервалы (антидэтект) ----------

def jittered(base, jitter=None):
    """Активная пауза: base * (1 ± jitter), каждый раз новая. Пол 60 с."""
    if jitter is None:
        jitter = max(0.0, float(config.POLL_JITTER))
    base = max(60, int(base))
    lo = base * (1.0 - jitter)
    hi = base * (1.0 + jitter)
    return random.uniform(lo, hi)


def pulse_delay():
    """Автопульс в тишине: случайная пауза из вилки 30–55 минут."""
    lo = max(600, int(config.POLL_PULSE_MIN))
    hi = max(lo, int(config.POLL_PULSE_MAX))
    return random.uniform(lo, hi)


def wake_delay():
    """Задержка пробуждения после сообщения владельца: 3–8 минут.

    НЕ секунды: игрок часто пишет боту прямо из игры — ранний опрос мог бы
    поймать переавторизацию и выбить игровую сессию.
    """
    lo = max(20, int(config.WAKE_DELAY_MIN))
    hi = max(lo, int(config.WAKE_DELAY_MAX))
    return random.uniform(lo, hi)


def control_delay():
    """Контрольный опрос после «⏰ Готово»: случайные 30–120 секунд."""
    lo = max(10, int(config.CONTROL_POLL_MIN))
    hi = max(lo, int(config.CONTROL_POLL_MAX))
    return random.uniform(lo, hi)


def morning_slumber(remaining, now=None):
    """Сколько спать до конца ночи кусками + случайный сдвиг на утро.

    Куски не длиннее 10 минут (настройки из меню применяются живьём), а
    последний кусок добирает случайные 0–NIGHT_WAKE_JITTER секунд — выход
    из ночи не бывает ровно в NIGHT_END (это тоже сигнатура).
    """
    now = now if now is not None else time.time()
    end = night_end_ts(now)
    rest = end - now
    if rest > 600:
        return 600.0
    return max(30.0, rest + random.uniform(30, max(60, config.NIGHT_WAKE_JITTER)))


def night_ticks(now=None, st=None):
    """Случайные моменты микротиков текущего ночного окна (1–2 шт).

    Кэшируются по ключу окна (start, end): пока окно то же — моменты те же,
    иначе цикл «просыпался на тик» и не находил его в новом списке.
    Пусто, если микротики выключены или сейчас не ночь.
    """
    now = now if now is not None else time.time()
    st = st or settings(now)
    if not st["night_microticks"] or not is_night(now, st=st):
        return []
    tz = night_tz(now)
    local = datetime.fromtimestamp(now, tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_s, end_s = _window_seconds(st)
    if start_s < end_s:
        start_dt = midnight + timedelta(seconds=start_s)
        end_dt = midnight + timedelta(seconds=end_s)
    else:
        if local.timestamp() >= (midnight + timedelta(seconds=start_s)).timestamp():
            start_dt = midnight + timedelta(seconds=start_s)
            end_dt = midnight + timedelta(days=1, seconds=end_s)
        else:
            start_dt = midnight + timedelta(seconds=start_s - 86400)
            end_dt = midnight + timedelta(seconds=end_s)
    key = (int(start_dt.timestamp()), int(end_dt.timestamp()))
    cached_key, cached_ticks = _TICKS_CACHE
    if cached_key == key:
        return list(cached_ticks)
    lo = start_dt.timestamp() + 300          # не в первые минуты окна
    hi = end_dt.timestamp() - 600            # и не в последние
    if hi <= lo:
        return []
    k = random.randint(1, 2)
    ticks = sorted(random.uniform(lo, hi) for _ in range(k))
    globals()["_TICKS_CACHE"] = (key, ticks)
    return list(ticks)


def night_ticks_consume(t):
    """Считать микротики <= t выполненными (убрать из кэша)."""
    key, ticks = _TICKS_CACHE
    if ticks:
        globals()["_TICKS_CACHE"] = (key, [x for x in ticks if x > t])
