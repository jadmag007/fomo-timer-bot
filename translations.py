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
    # ↓ допишите новые группы из trace.log, например:
    # "tClanChests": "🎁 Клановый сундук",
    # "tOutpostRewards": "📦 Награда аванпоста",
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
