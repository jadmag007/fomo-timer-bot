"""Пауза бота: кнопка «⏸ Пауза / ▶️ Продолжить» в главном меню.

Зачем: пользователь уходит от компьютера и не хочет, чтобы бот слал пуши
(«Готово ✅», предупреждения, предложения Да/Нет) в пустой чат.

Что делает пауза:
  * глушит ТОЛЬКО исходящие сообщения бота — таймеры продолжают ставиться,
    автотрекинг опрашивает игру, страница таймеров работает как обычно;
  * всё, что завершилось в паузе, попадает в список пропущенного
    (record_missed) — при снятии паузы приходит ОДНА сводка, без спама.

Состояние хранится в data/pause.json и переживает рестарт бота: если
поставить паузу и перезапустить компьютер, бот после старта останется
на паузе (пуши не хлынут внезапно).
"""
import json
import os
import threading
import time
from pathlib import Path

_PATH = Path("data/pause.json")
_LOCK = threading.Lock()
_MISSED_CAP = 30  # в сводку не тащим древнюю историю

# Состояние в памяти + зеркало на диске. missed: [{label, ends_at}, ...]
_state = {"paused": False, "paused_at": None, "missed": []}


def set_path(p):
    """Переназначить путь файла (тесты). Загружает состояние с диска."""
    global _PATH
    _PATH = Path(p)
    _state.update({"paused": False, "paused_at": None, "missed": []})
    _load()


def _load():
    try:
        d = json.loads(_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            _state["paused"] = bool(d.get("paused"))
            _state["paused_at"] = d.get("paused_at")
            m = d.get("missed")
            _state["missed"] = list(m) if isinstance(m, list) else []
    except (OSError, ValueError):
        pass  # файла нет / битый JSON — считаем, что паузы нет


def _save():
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _PATH)  # атомарно: рестарт посреди записи не портит файл
    except OSError:
        pass  # диск недоступен — пауза всё равно останется в памяти процесса


def is_paused() -> bool:
    return bool(_state["paused"])


def paused_at():
    """Unix-время включения паузы (None — пауза не стоит)."""
    return _state["paused_at"]


def set_paused(value: bool):
    """Вкл/выкл паузу. Возвращает снимок состояния (для логов/тестов)."""
    with _LOCK:
        _state["paused"] = bool(value)
        _state["paused_at"] = time.time() if value else None
        _save()
        return dict(_state)


def record_missed(label, ends_at):
    """Таймер завершился в паузе — запоминаем для сводки при возобновлении."""
    with _LOCK:
        if not _state["paused"]:
            return
        _state["missed"].append(
            {"label": str(label)[:120], "ends_at": float(ends_at)})
        del _state["missed"][:-_MISSED_CAP]
        _save()


def take_missed():
    """Забрать (и очистить) список пропущенного. Вызывается при снятии паузы."""
    with _LOCK:
        out = list(_state["missed"])
        _state["missed"].clear()
        _save()
        return out


def missed_count() -> int:
    return len(_state["missed"])
