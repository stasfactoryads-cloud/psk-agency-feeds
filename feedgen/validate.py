"""Проверка готового фида на соответствие техтребованиям Яндекс Недвижимости.

errors  : Яндекс отклонит фид или объявления, загружать нельзя
warnings: фид примется, но стоит поправить
"""

import re
import xml.etree.ElementTree as ET
from collections import Counter

from .yrl import NS

PHONE_RE = re.compile(
    r"^(\+7\d{10}|\+7 \d{3} \d{3} \d{2} \d{2}|\+?7 \(\d{3}\) \d{3}-\d{2}-\d{2}|8 \d{3} \d{3} \d{2} \d{2}"
    r"|8 \(\d{3}\) \d{3}-\d{2}-\d{2}|\d{10}|\d{3}-\d{3}-\d{2}-\d{2})$"
)

REQUIRED = [
    "type", "category", "creation-date", "location", "location/address",
    "sales-agent", "sales-agent/phone", "deal-status", "price/value", "area/value",
    "area/unit", "new-flat", "floor", "yandex-building-id", "yandex-house-id",
    "ready-quarter", "building-state", "image", "description",
]

ALLOWED = {
    "deal-status": {"первичная продажа", "продажа от застройщика", "прямая продажа", "переуступка",
                    "reassignment", "214ФЗ", "ФЗ 214", "214 ФЗ", "ФЗ214", "214", "по 214 фз"},
    "renovation": {"черновая", "чистовая", "под ключ", "без отделки", "предчистовая", "whitebox"},
    "building-state": {"built", "hand-over", "unfinished"},
    "ready-quarter": {"1", "2", "3", "4"},
    "building-type": {"кирпичный", "монолит", "панельный"},
    "sales-agent/category": {"агентство", "agency", "застройщик", "developer"},
    "category": {"квартира", "flat", "дом", "house", "таунхаус", "townhouse"},
}

FORBIDDEN_TEXT = {"\u0451": "буква «е» с точками", "\u0401": "буква «Е» с точками", "\u2014": "длинное тире"}


def _f(el, path):
    return el.find("/".join(f"{{{NS}}}{p}" for p in path.split("/")))


def validate_feed(path, public_base_url=None):
    errors, warnings = [], []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return {"offers": 0, "errors": [f"XML не парсится: {e}"], "warnings": []}

    offers = root.findall(f"{{{NS}}}offer")
    if not offers:
        errors.append("В фиде нет ни одного объявления")

    if public_base_url and "example" in public_base_url:
        errors.append(f"public_base_url не настроен ({public_base_url}): Яндекс не скачает планировки")

    per_issue = Counter()
    ids = Counter()
    for o in offers:
        ids[o.get("internal-id")] += 1
        for req in REQUIRED:
            el = _f(o, req)
            if el is None or (el.text is None and not len(el)):
                per_issue[f"нет обязательного тега <{req}>"] += 1
        for tag, allowed in ALLOWED.items():
            el = _f(o, tag)
            if el is not None and el.text not in allowed:
                per_issue[f"<{tag}> = «{el.text}» вне списка допустимых"] += 1
        for ph in o.findall(f"{{{NS}}}sales-agent/{{{NS}}}phone"):
            t = (ph.text or "").strip()
            if not PHONE_RE.match(t) or re.sub(r"\D", "", t).startswith(("880", "7880")):
                per_issue[f"телефон «{t}» в недопустимом формате"] += 1
        final = _f(o, "price/discount/final-price")
        if final is not None:
            try:
                fp, val = float(final.text), float(_f(o, "price/value").text)
                if not 0 < fp <= val:
                    per_issue["цена со скидкой не меньше цены без скидки: Яндекс не покажет скидку"] += 1
            except (TypeError, ValueError):
                per_issue["<final-price> не число"] += 1
        rooms, studio = _f(o, "rooms"), _f(o, "studio")
        if rooms is None and studio is None:
            per_issue["нет ни <rooms>, ни <studio>"] += 1

        imgs = o.findall(f"{{{NS}}}image")
        if imgs and imgs[0].get("tag") != "plan":
            per_issue["первая картинка не планировка"] += 1
        for img in imgs:
            u = img.text or ""
            if not u.startswith(("http://", "https://")) or re.search(r":\d+/", u.split("//", 1)[-1].split("/", 1)[0] + "/"):
                per_issue["картинка не по 80/443 порту"] += 1
            if u.lower().endswith(".svg"):
                per_issue["SVG-картинка (Яндекс не принимает)"] += 1

        desc = (_f(o, "description").text or "") if _f(o, "description") is not None else ""
        if len(desc) > 10000:
            per_issue["описание длиннее 10 000 знаков"] += 1
        for ch, label in FORBIDDEN_TEXT.items():
            if ch in desc:
                per_issue[f"в описании {label}"] += 1

    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        errors.append(f"Повторяются internal-id: {', '.join(dup[:10])}")

    hard = ("нет обязательного", "вне списка", "телефон", "SVG", "80/443", "нет ни", "цена со скидкой", "final-price")
    for issue, n in per_issue.items():
        (errors if any(h in issue for h in hard) else warnings).append(
            f"{issue}: {n} из {len(offers)}"
        )
    return {"offers": len(offers), "errors": errors, "warnings": warnings}
