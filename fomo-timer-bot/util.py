"""Утилиты: парсинг durations, форматирование, часовые пояса.

Форматы времени (как в игре):
  "22:24"   -> 22 минуты 24 секунды (мм:сс, два поля)
  "1:28:10" -> 1 час 28 минут 10 секунд (чч:мм:сс, три поля)
  "45м"     -> 45 минут;  "2ч" -> 2 часа;  "1ч 30м" -> 90 минут
  "30с"     -> 30 секунд; "8h" -> 8 часов
  "90"      -> голое число считается минутами
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import config

_TOKEN_RE = re.compile(r"(\d+)\s*(ч[а-яё]*|h|м[а-яё]*|m|с[а-яё]*|s)?", re.IGNORECASE)
_COLON_RE = re.compile(r"\d{1,3}(:\d{2}){1,2}")
_PLAIN_RE = re.compile(r"[0-9a-zа-я\s.,]+", re.IGNORECASE)


def parse_duration(text):
    """Строка -> секунды. None, если распознать не удалось."""
    if text is None:
        return None
    s = str(text).strip().lower().replace(",", ".")
    if not s:
        return None

    # Формат с двоеточиями: мм:сс или чч:мм:сс
    if _COLON_RE.fullmatch(s):
        parts = [int(p) for p in s.split(":")]
        if len(parts) == 3:
            h, m, sec = parts
        else:
            h, m, sec = 0, parts[0], parts[1]
        return h * 3600 + m * 60 + sec

    # Формат с суффиксами: 1ч 30м / 45м / 30с / 90
    if _PLAIN_RE.fullmatch(s):
        total, matched = 0, False
        for num, unit in _TOKEN_RE.findall(s):
            if not num:
                continue
            n, u = int(num), (unit or "").lower()
            matched = True
            if u.startswith("ч") or u == "h":
                total += n * 3600
            elif u.startswith("м") or u == "m":
                total += n * 60
            elif u.startswith("с") or u == "s":
                total += n
            else:  # голое число — минуты
                total += n * 60
        return total if matched else None
    return None


def fmt_delta(sec):
    """1344 -> '22м 24с'; 4964 -> '1ч 22м 44с'."""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}ч")
    if m or h:
        parts.append(f"{m}м")
    parts.append(f"{s}с")
    return " ".join(parts)


def fmt_clock(sec):
    """1344 -> '22:24'; 4964 -> '1:22:44' (как таймер в игре)."""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def safe_tz(name):
    """ZoneInfo с фолбэком на пояс по умолчанию."""
    for candidate in (name, config.DEFAULT_TZ, "UTC"):
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    return ZoneInfo("UTC")


def local_str(ts, tz):
    """Timestamp -> '27.08 07:15:03' в нужном поясе."""
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m %H:%M:%S")
