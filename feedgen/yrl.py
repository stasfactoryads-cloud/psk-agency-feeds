"""Сборка YRL-фида по техтребованиям Яндекс Недвижимости (продажа, новостройки)."""

import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from .description import build_description, discount_percent

NS = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"
ET.register_namespace("", NS)


def _q(tag):
    return f"{{{NS}}}{tag}"


def sub(parent, name, text=None, **attrs):
    el = ET.SubElement(parent, _q(name), {k.replace("_", "-"): v for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def building_state(year, quarter, now):
    if year and quarter and (year, quarter) <= (now.year, (now.month - 1) // 3 + 1):
        return "hand-over"
    return "unfinished"


def balcony_value(features):
    low = [f.lower() for f in features or []]
    if any("лодж" in f for f in low):
        return "лоджия"
    if any("балкон" in f for f in low):
        return "балкон"
    return None


def lot_url(cfg, project, flat, agency):
    url = f"{cfg.site_url}/flats/{flat['id']}"
    if agency.utm_source:
        url += "?" + urlencode({
            "utm_source": agency.utm_source,
            "utm_medium": "realty_feed",
            "utm_campaign": project.key,
        })
    return url


def build_offer(root, ctx, flat, agency):
    cfg, p = ctx["cfg"], ctx["project"]
    detail = ctx["details"].get(flat["id"]) or {}
    images = ctx["images"].get(flat["id"], {})
    project_api = ctx["project_api"]
    now = ctx["now"]
    ts = now.strftime("%Y-%m-%dT%H:%M:%S+03:00")

    offer = sub(root, "offer", internal_id=str(flat["id"]))
    sub(offer, "type", "продажа")
    sub(offer, "property-type", "жилая")
    sub(offer, "category", "квартира")
    sub(offer, "url", lot_url(cfg, p, flat, agency))
    sub(offer, "creation-date", ctx["first_seen"].get(str(flat["id"]), ts))
    sub(offer, "last-update-date", ts)

    # Местоположение
    loc = sub(offer, "location")
    sub(loc, "country", "Россия")
    sub(loc, "locality-name", "Санкт-Петербург")
    sub(loc, "address", (project_api.get("address") or "").replace("г.Санкт-Петербург, ", ""))
    coords = (project_api.get("coords") or "").split(",")
    if len(coords) == 2:
        sub(loc, "latitude", coords[0].strip())
        sub(loc, "longitude", coords[1].strip())
    for m in project_api.get("projectmetro_set") or []:
        metro = sub(loc, "metro")
        sub(metro, "name", m["metro_name"])
        if m.get("walk_time"):
            sub(metro, "time-on-foot", m["walk_time"])
        if m.get("car_time"):
            sub(metro, "time-on-transport", m["car_time"])

    # Продавец
    agent = sub(offer, "sales-agent")
    for phone in agency.phones:
        sub(agent, "phone", phone)
    sub(agent, "category", agency.category)
    if agency.agent_name:
        sub(agent, "name", agency.agent_name)
    sub(agent, "organization", agency.organization)
    if agency.url:
        sub(agent, "url", agency.url)
    if agency.email:
        sub(agent, "email", agency.email)

    sub(offer, "deal-status", p.deal_status)

    # Цена
    price = int(float(flat["price"]))
    old = int(float(flat.get("original_price") or 0))
    price_el = sub(offer, "price")
    if p.show_discount and discount_percent(flat) > 0:
        sub(price_el, "value", old)
        sub(price_el, "currency", "RUB")
        disc = sub(price_el, "discount")
        sub(disc, "final-price", price)
    else:
        sub(price_el, "value", price)
        sub(price_el, "currency", "RUB")

    # Площадь и комнаты
    area = sub(offer, "area")
    sub(area, "value", round(float(flat["area"]), 2))
    sub(area, "unit", "кв. м")

    rooms = flat.get("rooms") or 0
    if rooms:
        sub(offer, "rooms", rooms)
    else:
        sub(offer, "studio", "да")
    sub(offer, "new-flat", "да")
    sub(offer, "floor", flat["floor"])
    if flat.get("max_floor"):
        sub(offer, "floors-total", flat["max_floor"])

    renovation = p.renovation.get(flat.get("finishing") or "")
    if renovation:
        sub(offer, "renovation", renovation)
    balcony = balcony_value(detail.get("features"))
    if balcony:
        sub(offer, "balcony", balcony)
    if detail.get("floor_ceiling_height"):
        sub(offer, "ceiling-height", round(detail["floor_ceiling_height"], 2))

    # Изображения: планировка первой (требование Яндекса), затем этаж, затем рендеры ЖК
    if images.get("plan"):
        sub(offer, "image", cfg.public_url(f"plans/{images['plan']}"), tag="plan")
    if images.get("floor"):
        sub(offer, "image", cfg.public_url(f"plans/{images['floor']}"), tag="floor-plan")
    for url in ctx["renders"]:
        sub(offer, "image", url)

    sub(offer, "description", build_description(flat, detail, project_api, p, renovation, agency.slug))

    # Дом
    sub(offer, "building-name", f"ЖК «{p.name}»")
    sub(offer, "yandex-building-id", p.yandex_building_id)
    sub(offer, "yandex-house-id", p.house_id(flat))
    year, quarter = flat.get("completion_year"), flat.get("completion_quarter")
    sub(offer, "building-state", building_state(year, quarter, now))
    if year:
        sub(offer, "built-year", year)
    if quarter:
        sub(offer, "ready-quarter", quarter)
    sub(offer, "building-type", p.building_type)
    if p.section(flat):
        sub(offer, "building-section", p.section(flat))
    sub(offer, "lift", "да")
    sub(offer, "parking", "да")
    return offer


def build_feed(ctx, agency):
    root = ET.Element(_q("realty-feed"))
    sub(root, "generation-date", ctx["now"].strftime("%Y-%m-%dT%H:%M:%S+03:00"))
    for flat in ctx["flats"]:
        build_offer(root, ctx, flat, agency)
    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def write_feed(tree, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tree.write(tmp, encoding="UTF-8", xml_declaration=True)
    tmp.replace(path)

