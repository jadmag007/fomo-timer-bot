"""Публичный HTTPS-адрес для мини-приложения: туннель cloudflared.

Telegram открывает мини-приложения только по HTTPS с действительным
сертификатом. Бот работает дома, на обычном Windows-ПК без белого IP —
значит, нужен туннель. Выбран cloudflared (Cloudflare) в режиме quick
tunnel: бесплатно, БЕЗ регистрации и токенов — просто запущенный процесс.

Что делает модуль:
  * находит cloudflared рядом с ботом (cloudflared.exe / cloudflared);
  * если файла нет — скачивает с официального релиза Cloudflare (~20 МБ,
    один раз; .gitignore и zip его не включают);
  * запускает `cloudflared tunnel --url http://127.0.0.1:PORT` и вылавливает
    из вывода адрес вида https://xxxx.trycloudflare.com;
  * ПРОВЕРЯЕТ адрес по-настоящему (GET до ответа 200), и только после этого
    объявляет его (on_url → bot.py ставит кнопку меню). Дело в том, что имя
    регистрируется через 443 и появляется в выводе РАНЬШЕ, чем туннель
    подключается к edge-серверам (те ходят через порт 7844). Если провайдер
    или файрвол режет 7844, адрес есть, а страница отдаёт Cloudflare error
    1033 — раньше бот честно, но ошибочно рапортовал «туннель поднят»;
  * если страница так и не ответила — пробует другой протокол транспорта
    (http2 → quic: они ходят через TCP 7844 и UDP 7844 соответственно, у
    кого-то заблокирован только один из двух), а когда не помогли оба —
    один раз сообщает наверх (on_blocked) и продолжает перепроверять каждые
    несколько минут: включите VPN на ПК — туннель поднимется сам, и свежая
    кнопка придёт без перезапуска бота;
  * процесс упал → сообщает об этом (on_down, кнопка с мёртвым адресом
    убирается) и через 8 с тихо перезапускается (адрес будет новым);
  * ведёт статус (status()): жив ли туннель, подтверждён ли адрес, каким
    протоколом, перезапуски — его показывает /апи.

Если у вас СВОЙ адрес (VPS, свой туннель) — запишите его в
WEBAPP_PUBLIC_URL в .env, и cloudflared не понадобится вовсе.
"""
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

log = logging.getLogger("webapp.tunnel")

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "data" / "tunnel.log"
LOG_MAX_BYTES = 1 << 20          # ротация: не давать tunnel.log расти бесконечно
LOG_KEEP_BYTES = 128 << 10       # сколько хвоста оставляем при ротации

_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

DL_BASE = ("https://github.com/cloudflare/cloudflared/releases/latest/download/")


def _download_choice(sysname="", machine=""):
    """Подобрать сборку cloudflared под систему и архитектуру. -> (имя, url).

    Аргументы — для тестов; по умолчанию берём реальную платформу.
    Андроид в Termux — это linux aarch64: раньше там качался amd64-бинарник,
    который не запускался (Exec format error) — теперь для arm64 своя сборка.
    """
    sysname = sysname or (
        "win32" if sys.platform.startswith("win") else
        ("darwin" if sys.platform == "darwin" else "linux"))
    arch = (machine or platform.machine() or "").lower()
    is_arm = arch in ("aarch64", "arm64", "armv8l")
    if sysname == "win32":
        return "cloudflared.exe", DL_BASE + "cloudflared-windows-amd64.exe"
    if is_arm:
        name = ("cloudflared-darwin-arm64" if sysname == "darwin"
                else "cloudflared-linux-arm64")
    else:
        name = ("cloudflared-darwin-amd64" if sysname == "darwin"
                else "cloudflared-linux-amd64")
    return "cloudflared", DL_BASE + name

# Пауза между попытками перезапуска упавшего туннеля. Раньше было 60 с —
# за эту минуту кнопка меню вела на мёртвый адрес (Cloudflare error 1033).
# 8 с: даже при падении простой почти незаметен.
RESTART_DELAY = 8

# --- Проверка адреса (анти error 1033) ---
URL_WAIT_SEC = 30      # сколько ждать появления адреса в выводе cloudflared
PROBE_ATTEMPTS = 6     # GET-проверок адреса на каждый протокол
PROBE_DELAY = 6        # пауза между проверками, с (edge подключается не сразу)
BLOCKED_RETRY = 180    # пауза между циклами, когда адрес так и не подтвердился

# Порядок транспортных протоколов. http2 = TCP 7844, quic = UDP 7844.
# По умолчанию http2 (живучее за NAT), при неудаче пробуем quic.
PROTOCOL_SEQUENCE = ("http2", "quic")

# Состояние туннеля для /апи (и тестов): url и since обновляются, когда адрес
# ПРОШЁЛ проверку; blocked/last_probe_result — чем закончились проверки.
_STATUS = {"url": "", "since": 0.0, "restarts": 0, "down_at": 0.0,
           "running": False, "verified": False, "protocol": "",
           "blocked": False, "last_probe": 0.0, "last_probe_result": ""}


def status():
    """Снимок состояния туннеля: {url, since, restarts, down_at, running,
    verified, protocol, blocked, last_probe, last_probe_result}."""
    return dict(_STATUS)


def parse_tunnel_url(line: str):
    """Из строки вывода cloudflared достать адрес туннеля (или None).

    Отдельная функция — чтобы покрывать тестами без запуска процессов.
    """
    if not line:
        return None
    m = _URL_RE.search(line)
    return m.group(0) if m else None


def http_probe(url, timeout=8):
    """GET-запрос к адресу туннеля. -> (статус:int|None, начало тела).

    status None — ответа нет вообще (таймаут/DNS/сеть). HTTPError (4xx/5xx)
    раскрывается в свой код — Cloudflare отвечает на мёртвые туннели
    статусом 530 с «Error 1033» внутри.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "fomo-timer-bot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (getattr(resp, "status", 200) or 200,
                    (resp.read(4096) or b"").decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            body = (e.read(4096) or b"").decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception:
        return None, ""


def classify_probe(res):
    """(статус, тело) -> 'ok' | 'cf_error' | 'unreachable'.

    ok          — наш веб-сервер ответил 200 (туннель действительно работает);
    cf_error    — ответ пришёл, но не 200: Cloudflare отдаёт страницу ошибки
                  (530/error 1033) — имя зарегистрировано, туннель не подключён;
    unreachable — ответа нет: сеть не пускает даже к trycloudflare.com.
    """
    code = res[0]
    if code is None:
        return "unreachable"
    return "ok" if code == 200 else "cf_error"


def verify(url, attempts=PROBE_ATTEMPTS, delay=PROBE_DELAY,
           sleep=time.sleep, probe=None):
    """Открывается ли адрес по-настоящему: до attempts проверок с паузой.

    Туннель подключается к edge не мгновенно, поэтому первая проверка может
    быть неудачной — крутим до успеха или до исчерпания попыток.
    probe/sleep подменяются в тестах.
    """
    probe = probe or http_probe
    for i in range(max(1, int(attempts))):
        res = probe(url)
        verdict = classify_probe(res)
        _STATUS["last_probe"] = time.time()
        _STATUS["last_probe_result"] = verdict
        if verdict == "ok":
            return True
        if i < int(attempts) - 1:
            sleep(delay)
    return False


def cloudflared_path():
    """Путь к cloudflared рядом с ботом, если он уже скачан/установлен."""
    names = ["cloudflared.exe", "cloudflared"]
    for n in names:
        p = ROOT / n
        if p.is_file():
            return p
    # возможно, установлен в системе
    for n in ("cloudflared.exe", "cloudflared"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = Path(d) / n
            try:
                if p.is_file():
                    return p
            except OSError:
                continue
    return None


def download_cloudflared(dest=None, timeout=180) -> Path:
    """Скачать cloudflared с официального релиза. -> путь или исключение.

    Качаем в .part и переименовываем атомарно: обрыв сети раньше оставлял
    битый бинарник под финальным именем, ensure_binary его «находил» —
    и туннель вечно не поднимался.
    """
    fname, url = _download_choice()
    dest = Path(dest) if dest else ROOT / fname
    tmp = dest.with_name(dest.name + ".part")
    log.info("Мини-апп: скачиваю cloudflared (~20 МБ, один раз)…")
    req = urllib.request.Request(url, headers={"User-Agent": "fomo-timer-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    if sysname != "win32":
        try:
            tmp.chmod(tmp.stat().st_mode | 0o111)
        except OSError:
            pass
    os.replace(tmp, dest)
    log.info("Мини-апп: cloudflared готов: %s (%s КБ)",
             dest.name, dest.stat().st_size // 1024)
    return dest


def ensure_binary(auto_download=True):
    """Найти бинарник; при отсутствии и разрешении — скачать. None — не вышло."""
    p = cloudflared_path()
    if p:
        return p
    if not auto_download:
        return None
    try:
        return download_cloudflared()
    except Exception as e:
        log.warning("Мини-апп: не удалось скачать cloudflared: %s", e)
        return None


def _log_line(line: str):
    """Строка вывода cloudflared -> в файл data/tunnel.log (с ротацией)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if LOG_PATH.stat().st_size > LOG_MAX_BYTES:
                data = LOG_PATH.read_bytes()[-LOG_KEEP_BYTES:]
                marker = "…[старые строки обрезаны]\n".encode("utf-8")
                LOG_PATH.write_bytes(marker + data)
        except OSError:
            pass
        with LOG_PATH.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass


def build_cmd(binary, port, protocol="http2"):
    """Командная строка cloudflared. Отдельная функция — покрывается тестами.

    --protocol http2: QUIC (UDP) у многих провайдеров режется — http2 поверх
    TCP заметно живучее. Если не помог и он, watcher попробует quic.
    --edge-ip-version 4: не пытаться идти по IPv6, у домашних сетей с ним
    часто плохо.
    """
    return [str(binary), "tunnel", "--url", f"http://127.0.0.1:{int(port)}",
            "--no-autoupdate", "--protocol", protocol,
            "--edge-ip-version", "4"]


def protocol_list():
    """Какие протоколы пробовать и в каком порядке (из config.TUNNEL_PROTOCOL).

    auto  -> ("http2", "quic"); явное значение фиксирует один протокол.
    """
    p = (getattr(config, "TUNNEL_PROTOCOL", "auto") or "auto").strip().lower()
    if p in PROTOCOL_SEQUENCE:
        return (p,)
    return PROTOCOL_SEQUENCE


def _kill(proc):
    """Аккуратно добить процесс cloudflared."""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# Живой процесс cloudflared — для уборки при выходе из бота (иначе на Windows
# после каждого рестарта start.bat копятся осиротевшие cloudflared.exe)
_CURRENT = {"proc": None}


def _kill_current():
    p = _CURRENT.get("proc")
    if p is not None:
        _kill(p)
        _CURRENT["proc"] = None


def _finish_down():
    _STATUS["running"] = False
    _STATUS["down_at"] = time.time()
    _STATUS["verified"] = False


def _run_once(binary, port, protocol, on_url, on_down):
    """Один запуск cloudflared: адрес -> проверка -> (если жив) слежение.

    -> 'ok'         адрес работал (объявлен через on_url), потом процесс умер
                    (вызван on_down);
       'no_url'     адрес так и не выдан / процесс умер сразу;
       'unverified' адрес выдан, но страница так и не ответила (порт 7844
                    заблокирован) — наверх НЕ сообщаем: мёртвую кнопку
                    ставить нельзя.
    """
    cmd = build_cmd(binary, port, protocol)
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace", **kwargs)
    except Exception as e:
        log.warning("Мини-апп: cloudflared не запустился: %s", e)
        return "no_url"
    _CURRENT["proc"] = proc
    _STATUS["running"] = True
    _STATUS["verified"] = False
    _STATUS["protocol"] = protocol

    # Вывод читает отдельный поток постоянно (иначе pipe переполнится и
    # cloudflared зависнет), а главный поток ждёт адрес/конец процесса.
    holder = {"url": None, "eof": False}

    def reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            line = (line or "").strip()
            if not line:
                continue
            _log_line(line)
            if holder["url"] is None:
                u = parse_tunnel_url(line)
                if u:
                    holder["url"] = u
        holder["eof"] = True

    threading.Thread(target=reader, daemon=True, name="tunnel-log").start()

    deadline = time.time() + URL_WAIT_SEC
    while holder["url"] is None and not holder["eof"] and time.time() < deadline:
        time.sleep(0.3)

    url = holder["url"]
    if not url:
        _kill(proc)
        _finish_down()
        return "no_url"

    _STATUS["url"] = url
    _STATUS["since"] = time.time()
    log.info("Мини-апп: адрес выдан (%s), проверяю, открывается ли страница…", url)
    if not verify(url):
        log.warning("Мини-апп: адрес %s НЕ открывается (Cloudflare error 1033) — "
                    "порт 7844 заблокирован сетью/провайдером", url)
        _kill(proc)
        _STATUS["url"] = ""      # мёртвый адрес наружу не показываем
        _STATUS["since"] = 0.0
        _finish_down()
        return "unverified"

    _STATUS["verified"] = True
    log.info("Мини-апп: туннель проверен — страница отвечает: %s", url)
    try:
        on_url(url)
    except Exception:
        log.exception("Мини-апп: on_url упал")

    while not holder["eof"]:     # жив, пока жив процесс cloudflared
        time.sleep(1.0)
    _finish_down()
    _STATUS["restarts"] += 1
    _STATUS["url"] = ""
    try:
        on_down()
    except Exception:
        log.exception("Мини-апп: on_down упал")
    return "ok"


def start(port=None, on_url=lambda u: None, on_down=lambda: None,
          on_blocked=lambda reason: None, auto_download=True):
    """Фоновый поток-надзиратель: держит туннель живым, пока бот работает.

    on_url вызывается ТОЛЬКО с проверенным адресом; on_down — когда
    проверенный туннель умер; on_blocked — один раз на серию неудач, когда
    адрес не подтверждается ни одним протоколом (сеть режет 7844).

    Скачивание cloudflared (~20 МБ) происходит ВНУТРИ потока-надзирателя:
    раньше ensure_binary() выполнялся прямо в event-loop и на первом запуске
    подвешивал весь бот до трёх минут.

    Возвращает threading.Thread (daemon).
    """
    import atexit
    atexit.register(_kill_current)   # выход из бота — cloudflared не осиротеет
    port = int(port if port is not None else config.WEBAPP_PORT)
    protocols = protocol_list()

    def watcher():
        binary = None
        i = 0
        blocked_sent = False
        while True:
            if binary is None:
                binary = ensure_binary(auto_download=auto_download)
                if binary is None:
                    log.warning("Мини-апп: cloudflared недоступен — публичного "
                                "адреса не будет (свой адрес можно вписать в "
                                "WEBAPP_PUBLIC_URL). Попробую снова через 30 с")
                    time.sleep(30)
                    continue
            proto = protocols[i % len(protocols)]
            outcome = _run_once(binary, port, proto, on_url, on_down)
            if outcome == "ok":
                i = 0
                blocked_sent = False
                _STATUS["blocked"] = False
                log.warning("Мини-апп: туннель остановился (адрес был) — "
                            "перезапуск через %s с", RESTART_DELAY)
                time.sleep(RESTART_DELAY)
            elif outcome == "no_url":
                i += 1
                log.warning("Мини-апп: cloudflared не выдал адрес — "
                            "повтор через %s с", RESTART_DELAY)
                time.sleep(RESTART_DELAY)
            else:  # unverified
                i += 1
                if not blocked_sent:
                    blocked_sent = True
                    _STATUS["blocked"] = True
                    reason = ("Сеть блокирует порты Cloudflare-туннеля "
                              "(TCP/UDP 7844): адрес выдаётся, но страница не "
                              "открывается (error 1033). Помогает VPN на "
                              "компьютере с ботом или свой адрес в "
                              "WEBAPP_PUBLIC_URL.")
                    log.warning("Мини-апп: туннель не подтверждён. %s", reason)
                    try:
                        on_blocked(reason)
                    except Exception:
                        log.exception("Мини-апп: on_blocked упал")
                log.warning("Мини-апп: перепроверяю через %s с (протокол %s) — "
                            "как только сеть пропустит туннель, придёт свежая "
                            "кнопка", BLOCKED_RETRY,
                            protocols[i % len(protocols)])
                time.sleep(BLOCKED_RETRY)

    th = threading.Thread(target=watcher, daemon=True, name="webapp-tunnel")
    th.start()
    return th
