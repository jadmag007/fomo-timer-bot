"""termux_notify.py — Termux-режим: напоминания таймеров в шторку Android.

ВКЛЮЧЕНИЕ: TERMUX_NOTIFY=true в .env (кнопка ⚙️ на странице таймеров или
правка файла руками). Тумблер читается ЖИВО из .env — рестарт не нужен.

ЧТО МЕНЯЕТСЯ (только это, всё остальное работает как раньше):
  * напоминания таймеров («✅ Готово», «🚩 пора отправлять войска»,
    «⏳ через минуту», сундуки) НЕ пишутся в Telegram — вместо этого бот
    вызывает termux-notification, и карточка появляется в шторке Android;
  * отложенные пуши юзербота (sched_push, «страховка» на серверах Telegram)
    не создаются вовсе: sched_push.available() в Termux-режиме всегда False —
    в чате с ботом не копятся отложенные сообщения;
  * страница таймеров, пауза, тихий режим групп и ночной режим — без изменений.

ТРЕБОВАНИЯ: приложение Termux:API (F-Droid, ставится ТАК ЖЕ, как основной
Termux — тот же источник) и пакет в самом Termux: pkg install termux-api.
Проверка руками: termux-notification --title "Fomo" --content "тест".

Честно про надёжность: шторка живёт на устройстве. Если Termux убит системой
или Termux:API не установлен — уведомление не уйдёт: недоставленный пуш
повторяется по обычному окну ретраев (как при сетевом сбое Telegram), а все
таймеры в любом случае видны на странице, включая «🎁 Ждёт забора».
"""
import asyncio
import html as _html
import logging
import re
import shutil

import config

log = logging.getLogger("termux")

# <b>, </b>, одиночные <br/> и любые другие теги — вычищаем перед шторкой
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_TITLE = 64          # заголовок карточки длиннее не нужен
_PROC_TIMEOUT = 15.0     # termux-notification обычно отвечает мгновенно

_warned_no_bin = False


def strip_html(text: str) -> str:
    """«✅ <b>Готово!</b>» -> «✅ Готово!». <br> — в перенос строки, сущности
    (&amp; и т.п.) раскрываются. Тексты пушей пишутся под Telegram-HTML."""
    t = (text or "")
    t = t.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    t = _TAG_RE.sub("", t)
    return _html.unescape(t).strip()


def binary_available() -> bool:
    """Установлен ли termux-api (бинарник termux-notification в PATH)."""
    return shutil.which("termux-notification") is not None


def enabled() -> bool:
    """Termux-режим включён? Живое чтение .env (без рестарта)."""
    return config.termux_notify_enabled()


async def send(text: str, notif_id: str = "fomo") -> bool:
    """Показать уведомление в шторке Android. True — команда отработала.

    False (нет бинарника / ошибка команды) = «не доставлено»: планировщик
    повторит пуш в ближайших тиках, как это уже делает для сетевых сбоев ТГ.
    Первая строка текста становится заголовком карточки, остальное — содержимым.
    notif_id группирует карточки одной группы таймеров (шторка не засоряется).
    """
    global _warned_no_bin
    plain = strip_html(text)
    if not plain:
        return True                      # пусто — показывать нечего
    if not binary_available():
        if not _warned_no_bin:
            log.warning("termux-notification не найден: поставьте приложение "
                        "Termux:API (F-Droid) и выполните 'pkg install termux-api'. "
                        "Пуши таймеров сейчас никуда не доставляются!")
            _warned_no_bin = True
        return False
    head, _, rest = plain.partition("\n")
    title = head.strip()[:_MAX_TITLE] or "Fomo Timer"
    content = rest.strip() or title
    try:
        proc = await asyncio.create_subprocess_exec(
            "termux-notification",
            "--title", title,
            "--content", content,
            "--id", str(notif_id or "fomo"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        if not _warned_no_bin:
            log.warning("termux-notification исчез из PATH — проверьте Termux:API")
            _warned_no_bin = True
        return False
    except Exception as e:
        log.warning("termux-notification не запущен: %s: %s", type(e).__name__, e)
        return False
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=_PROC_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        log.warning("termux-notification не ответил за %s с", int(_PROC_TIMEOUT))
        return False
    if proc.returncode != 0:
        log.warning("termux-notification вернул %s: %s", proc.returncode,
                    (err or b"").decode("utf-8", "replace")[:200])
        return False
    return True
