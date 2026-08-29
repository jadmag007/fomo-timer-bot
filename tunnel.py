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
  * адрес отдаёт в callback (bot.py ставит кнопку меню Telegram);
  * процесс упал → через 60 с тихо перезапускается (адрес будет новым).

Если у вас СВОЙ адрес (VPS, свой туннель) — запишите его в
WEBAPP_PUBLIC_URL в .env, и cloudflared не понадобится вовсе.
"""
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import config

log = logging.getLogger("webapp.tunnel")

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "data" / "tunnel.log"

_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

DOWNLOADS = {
    ("win32", "cloudflared.exe"):
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-windows-amd64.exe",
    ("linux", "cloudflared"):
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-linux-amd64",
    ("darwin", "cloudflared"):
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-darwin-amd64",
}
RESTART_DELAY = 60  # секунд между попытками перезапуска упавшего туннеля


def parse_tunnel_url(line: str):
    """Из строки вывода cloudflared достать адрес туннеля (или None).

    Отдельная функция — чтобы покрывать тестами без запуска процессов.
    """
    if not line:
        return None
    m = _URL_RE.search(line)
    return m.group(0) if m else None


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
    """Скачать cloudflared с официального релиза. -> путь или исключение."""
    sysname = "win32" if sys.platform.startswith("win") else (
        "darwin" if sys.platform == "darwin" else "linux")
    fname = {"win32": "cloudflared.exe", "darwin": "cloudflared",
             "linux": "cloudflared"}[sysname]
    url = DOWNLOADS[(sysname, fname)]
    dest = Path(dest) if dest else ROOT / fname
    log.info("Мини-апп: скачиваю cloudflared (~20 МБ, один раз)…")
    req = urllib.request.Request(url, headers={"User-Agent": "fomo-timer-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    if sysname != "win32":
        try:
            dest.chmod(dest.stat().st_mode | 0o111)
        except OSError:
            pass
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
    """Строка вывода cloudflared -> в файл data/tunnel.log (без раздувания)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass


def _run_once(binary, port, on_url):
    """Один запуск cloudflared до падения процесса. -> адрес туннеля или None."""
    cmd = [str(binary), "tunnel", "--url", f"http://127.0.0.1:{int(port)}",
           "--no-autoupdate"]
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace", **kwargs)
    except Exception as e:
        log.warning("Мини-апп: cloudflared не запустился: %s", e)
        return None
    url = None
    assert proc.stdout is not None
    for line in proc.stdout:            # pipe читаем постоянно, иначе забьётся
        line = (line or "").strip()
        if not line:
            continue
        _log_line(line)
        if url is None:
            url = parse_tunnel_url(line)
            if url:
                log.info("Мини-апп: туннель поднят: %s", url)
                try:
                    on_url(url)
                except Exception:
                    log.exception("Мини-апп: on_url упал")
    try:
        proc.wait(timeout=None)
    except Exception:
        proc.kill()
    return url


def start(port=None, on_url=lambda u: None, auto_download=True):
    """Фоновый поток-надзиратель: держит туннель живым, пока бот работает.

    Возвращает threading.Thread (daemon) или None, если cloudflared
    недоступен (тогда мини-апп останется только локальным, а /app честно
    об этом скажет).
    """
    port = int(port if port is not None else config.WEBAPP_PORT)
    binary = ensure_binary(auto_download=auto_download)
    if binary is None:
        log.warning("Мини-апп: cloudflared недоступен — публичного адреса не "
                    "будет (свой адрес можно вписать в WEBAPP_PUBLIC_URL)")
        return None

    def watcher():
        while True:
            url = _run_once(binary, port, on_url)
            log.warning("Мини-апп: туннель остановился (адрес был: %s) — "
                        "перезапуск через %s с", url or "не успел подняться",
                        RESTART_DELAY)
            time.sleep(RESTART_DELAY)

    th = threading.Thread(target=watcher, daemon=True, name="webapp-tunnel")
    th.start()
    return th
