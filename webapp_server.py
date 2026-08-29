"""Мини-приложение в боте: локальный веб-сервер + JSON API для него.

Зачем: пуш-уведомления удобны, но видеть ВСЕ таймеры сразу и управлять ими
кнопками удобнее в мини-аппе. Он открывается прямо в Telegram (кнопка меню
слева от поля ввода или команда /app) и показывает живые отсчёты, тихий
режим по группам, отмену таймеров и кнопку «Обновить».

Как это устроено:
  * этот модуль — маленький HTTP-сервер на stdlib (127.0.0.1, WEBAPP_PORT):
    отдаёт webapp/index.html и JSON для /api/*;
  * публичный HTTPS-адрес даёт туннель cloudflared (см. tunnel.py) — без него
    Telegram не открывает веб-приложения; URL меняется при рестарте,
    кнопка меню обновляется автоматически (bot.py);
  * доступ защищён подписью initData (официальная схема Telegram Mini Apps):
    страница получает initData при открытии и передаёт её в заголовке
    «Authorization: tma …», сервер проверяет HMAC подпись бота и то, что
    пользователь — владелец бота. Чужой по ссылке ничего не получит (401);
  * ЛОКАЛЬНЫЙ режим (WEBAPP_LOCAL_DEBUG, по умолчанию включён): запрос с
    этого же ПК без Telegram-подписи — это владелец в обычном браузере
    (http://127.0.0.1:PORT). Спасение, когда сеть режет туннель (error
    1033). Трафик из интернета через туннель НЕ проходит: у него всегда
    есть служебные заголовки Cloudflare, а Host — не локальный (см.
    local_mode_ok).

API (все требуют заголовок Authorization: tma <initData>):
  GET  /api/state     — всё для отрисовки: таймеры, группы, настройки, статус
  POST /api/settings  — {"bucket": "tTroops", "muted": true} или {"all": true}
  POST /api/refresh   — внеочередной опрос API игры (как кнопка 🔄)
  POST /api/cancel    — {"id": 12} отменить таймер
"""
import hashlib
import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

import config
import db
import pause_state
import webapp_prefs
import translations as tr

log = logging.getLogger("webapp")

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "webapp" / "index.html"

# initData живёт в открытой странице долго; 30 суток — разумный предел
# свежести подписи (после него страница просто попросит переоткрыть).
INITDATA_MAX_AGE = 30 * 24 * 3600

# Максимальный размер тела POST (наши запросы — десятки байт)
BODY_MAX = 16 * 1024

# Служебные заголовки, которые Cloudflare/cloudflared добавляет ЛЮБОМУ
# запросу из интернета. Локальный браузер их не шлёт — по ним отличаем
# трафик через туннель от владельца, открывшего страницу на этом же ПК.
TUNNEL_MARKERS = frozenset(
    ("cf-connecting-ip", "cf-ray", "cf-worker", "x-forwarded-for",
     "x-forwarded-proto", "x-real-ip"))

# Раз в час напоминаем в лог, что локальный режим активен (не на каждый запрос)
_LOCAL_LOGGED = {"at": 0.0}

# --- Провайдеры, которые bot.py связывает с живым ботом (для тестов — заглушки)
_PUBLIC_URL = ""        # текущий публичный HTTPS-адрес мини-аппа
_LOOP = None            # главный asyncio-цикл бота
_REFRESH_SUBMIT = None  # -> concurrent.futures.Future опроса API игры


def set_public_url(url: str):
    """Запомнить актуальный публичный адрес (вызывает bot.py/tunnel.py)."""
    global _PUBLIC_URL
    _PUBLIC_URL = (url or "").strip()


def current_url() -> str:
    return _PUBLIC_URL


def set_providers(loop=None, refresh_submit=None):
    """Связать сервер с ботом: цикл и способ запустить внеочередной опрос."""
    global _LOOP, _REFRESH_SUBMIT
    _LOOP = loop
    _REFRESH_SUBMIT = refresh_submit


def allowed_user_ids() -> set:
    """Кому разрешён вход: владелец из .env или первый /start боту."""
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


# ---------- Проверка подписи initData (официальная схема Telegram) ----------

def build_secret_key(bot_token: str) -> bytes:
    """Секрет для проверки: HMAC_SHA256(key=b'WebAppData', msg=bot_token)."""
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def validate_init_data(init_data: str, bot_token: str, allowed_ids=None,
                       now=None) -> tuple:
    """Проверить подпись initData. -> (ok, user_id, причина).

    Схема из документации Telegram Mini Apps:
      data_check_string = поля кроме hash, отсортированные, 'k=v' через \\n
      secret = HMAC_SHA256(key='WebAppData', msg=bot_token)
      hash == HMAC_SHA256(key=secret, msg=data_check_string)
    Дополнительно: auth_date не старше INITDATA_MAX_AGE и пользователь
    входит в allowed_ids (владелец бота).
    """
    now = now if now is not None else time.time()
    if not init_data or not bot_token:
        return False, None, "нет initData или токена"
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return False, None, "initData не разобрать"
    fields = dict(pairs)
    got_hash = fields.get("hash", "")
    if not got_hash:
        return False, None, "нет hash"
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(pairs) if k not in ("hash", "signature"))
    secret = build_secret_key(bot_token)
    calc = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got_hash):
        return False, None, "подпись не совпала"
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return False, None, "нет auth_date"
    if auth_date <= 0 or now - auth_date > INITDATA_MAX_AGE:
        return False, None, "initData устарела — переоткрой мини-апп"
    try:
        user = json.loads(fields.get("user", "{}"))
        user_id = int(user.get("id", 0))
    except (ValueError, TypeError):
        return False, None, "нет пользователя"
    if user_id <= 0:
        return False, None, "нет пользователя"
    if allowed_ids is not None and user_id not in allowed_ids:
        return False, user_id, "это мини-апп владельца бота"
    return True, user_id, ""


# ---------- Состояние для /api/state ----------

def _bucket_order():
    """Порядок групп: как в translations.BUCKETS, неизвестные — в конец."""
    known = list(tr.BUCKETS.keys())
    return {k: i for i, k in enumerate(known)}


def local_mode_ok(client_host="", header_names=(), host_header="",
                  server_port=0, enabled=None) -> bool:
    """Это запрос владельца с того же ПК (браузер), а не из интернета?

    Локальный режим разрешает доступ без Telegram-подписи ТОЛЬКО когда
    сходится ВСЁ:
      * режим включён (WEBAPP_LOCAL_DEBUG в .env, по умолчанию true);
      * запрос пришёл с loopback (127.0.0.1 / ::1) — сервер снаружи не виден;
      * среди заголовков нет ни одного из TUNNEL_MARKERS — cloudflared
        добавляет их каждому запросу из интернета, значит через туннель
        придёт чужой, и ему тут нечего делать;
      * заголовок Host — локальный с нашим портом (у туннельного трафика
        Host = адрес trycloudflare.com).
    Отдельная функция — чтобы покрывать тестами без HTTP.
    """
    if enabled is None:
        enabled = bool(getattr(config, "WEBAPP_LOCAL_DEBUG", True))
    if not enabled:
        return False
    if (client_host or "") not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return False
    headers = {str(h).lower() for h in (header_names or ())}
    if headers & TUNNEL_MARKERS:
        return False
    hh = (host_header or "").strip().lower()
    if hh:
        if ":" not in hh:
            return False          # браузер всегда шлёт Host с портом
        hostpart, _, portpart = hh.rpartition(":")
        try:
            if int(portpart) != int(server_port or 0):
                return False
        except ValueError:
            return False
        if hostpart.strip("[]") not in ("127.0.0.1", "localhost", "::1"):
            return False
    return True


def build_state(now=None, local=False):
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
        "app": {"url": current_url(), "refresh": _REFRESH_SUBMIT is not None,
                "local": bool(local),
                "game_url": f"https://t.me/{config.FOMO_GAME_BOT}/{config.FOMO_APP_NAME}"},
    }


def _api_status():
    """Короткий статус автотрекинга для шапки мини-аппа (без aiogram-зависимостей)."""
    import api_poller
    s = api_poller.status()
    return {
        "native": s.get("native", False),
        "token_dead": s.get("token_dead", False),
        "last_poll": s.get("last_poll"),
        "last_status": s.get("last_status"),
        "last_error": s.get("last_error", ""),
        "last_all_poll": s.get("last_all_poll"),
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

    def _auth(self):
        """Проверить Authorization: tma <initData>. -> user_id или None.

        Без подписи, но с этого же ПК (браузер владельца) — локальный режим:
        доступ как у владельца (см. local_mode_ok и WEBAPP_LOCAL_DEBUG).
        """
        header = self.headers.get("Authorization", "")
        raw = header[4:].strip() if header.lower().startswith("tma ") else ""
        ok, user_id, why = validate_init_data(raw, config.BOT_TOKEN,
                                              allowed_user_ids())
        if ok:
            self._local = False
            return user_id
        if not raw:
            ids = allowed_user_ids()
            if ids and local_mode_ok(
                    self.client_address[0] if self.client_address else "",
                    self.headers.keys(), self.headers.get("Host", ""),
                    self.server.server_address[1]):
                self._local = True
                uid = next(iter(ids))
                now = time.time()
                if now - _LOCAL_LOGGED["at"] > 3600:
                    _LOCAL_LOGGED["at"] = now
                    log.info("Мини-апп: ЛОКАЛЬНЫЙ режим — страница в браузере "
                             "этого ПК работает без Telegram-подписи "
                             "(владелец id=%s)", uid)
                return uid
        elif raw:
            log.info("Мини-апп: отказ в доступе (%s)", why)
        return None

    def _deny(self):
        """401 с подсказкой про оба входа: Telegram и локальный браузер."""
        try:
            port = int(self.server.server_address[1])
        except Exception:
            port = 8080
        self._fail(401, "Открой мини-апп через Telegram (кнопка меню у бота) "
                        "или в браузере на компьютере с ботом: "
                        "http://127.0.0.1:%d" % port)

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
            if self._auth() is None:
                self._deny()
                return
            try:
                self._json(build_state(local=getattr(self, "_local", False)))
            except Exception as e:
                log.exception("api/state ошибка")
                self._fail(500, str(e)[:200])
            return
        self._fail(404, "нет такого пути")

    # --- POST ---

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        user_id = self._auth()
        if user_id is None:
            self._deny()
            return
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
                self._api_cancel(data, user_id)
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

    def _api_cancel(self, data, user_id):
        try:
            tid = int(data.get("id", 0))
        except (TypeError, ValueError):
            self._fail(400, "нет id таймера")
            return
        ok = db.cancel(user_id, tid)
        self._json({"ok": ok, "error": "" if ok else "таймер не найден"})


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
    srv = make_server(host, port)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                          daemon=True, name="webapp-server")
    th.start()
    log.info("Мини-апп: локальный сервер http://%s:%s", host,
             srv.server_address[1])
    return srv
