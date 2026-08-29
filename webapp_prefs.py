"""Настройки мини-приложения: тихий режим (кто кого тревит пушами).

Мини-апп (кнопка меню слева от поля ввода) показывает все таймеры сразу и
даёт кнопки управления. Одна из них — «тихий режим»: можно выключить пуши
для отдельной группы (например, клановые сундуки каждые N часов шумят)
или для всех сразу. Обычные уведомления при этом продолжают работать —

  * таймеры НЕ замьюченных групп приходят как раньше («✅ Готово»);
  * у замьюченных групп пуш НЕ отправляется вовсе: таймер тихо помечается
    finished (догонять «как накопилось» после включения звука не нужно —
    всё видно в самом мини-аппе, где тишина управляется одной кнопкой);
  * предупреждение «🚩 пора отправлять войска» по осадам тоже уважает
    тихий режим.

Настройки лежат в data/webapp_settings.json (data/ уже в .gitignore) —
переживают рестарт, правятся из мини-аппа на лету. Файл крошечный, читаем
лениво с проверкой mtime, чтобы планировщик (тик раз в секунду) не тратил
лишнего.
"""
import json
import os
import threading
from pathlib import Path

_PATH = Path("data/webapp_settings.json")
_LOCK = threading.Lock()
_CACHE = None       # последний прочитанный словарь
_CACHE_MTIME = None

DEFAULTS = {"all": False, "muted": {}}


def set_path(p):
    """Подменить путь файла (для тестов)."""
    global _PATH, _CACHE, _CACHE_MTIME
    _PATH = Path(p)
    _CACHE = None
    _CACHE_MTIME = None


def _load():
    """Прочитать файл, если он менялся с прошлого раза. Без бросков."""
    global _CACHE, _CACHE_MTIME
    try:
        mtime = _PATH.stat().st_mtime
    except OSError:
        _CACHE, _CACHE_MTIME = dict(DEFAULTS, muted=dict(DEFAULTS["muted"])), None
        return _CACHE
    if _CACHE is not None and mtime == _CACHE_MTIME:
        return _CACHE
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        data = {
            "all": bool(raw.get("all", False)),
            "muted": {str(k): bool(v) for k, v in (raw.get("muted") or {}).items()},
        }
    except Exception:  # битый/чужой файл — начинаем с чистого
        data = dict(DEFAULTS, muted={})
    _CACHE, _CACHE_MTIME = data, mtime
    return data


def _save(data):
    global _CACHE, _CACHE_MTIME
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _PATH)  # атомарно: обрыв записи не оставит битый JSON
        _CACHE = data
        try:
            _CACHE_MTIME = _PATH.stat().st_mtime
        except OSError:
            _CACHE_MTIME = None
        return True
    except Exception:
        return False


def snapshot() -> dict:
    """Текущие настройки: {"all": bool, "buckets": set[str]}."""
    d = _load()
    return {"all": d["all"], "buckets": {k for k, v in d["muted"].items() if v}}


def is_muted(bucket: str) -> bool:
    """True, если пуши по этой группе молчат (или включён глобальный тихий режим).

    bucket='' — ручные таймеры: они подчиняются только глобальному режиму.
    """
    d = _load()
    if d["all"]:
        return True
    return bool(bucket) and bool(d["muted"].get(str(bucket), False))


def set_bucket(bucket: str, muted: bool) -> bool:
    """Заглушить/вернуть звук одной группе (кнопка колокольчика в мини-аппе)."""
    with _LOCK:
        d = _load()
        muted_map = dict(d["muted"])
        if muted:
            muted_map[str(bucket)] = True
        else:
            muted_map.pop(str(bucket), None)
        return _save({"all": d["all"], "muted": muted_map})


def set_all(all_muted: bool) -> bool:
    """Глобальный тихий режим (кнопка 🔕 в мини-аппе)."""
    with _LOCK:
        d = _load()
        return _save({"all": bool(all_muted), "muted": dict(d["muted"])})
