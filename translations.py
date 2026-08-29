"""translations.py — ЕДИНОЕ МЕСТО для переводов таймеров игры.

Бот показывает подписи по-русски: «🏗 Стройка: Замок», «⚔️ Тренировка ×55».
Ключи приходят из API игры (POST /user/data/timers) по-английски, здесь они
переводятся. Правьте только этот файл — код бота менять не нужно.

Как добавить перевод нового таймера (2 минуты):
  1. Пришлите боту /трассировка — бот начнёт писать сырые ответы игры
     в файл data/trace.log (включить можно и кнопкой на экране /апи).
  2. Подождите пару опросов (или запустите улучшение в игре), затем
     пришлите /трейслог — бот пришлёт файл лога.
  3. Найдите в логе нужный ключ: имя группы (tClanChests…), ключ объекта
     (troopKey/skillKey/buildingKey) или имя поля с датой.
  4. Впишите его в нужный словарь ниже (формат «ключ игры: по-русски»)
     и перезапустите бота (start.bat).

Нет ключа в словаре — бот НЕ падает: покажет приличное английское имя
(«clan_chest» -> «Clan chest»), чтобы таймер всё равно работал.
"""
import re

# ---------- Группы таймеров (ключи списков в ответе /user/data/timers) ----------
# t* — списки объектов {dateStart, dateEnd, buildingKey/troopKey/skillKey…}
BUCKETS = {
    "tBuildings": "🏗 Стройка",
    "tSkills": "📚 Навык",
    "tTroops": "⚔️ Тренировка",
    "tHospital": "🏥 Лечение",
    "tAttacks": "🗡 Атака",
    "tScouts": "🔍 Разведка",
    "tReturns": "↩️ Возврат",
    "tScoutReturns": "↩️ Возврат разведки",
    "tWars": "⚔️ Война",
    "tWarUser": "⚔️ Война",
    "tReinforcements": "🛡 Подкрепление",
    "tReinforcementReturns": "↩️ Возврат подкреплений",
    "tJourneys": "🧭 Поход",
    "tOutpostSiegesMine": "🏰 Осада аутпоста",
    "tOutpostDefensesMine": "🛡 Оборона аутпоста",
    "tOutpostMarchesMine": "🧭 Марш аутпоста",
    # Награды из /user/data/all (списки st* — не t*): клановые сундуки и
    # награды аванпостов. Бот опрашивает этот эндпоинт раз в
    # FOMO_ALL_INTERVAL секунд и ставит таймеры по dateEnd.
    "stClanRewards": "🎁 Клановый сундук",
    "stOutpostRewards": "📦 Награда аванпоста",
    # Сундуки аутпостов, готовые к забору прямо сейчас
    # (outpostClaimableCountByOutpostId в /user/data/timers) — мгновенное
    # напоминание, появляется в списке только до момента забора.
    "tOutpostClaimable": "🏰 Сундук аутпоста",
}

# ---------- Здания (buildingKey) ----------
BUILDINGS = {
    "castle": "Замок", "hospital": "Госпиталь", "barracks": "Казарма",
    "stable": "Конюшня", "archery_range": "Стрельбище",
    "siege_workshop": "Осадный цех", "scout_camp": "Лагерь разведки",
    "farm": "Ферма", "sawmill": "Лесопилка", "quarry": "Карьер",
    "mine": "Шахта", "warehouse": "Склад", "academy": "Академия",
    "wall": "Стена", "tower": "Башня", "house": "Дом",
    "trading_post": "Торговый пост", "market": "Рынок",
}

# ---------- Войска (troopKey) ----------
# Известные на сегодня ключи игры: dog_siege_30, dog_scout_10 — официальных
# названий этих отрядов мы не знаем, поэтому переводить не рискнули.
TROOPS = {
    # Примеры формата — заполните по trace.log:
    # "halberdier": "Алебардщик",
    # "dog_siege_30": "Осадный отряд",
}

# ---------- Навыки (skillKey) ----------
SKILLS = {
    # Пример: "load_1": "Грузоподъёмность I",
}

# ---------- Награды: клановые сундуки и аванпосты (st*-списки /user/data/all) ----------
# Механика клановых сундуков (проверено по dbClanRewards из /dbs и игроком):
#   сундук clan_N доступен с уровня клана N, перезарядка N часов (1..30),
#   собрать всё сразу — кнопка в игре (/clan/rewards/claim-all);
#   пока сундук на перезарядке, он приходит в stClanRewards {key, dateEnd}.
# Награды аванпостов: ключ avanpost_r<кольцо>_lvl_<уровень>, перезарядка
# по таблице dbOutpostRewards (r1_lvl_1 — каждые 4 часа, r1_lvl_2 — 24 ч…).
_CLAN_KEY_RE = re.compile(r"^clan_(\d+)$")
_OUTPOST_KEY_RE = re.compile(r"^avanpost_r(\d+)_lvl_(\d+)$")


def reward_label(list_name, item) -> str:
    """Подпись награды из st*-списков /user/data/all.

    clan_7            -> «🎁 Клановый сундук · 7 ч» (часы = номер сундука)
    avanpost_r1_lvl_1 -> «📦 Награда аванпоста · кольцо 1, ур. 1»
    неизвестный ключ  -> «<группа>: <приличное имя ключа>»
    """
    base = bucket(list_name)
    key = item.get("key") if isinstance(item, dict) else None
    if not key:
        return base
    m = _CLAN_KEY_RE.match(str(key))
    if m:
        return f"{base} · {int(m.group(1))} ч"
    m = _OUTPOST_KEY_RE.match(str(key))
    if m:
        return f"{base} · кольцо {m.group(1)}, ур. {m.group(2)}"
    return f"{base}: {pretty(key)}"

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")


def pretty(key) -> str:
    """«clan_chest_ready» -> «Clan chest ready»; «clanChestReady» -> «Clan Chest Ready».

    Используется как фолбэк для любых ключей, которых нет в словарях выше.
    """
    s = _CAMEL_RE.sub(r" \1", str(key)).replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else s


def bucket(key) -> str:
    """Имя группы таймеров по-русски: tBuildings -> «🏗 Стройка».
    Неизвестная группа t* -> приличное имя («tClanChests» -> «Clan Chests»)."""
    k = str(key)
    if k in BUCKETS:
        return BUCKETS[k]
    if k.startswith("t") and len(k) > 1:
        return pretty(k[1:])
    return pretty(k)


def building(key) -> str:
    return BUILDINGS.get(str(key)) or pretty(key)


def troop(key) -> str:
    return TROOPS.get(str(key)) or pretty(key)


def skill(key) -> str:
    return SKILLS.get(str(key)) or pretty(key)


def item_label(bucket_label, item) -> str:
    """Подпись таймера: «⚔️ Тренировка: Осадный отряд ×55».

    Приоритет: войско (то, ЧТО готовится) -> навык -> здание -> просто группа.
    """
    cnt = item.get("count") if isinstance(item, dict) else None
    t = item.get("troopKey") if isinstance(item, dict) else None
    s = item.get("skillKey") if isinstance(item, dict) else None
    b = item.get("buildingKey") if isinstance(item, dict) else None
    if t:
        name = troop(t)
        return f"{bucket_label}: {name}" + (f" ×{cnt}" if cnt else "")
    if s:
        return f"{bucket_label}: {skill(s)}"
    if b:
        return f"{bucket_label}: {building(b)}"
    return bucket_label
