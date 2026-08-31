"""trace.py — трассировка сырых ответов API игры (поиск новых типов таймеров).

Зачем: часть таймеров игры бот не показывает, пока их ключи не известны коду
(клановые сундуки раз в час, награды аванпостов раз в 4 часа и т.п.). Трассировка
пишет ВСЁ содержимое ответа /user/data/timers в data/trace.log:

  * все ключи data со сводкой (какие списки есть и сколько в них элементов);
  * каждое поле, похожее на дату/время, с JSON-путём и значением —
    именно по ним в translations.py добавляются новые группы переводов;
  * полный JSON ответа (до 20 000 символов).

Включение: пришлите боту /трассировка (или кнопка на экране /апи), либо
строка API_TRACE=true в .env. Выгрузка лога: /трейслог — бот пришлёт файл.
Ротация: при превышении MAX_BYTES файл переезжает в trace.log.old (копия одна).
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("trace")

LOG_PATH = Path("data/trace.log")
MAX_BYTES = 2_000_000          # ~2 МБ на файл, старая копия одна (trace.log.old)
SNIPPET_MAX = 20_000           # предел полного JSON в логе
WALK_DEPTH = 6                 # глубже в JSON не ходим
WALK_LIST_MAX = 60             # первых элементов массива разбираем

# «2026-08-27 21:24:41» / ISO — строковые даты игры
DATE_STR_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)?$")


def enabled() -> bool:
    return bool(config.API_TRACE)


def _walk(node, path="$", depth=0):
    """Рекурсивный обход JSON -> [(путь, ключ, значение)]."""
    out = []
    if depth > WALK_DEPTH:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}"
            out.append((p, str(k), v))
            out.extend(_walk(v, p, depth + 1))
    elif isinstance(node, list):
        for i, v in enumerate(node[:WALK_LIST_MAX]):
            p = f"{path}[{i}]"
            out.append((p, str(i), v))
            out.extend(_walk(v, p, depth + 1))
    return out


def _classify_time(v):
    """Значение похоже на дату/время -> ('date'|'unix'|'ms'|None)."""
    if isinstance(v, str):
        return "date" if DATE_STR_RE.match(v.strip()) else None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 1_500_000_000 <= v <= 3_000_000_000:
            return "unix"
        if 1_500_000_000_000 <= v <= 3_000_000_000_000:
            return "ms"
    return None


def summarize(payload) -> list:
    """Ответ API -> строки для лога (сводка + все поля-времена)."""
    lines = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        lines.append("data (ключи верхнего уровня):")
        for k, v in data.items():
            if isinstance(v, list):
                lines.append(f"  {k}: list[{len(v)}]")
            elif isinstance(v, dict):
                lines.append(f"  {k}: dict({', '.join(list(v)[:6]) or 'пусто'})")
            else:
                lines.append(f"  {k}: {v!r}"[:160])
    else:
        lines.append(f"(в ответе нет словаря data; верхний уровень: "
                     f"{', '.join(list(payload)[:10]) if isinstance(payload, dict) else type(payload).__name__})")
    times = []
    for path, _k, v in _walk(payload):
        kind = _classify_time(v)
        if kind:
            times.append(f"  [{kind}] {path} = {v!r}")
    lines.append("Поля, похожие на даты/время (кандидаты — по ним добавляются новые таймеры):")
    lines.extend(times or ["  (не найдено)"])
    return lines


def _rotate():
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
            old = LOG_PATH.with_name(LOG_PATH.name + ".old")
            if old.exists():
                old.unlink()
            LOG_PATH.rename(old)
    except OSError:
        log.exception("Не удалось ротировать trace.log")


def log_event(source, text) -> bool:
    """Записать в trace.log событие без снимка (например, ошибку all-опроса).

    Раньше неудачный /user/data/all был виден только в консоли — пользователь
    же смотрит trace.log (/трейслог). Теперь ошибка видна и там.
    """
    if not enabled():
        return False
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate()
        ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write("=" * 74 + "\n"
                    f"== {ts} · источник: {source} · СОБЫТИЕ\n{text}\n")
        return True
    except Exception:
        log.exception("Не удалось записать trace.log")
        return False


def log_response(source, status, payload, found=None, added=0) -> bool:
    """Записать ответ API в trace.log. True — запись сделана.
    found — находки extract_fomo (что бот увидел), added — сколько поставил."""
    if not enabled():
        return False
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate()
        ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "=" * 74,
            f"== {ts} · источник: {source} · HTTP {status}",
        ]
        if found is not None:
            lines.append(f"Бот увидел таймеров: {len(found)}, добавил: {added}")
            for up in found[:40]:
                lines.append(f"  • {up.get('label')}  (окончание {up.get('ends_at')})")
        lines.extend(summarize(payload))
        try:
            lines.append("Полный JSON ответа:")
            lines.append(json.dumps(payload, ensure_ascii=False, indent=1)[:SNIPPET_MAX])
        except (TypeError, ValueError):
            pass
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception:
        log.exception("Не удалось записать trace.log")
        return False
