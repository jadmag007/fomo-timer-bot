"""SQLite-хранилище пользователей и таймеров (stdlib sqlite3, без внешних зависимостей)."""
import os
import sqlite3
import time

import config

_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    tg_id      INTEGER PRIMARY KEY,
    tz         TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS timers(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id      INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    label      TEXT NOT NULL,
    ends_at    REAL NOT NULL,
    created_at REAL NOT NULL,
    warn_sent  INTEGER NOT NULL DEFAULT 0,
    done_sent  INTEGER NOT NULL DEFAULT 0,
    bucket     TEXT NOT NULL DEFAULT '',
    prenote_sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_timers_due ON timers(done_sent, ends_at);
CREATE INDEX IF NOT EXISTS idx_timers_prewarn ON timers(done_sent, prenote_sent, bucket);
"""

# Миграции для баз, созданных прошлыми версиями: (колонка, определение).
# Проверка по PRAGMA table_info — старая база получит недостающие колонки.
_MIGRATE_COLUMNS = [
    ("timers", "bucket", "TEXT NOT NULL DEFAULT ''"),
    ("timers", "prenote_sent", "INTEGER NOT NULL DEFAULT 0"),
]

# Осадные таймеры, поставленные до появления bucket: опознаём по метке
# (перевод группы tOutpostSiegesMine в translations.py — «🏰 Осада аутпоста»).
_SIEGE_LABEL_PREFIX = "🏰 Осада аутпоста"


def init(path=None):
    """Открыть БД, создать таблицы при первом запуске и дозалить колонки.

    Порядок важен: сначала ALTER-миграция колонок (старая база), потом
    схема с индексами (на свежей базе PRAGMA пуст — миграция пропустит),
    и только затем backfill bucket по меткам.
    """
    global _conn
    path = path or config.DB_PATH
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _migrate_columns()
    _conn.executescript(SCHEMA)
    _migrate_backfill()
    _conn.commit()
    return _conn


def _migrate_columns():
    """Добавить в старые базы недостающие колонки (без потери данных)."""
    for table, col, ddl in _MIGRATE_COLUMNS:
        cols = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def _migrate_backfill():
    """Осадные таймеры из старых версий не имели bucket — заполняем по метке
    (перевод группы tOutpostSiegesMine в translations.py — «🏰 Осада аутпоста»)."""
    _conn.execute(
        "UPDATE timers SET bucket=? WHERE bucket='' AND label LIKE ?",
        ("tOutpostSiegesMine", _SIEGE_LABEL_PREFIX + "%"),
    )


def _db():
    if _conn is None:
        init()
    return _conn


# ---------- Пользователи ----------

def get_user(tg_id):
    return _db().execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()


def first_user():
    """Первый зарегистрировавшийся пользователь (владелец по умолчанию)."""
    return _db().execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()


def upsert_user(tg_id, tz=None):
    _db().execute(
        "INSERT INTO users(tg_id, tz) VALUES(?, ?) "
        "ON CONFLICT(tg_id) DO NOTHING",
        (tg_id, tz or config.DEFAULT_TZ),
    )
    _db().commit()


def set_tz(tg_id, tz):
    upsert_user(tg_id)
    _db().execute("UPDATE users SET tz=? WHERE tg_id=?", (tz, tg_id))
    _db().commit()


# ---------- Таймеры ----------

def add_timer(tg_id, chat_id, label, ends_at, created_at=None, bucket=""):
    cur = _db().execute(
        "INSERT INTO timers(tg_id, chat_id, label, ends_at, created_at, bucket) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (tg_id, chat_id, label, ends_at,
         created_at if created_at is not None else time.time(),
         bucket or ""),
    )
    _db().commit()
    return cur.lastrowid


def active(tg_id):
    return _db().execute(
        "SELECT * FROM timers WHERE tg_id=? AND done_sent=0 ORDER BY ends_at",
        (tg_id,),
    ).fetchall()


def get_timer(timer_id):
    return _db().execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone()


def cancel(tg_id, timer_id):
    cur = _db().execute(
        "DELETE FROM timers WHERE id=? AND tg_id=?", (timer_id, tg_id)
    )
    _db().commit()
    return cur.rowcount > 0


def due_warn(now):
    """Таймеры, по которым пора слать предупреждение T-1мин."""
    return _db().execute(
        "SELECT * FROM timers "
        "WHERE done_sent=0 AND warn_sent=0 "
        "AND ends_at > ? AND ends_at - ? <= ? "
        "AND ends_at - created_at >= ?",
        (now, now, config.WARN_BEFORE_SEC + 1, config.WARN_MIN_DURATION),
    ).fetchall()


def due_prewarn(now):
    """Осады аванпостов, по которым пора слать предупреждение T-1час.

    Окно: осталось от 2 минут до SIEGE_PREWARN_SEC (+5с запас на тик).
    Нижняя граница 2 мин — в последние секунды шуметь бессмысленно: вот-вот
    придёт обычное «✅ Готово!». Ручные таймеры (bucket='') не задеваем.
    SIEGE_PREWARN_SEC=0 — предупреждение выключено.
    """
    if config.SIEGE_PREWARN_SEC <= 0:
        return []
    floor = min(120, config.SIEGE_PREWARN_SEC)
    return _db().execute(
        "SELECT * FROM timers "
        "WHERE done_sent=0 AND prenote_sent=0 AND bucket='tOutpostSiegesMine' "
        "AND ends_at > ? AND ends_at - ? <= ? AND ends_at - ? > ?",
        (now, now, config.SIEGE_PREWARN_SEC + 5, now, floor),
    ).fetchall()


def due_done(now):
    """Таймеры, время которых вышло (в т.ч. просроченные после офлайна бота)."""
    return _db().execute(
        "SELECT * FROM timers WHERE done_sent=0 AND ends_at <= ?", (now,)
    ).fetchall()


def mark_warn(timer_id):
    _db().execute("UPDATE timers SET warn_sent=1 WHERE id=?", (timer_id,))
    _db().commit()


def mark_prewarn(timer_id):
    _db().execute("UPDATE timers SET prenote_sent=1 WHERE id=?", (timer_id,))
    _db().commit()


def mark_done(timer_id):
    _db().execute(
        "UPDATE timers SET done_sent=1, warn_sent=1 WHERE id=?", (timer_id,)
    )
    _db().commit()
