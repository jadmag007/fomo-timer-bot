#!/usr/bin/env python3
"""har_apply.py — автонастройка автотрекинга без ручного редактирования .env.

Подаёте ему файл трафика мини-аппа (HAR из DevTools ИЛИ текст «Copy as cURL»),
а он сам:
  1. Находит эндпоинт состояния игры (по полям-таймерам в ответе).
  2. Достаёт заголовок авторизации (Authorization / X-Api-Key / JWT).
  3. Прописывает API_STATE_URL, API_AUTH_HEADER, API_ENABLED=true в .env
     (остальные строки .env не трогает; файла нет — создаётся из .env.example).
  4. Делает контрольный GET-запрос и показывает код ответа:
     200 = всё живое, 401 = токен уже мёртв, снять заново.

Использование:
    python tools/har_apply.py fomo.har
    python tools/har_apply.py request.txt          # Copy as cURL (bash/cmd)
    python tools/har_apply.py fomo.har --no-test   # без контрольного запроса
Тот же разбор встроен в бота: папка token_updates/ обрабатывается сама.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):  # запуск как скрипт: python tools/har_apply.py ...
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import har_inspect  # noqa: E402

# --- Разбор «Copy as cURL» (варианты bash '...' / $'...' / cmd "...") ---
_CURL_URL_RE = re.compile(r"https?://[^\s'\"\\]+")
_CURL_HDR_RES = [
    re.compile(r"-H\s+\$'([^']+)'"),
    re.compile(r"-H\s+'([^']+)'"),
    re.compile(r'-H\s+"([^"]+)"'),
]


def parse_curl(text):
    """Текст 'Copy as cURL' -> (url, 'Name: value' или '')."""
    m = _CURL_URL_RE.search(text)
    url = m.group(0).rstrip("\\").rstrip("'\"") if m else ""
    auth = ""
    for line in text.splitlines():
        for rex in _CURL_HDR_RES:
            for hdr in rex.findall(line):
                name, _, value = hdr.partition(":")
                if not value:
                    continue
                if har_inspect.AUTH_KEY_RE.search(name) or har_inspect.JWT_RE.search(value):
                    auth = hdr.strip()
    # заголовки могут быть и в одну строку (cmd) — второй проход без разбора строк
    if not auth:
        for rex in _CURL_HDR_RES:
            for hdr in rex.findall(text):
                name, _, value = hdr.partition(":")
                if value and (har_inspect.AUTH_KEY_RE.search(name) or har_inspect.JWT_RE.search(value)):
                    auth = hdr.strip()
    return url, auth


def extract_credentials(path):
    """Файл (.har или cURL) -> параметры запроса к API игры.

    Возвращает: {state_url, auth_header, method, body, extra, source,
    timers_found, init_data}. extra — заголовки-подписи (api-* / user-agent)
    одним набором: у Fomo Fighters запрос действителен только со ВСЕМИ
    подписями. init_data — строка initData из POST /telegram/auth (если есть
    в файле): бот сам подпишет любой запрос и сам реанимирует ключ, и эти
    подписи из HAR становятся не нужны.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if text.lstrip().startswith("{"):
        try:
            rows = har_inspect.parse_har(p)
            best = har_inspect.pick_best(rows)
            if best:
                # В логе бывает несколько сессий (переподключения игры): подписи
                # ранних сессий уже мертвы. Среди запросов с тем же адресом берём
                # ПОСЛЕДНИЙ успешный — у него самые свежие подписи.
                same = [r for r in rows
                        if r["url"] == best["url"] and r["status"] == 200] or [best]
                best = same[-1]
                # Одиночный заголовок авторизации (Authorization / X-Api-Key / JWT).
                # Подписи api-* сюда НЕ попадают — они идут набором в extra.
                auth_name, auth_value = "", ""
                for r in rows:
                    if r["auth_name"] and not re.match(r"^api-", r["auth_name"], re.I):
                        auth_name, auth_value = r["auth_name"], r["auth_value"]
                        break
                if best["auth_name"] and not re.match(r"^api-", best["auth_name"], re.I):
                    auth_name, auth_value = best["auth_name"], best["auth_value"]
                extra = {}
                for name in ("api-key", "api-hash", "api-time", "api-version", "user-agent"):
                    v = (best.get("req_headers") or {}).get(name)
                    if v:
                        extra[name] = v
                method = (best.get("method") or "GET").upper()
                return {
                    "state_url": best["url"],
                    "auth_header": (f"{auth_name}: {auth_value}" if auth_name and auth_value else ""),
                    "method": method if method in ("GET", "POST") else "GET",
                    "body": (best.get("post_data") or "").strip(),
                    "extra": extra,
                    "source": "har",
                    "timers_found": len(best["time_fields"]),
                    "init_data": _extract_init_data(rows),
                }
        except json.JSONDecodeError:
            pass  # не HAR — пробуем как cURL
    url, auth = parse_curl(text)
    if url:
        return {"state_url": url, "auth_header": auth, "method": "GET", "body": "",
                "extra": {}, "source": "curl", "timers_found": -1, "init_data": ""}
    return None


def _extract_init_data(rows):
    """HAR-строки -> initData из ПОСЛЕДНЕГО POST /telegram/auth (или '')."""
    for r in reversed(rows):
        url = r.get("url") or ""
        pd = r.get("post_data") or ""
        if "telegram/auth" in url and pd.strip().startswith("{"):
            try:
                j = json.loads(pd)
                cand = ((j.get("data") or {}).get("initData")) or ""
                if cand.strip():
                    return cand.strip()
            except (json.JSONDecodeError, AttributeError):
                continue
    return ""


def update_env(updates, env_path=".env"):
    """Обновить указанные ключи в .env, сохранив остальные строки и комментарии.

    Файла нет -> копия .env.example (если есть) -> обновление.
    Возвращает путь к файлу.
    """
    env = Path(env_path)
    if not env.exists():
        example = Path(env).parent / ".env.example"
        if example.exists():
            shutil.copyfile(example, env)
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out, done = [], set()
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}={v}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    return env.resolve()


def mask_auth(auth_header):
    """'Authorization: Bearer eyJhbGci...' -> 'Authorization: Bearer eyJhbGci…' (маска)."""
    if not auth_header:
        return "(не найден)"
    name, _, value = auth_header.partition(":")
    value = value.strip()
    if len(value) > 18:
        value = value[:15] + "…"
    return f"{name.strip()}: {value}"


def native_test(init_data, base_url="https://api.fomofighters.xyz", timeout=20):
    """Контрольный цикл нативного режима: auth + timers со самоподписью.
    -> (код, фрагмент). Это и есть проверка «ключа», который живёт в initData,
    — работает даже для давно снятого файла, если initData ещё принята."""
    import time as _t
    import urllib.error
    import urllib.request

    from fomo_client import api_hash, build_auth_body, init_data_hash  # единый источник правды

    base = (base_url or "https://api.fomofighters.xyz").rstrip("/")
    origin = "https://game.fomofighters.xyz"

    def call(path, obj, api_key):
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        t = int(_t.time())
        headers = {
            "Content-Type": "application/json", "Api-Key": api_key,
            "Api-Time": str(t), "Api-Hash": api_hash(t, body),
            "Is-Beta-Server": "null", "User-Agent": "Mozilla/5.0",
            "Origin": origin, "Referer": origin + "/",
        }
        req = urllib.request.Request(base + path, data=body.encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(200).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read(200).decode("utf-8", "replace")
        except Exception as e:
            return None, str(e)

    st, txt = call("/telegram/auth", build_auth_body(init_data), "empty")
    ok_auth = st == 200 and '"success":true' in txt.replace(" ", "")
    if not ok_auth:
        return st or 0, f"auth не принят: {txt[:120]}"
    return call("/user/data/timers", {"data": {"lang": "ru"}},
                init_data_hash(init_data))


def test_endpoint(state_url, auth_header, timeout=15, method="GET", body="", extra=None):
    """Контрольный запрос (метод/тело/подписи — как у игры). -> (код, фрагмент)."""
    import urllib.error
    import urllib.request

    headers = dict(extra or {})
    if auth_header:
        name, _, value = auth_header.partition(":")
        headers[name.strip()] = value.strip()
    data = body.encode("utf-8") if body else None
    if data and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(state_url, data=data, headers=headers,
                                 method=(method or "GET").upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace")
    except Exception as e:  # сеть, DNS, таймаут
        return None, str(e)


def apply_file(path, env_path=".env", do_test=True):
    """Полный цикл для одного файла. -> (ok: bool, отчёт для человека: str)."""
    creds = extract_credentials(path)
    lines = []
    if not creds or not creds.get("state_url"):
        return False, "Не нашёл в файле ни HAR-записей, ни cURL-команды с URL."
    lines.append(f"Эндпоинт: {creds['state_url']}")
    if (creds.get("method") or "GET").upper() != "GET" or creds.get("body"):
        lines.append(f"Метод: {(creds.get('method') or 'GET').upper()}"
                     + (f", тело: {creds['body'][:60]}" if creds.get("body") else ""))
    if creds.get("auth_header"):
        lines.append(f"Авторизация: {mask_auth(creds['auth_header'])}")
    elif creds.get("extra"):
        lines.append("Авторизация: подписи api-* (набор в API_HEADERS_JSON)")
    if creds.get("extra"):
        names = ", ".join(sorted(k for k in creds["extra"] if k != "user-agent"))
        lines.append(f"Подписи: {names}")
    init_data = creds.get("init_data") or ""
    if init_data:
        lines.append("initData: найдена ✅ — нативный режим (бот сам подпишет "
                     "запрос и сам продлит ключ, HAR больше не нужен)")
    if creds["source"] == "har":
        if creds["timers_found"]:
            lines.append(f"Полей-таймеров в ответе: {creds['timers_found']}")
        else:
            lines.append("Тел ответов в файле нет — таймеры найдём при живом опросе API")
    updates = {
        "API_ENABLED": "true",
        "API_STATE_URL": creds["state_url"],
        # Пишем ВСЕГДА (в т.ч. пустыми), чтобы не оставались значения от старого файла
        "API_AUTH_HEADER": creds.get("auth_header", ""),
        "API_METHOD": (creds.get("method") or "GET").upper(),
        "API_BODY": creds.get("body", ""),
        "API_HEADERS_JSON": json.dumps(creds.get("extra") or {}, ensure_ascii=False),
        "FOMO_INIT_DATA": init_data,
    }
    env = update_env(updates, env_path)
    lines.append(f"Записано в {env} (API_ENABLED=true)")
    if do_test:
        if init_data:
            code, snippet = native_test(init_data)
            if code == 200:
                lines.append("Проверка (нативно: auth + таймеры): HTTP 200 ✅ — всё живое")
            elif code == 401:
                lines.append("Проверка: HTTP 401 ❌ — initData не принята; "
                             "запустите login_bot.bat (юзербот) или снимите HAR заново")
            elif code == 0:
                lines.append(f"Проверка не прошла: {snippet}")
            else:
                lines.append(f"Проверка: HTTP {code} (тело: {snippet[:80]!r})")
        else:
            code, snippet = test_endpoint(
                creds["state_url"], creds["auth_header"],
                method=creds.get("method", "GET"), body=creds.get("body", ""),
                extra=creds.get("extra") or {},
            )
            if code == 200:
                lines.append("Проверка: HTTP 200 ✅ — API отвечает")
            elif code in (401, 403):
                lines.append(f"Проверка: HTTP {code} ❌ — подписи мёртвы; "
                             "если в файле был /telegram/auth — я бы сам всё починил, "
                             "но initData не найдена: снимите HAR заново (игра должна "
                             "быть подключена) или настройте юзербот (login_bot.bat)")
            elif code is None:
                lines.append(f"Проверка не прошла: {snippet}")
            else:
                lines.append(f"Проверка: HTTP {code} (тело: {snippet[:80]!r})")
        if code != 200:
            return False, "\n".join(lines)
    return True, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Автонастройка автотрекинга из HAR/cURL")
    ap.add_argument("file", help=".har из DevTools или текстовый файл с Copy as cURL")
    ap.add_argument("--env", default=".env", help="путь к .env (по умолчанию ./.env)")
    ap.add_argument("--no-test", action="store_true", help="не делать контрольный запрос")
    args = ap.parse_args()
    try:
        ok, report = apply_file(args.file, args.env, do_test=not args.no_test)
    except FileNotFoundError:
        sys.exit(f"Файл не найден: {args.file}")
    print(report)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
