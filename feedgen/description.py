"""Описание лота для <description>.

Собирается из данных API: параметры квартиры (карточка лота) + преимущества
проекта (project_benefits, projectutpslide_set). Порядок блоков о проекте
меняется по хешу (агентство + лот), чтобы у разных агентств тексты не совпадали
дословно.

Правила текста ГК «ПСК»: без буквы «е» с точками, без длинного тире, без телефонов,
в тексте есть название ЖК и «ГК «ПСК»».
"""

import hashlib
import html
import re

QUARTERS = {1: "I", 2: "II", 3: "III", 4: "IV"}
MAX_LEN = 10000           # лимит Яндекс Недвижимости
BENEFITS_IN_TEXT = 5

FIXES = {
    "Whit Box": "White Box",
}

FINISHING_TEXT = {
    "под ключ": "Квартира с отделкой под ключ: можно заезжать без ремонта.",
    "предчистовая": "Предчистовая отделка (White Box): стены и полы подготовлены под финишную отделку по вашему вкусу.",
    "чистовая": "Чистовая отделка.",
    "черновая": "Черновая отделка.",
    "whitebox": "Отделка White Box: стены и полы подготовлены под финишную отделку по вашему вкусу.",
    "без отделки": "Без отделки.",
}

PHONE_RE = re.compile(r"(\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")


def sanitize(text):
    text = text.replace("\u0451", "\u0435").replace("\u0401", "\u0415")
    text = text.replace("\u2014", "\u2013")          # длинное тире -> короткое
    text = text.replace("‐", "-").replace("‑", "-")
    for bad, good in FIXES.items():
        text = text.replace(bad, good)
    text = PHONE_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def strip_html(markup):
    text = re.sub(r"<br\s*/?>", "\n", markup or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def point(title, text):
    # «Заголовок. Текст.»: регистр текста не трогаем, чтобы не испортить имена собственные
    return f"{sentence_case(title)}. {text.rstrip('.')}."


def sentence_case(title):
    """Заголовки КАПСОМ приводим к обычному виду, остальные оставляем как есть."""
    t = title.strip()
    if t.isupper():
        t = t.lower()
    return t[:1].upper() + t[1:]


def fmt_area(area):
    return f"{area:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def rooms_label(rooms, apartments=False):
    if apartments:
        return "Апартаменты-студия" if not rooms else f"{rooms}-комнатные апартаменты"
    if not rooms:
        return "Квартира-студия"
    return f"{rooms}-комнатная квартира"


def discount_percent(flat):
    try:
        old = float(flat.get("original_price") or 0)
        price = float(flat.get("price") or 0)
    except (TypeError, ValueError):
        return 0
    if old > price > 0:
        return round((1 - price / old) * 100)
    return 0


def project_points(project_api):
    """Преимущества проекта из API в виде готовых предложений."""
    points = []
    for b in sorted(project_api.get("about_project_advantages") or [], key=lambda x: x.get("order", 0)):
        title, text = b.get("title", ""), strip_html(b.get("text", ""))
        if title and text:
            points.append(point(title, text))
    return points


def utp_points(detail):
    points = []
    for s in sorted((detail or {}).get("projectutpslide_set") or [], key=lambda x: x.get("order", 0)):
        title, text = s.get("title", ""), strip_html(s.get("text", ""))
        if title and text and "метро" not in title.lower():   # метро уже есть отдельной строкой
            points.append(point(title, text))
    return points


def rotate(items, seed):
    if not items:
        return items
    shift = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(items)
    return items[shift:] + items[:shift]


def build_description(flat, detail, project_api, project_cfg, renovation, agency_slug, state="unfinished"):
    name = project_cfg.title
    rooms = flat.get("rooms") or 0
    area = fmt_area(float(flat.get("area") or 0))
    floor, max_floor = flat.get("floor"), flat.get("max_floor")

    lines = []
    head = f"{rooms_label(rooms, flat.get('type') == 'apartments')} {area} м²"
    if floor and max_floor:
        head += f" на {floor} этаже {max_floor}-этажного дома"
    place = {"ЖК": "ЖК", "Апарт-отель": "апарт-отеле"}.get(project_cfg.label, project_cfg.label)
    lines.append(f"{head} в {place} «{project_cfg.name}» от ГК «ПСК».")

    facts = []
    if renovation and renovation in FINISHING_TEXT:
        text = FINISHING_TEXT[renovation]
        if flat.get("type") == "apartments":
            text = text.replace("Квартира с отделкой", "Апартаменты с отделкой")
        facts.append(text)
    if detail and detail.get("floor_ceiling_height"):
        facts.append(f"Высота потолков {fmt_area(detail['floor_ceiling_height'])} м.")
    features = [f.lower() for f in (detail or {}).get("features") or []]
    if any("лодж" in f for f in features):
        facts.append("Есть лоджия.")
    elif any("балкон" in f for f in features):
        facts.append("Есть балкон.")
    pct = discount_percent(flat)
    if pct > 0:
        facts.append(f"Выгода {pct}% от базовой стоимости.")
    q, y = flat.get("completion_quarter"), flat.get("completion_year")
    section = project_cfg.section(flat)
    if state == "hand-over":
        facts.append(f"{section.capitalize()}, дом сдан." if section else "Дом сдан.")
    elif state == "built":
        facts.append(f"{section.capitalize()}, дом построен, идет передача ключей." if section
                     else "Дом построен, идет передача ключей.")
    elif q and y:
        prefix = f"{section.capitalize()}, с" if section else "С"
        facts.append(f"{prefix}рок сдачи: {QUARTERS.get(q, q)} квартал {y} года.")
    if facts:
        lines.append(" ".join(facts))

    metro = (project_api.get("projectmetro_set") or [])
    if metro:
        m = metro[0]
        if m.get("walk_time"):
            lines.append(f"Метро «{m['metro_name']}» в {m['walk_time']} минутах пешком.")
        elif m.get("car_time"):
            lines.append(f"Метро «{m['metro_name']}» в {m['car_time']} минутах на транспорте.")

    seed = f"{agency_slug}:{flat['id']}"
    about = rotate(project_points(project_api), seed)[:BENEFITS_IN_TEXT]
    utp = utp_points(detail)
    if about or utp:
        lines.append(f"О проекте «{project_cfg.name}»:")
        lines.extend(f"• {p}" for p in about + utp)

    lines.append(f"Застройщик ГК «ПСК». Номер лота на сайте застройщика: {flat.get('number')}.")

    text = sanitize("\n".join(lines))
    return text[:MAX_LEN]
