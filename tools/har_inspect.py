#!/usr/bin/env python3
"""har_inspect.py — помощник для автотрекинга (Вариант 2).

Вы экспортировали трафик мини-аппа из Telegram Desktop DevTools в файл .har
(как — см. README, «Автотрекинг, шаг 1»). Этот скрипт разберёт его и:

  1. Перечислит все XHR/fetch-запросы к API игры (метод, URL, статус, размер ответа).
  2. Отметит «кандидатов» — ответы, где в JSON есть поля, похожие на таймеры
     (finished_at / ends_at / remaining / time_left / deadline / upgrade_end …).
  3. Покажет найденные поля со значениями и где они лежат (JSON-путь).
  4. Проверит заголовки запросов на токен авторизации (Authorization, X-Api-Key,
     длинные JWT) — его же потом вставим в .env (API_AUTH_HEADER).
  5. Сохранит человекочитаемый отчёт в har_report.txt и «скелет» для api_poller.py.

Использование:
    python tools/har_inspect.py экспорт.har
    python tools/har_inspect.py экспорт.har --host api.example.com   # фильтр по домену
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Поля, типичные для «времени окончания» / «остатка времени»
TIME_KEY_RE = re.compile(
    r"(finish|end[sd]?_?at|complete[d]?_?(?:at|time|date)|deadline|expire[s]?_?(?:at)?|"
    r"upgrade_?(?:end|finish|complete)\w*|build_?(?:end|finish|complete)\w*|"
    r"remaining|time_?left|left_?time|cooldown|ready_?(?:at|time)?|unlock_?(?:at|time)?|"
    r"reset_?date|cap_?reset|"
    r"(?:end|ends|finish|complete|expire)s?_?(?:at|time)?_?(?:ms|millis|seconds?))",
    re.IGNORECASE,
)
AUTH_KEY_RE = re.compile(r"(authorization|api[-_]?key|x-auth|token|bearer)", re.IGNORECASE)
JWT_RE = re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}\b")
# «Говорящие» адреса: эндпоинты со такими словами почти наверняка про таймеры игры
# (…/timers, …/rooms/… — стройка, тренировка войск, исследования)
URL_PRIORITY_RE = re.compile(
    r"(timer|room|upgrade|build|train|research|queue|production)", re.I)
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def walk(node, path="$", hits=None):
    """Рекурсивно собрать все (json-путь, ключ, значение)."""
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}"
            hits.append((p, k, v))
            walk(v, p, hits)
    elif isinstance(node, list):
        for i, v in enumerate(node[:50]):  # глубже 50 элементов массива не ходим
            walk(v, f"{path}[{i}]", hits)
    return hits


def classify_time_value(v):
    """Похоже ли значение на время/длительность. Возвращает ('unix'|'ms'|'iso'|'delta'|'clock'|None)."""
    if isinstance(v, (int, float)):
        if 1_500_000_000 <= v <= 3_000_000_000:          # unix-секунды (2017..2065)
            return "unix"
        if 1_500_000_000_000 <= v <= 3_000_000_000_000:  # unix-миллисекунды
            return "ms"
        if 0 < v < 40 * 24 * 3600:                       # остаток в секундах
            return "delta"
        return None
    if isinstance(v, str):
        if ISO_RE.search(v):
            return "iso"
        if re.fullmatch(r"\d{1,3}(:\d{2}){1,2}", v.strip()):  # 22:24 / 1:28:10
            return "clock"
    return None


def looks_like_api(url, api_host=None):
    """Отфильтровать статику и служебные запросы Telegram: интересует только игра."""
    if api_host:
        return api_host.lower() in url.lower()
    bad = ("telegram.org", "t.me/", "telegram-browser", "cdn-tele", "core-cache",
           "web.telegram", ".js", ".css", ".png", ".jpg", ".woff", ".svg", ".mp3")
    return not any(b in url.lower() for b in bad)


def parse_har(har_path, api_host=None):
    """HAR -> список записей API (для программного использования).

    Каждая запись: {method, status, url, size, parsed, auth_name, auth_value,
    time_fields}. Логика фильтрации та же, что в analyze().
    """
    har = json.loads(Path(har_path).read_text(encoding="utf-8", errors="replace"))
    entries = har.get("log", {}).get("entries", [])
    rows = []
    for e in entries:
        req, resp = e.get("request", {}), e.get("response", {})
        url = req.get("url", "")
        method = req.get("method", "?").upper()
        status = resp.get("status", 0)
        if method == "OPTIONS" or not looks_like_api(url, api_host):
            continue
        body = (resp.get("content", {}) or {}).get("text") or ""
        parsed = None
        if body.lstrip()[:1] in ("{", "["):
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
        auth_name, auth_value = "", ""
        req_headers = {h.get("name", "").lower(): h.get("value", "")
                       for h in req.get("headers", [])}
        for h in req.get("headers", []):
            if AUTH_KEY_RE.search(h.get("name", "")) or JWT_RE.search(h.get("value", "")):
                auth_name, auth_value = h.get("name", ""), h.get("value", "")
                break
        post_data = (req.get("postData", {}) or {}).get("text") or ""
        time_fields = []
        if parsed is not None:
            for p, k, v in walk(parsed):
                kind = classify_time_value(v)
                if kind and TIME_KEY_RE.search(k):
                    time_fields.append((p, k, v, kind))
        rows.append({
            "method": method, "status": status, "url": url, "size": len(body),
            "parsed": parsed, "auth_name": auth_name, "auth_value": auth_value,
            "time_fields": time_fields, "post_data": post_data,
            "req_headers": req_headers,
        })
    return rows


def pick_best(rows):
    """Самый перспективный эндпоинт для .env.

    Приоритет: «говорящий» URL (timer — сильнее всего, затем room/upgrade/…),
    затем больше таймер-полей, затем наличие тела ответа, затем 200-ответ,
    затем с авторизацией, затем крупнее тело. При равном score побеждает
    ПОЗДНИЙ запрос — в логе он от самой свежей сессии (ранние подписи игры
    умирают при переподключении). Работает и для HAR без тел ответов
    (Copy all as HAR) — там решает адрес запроса. None, если строк нет.
    """
    def score(r):
        url = r["url"] or ""
        if re.search(r"timer", url, re.I):
            prio = 2
        elif URL_PRIORITY_RE.search(url):
            prio = 1
        else:
            prio = 0
        return (prio, len(r["time_fields"]), r["parsed"] is not None,
                r["status"] == 200, bool(r["auth_name"]), r["size"])
    return max(reversed(rows), key=score) if rows else None


def analyze(har_path, api_host=None):
    har = json.loads(Path(har_path).read_text(encoding="utf-8", errors="replace"))
    entries = har.get("log", {}).get("entries", [])
    print(f"Загружен HAR: {har_path}, записей: {len(entries)}\n")

    api_rows, candidates = [], []
    for e in entries:
        req, resp = e.get("request", {}), e.get("response", {})
        url = req.get("url", "")
        method = req.get("method", "?")
        status = resp.get("status", 0)
        if method.upper() == "OPTIONS" or not looks_like_api(url, api_host):
            continue
        body = (resp.get("content", {}) or {}).get("text") or ""
        parsed = None
        if body.lstrip()[:1] in ("{", "["):
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None

        # токен авторизации в заголовках
        auth = ""
        for h in req.get("headers", []):
            if AUTH_KEY_RE.search(h.get("name", "")) or JWT_RE.search(h.get("value", "")):
                val = h.get("value", "")
                auth = f"{h.get('name')}: {val[:60]}{'…' if len(val) > 60 else ''}"
                break

        time_fields = []
        if parsed is not None:
            for p, k, v in walk(parsed):
                kind = classify_time_value(v)
                if kind and TIME_KEY_RE.search(k):
                    time_fields.append((p, k, v, kind))

        size = len(body)
        api_rows.append((method, status, url, size, auth, parsed))
        if time_fields:
            candidates.append((method, status, url, time_fields, auth))

    # --- Отчёт в консоль ---
    print("=" * 100)
    print("1) ЗАПРОСЫ К API (кроме служебных):")
    for method, status, url, size, auth, _ in api_rows:
        print(f"  {method:<5} {status:<4} {size:>8}B  {url[:90]}")
        if auth:
            print(f"        └─ auth: {auth}")

    print("\n" + "=" * 100)
    if not candidates:
        print("2) КАНДИДАТЫ С ТАЙМЕРАМИ: не найдены 🤔")
        print("   Советы: откройте в игре экран с улучшениями/стройками и повторите")
        print("   экспорт HAR (закройте мини-апп, откройте снова, походите по экранам).")
    else:
        print("2) КАНДИДАТЫ — в ответах есть поля, похожие на таймеры:")
        for i, (method, status, url, fields, auth) in enumerate(candidates, 1):
            print(f"\n  [{i}] {method} {url}")
            if auth:
                print(f"      auth: {auth}")
            for p, k, v, kind in fields[:25]:
                print(f"      ⏱ {p} = {v!r}  ({kind})")

    # --- Отчёт в файл ---
    rep = Path("har_report.txt")
    with rep.open("w", encoding="utf-8") as f:
        f.write("Запросы к API:\n")
        for method, status, url, size, auth, _ in api_rows:
            f.write(f"  {method} {status} {size}B {url}\n")
            if auth:
                f.write(f"      auth: {auth}\n")
        f.write("\n\nКандидаты с таймерами:\n")
        for method, status, url, fields, auth in candidates:
            f.write(f"\n{method} {url}\n")
            if auth:
                f.write(f"  auth: {auth}\n")
            for p, k, v, kind in fields:
                f.write(f"  {p} = {v!r} ({kind})\n")
        # Полные JSON первых трёх кандидатов — по ним заполним extract_upgrades()
        cand_urls = [c[2] for c in candidates[:3]]
        for method, status, url, size, auth, parsed in api_rows:
            if url in cand_urls and parsed is not None:
                f.write(f"\n\nПолный JSON ответа для {url}:\n")
                f.write(json.dumps(parsed, ensure_ascii=False, indent=2)[:20000])
    print(f"\n📄 Полный отчёт: {rep.resolve()}")
    if candidates:
        print("\nСледующий шаг (README → «Автотрекинг, шаг 3»): пришлите файл har_report.txt —")
        print("по нему заполняется extract_upgrades() в api_poller.py, и автотрекинг готов.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Анализ HAR-экспорта мини-аппа (поиск таймеров)")
    ap.add_argument("har", help="файл .har из DevTools (Save all as HAR with content)")
    ap.add_argument("--host", default=None, help="домен API игры, напр. api.fomofighters.game")
    args = ap.parse_args()
    try:
        analyze(args.har, args.host)
    except FileNotFoundError:
        sys.exit(f"Файл не найден: {args.har}")
    except json.JSONDecodeError as e:
        sys.exit(f"Не удалось разобрать HAR (это точно файл .har?): {e}")
