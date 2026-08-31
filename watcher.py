"""watcher.py — автоматическое обновление ключа по файлу fomo.txt.

Два пути попадания файла (оба без команд и без рестарта):

  1. token_updates/ — перетащить fomo.txt на update_token.bat, в папку или
     просто сохранённый HAR любого имени с расширением .har/.txt/.curl/.log/.json.
  2. Корень папки бота — просто положить fomo.txt рядом с bot.py. Подхватывается
     сам, когда юзербота нет (userbot.session отсутствует) — бот «съедает» файл,
     настраивается и перехватывает автотрекинг без ваших действий.

Что происходит с файлом дальше (автоматически):

  1. tools/har_apply.py извлекает initData (/telegram/auth) и подписи.
  2. .env обновляется, config.reload() подтягивает значения без рестарта.
  3. Контрольный цикл: auth + запрос таймеров со СВОЕЙ подписью — 200 или нет.
  4. В личку летит отчёт: «Токен обновлён ✅» либо что пошло не так.
  5. Найденные таймеры добавляются молча (режим Да/Нет — /вопросы).
  6. Файл переезжает в token_updates/done/ (там ваш игровой токен — не публиковать).
"""
import asyncio
import html
import json
import logging
import shutil
import time
from pathlib import Path

import api_poller
import config
import db
import pause_state
import userbot
from tools import har_apply

log = logging.getLogger("watcher")

ROOT = Path(__file__).resolve().parent
WATCH = ROOT / config.TOKEN_UPDATES_DIR
DONE = WATCH / "done"
SUPPORTED = {".har", ".txt", ".curl", ".log", ".json"}
SCAN_INTERVAL = 2.0  # сек между обходами папки
ROOT_STATE = Path("data") / "root_fomo_state.json"  # пометка «корневой файл уже обработан»


def owner_chat_id():
    if config.API_OWNER_TG_ID:
        return config.API_OWNER_TG_ID
    row = db.first_user()
    return row["tg_id"] if row else None


def process_file(path: Path):
    """Синхронная часть (в отдельном потоке): разобрать файл, обновить .env,
    собрать найденные таймеры.

    -> (ok, текст отчёта, находки)
    """
    try:
        ok, report = har_apply.apply_file(path, env_path=ROOT / ".env", do_test=True)
    except Exception as e:
        return False, f"Не удалось разобрать файл {path.name}: {e}", []
    if ok:
        config.reload()
        api_poller.reset_state()
    try:
        found = api_poller.extract_from_har(path)
    except Exception:
        log.exception("Не удалось извлечь таймеры из файла")
        found = []
    if config.API_ASK_BEFORE_ADD:
        try:
            entries = api_poller.build_proposals(found)
        except Exception:
            log.exception("Не удалось собрать предложения таймеров")
            entries = []
    else:
        entries = found  # авто-режим: ставим всё без вопросов (дедуп в maybe_add)
    if ok and not entries:
        report += ("\nАктивных таймеров в файле не увидел — подхвачу те, "
                   "которые найдёт первый опрос API.")
    return ok, report, entries


# ---------- Автоподхват fomo.txt из корня папки бота ----------

def root_fomo_files():
    """fomo*.txt / fomo*.har / … в корне проекта (кроме служебных папок)."""
    out = []
    try:
        for p in ROOT.iterdir():
            if p.is_file() and p.name.lower().startswith("fomo") \
                    and p.suffix.lower() in SUPPORTED:
                out.append(p)
    except OSError:
        pass
    return sorted(out)


def _file_sig(p: Path):
    """Подпись файла (имя+размер+mtime) — чтобы обрабатывать только новые."""
    try:
        st = p.stat()
        return f"{p.name}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return None


def _load_root_state():
    try:
        return json.loads(ROOT_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_root_state(sig):
    try:
        ROOT_STATE.parent.mkdir(parents=True, exist_ok=True)
        ROOT_STATE.write_text(json.dumps({"sig": sig, "ts": time.time()}), encoding="utf-8")
    except OSError:
        log.exception("Не удалось сохранить root_fomo_state.json")


def take_root_file():
    """Если в корне лежит НОВЫЙ fomo-файл и юзербота нет — перенести его в
    token_updates/ (обработается стандартным путём в этом же цикле).

    Файл с той же подписью (имя/размер/время), что уже обрабатывали, не трогаем.
    С юзерботом корневой файл тоже не нужен — там ключи обновляются сами.
    """
    files = root_fomo_files()
    if not files:
        return False
    src = files[0]
    sig = _file_sig(src)
    state = _load_root_state()
    if sig and state.get("sig") == sig:
        return False  # этот файл уже видели
    _save_root_state(sig)
    if userbot.session_ready():
        log.info("Watcher: fomo-файл в корне не нужен — работает юзербот "
                 "(ключи обновляются сами): %s", src.name)
        return False
    try:
        WATCH.mkdir(exist_ok=True)
        dst = _unique_dst(WATCH / src.name)
        shutil.move(str(src), str(dst))
        log.info("Watcher: fomo-файл из корня перенесён в token_updates/: %s", dst.name)
        return True
    except OSError:
        log.exception("Не удалось перенести %s в token_updates/", src.name)
        return False


def _unique_dst(dst: Path) -> Path:
    if not dst.exists():
        return dst
    return dst.with_name(f"{dst.stem}_{int(time.time())}{dst.suffix}")


def cleanup_done(keep=20):
    """token_updates/done/ не должен расти вечно: каждый файл — живой игровой
    токен. Храним последние keep обработанных, старше — удаляем."""
    try:
        files = sorted(DONE.glob("*"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError:
        return
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


async def loop(bot):
    """Фоновая задача бота: раз в SCAN_INTERVAL секунд обходим папку и корень."""
    WATCH.mkdir(exist_ok=True)
    DONE.mkdir(exist_ok=True)
    log.info("Watcher следит за %s (+ корень проекта)", WATCH)
    while True:
        try:
            take_root_file()
            cleanup_done()
            for f in sorted(WATCH.iterdir()):
                if not f.is_file() or f.suffix.lower() not in SUPPORTED:
                    continue
                log.info("Watcher: обрабатываю %s", f.name)
                ok, report, entries = await asyncio.to_thread(process_file, f)
                try:
                    shutil.move(str(f), str(_unique_dst(DONE / f.name)))
                except Exception:
                    log.exception("Не удалось переместить %s в done/", f.name)
                chat = owner_chat_id()
                if chat:
                    head = ("🔑 <b>Токен обновлён ✅</b>" if ok
                            else "⚠️ <b>Файл обработан, но что-то не так</b>")
                    if entries and not config.API_ASK_BEFORE_ADD:
                        n = sum(api_poller.maybe_add(up) for up in entries)
                        if n:
                            report += (f"\n\n⏱ Из файла автоматически добавлено "
                                       f"таймеров: {n} (список: /таймеры)")
                    tail = ("\n\nФайл переехал в <code>token_updates/done/</code>. "
                            "Внутри ваш игровой токен — наружу не выкладывайте.")
                    try:
                        # report — сырой текст ответа API/ошибок: экранируем,
                        # иначе случайный '<' роняет отправку и отчёт теряется
                        await bot.send_message(
                            chat, f"{head}\n<pre>{html.escape(report)}</pre>{tail}")
                    except Exception:
                        log.exception("Не удалось отправить отчёт в Telegram")
                    if entries and config.API_ASK_BEFORE_ADD:
                        # Гейт паузы (как у propose_new в api_poller): на паузе
                        # предложение не уходит — таймеры предложатся после
                        # снятия следующим опросом, ничего не теряется.
                        if pause_state.is_paused():
                            log.info("Watcher: бот на паузе — предложение «Да/Нет» "
                                     "не отправляю (%s шт.), спросит после снятия",
                                     len(entries))
                        else:
                            gid = api_poller.register_pending(chat, entries)
                            try:
                                await bot.send_message(
                                    chat,
                                    api_poller.proposal_text(entries),
                                    reply_markup=api_poller.proposal_kb(gid),
                                )
                            except Exception:
                                log.exception("Не удалось отправить предложение таймеров")
        except Exception:
            log.exception("Ошибка в цикле watcher")
        await asyncio.sleep(SCAN_INTERVAL)
