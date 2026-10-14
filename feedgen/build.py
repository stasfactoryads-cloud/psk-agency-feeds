"""Полный цикл: API -> картинки -> фиды агентств -> проверка -> status.json и index.html."""

import html
import json
import logging
from datetime import datetime, timedelta, timezone

from . import api
from .images import build_images, cleanup_unused
from .site import write_site
from .validate import validate_feed
from .yrl import NS, build_feed, write_feed
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))


def _load_first_seen(cfg):
    path = cfg.cache_dir / "first_seen.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def run(cfg, only=None, with_images=True):
    now = datetime.now(MSK)
    p = cfg.project
    log.info("Сборка фидов ЖК «%s», %s", p.name, now.strftime("%Y-%m-%d %H:%M МСК"))

    project_api = api.fetch_project(cfg.api_base, p.api_id)
    flats = api.select_project_flats(api.fetch_all_flats(cfg.api_base), p)
    flats.sort(key=lambda f: (f.get("rooms") or 0, f.get("floor") or 0, f["id"]))
    log.info("Лотов в продаже: %d", len(flats))
    if not flats:
        raise RuntimeError("API не вернуло ни одного лота: фиды не перезаписываю")

    details = api.fetch_details(cfg.api_base, [f["id"] for f in flats], cfg.api_workers)

    if with_images:
        images = build_images(flats, cfg.plans_dir, cfg.image_size)
        cleanup_unused(cfg.plans_dir, {n for e in images.values() for n in e.values() if n})
    else:
        images = {}

    renders = [s["image"] for s in sorted(project_api.get("slider_images") or [], key=lambda s: s.get("order", 0))
               if s.get("image", "").lower().endswith((".jpg", ".jpeg", ".png"))][:p.render_count]

    first_seen = _load_first_seen(cfg)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S+03:00")
    for f in flats:
        first_seen.setdefault(str(f["id"]), ts)
    _save_json(cfg.cache_dir / "first_seen.json", first_seen)

    ctx = {"cfg": cfg, "now": now, "flats": flats, "details": details, "images": images,
           "project_api": project_api, "renders": renders, "first_seen": first_seen}

    try:
        prev_lots = json.loads((cfg.output_dir / "status.json").read_text()).get("lots")
    except (FileNotFoundError, json.JSONDecodeError):
        prev_lots = None
    report = {"generated_at": ts, "project": p.name, "lots": len(flats), "prev_lots": prev_lots, "feeds": []}
    for agency in cfg.agencies:
        if only and agency.slug not in only:
            continue
        path = cfg.feeds_dir / f"{agency.slug}.xml"
        write_feed(build_feed(ctx, agency), path)
        check = validate_feed(path, cfg.public_base_url)
        preview = cfg.output_dir / "preview" / f"{agency.slug}.html"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(render_preview(path, agency, cfg), encoding="utf-8")
        status = "ok" if not check["errors"] else "error"
        log.info("%-30s %s  офферов %d, ошибок %d, замечаний %d",
                 agency.name, status.upper(), check["offers"], len(check["errors"]), len(check["warnings"]))
        for e in check["errors"]:
            log.warning("  [%s] %s", agency.slug, e)
        report["feeds"].append({
            "agency": agency.name, "slug": agency.slug,
            "url": cfg.public_url(f"feeds/{agency.slug}.xml"),
            "status": status, **check,
        })

    write_site(cfg, report)
    return report


def render_preview(feed_path, agency, cfg):
    """Наглядная страница фида: как лоты увидит человек, картинки по относительным путям."""
    q = lambda el, p: el.find("/".join(f"{{{NS}}}{x}" for x in p.split("/")))
    t = lambda el, p: (q(el, p).text if q(el, p) is not None else "") or ""
    base = cfg.public_base_url.rstrip("/") + "/"
    root = ET.parse(feed_path).getroot()
    cards = []
    for o in root.findall(f"{{{NS}}}offer"):
        imgs = [i.text.replace(base, "../") for i in o.findall(f"{{{NS}}}image")]
        rooms = "Студия" if q(o, "studio") is not None else f"{t(o, 'rooms')}-комн."
        price = f"{int(t(o, 'price/value')):,}".replace(",", " ")
        agent = q(o, "sales-agent")
        contact = " · ".join(x for x in (t(agent, "organization"), t(agent, "name"),
                                          ", ".join(p.text for p in agent.findall(f"{{{NS}}}phone"))) if x)
        gallery = "".join(f'<a href="{html.escape(u)}" target="_blank"><img loading="lazy" src="{html.escape(u)}"></a>' for u in imgs)
        cards.append(f"""<article><div class="gal">{gallery}</div>
<h2>{rooms} {t(o, 'area/value')} м² · этаж {t(o, 'floor')}/{t(o, 'floors-total')} · {price} ₽</h2>
<p class="meta">ID {o.get('internal-id')} · {html.escape(t(o, 'renovation'))} · потолки {t(o, 'ceiling-height')} м · сдача {t(o, 'ready-quarter')} кв. {t(o, 'built-year')} · <a href="{html.escape(t(o, 'url'))}" target="_blank">страница лота</a></p>
<p class="meta">{html.escape(contact)}</p>
<pre>{html.escape(t(o, 'description'))}</pre></article>""")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Фид {html.escape(agency.name)}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;color:#111;background:#f6f6f6;margin:0;padding:24px 16px}}
main{{max-width:1100px;margin:0 auto}}article{{background:#fff;border:1px solid #e2e2e2;border-radius:8px;padding:16px;margin:0 0 16px}}
.gal{{display:flex;gap:8px;overflow-x:auto}}.gal img{{height:180px;border:1px solid #eee;border-radius:4px;background:#fff}}
h2{{font-size:18px;margin:12px 0 4px}}.meta{{color:#555;margin:2px 0}}.meta a{{color:#111}}
pre{{white-space:pre-wrap;font:inherit;background:#fafafa;border-left:3px solid #ddd;padding:8px 12px;margin:10px 0 0}}</style></head>
<body><main><h1>Фид «{html.escape(agency.name)}»: ЖК «{html.escape(cfg.project.name)}»</h1>
<p>Лотов: {len(cards)}. Файл фида: <a href="../feeds/{agency.slug}.xml">{agency.slug}.xml</a></p>
{''.join(cards)}</main></body></html>"""
