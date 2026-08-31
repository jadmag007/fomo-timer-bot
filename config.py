"""Конфигурация бота. Все настройки берутся из .env (см. .env.example)."""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

# .env живёт рядом с config.py (в корне папки бота) — путь детерминированный,
# не зависит от текущего каталога запуска.
_ENV_PATH = str(Path(__file__).resolve().parent / ".env")

load_dotenv(_ENV_PATH)

# --- Версия. ПРАВИЛО: бампается при КАЖДОМ изменении кода/документации ---
# 0.1.0.2 — локальный режим мини-аппа: страница работает в браузере на ПК с
# ботом без Telegram-подписи (спасение при error 1033, когда сеть режет туннель).
# 0.1.0.3 — Termux/Android: install.sh и start.sh понимают Termux (wake-lock,
# готовый aiohttp, без venv), tunnel.py качает arm64-сборку cloudflared,
# гайд TERMUX.md.
# 0.1.0.4 — фикс запуска после git clone на андроиде: git с Windows не хранит
# exec-бит -> «./install.sh: Permission denied». Везде bash install.sh /
# bash start.sh, install.sh сам чинит права .sh, в TERMUX.md вшит адрес
# репозитория.
# 0.1.0.5 — фикс установки зависимостей на Termux: в репо Termux НЕТ готовых
# aiohttp/pydantic, pydantic-core (Rust) не собирается без тулчейна (rustup
# не умеет android-таргет) -> install.sh ставит rust+binutils из репо Termux
# (умеет aarch64-linux-android), aiohttp в pure-python (AIOHTTP_NO_EXTENSIONS).
# 0.1.0.6 — Termux: pydantic-core ставится ГОТОВЫМ колесом android_24_arm64_v8a
# из зеркала TUR PyPI (--only-binary :all:) — без Rust и 10-25-минутной сборки,
# которые у 0.1.0.5 упали на телефоне (rustup не умеет android-таргет).
# Запасной путь: rust+binutils из репо Termux + maturin оттуда же +
# --no-build-isolation. В TERMUX.md — пошаговый перенос .env+data через свой
# git-репозиторий (сделать репо приватным, PAT, git add -f, git pull).
# 0.1.0.7 — Termux, найден главный виновник повторных падений: колесо ядра
# 2.41.5 вставало, но следующий шаг (pip install -r requirements.txt) ставил
# СВЕЖИЙ pydantic 2.13.x, а ему нужно ядро 2.46.x — готовой сборки под андроид
# нет, и pip снова падал в сборку Rust. Теперь ПАРА pydantic 2.12.5 +
# pydantic-core 2.41.5 (единственное ядро в TUR для cp313/cp314) ставится
# заодно и ДО aiogram; плюс pure-python флаги yarl/multidict/frozenlist/
# propcache, пропуск шага если pydantic уже стоит, баннер версии установщика
# и внятный совет (git pull / VPN) с exit 1 вместо тихого продолжения.
# 0.1.0.8 — install.sh САМООБНОВЛЯЕТСЯ: перед установкой уводит локальные
# правки служебных файлов в git stash (возврат: git stash pop), тянет свежий
# код (git pull --ff-only) и перезапускает себя свежей копией (exec). Повод:
# git pull у пользователя падал с «Your local changes ... would be overwritten
# by merge» — телефон молча запускал СТАРЫЙ установщик (без баннера это было
# не видно), и фиксы 0.1.0.5–0.1.0.7 до него не доезжали. Плюс мягкое
# сообщение вместо ошибки при запрете pip обновлять самого себя в Termux.
# 0.1.1.0 — МИНИ-АПП В TELEGRAM УБРАН ДО ЛУЧШИХ ВРЕМЁН (вернёмся — код
# останется в истории git): удалены туннель cloudflared (tunnel.py), кнопки
# WebApp/меню, валидация initData и публичные адреса (WEBAPP_PUBLIC_URL,
# WEBAPP_TUNNEL_PROTOCOL, WEBAPP_LOCAL_DEBUG). Оставлена и стала основной
# ЛОКАЛЬНАЯ СТРАНИЦА ТАЙМЕРОВ в обычном браузере: http://127.0.0.1:8080
# на том устройстве, где запущен бот (ПК или телефон в Termux). Пуши,
# автотрекинг, пауза и тихий режим не тронуты.
# 0.1.1.0 (доп.) — ХОТФИКС входа юзербота: InputBotAppShortName вызывался с
# несуществующими аргументами id=/access_hash= и гарантированно падал
# TypeError «got an unexpected keyword argument 'id'» на ЛЮБОЙ версии
# telethon — после успешного ввода кода сессия удалялась и «вход» не
# проходил никогда. Теперь вызов совместим со всеми версиями telethon
# (bot_id в новых, только short_name в старых — userbot.build_short_name_app),
# а падение проверки мини-аппа больше НЕ удаляет валидную сессию: бот сам
# добудет ключ этой сессией (api_poller самовключение + refresh_init_data).
# 0.1.1.1 — вход юзербота ставит владельца автотрекинга САМ: юзербот — это
# ваш собственный аккаунт, поэтому его Telegram ID сохраняется в .env
# (API_OWNER_TG_ID) и строка пользователя создаётся в базе: пушам и
# автотаймерам не нужен ни /start, ни ручное заполнение .env. Предупреждение
# «некому ставить таймер» больше не спамит каждый цикл (раз в 10 минут).
# API_OWNER_TG_ID стал приоритетом, а не тупиком: если строки в базе нет,
# владелец берётся как раньше — первый /start. start.sh (Termux/Linux) при
# запуске сам тихо подтягивает обновления из GitHub (fetch + stash правок +
# pull --ff-only, личные файлы .env/data/ не трогаются) и печатает версию,
# как start.bat на Windows.
# 0.1.1.2 — ОПРОСЫ БОЛЬШЕ НЕ МЕШАЮТ ИГРАТЬ: опрос игры шёл каждые 45 секунд,
# и каждая переавторизация бота выбивала сессию игрока — приходилось постоянно
# перелогиниваться в игре. Теперь опрос при старте и раз в 5 минут
# (API_POLL_INTERVAL=300, пол 60 с), превентивная реанимация ключа раз в 6 ч
# УБРАНА: auth только когда ключа нет или сервер ответил 401; при каждой
# реанимации бот предупреждает в чат («если ты в игре — могла попросить
# перезайти»), не чаще раза в 30 минут. ОТСРОЧЕННЫЕ ПУШИ ЧЕРЕЗ СЕРВЕРА
# TELEGRAM: бот планирует «⏰ Готово: …» на момент финиша таймера через
# юзербота (MTProto schedule) В ЧАТ С БОТОМ — сообщение придёт минута в минуту,
# даже если бот выключен. Бот доставил свой пуш — запланированный дубль
# снимается; на паузе всё запланированное снимается, после возобновления
# распланируется заново. Честно про уведомления: юзербот — ваш же аккаунт,
# отложка пишется «от вас» и приходит БЕЗ звука (Telegram не уведомляет о
# своих сообщениях); уведомление приносит сам бот (пуши онлайн / догоняющий).
# Чужие отложенные сообщения в чате с ботом не трогаются
# (только с нашим маркером «⏰ Готово: »). Новый модуль sched_push.py,
# флаг USERBOT_SCHEDULE (по умолчанию вкл, требует userbot.session),
# имя бота пишется в .env само (BOT_USERNAME).
# 0.1.1.3 — МОЗГ ОПРОСНИКА: СКРЫТНОСТЬ БЕЗ ПОТЕРИ АВТОМАТИЗАЦИИ. Опросник
# живёт как игрок: АКТИВНЫЙ (опрос раз в 300 с ±40% случайности — двух
# одинаковых пауз подряд не бывает) -> после 3 опросов без нового таймера
# ТИХИЙ (автопульс раз в случайные 30–55 мин) -> НОЧЬ (окно по часовой зоне,
# по умолчанию 00:00–08:00): полный штиль игровых запросов либо 1–2
# ночных микротика (включается в боте). Утро — не ровно в 08:00, а со
# случайным сдвигом. Пробуждение БЕЗ тапков: любое сообщение боту (через
# случайные 3–8 мин, чтобы не поймать переавторизацию посреди игры),
# контрольный опрос через 30–120 с после каждого «⏰ Готово» (игрок пошёл
# собирать — мы как раз рядом), автопульс сам находит новые таймеры.
# Свежая реавторизация держит паузу 10 минут — игре даём успокоиться.
# Ночное окно/микротики/тишина редактируются в боте (меню /апи → «🌙 Ночь»)
# и на локальной странице, хранятся в базе. Кнопка/команда /app убрана.
# Кнопка мини-аппа у поля ввода (чат-меню), оставшаяся от старых версий
# НА СЕРВЕРАХ TELEGRAM, сбрасывается при каждом старте (MenuButtonDefault).
# 0.1.1.4 — ПОЧИНЕН github_push.sh (публикация с телефона): старая версия
# распаковывала zip КАК ЕСТЬ в папку бота, а zip содержит обёртку
# fomo-timer-bot/ — файлы ложились во ВЛОЖЕННУЮ папку (в коммит уезжал
# мусор вида fomo-timer-bot/bot.py). Теперь: распаковка во ВРЕМЕННУЮ
# папку + автоматический стрип обёртки (как у github_push.bat на ПК);
# устаревшие вложенные копии и мусор (*.part, zip) удаляются/вынимаются
# из git САМИ. Добавлено: git init при отсутствии репо с ПОДХВАТОМ истории
# GitHub (релиз = один чистый коммит поверх, без force), git identity
# выводится из адреса репо автоматически (ник@users.noreply.github.com —
# ошибка «Please tell me who you are» больше не встретится), ПУШ С
# РЕТРАЯМИ (4 попытки, HTTP/1.1 + postBuffer — мобильные сети рвут TLS
# к GitHub, «unexpected eof while reading» лечится повтором), GITHUB_TOKEN
# из .env идёт через credential helper (не светится в URL/логах), честные
# сообщения («N коммитов ещё НЕ на GitHub» вместо вравшего «already
# pushed»), понятные подсказки при отказе авторизации/сети.
# 0.1.1.6 — тихий старт: фоновые задачи больше не убиваются сборщиком
# мусора. asyncio держит задачи только слабо — при запуске бота GC уничтожил
# 5 из 6 задач отложенных пушей прямо во время подключения к Telegram
# («Task was destroyed but it is pending», «coroutine ignored
# GeneratorExit», в придачу sqlite «Cannot operate on a closed database»),
# и часть отложенных пушей не планировалась. Теперь все фоновые задачи
# держатся под сильными ссылками (sched_push.spawn — им же пользуются
# обработчики кнопок), а доступ к юзербот-сессии сериализован замком:
# один telethon-клиент за раз вместо шести параллельных на одном
# userbot.session (блокировок sqlite и гонок больше нет).
# Продолжение 0.1.1.6: (1) старт БОЛЬШЕ НЕ СПРАШИВАЕТ логин/пароль GitHub —
# автообновление в start.sh само берёт GITHUB_TOKEN из .env (как
# github_push.sh) и работает с GIT_TERMINAL_PROMPT=0: git физически не может
# ничего спросить; нет сети или токена — молча запускается локальная версия
# (боту GitHub не нужен, а пароль аккаунта git всё равно не принимает —
# только PAT); (2) тихая ОСТАНОВКА: bot.py сам отменяет фоновые задачи и
# дожидается их до закрытия цикла (sched_push.shutdown) — telethon-клиенты
# закрываются штатно, userbot.session не остаётся открытой, стоп-лог чистый.
# 0.1.1.7 — вебморда по замечаниям: (1) настройки ночного режима переехали
# ВНИЗ страницы (были над таймерами — дико неудобно, каждый вход в
# настройки прокручивал таймеры прочь); (2) кнопка «🎮 Игра» теперь
# настоящая ссылка <a target=_blank> вместо button+window.open — попап-
# блокировщики и встроенные браузеры молча гасили window.open, кнопка
# выглядела мёртвой; (3) сортировка групп на странице: сначала ТРЕНИРОВКА
# ВОЙСК, потом СТРОЙКА, дальше как раньше (порядок BUCKETS для пушей и
# меню бота не тронут — сортировка только на экране).
# 0.1.1.5 — github_push.sh: ПОБЕДА над кучей дублей в Download. Браузер
# при повторном скачивании называет архивы «fomo-timer-bot (1).zip»,
# «(2)»… — а скрипт искал только плоское имя fomo-timer-bot.zip и мог
# установить САМЫЙ СТАРЫЙ архив из пяти (у пользователя лежал в т.ч.
# 25-КБ zip двухнедельной давности — вот откуда мусор в телефонном
# коммите). Теперь скрипт сравнивает ВСЕ fomo-timer-bot*.zip по дате
# изменения и берёт самый свежий. После успешной установки ВСЕ
# скачанные копии (и плоская, и нумерованные) удаляются из Download —
# куча не растёт. Повторный запуск БЕЗ нового zip не падает, а просто
# дожимает незапушенные коммиты (нужно после сетевого сбоя), и только
# если в папке совсем нет установленного бота — просит скачать архив.
# 0.1.1.8 — сундуки на странице таймеров. «Готов к забору» (сундук аутпоста,
# клановые сундуки, награды аванпостов) теперь ЛИПКИЙ: пуш приходит как раньше,
# но карточка не исчезает через секунду, а остаётся на странице («🎁 Ждёт
# забора») до тех пор, пока игра не покажет, что награда забрана (тогда
# карточка снимается сама). Раньше мгновенные таймеры закрывались сразу после
# пуша — и в морде их не было видно вовсе (жалоба: «уведомление приходит,
# а таймера нет»). Напоминание в ТГ при этом не дублируется.
# 0.1.1.9 — Termux-режим уведомлений и редактор .env на странице таймеров.
# TERMUX_NOTIFY=true в .env: напоминания таймеров («✅ Готово», «🚩 осада»,
# «⏳ минута», сундуки) НЕ пишутся в Telegram — вместо этого бот вызывает
# termux-notification, и карточка появляется в шторке уведомлений Android.
# Отложенные пуши юзербота (страховка «⏰ Готово: …» на серверах Telegram)
# в этом режиме не создаются вовсе — в чате не копится лишнее. Тумблер
# читается ЖИВО из .env: включается кнопкой ⚙️ на странице таймеров без
# рестарта. Там же — редактор локального .env из браузера (сохранение с
# бэкапом .env.bak; большинство настроек применяется сразу, reload_from_env).
# Требуется приложение Termux:API (F-Droid) + pkg install termux-api; без них
# бот работает как раньше, недоставленные пуши повторяются по окну ретраев.
APP_VERSION = "0.1.1.9"

# --- Основное ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Moscow").strip()
DB_PATH = os.getenv("DB_PATH", "data/fomo_timers.db").strip()
# @username самого бота — ЗАПОЛНЯЕТСЯ САМО при старте (bot.py -> set_bot_username).
# Нужно sched_push: отложенные пуши планируются в чат с ботом.
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()

# --- Автотрекинг через API игры (заполняется само, руками — только флаги) ---
API_ENABLED = os.getenv("API_ENABLED", "false").strip().lower() == "true"
# Полный адрес(а) эндпоинта состояния (можно несколько — через запятую)
API_STATE_URL = os.getenv("API_STATE_URL", "").strip()
API_AUTH_HEADER = os.getenv("API_AUTH_HEADER", "").strip()  # напр.: Authorization: Bearer eyJhbGci...
# Метод и тело запроса (для POST-эндпоинтов со подписями — как у Fomo Fighters)
API_METHOD = (os.getenv("API_METHOD", "GET").strip().upper() or "GET")
API_BODY = os.getenv("API_BODY", "").strip()
# Дополнительные заголовки JSON-словарём (подписи api-* и т.п.), напр.:
#   {"api-key": "…", "api-hash": "…", "api-time": "…", "api-version": "…"}
API_HEADERS_JSON = os.getenv("API_HEADERS_JSON", "").strip()
# Как часто опрашивать игру, секунд. Раньше было 45 — каждая переавторизация
# бота выбивала сессию игрока, и приходилось постоянно перелогиниваться в игре.
# Теперь 300 (5 минут): опрос при старте бота, дальше раз в 5 минут — игре
# ничего не мешает. Меньше 60 бот всё равно не возьмёт (защита от спама).
API_POLL_INTERVAL = int(os.getenv("API_POLL_INTERVAL", "300"))
# --- Мозг опросника: рандом везде, автопульс, ночной сон (см. BRAIN.md) ---
# Разброс активного интервала: пауза = база * (1 ± POLL_JITTER), каждый раз
# новая — идеальная периодичность (главная сигнатура бота) исчезает.
POLL_JITTER = float(os.getenv("POLL_JITTER", "0.4") or 0.4)
# Автопульс в тихом режиме (3 опроса без нового таймера): редкая проверка
# через случайный интервал из вилки [POLL_PULSE_MIN, POLL_PULSE_MAX].
# Пульс находит новые таймеры сам — игроку ничего нажимать не нужно.
# Совместимость: заданный QUIET_HEARTBEAT_SEC>0 превращается в вилку из него.
_qhs_raw = os.getenv("QUIET_HEARTBEAT_SEC", "").strip()
_qhs = int(_qhs_raw or 0) if _qhs_raw.lstrip("-").isdigit() else 0
POLL_PULSE_MIN = int(os.getenv("POLL_PULSE_MIN", "") or (_qhs if _qhs > 0 else 1800))
POLL_PULSE_MAX = int(os.getenv("POLL_PULSE_MAX", "") or (_qhs if _qhs > 0 else 3300))
# НОЧНОЙ РЕЖИМ (люди ночью спят): в окне ночи игровые запросы прекращаются.
# Часовой пояс ночи: пусто = пояс владельца из бота (/tz), иначе IANA-имя.
BOT_TZ = os.getenv("BOT_TZ", "").strip()
NIGHT_START = os.getenv("NIGHT_START", "00:00").strip()
NIGHT_END = os.getenv("NIGHT_END", "08:00").strip()
# Тишина ночью: true — ноль запросов всю ночь; false — ночью живёт автопульс.
NIGHT_SILENT = os.getenv("NIGHT_SILENT", "true").strip().lower() == "true"
# Ночные микротики: 1–2 случайные проверки за ночь (на случай ночной игры).
# Включается кнопкой в боте; NIGHT_SILENT=true при микротиках не мешает.
NIGHT_MICROTICKS = os.getenv("NIGHT_MICROTICKS", "false").strip().lower() == "true"
# Выход из ночи не ровно в NIGHT_END (сигнатура), а со случайным сдвигом до
# NIGHT_WAKE_JITTER секунд (15 минут).
NIGHT_WAKE_JITTER = int(os.getenv("NIGHT_WAKE_JITTER", "900") or 900)
# Пробуждение по сообщению владельца: через случайные WAKE_DELAY 3–8 минут.
# НЕ 5–30 с: игрок часто пишет боту прямо из игры — ранний опрос мог
# поймать переавторизацию и выбить сессию.
WAKE_DELAY_MIN = int(os.getenv("WAKE_DELAY_MIN", "180") or 180)
WAKE_DELAY_MAX = int(os.getenv("WAKE_DELAY_MAX", "480") or 480)
# «Игрок занят»: если владелец писал боту в последние OWNER_BUSY_WINDOW сек,
# плановые опросы откладываются (скорее всего он прямо сейчас в игре).
OWNER_BUSY_WINDOW = int(os.getenv("OWNER_BUSY_WINDOW", "180") or 180)
# После реанимации ключа не трогаем игру REAUTH_COOLDOWN секунд.
REAUTH_COOLDOWN = int(os.getenv("REAUTH_COOLDOWN", "600") or 600)
# Контрольный опрос после доставленного «⏰ Готово»: игрок идёт собирать —
# случайные 30–120 с, наша активность совпадает с его.
CONTROL_POLL_MIN = int(os.getenv("CONTROL_POLL_MIN", "30") or 30)
CONTROL_POLL_MAX = int(os.getenv("CONTROL_POLL_MAX", "120") or 120)
QUIET_HEARTBEAT_SEC = int(os.getenv("QUIET_HEARTBEAT_SEC", "0") or 0)
# Ваш tg_id для автотрекинга (узнать: @userinfobot). Пусто — берётся первый /start боту.
API_OWNER_TG_ID = int(os.getenv("API_OWNER_TG_ID", "0") or 0)
# Спрашивать «Да/Нет» перед добавлением найденных таймеров. По умолчанию НЕТ —
# ставим молча; переключить: команда /вопросы или кнопка на экране /апи
API_ASK_BEFORE_ADD = os.getenv("API_ASK_BEFORE_ADD", "false").strip().lower() == "true"
# Трассировка сырых ответов API в data/trace.log (поиск новых типов таймеров).
# Переключается в боте: /трассировка, файл: /трейслог
API_TRACE = os.getenv("API_TRACE", "false").strip().lower() == "true"
# Папка, куда пользователь кидает .har / fomo.txt — бот сам разберёт и обновится
TOKEN_UPDATES_DIR = "token_updates"

# --- Нативный режим Fomo Fighters (бот сам подписывает и сам чинит ключ) ---
# initData мини-аппа (urlencoded, из /telegram/auth в fomo.txt или от юзербота).
# Есть это значение — подписи из HAR больше не нужны: всё считается само.
FOMO_INIT_DATA = os.getenv("FOMO_INIT_DATA", "").strip()
FOMO_API_BASE = os.getenv("FOMO_API_BASE", "https://api.fomofighters.xyz").strip().rstrip("/")
FOMO_GAME_BOT = os.getenv("FOMO_GAME_BOT", "fomo_fighters_bot").strip()
FOMO_APP_NAME = os.getenv("FOMO_APP_NAME", "game").strip()
FOMO_LANG = os.getenv("FOMO_LANG", "ru").strip()
FOMO_WEB_ORIGIN = os.getenv("FOMO_WEB_ORIGIN", "https://game.fomofighters.xyz").strip().rstrip("/")
# Превентивная реанимация ключа (auth), секунд — С 0.1.1.2 НЕ ИСПОЛЬЗУЕТСЯ:
# auth теперь только по необходимости (нет ключа / ответ 401), потому что
# каждая переавторизация могла выкидывать игрока из игры. Ключ оставлен для
# совместимости старых .env.
FOMO_REAUTH_INTERVAL = int(os.getenv("FOMO_REAUTH_INTERVAL", "21600") or 21600)
# Как часто опрашивать /user/data/all (клановые сундуки, награды аванпостов),
# секунд. Лёгкий /user/data/timers ходит по API_POLL_INTERVAL.
FOMO_ALL_INTERVAL = int(os.getenv("FOMO_ALL_INTERVAL", "300") or 300)
# За сколько секунд до конца ОСАДЫ АВАНПОСТА прислать отдельное предупреждение
# («успейте отправить войска»). По умолчанию — за час.
SIEGE_PREWARN_SEC = int(os.getenv("SIEGE_PREWARN_SEC", "3600") or 3600)

# --- Страница таймеров в браузере (локальная, без Telegram-обвязки) ---
# Все таймеры на одном экране: живые отсчёты, тихий режим по группам, отмена,
# кнопка «Обновить». Открывается в ОБЫЧНОМ браузере на том устройстве, где
# запущен бот: http://127.0.0.1:PORT (на ПК — браузер ПК, на телефоне в
# Termux — браузер телефона). Сервер слушает только 127.0.0.1, наружу ничем
# не торчит; уведомления в чат работают как раньше и от страницы не зависят.
WEBAPP_ENABLED = os.getenv("WEBAPP_ENABLED", "true").strip().lower() == "true"
# Локальный порт веб-сервера (слушает только 127.0.0.1). Если занят — берёт
# следующий свободный.
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080") or 8080)

# --- Юзербот (свежая initData автоматически, логин один раз через login_bot.bat) ---
# По умолчанию — общедоступная пара Telegram Desktop; можно вписать свою из
# my.telegram.org -> API development tools
USERBOT_API_ID = int(os.getenv("USERBOT_API_ID", "6") or 6)
USERBOT_API_HASH = os.getenv("USERBOT_API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
USERBOT_SESSION_PATH = os.getenv("USERBOT_SESSION_PATH", "userbot.session").strip()
# Отложенные пуши через серверы Telegram (MTProto schedule юзерботом
# В ЧАТ С БОТОМ): доставятся в срок даже при выключенном боте. Требует
# userbot.session (login_bot.bat); нет сессии — тихо не используется.
USERBOT_SCHEDULE = os.getenv("USERBOT_SCHEDULE", "true").strip().lower() == "true"

# --- Поведение напоминаний ---
# T-1мин выключен выбором «Только T-0»; при желании включите в .env (WARN_ENABLED=true)
WARN_ENABLED = os.getenv("WARN_ENABLED", "false").strip().lower() == "true"
WARN_BEFORE_SEC = 60
WARN_MIN_DURATION = 180

# --- Ограничения таймеров ---
MAX_TIMER_SEC = 10 * 24 * 3600   # 10 суток сверху
MIN_TIMER_SEC = 10               # снизу

# --- Кнопки быстрого таймера (секунды) ---
QUICK_PRESETS = [300, 900, 1800, 2700, 3600, 7200, 14400, 28800, 43200, 86400]


# ---------- Работа с .env ----------

def env_get(key, default="", env_path=None) -> str:
    """Прочитать значение ключа прямо из файла .env, минуя память процесса.

    Нужен, когда .env обновляется извне (login_userbot.py сохранил личные
    api-ключи), а работающий процесс ещё не перечитывал конфиг.
    Путь по умолчанию — .env РЯДОМ С config.py (не зависит от CWD: раньше
    запуск не из папки проекта молча читал чужой/несуществующий файл).
    """
    try:
        p = Path(env_path) if env_path else Path(_ENV_PATH)
        if not p.exists():
            return default
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    except Exception:
        pass
    return default


def env_file_path() -> Path:
    """Путь к файлу .env бота (для редактора настроек на странице таймеров)."""
    return Path(_ENV_PATH)


def termux_notify_enabled(env_path=None) -> bool:
    """Termux-режим уведомлений (TERMUX_NOTIFY=true): напоминания таймеров
    идут в шторку Android (termux-notification) вместо Telegram.

    Читается ЖИВО из .env при каждом вызове — тумблер на странице таймеров
    (кнопка ⚙️) действует без рестарта бота. По умолчанию выключен.
    """
    raw = env_get("TERMUX_NOTIFY", os.getenv("TERMUX_NOTIFY", "false"),
                  env_path=env_path)
    return raw.strip().lower() == "true"


def reload_from_env() -> bool:
    """Перечитать .env и обновить СКАЛЯРНЫЕ настройки в памяти процесса.

    Вызывается после сохранения .env со страницы таймеров (кнопка ⚙️):
    большинство тумблеров и порогов начинает работать без рестарта.
    Намеренно НЕ трогает то, что связано с живыми объектами: BOT_TOKEN
    (сессия бота уже создана), WEBAPP_PORT (порт уже занят), DB_PATH
    (соединение открыто), USERBOT_API_* (клиент юзербота строится на лету),
    FOMO_INIT_DATA (обновляется своим циклом). Возвращает True при успехе.
    """
    try:
        load_dotenv(_ENV_PATH, override=True)
    except Exception:
        return False
    g = globals()

    def _s(name, dflt=""):
        g[name] = os.getenv(name, dflt).strip()

    def _b(name, dflt="false"):
        g[name] = os.getenv(name, dflt).strip().lower() == "true"

    def _i(name, dflt):
        try:
            g[name] = int(os.getenv(name, str(dflt)) or dflt)
        except (TypeError, ValueError):
            pass

    def _f(name, dflt):
        try:
            g[name] = float(os.getenv(name, str(dflt)) or dflt)
        except (TypeError, ValueError):
            pass

    _s("DEFAULT_TZ")
    _s("BOT_TZ")
    _b("API_ENABLED")
    _s("API_STATE_URL")
    _s("API_AUTH_HEADER")
    _s("API_METHOD", "GET")
    g["API_METHOD"] = (g["API_METHOD"] or "GET").upper()
    _s("API_BODY")
    _s("API_HEADERS_JSON")
    _i("API_POLL_INTERVAL", 300)
    _f("POLL_JITTER", 0.4)
    _i("POLL_PULSE_MIN", 1800)
    _i("POLL_PULSE_MAX", 3300)
    _s("NIGHT_START", "00:00")
    _s("NIGHT_END", "08:00")
    _b("NIGHT_SILENT", "true")
    _b("NIGHT_MICROTICKS")
    _i("NIGHT_WAKE_JITTER", 900)
    _i("WAKE_DELAY_MIN", 180)
    _i("WAKE_DELAY_MAX", 480)
    _i("OWNER_BUSY_WINDOW", 180)
    _i("REAUTH_COOLDOWN", 600)
    _i("CONTROL_POLL_MIN", 30)
    _i("CONTROL_POLL_MAX", 120)
    _i("FOMO_ALL_INTERVAL", 300)
    _i("SIEGE_PREWARN_SEC", 3600)
    _b("WARN_ENABLED")
    _b("API_ASK_BEFORE_ADD")
    _b("API_TRACE")
    _i("API_OWNER_TG_ID", 0)
    _b("USERBOT_SCHEDULE")
    _b("WEBAPP_ENABLED")
    return True


def _update_env_keys(updates: dict, env_path=".env") -> bool:
    """Записать значения ключей в .env, сохранив остальные строки и комментарии.

    Файла нет -> создаётся из .env.example (если есть). Отсутствующие ключи
    дописываются в конец. Возвращает True при успехе.
    """
    try:
        env = Path(env_path)
        if not env.exists():
            example = env.parent / ".env.example"
            if example.exists():
                shutil.copyfile(example, env)
        lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
        pending = {str(k): str(v) for k, v in updates.items()}
        out = []
        for line in lines:
            stripped = line.strip()
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in pending and stripped and not stripped.startswith("#"):
                out.append(f"{key}={pending.pop(key)}")
            else:
                out.append(line)
        for key, val in pending.items():
            out.append(f"{key}={val}")
        env.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def set_fomo_init_data(value: str, env_path=".env") -> bool:
    """Сохранить свежую initData в .env (юзербот добыл новую — запомним)."""
    global FOMO_INIT_DATA
    FOMO_INIT_DATA = (value or "").strip()
    return _update_env_keys({"FOMO_INIT_DATA": FOMO_INIT_DATA}, env_path)


def set_userbot_api(api_id, api_hash, env_path=".env") -> bool:
    """Сохранить личные api_id/api_hash юзербота в .env (после 403 RECAPTCHA
    login_userbot.py вызывает это сам)."""
    global USERBOT_API_ID, USERBOT_API_HASH
    try:
        USERBOT_API_ID = int(str(api_id).strip())
    except (ValueError, TypeError):
        return False
    USERBOT_API_HASH = (api_hash or "").strip()
    return _update_env_keys({"USERBOT_API_ID": str(USERBOT_API_ID),
                             "USERBOT_API_HASH": USERBOT_API_HASH}, env_path)


def set_bot_username(username, env_path=".env") -> bool:
    """Запомнить @username самого бота (bot.py пишет при старте).

    Нужно sched_push: отложенные пуши планируются в чат с ботом. Повторная
    запись с тем же именем — no-op (не мусорим .env).
    """
    global BOT_USERNAME
    u = (str(username or "").strip().lstrip("@"))
    if not u:
        return False
    if u == BOT_USERNAME:
        return True
    BOT_USERNAME = u
    return _update_env_keys({"BOT_USERNAME": BOT_USERNAME}, env_path)


def set_ask_before_add(value: bool, env_path=".env") -> bool:
    """Переключить режим подтверждения: True — список с кнопками Да/Нет,
    False — ставить молча. Пишет в .env (команда /вопросы)."""
    global API_ASK_BEFORE_ADD
    API_ASK_BEFORE_ADD = bool(value)
    return _update_env_keys({"API_ASK_BEFORE_ADD": "true" if API_ASK_BEFORE_ADD else "false"},
                            env_path)


def set_trace(value: bool, env_path=".env") -> bool:
    """Вкл/выкл трассировку сырых ответов API (data/trace.log). /трассировка."""
    global API_TRACE
    API_TRACE = bool(value)
    return _update_env_keys({"API_TRACE": "true" if API_TRACE else "false"}, env_path)


def set_api_enabled(value: bool, env_path=".env") -> bool:
    """Включить/выключить автотрекинг в .env.

    login_userbot.py вызывает это сам после успешного входа юзербота:
    залогинил Telegram — таймеры пошли сами, fomo.txt не нужен.
    """
    global API_ENABLED
    API_ENABLED = bool(value)
    return _update_env_keys({"API_ENABLED": "true" if API_ENABLED else "false"}, env_path)


def set_api_owner_tg_id(value, env_path=".env") -> bool:
    """Сохранить владельца автотрекинга в .env (API_OWNER_TG_ID).

    login_userbot.py вызывает это сам: юзербот = ваш собственный аккаунт,
    так что его Telegram ID и есть владелец таймеров и пушей. /start не
    обязателен, руками .env заполнять не нужно.
    """
    global API_OWNER_TG_ID
    try:
        API_OWNER_TG_ID = int(str(value).strip())
    except (ValueError, TypeError):
        return False
    if API_OWNER_TG_ID <= 0:
        return False
    return _update_env_keys({"API_OWNER_TG_ID": str(API_OWNER_TG_ID)}, env_path)


def reload():
    """Перечитать .env с диска (watcher обновил файл — подтягиваем без рестарта).

    load_dotenv(override=True) заново читает файл и перезаписывает os.environ,
    после чего os.getenv ниже отдаёт свежие значения. Без этого reload читал бы
    только память процесса и «горячее» обновление не работало бы вовсе.
    """
    global API_ENABLED, API_STATE_URL, API_AUTH_HEADER, API_POLL_INTERVAL, API_OWNER_TG_ID
    global API_ASK_BEFORE_ADD, API_METHOD, API_BODY, API_HEADERS_JSON, API_TRACE
    global FOMO_INIT_DATA, FOMO_API_BASE, FOMO_GAME_BOT, FOMO_APP_NAME, FOMO_LANG, FOMO_REAUTH_INTERVAL
    global FOMO_WEB_ORIGIN, USERBOT_API_ID, USERBOT_API_HASH, USERBOT_SESSION_PATH
    global FOMO_ALL_INTERVAL, SIEGE_PREWARN_SEC  # без этого reload писал бы в локальную переменную
    global WEBAPP_ENABLED, WEBAPP_PORT, USERBOT_SCHEDULE, BOT_USERNAME, QUIET_HEARTBEAT_SEC
    try:
        load_dotenv(_ENV_PATH, override=True)
    except Exception:
        pass
    API_ENABLED = os.getenv("API_ENABLED", "false").strip().lower() == "true"
    API_ASK_BEFORE_ADD = os.getenv("API_ASK_BEFORE_ADD", "false").strip().lower() == "true"
    API_TRACE = os.getenv("API_TRACE", "false").strip().lower() == "true"
    API_STATE_URL = os.getenv("API_STATE_URL", "").strip()
    API_AUTH_HEADER = os.getenv("API_AUTH_HEADER", "").strip()
    API_METHOD = (os.getenv("API_METHOD", "GET").strip().upper() or "GET")
    API_BODY = os.getenv("API_BODY", "").strip()
    API_HEADERS_JSON = os.getenv("API_HEADERS_JSON", "").strip()
    try:
        API_POLL_INTERVAL = int(os.getenv("API_POLL_INTERVAL", "300"))
    except ValueError:
        API_POLL_INTERVAL = 300
    try:
        API_OWNER_TG_ID = int(os.getenv("API_OWNER_TG_ID", "0") or 0)
    except ValueError:
        API_OWNER_TG_ID = 0
    FOMO_INIT_DATA = os.getenv("FOMO_INIT_DATA", "").strip()
    FOMO_API_BASE = os.getenv("FOMO_API_BASE", "https://api.fomofighters.xyz").strip().rstrip("/")
    FOMO_GAME_BOT = os.getenv("FOMO_GAME_BOT", "fomo_fighters_bot").strip()
    FOMO_APP_NAME = os.getenv("FOMO_APP_NAME", "game").strip()
    FOMO_LANG = os.getenv("FOMO_LANG", "ru").strip()
    FOMO_WEB_ORIGIN = os.getenv("FOMO_WEB_ORIGIN", "https://game.fomofighters.xyz").strip().rstrip("/")
    try:
        FOMO_REAUTH_INTERVAL = int(os.getenv("FOMO_REAUTH_INTERVAL", "21600") or 21600)
    except ValueError:
        FOMO_REAUTH_INTERVAL = 21600
    try:
        FOMO_ALL_INTERVAL = int(os.getenv("FOMO_ALL_INTERVAL", "300") or 300)
    except ValueError:
        FOMO_ALL_INTERVAL = 300
    try:
        SIEGE_PREWARN_SEC = int(os.getenv("SIEGE_PREWARN_SEC", "3600") or 3600)
    except ValueError:
        SIEGE_PREWARN_SEC = 3600
    WEBAPP_ENABLED = os.getenv("WEBAPP_ENABLED", "true").strip().lower() == "true"
    try:
        WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080") or 8080)
    except ValueError:
        WEBAPP_PORT = 8080
    BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()
    try:
        QUIET_HEARTBEAT_SEC = int(os.getenv("QUIET_HEARTBEAT_SEC", "0") or 0)
    except ValueError:
        QUIET_HEARTBEAT_SEC = 0
    global POLL_JITTER, POLL_PULSE_MIN, POLL_PULSE_MAX, BOT_TZ
    global NIGHT_START, NIGHT_END, NIGHT_SILENT, NIGHT_MICROTICKS, NIGHT_WAKE_JITTER
    global WAKE_DELAY_MIN, WAKE_DELAY_MAX, OWNER_BUSY_WINDOW, REAUTH_COOLDOWN
    global CONTROL_POLL_MIN, CONTROL_POLL_MAX
    try:
        POLL_JITTER = float(os.getenv("POLL_JITTER", "0.4") or 0.4)
    except ValueError:
        POLL_JITTER = 0.4
    _qhs = QUIET_HEARTBEAT_SEC
    try:
        POLL_PULSE_MIN = int(os.getenv("POLL_PULSE_MIN", "") or (_qhs if _qhs > 0 else 1800))
    except ValueError:
        POLL_PULSE_MIN = 1800
    try:
        POLL_PULSE_MAX = int(os.getenv("POLL_PULSE_MAX", "") or (_qhs if _qhs > 0 else 3300))
    except ValueError:
        POLL_PULSE_MAX = 3300
    BOT_TZ = os.getenv("BOT_TZ", "").strip()
    NIGHT_START = os.getenv("NIGHT_START", "00:00").strip()
    NIGHT_END = os.getenv("NIGHT_END", "08:00").strip()
    NIGHT_SILENT = os.getenv("NIGHT_SILENT", "true").strip().lower() == "true"
    NIGHT_MICROTICKS = os.getenv("NIGHT_MICROTICKS", "false").strip().lower() == "true"
    try:
        NIGHT_WAKE_JITTER = int(os.getenv("NIGHT_WAKE_JITTER", "900") or 900)
    except ValueError:
        NIGHT_WAKE_JITTER = 900
    try:
        WAKE_DELAY_MIN = int(os.getenv("WAKE_DELAY_MIN", "180") or 180)
    except ValueError:
        WAKE_DELAY_MIN = 180
    try:
        WAKE_DELAY_MAX = int(os.getenv("WAKE_DELAY_MAX", "480") or 480)
    except ValueError:
        WAKE_DELAY_MAX = 480
    try:
        OWNER_BUSY_WINDOW = int(os.getenv("OWNER_BUSY_WINDOW", "180") or 180)
    except ValueError:
        OWNER_BUSY_WINDOW = 180
    try:
        REAUTH_COOLDOWN = int(os.getenv("REAUTH_COOLDOWN", "600") or 600)
    except ValueError:
        REAUTH_COOLDOWN = 600
    try:
        CONTROL_POLL_MIN = int(os.getenv("CONTROL_POLL_MIN", "30") or 30)
    except ValueError:
        CONTROL_POLL_MIN = 30
    try:
        CONTROL_POLL_MAX = int(os.getenv("CONTROL_POLL_MAX", "120") or 120)
    except ValueError:
        CONTROL_POLL_MAX = 120
    try:
        USERBOT_API_ID = int(os.getenv("USERBOT_API_ID", "6") or 6)
    except ValueError:
        USERBOT_API_ID = 6
    USERBOT_API_HASH = os.getenv("USERBOT_API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
    USERBOT_SESSION_PATH = os.getenv("USERBOT_SESSION_PATH", "userbot.session").strip()
    USERBOT_SCHEDULE = os.getenv("USERBOT_SCHEDULE", "true").strip().lower() == "true"
