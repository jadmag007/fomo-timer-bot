"""Страница таймеров в браузере: локальный веб-сервер + JSON API.

Зачем: пуш-уведомления удобны, но видеть ВСЕ таймеры сразу и управлять ими
кнопками удобнее на одном экране. Страница открывается в ОБЫЧНОМ браузере
на том устройстве, где запущен бот: http://127.0.0.1:8080 (на ПК — браузер
ПК; на телефоне в Termux — браузер того же телефона). Никакой Telegram-
обвязки: мини-апп (кнопка меню, туннель cloudflared, подпись initData)
убран до лучших времён — при необходимости он вернётся из истории git.

Как это устроено:
  * этот модуль — маленький HTTP-сервер на stdlib (127.0.0.1, WEBAPP_PORT):
    отдаёт webapp/index.html и JSON для /api/*;
  * слушает ТОЛЬКО 127.0.0.1 — снаружи (из сети/интернета) сервер не виден,
    никакой авторизации не нужно: страница доступна только тому, кто за
    этим же компьютером/телефоном, а это и есть владелец бота;
  * порт берётся из WEBAPP_PORT; если занят — следующий свободный (до +10).

API (без заголовков авторизации — доступ только с того же устройства):
  GET  /             — webapp/index.html (страница таймеров)
  GET  /api/state    — всё для отрисовки: таймеры, группы, настройки, статус
  POST /api/settings — {"bucket": "tTroops", "muted": true} или {"all": true}
  POST /api/refresh  — внеочередной опрос API игры (как кнопка 🔄)
  POST /api/cancel   — {"id": 12} отменить таймер
  POST /api/brain    — {"key": "night_start", "value": "23:30"}: ночной режим
                       (night_start/night_end "HH:MM", night_silent/
                       night_microticks true|false, {"reset": true} — дефолты)
  GET  /api/env      — кнопка ⚙️: содержимое локального .env + статус Termux
  POST /api/env      — {"raw": "..."} сохранить .env целиком (бэкап .env.bak,
                       скалярные настройки применяются сразу) или
                       {"key": "TERMUX_NOTIFY", "value": "true"} — точечно
"""
import json
import logging
import os
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import db
import pause_state
import termux_notify
import webapp_prefs
import translations as tr

log = logging.getLogger("webapp")

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "webapp" / "index.html"

# Максимальный размер тела POST. Раньше 16 КБ хватало (запросы — десятки
# байт); с 0.1.1.9 кнопка ⚙️ шит .env целиком (.env.example ~10 КБ) — 64 КБ.
BODY_MAX = 64 * 1024

# Имя ключа .env для точечной правки (POST /api/env {"key": ...})
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Максимальный размер сохраняемого .env (защита от случайной простыни)
ENV_MAX = 128 * 1024

# --- Провайдеры, которые bot.py связывает с живым ботом (для тестов — заглушки)
_LOOP = None            # главный asyncio-цикл бота
_REFRESH_SUBMIT = None  # -> concurrent.futures.Future опроса API игры
_PORT = 0               # фактический порт запущенного сервера (0 — не запущен)


def current_port() -> int:
    """Фактический порт страницы (может отличаться от WEBAPP_PORT, если занят)."""
    return _PORT or int(getattr(config, "WEBAPP_PORT", 8080) or 8080)


def local_url() -> str:
    """Адрес страницы для подсказок в боте: http://127.0.0.1:PORT."""
    return f"http://127.0.0.1:{current_port()}"


def set_providers(loop=None, refresh_submit=None):
    """Связать сервер с ботом: цикл и способ запустить внеочередной опрос."""
    global _LOOP, _REFRESH_SUBMIT
    _LOOP = loop
    _REFRESH_SUBMIT = refresh_submit


def allowed_user_ids() -> set:
    """Чей таймер-лист показывать: владелец из .env или первый /start боту."""
    ids = set()
    if config.API_OWNER_TG_ID:
        ids.add(config.API_OWNER_TG_ID)
    else:
        try:
            u = db.first_user()
            if u:
                ids.add(u["tg_id"])
        except Exception:
            pass
    return ids


# ---------- Состояние для /api/state ----------

# 0.1.1.7: пользовательская сортировка СТРАНИЦЫ — сначала тренировка войск,
# потом стройка, дальше как в translations.BUCKETS. Сам BUCKETS не трогаем:
# его порядок используют подписи и меню бота, здесь важен только экран.
_WEBAPP_FIRST = ("tTroops", "tBuildings")


def _bucket_order():
    """Порядок групп на странице: тренировка -> стройка -> остальное.

    Влияет только на /api/state (порядок карточек и групп). Неизвестные
    бакеты — в конец, как и раньше.
    """
    known = list(tr.BUCKETS.keys())
    head = [k for k in _WEBAPP_FIRST if k in known]
    rest = [k for k in known if k not in head]
    return {k: i for i, k in enumerate(head + rest)}


def build_state(now=None):
    """Всё, что нужно странице для отрисовки (и тестам — для проверки)."""
    now = now if now is not None else time.time()
    prefs = webapp_prefs.snapshot()
    order = _bucket_order()
    timers, groups = [], {}
    owner_ids = allowed_user_ids()
    owner = None
    if owner_ids:
        try:
            u = db.get_user(next(iter(owner_ids)))
            owner = u
        except Exception:
            owner = None
    if owner:
        try:
            rows = db.active(owner["tg_id"])
        except Exception:
            rows = []
        for r in rows:
            b = r["bucket"] or ""
            muted = webapp_prefs.is_muted(b)
            t = {
                "id": r["id"],
                "label": r["label"],
                "ends_at": r["ends_at"],
                "created_at": r["created_at"],
                "bucket": b,
                "bucket_title": tr.bucket(b) if b else "⏱ Ручной",
                "muted": muted,
                "siege": b == "tOutpostSiegesMine",
                # 0.1.1.8: липкий сундук — «ждёт забора», карточка до момента сбора
                "sticky": bool(r["sticky"]) if "sticky" in r.keys() else False,
            }
            timers.append(t)
            # у одной группы один bucket -> muted одинаков у всех членов
            g = groups.setdefault(b, {"key": b, "title": t["bucket_title"],
                                      "count": 0, "muted": muted})
            g["count"] += 1
            g["muted"] = muted
    timers.sort(key=lambda t: (order.get(t["bucket"], 999), t["ends_at"]))
    group_list = [groups[b] for b in sorted(groups, key=lambda b: order.get(b, 999))]
    try:
        st = _api_status()
    except Exception:
        st = {}
    return {
        "ok": True,
        "version": config.APP_VERSION,
        "now": now,
        "paused": pause_state.is_paused(),
        "user": owner["tg_id"] if owner else None,
        "timers": timers,
        "groups": group_list,
        "settings": {"all": prefs["all"], "muted": {b: True for b in prefs["buckets"]}},
        "api": st,
        "app": {"refresh": _REFRESH_SUBMIT is not None,
                "game_url": f"https://t.me/{config.FOMO_GAME_BOT}/{config.FOMO_APP_NAME}",
                # 0.1.1.9: Termux-режим — бейдж в шапке + статус для кнопки ⚙️
                "termux": config.termux_notify_enabled(),
                "termux_bin": termux_notify.binary_available()},
    }


def _api_status():
    """Короткий статус автотрекинга для шапки страницы (без aiogram-зависимостей)."""
    import api_poller
    s = api_poller.status()
    return {
        "native": s.get("native", False),
        "token_dead": s.get("token_dead", False),
        "last_poll": s.get("last_poll"),
        "last_status": s.get("last_status"),
        "last_error": s.get("last_error", ""),
        "last_all_poll": s.get("last_all_poll"),
        # Мозг опросника: режим + ночной режим (экран настроек на странице)
        "mode": s.get("mode", "off"),
        "quiet": bool(s.get("quiet")),
        "night": s.get("night") or {},
    }


# ---------- HTTP-обработчик ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "FomoTimerWebApp/" + config.APP_VERSION

    def log_message(self, fmt, *args):  # шум в консоль не льём
        pass

    # --- служебное ---

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, code, msg):
        self._json({"ok": False, "error": msg}, code)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > BODY_MAX:
            return None
        try:
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    # --- GET ---

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            try:
                body = INDEX_PATH.read_bytes()
            except OSError:
                body = ("<!doctype html><meta charset='utf-8'><title>Fomo Timer</title>"
                        "<p style='font:16px sans-serif;padding:2em'>Файл "
                        "webapp/index.html не найден в папке бота.</p>").encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            try:
                self._json(build_state())
            except Exception as e:
                log.exception("api/state ошибка")
                self._fail(500, str(e)[:200])
            return
        if path == "/api/env":
            self._api_env_get()
            return
        self._fail(404, "нет такого пути")

    # --- POST ---

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        data = self._read_json()
        if data is None:
            self._fail(400, "ожидался JSON")
            return
        try:
            if path == "/api/settings":
                self._api_settings(data)
                return
            if path == "/api/refresh":
                self._api_refresh(data)
                return
            if path == "/api/cancel":
                self._api_cancel(data)
                return
            if path == "/api/brain":
                self._api_brain(data)
                return
            if path == "/api/env":
                self._api_env_save(data)
                return
        except Exception as e:
            log.exception("api POST ошибка")
            self._fail(500, str(e)[:200])
            return
        self._fail(404, "нет такого пути")

    def _api_settings(self, data):
        if "all" in data:
            webapp_prefs.set_all(bool(data["all"]))
        bucket = data.get("bucket")
        if bucket:
            webapp_prefs.set_bucket(str(bucket), bool(data.get("muted", False)))
        prefs = webapp_prefs.snapshot()
        self._json({"ok": True,
                    "settings": {"all": prefs["all"],
                                 "muted": {b: True for b in prefs["buckets"]}}})

    def _api_refresh(self, _data):
        if _REFRESH_SUBMIT is None:
            self._fail(503, "опрос игры ещё не запущен")
            return
        try:
            _REFRESH_SUBMIT()
            self._json({"ok": True, "queued": True})
        except Exception as e:
            self._fail(500, str(e)[:200])

    def _api_cancel(self, data):
        owner_ids = allowed_user_ids()
        if not owner_ids:
            self._fail(503, "владелец ещё не определён (нажмите /start у бота)")
            return
        try:
            tid = int(data.get("id", 0))
        except (TypeError, ValueError):
            self._fail(400, "нет id таймера")
            return
        ok = db.cancel(next(iter(owner_ids)), tid)
        self._json({"ok": ok, "error": "" if ok else "таймер не найден"})

    def _api_brain(self, data):
        """Ночной режим со страницы: тот же набор, что и в меню бота."""
        import api_poller
        import pollbrain
        if data.get("reset"):
            pollbrain.reset_night()
        else:
            key = str(data.get("key", ""))
            if key not in ("night_start", "night_end", "night_silent",
                           "night_microticks", "tz"):
                self._fail(400, "неизвестный ключ")
                return
            if not pollbrain.set_night(key, data.get("value")):
                self._fail(400, "плохое значение (время — ЧЧ:ММ)")
                return
        try:
            st = api_poller.status()
        except Exception:
            st = {"night": {}}
        self._json({"ok": True, "night": st.get("night") or {}})

    # ---------- .env: редактор кнопки ⚙️ (0.1.1.9) ----------

    def _api_env_get(self):
        """Содержимое локального .env + статус Termux-режима.

        Сервер слушает только 127.0.0.1: страница доступна исключительно
        на устройстве с ботом, там же лежит и .env — отдельная авторизация
        не нужна (тот же уровень доверия, что у /api/cancel).
        """
        p = config.env_file_path()
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            raw = ""                      # .env ещё не создан — редактор начнёт с пустого
        self._json({
            "ok": True,
            "path": p.name,
            "raw": raw,
            "termux": config.termux_notify_enabled(),
            "termux_bin": termux_notify.binary_available(),
        })

    def _api_env_save(self, data):
        """Сохранить .env: либо целиком {"raw": ...}, либо точечно {"key", "value"}.

        Целиком: старый файл копится в .env.bak, новый пишется атомарно;
        строки вида КЛЮЧ=ЗНАЧЕНИЕ, комментарии и пустые строки — как прислал.
        После записи config.reload_from_env() применяет скалярные настройки
        без рестарта (см. описание там — что применяется сразу, что нет).
        """
        p = config.env_file_path()
        if "key" in data:
            key = str(data.get("key", "")).strip()
            if not _ENV_KEY_RE.match(key):
                self._fail(400, "плохое имя ключа")
                return
            value = str(data.get("value", ""))
            if len(value) > 4096:
                self._fail(400, "значение слишком длинное")
                return
            if not config._update_env_keys({key: value}, str(p)):
                self._fail(500, "не удалось записать .env")
                return
            config.reload_from_env()
            self._json({"ok": True,
                        "termux": config.termux_notify_enabled(),
                        "termux_bin": termux_notify.binary_available()})
            return
        raw = data.get("raw")
        if not isinstance(raw, str) or not raw.strip():
            self._fail(400, "пустой .env — так файл сохранить нельзя")
            return
        if "=" not in raw:
            self._fail(400, "в тексте нет строк вида КЛЮЧ=ЗНАЧЕНИЕ")
            return
        if len(raw) > ENV_MAX:
            self._fail(400, "файл слишком большой (>128 КБ)")
            return
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        backup = False
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                shutil.copyfile(p, p.with_name(p.name + ".bak"))
                backup = True
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(raw, encoding="utf-8")
            os.replace(tmp, p)           # атомарно: обрыв не оставит битый .env
        except OSError as e:
            self._fail(500, f"запись .env не удалась: {e}")
            return
        config.reload_from_env()
        self._json({"ok": True, "backup": backup,
                    "termux": config.termux_notify_enabled(),
                    "termux_bin": termux_notify.binary_available()})


# ---------- Запуск ----------

def make_server(host="127.0.0.1", port=8080):
    """Сервер на первом свободном порту из range(port, port+10)."""
    last = None
    for p in range(int(port), int(port) + 10):
        try:
            return ThreadingHTTPServer((host, p), Handler)
        except OSError as e:
            last = e
    raise OSError(f"нет свободного порта от {port}: {last}")


def start(host="127.0.0.1", port=8080):
    """Поднять сервер в фоновом потоке (daemon). -> ThreadingHTTPServer."""
    global _PORT
    srv = make_server(host, port)
    _PORT = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                          daemon=True, name="webapp-server")
    th.start()
    log.info("Страница таймеров: http://%s:%s (только это устройство)", host,
             srv.server_address[1])
    return srv
