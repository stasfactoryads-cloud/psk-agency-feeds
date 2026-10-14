"""Планировки: SVG с сайта -> JPG, который принимает Яндекс Недвижимость.

Две картинки на лот:
  plan  : планировка квартиры (поле plan в API)
  floor : поэтажный план с подсвеченной квартирой (floor_plan + floor_plan_hover)

Файлы кешируются по хешу исходника: повторная генерация не перерисовывает
то, что не менялось.
"""

import hashlib
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pymupdf
from PIL import Image

from .api import get_bytes

log = logging.getLogger(__name__)

HIGHLIGHT_STYLE = 'fill="#E8452C" fill-opacity="0.55" stroke="#B3261E" stroke-width="3"'
PADDING_RATIO = 0.04
JPEG_QUALITY = 90


def _key(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def plan_filename(flat):
    return f"plan-{_key(flat['plan'])}.jpg" if flat.get("plan") else None


def floor_filename(flat):
    if not flat.get("floor_plan") or not flat.get("floor_plan_hover"):
        return None
    return f"floor-{_key(flat['floor_plan'], flat['floor_plan_hover'])}.jpg"


def highlight_svg(svg_bytes, hover_markup):
    """Добавляет контур квартиры поверх поэтажного плана."""
    svg = svg_bytes.decode("utf-8", errors="replace")
    shape = re.sub(r'\s(fill|stroke|style|class)="[^"]*"', "", hover_markup.strip())
    shape = re.sub(r"^<(polygon|polyline|path|rect)\b", rf"<\1 {HIGHLIGHT_STYLE}", shape)
    pos = svg.rfind("</svg>")
    if pos == -1:
        return svg_bytes
    return (svg[:pos] + f"<g>{shape}</g>" + svg[pos:]).encode("utf-8")


def render_svg(svg_bytes, size):
    """SVG -> JPG на белом фоне, длинная сторона = size, сохраняем пропорции."""
    doc = pymupdf.open(stream=svg_bytes, filetype="svg")
    page = doc[0]
    w, h = page.rect.width, page.rect.height
    inner = size * (1 - 2 * PADDING_RATIO)
    zoom = inner / max(w, h)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    doc.close()

    pad = int(size * PADDING_RATIO)
    canvas = Image.new("RGB", (img.width + 2 * pad, img.height + 2 * pad), "white")
    canvas.paste(img, (pad, pad))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def _job(flat, kind, target, size):
    if target.exists():
        return target.name, True
    try:
        if kind == "plan":
            svg = get_bytes(flat["plan"])
        else:
            svg = highlight_svg(get_bytes(flat["floor_plan"]), flat["floor_plan_hover"])
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(render_svg(svg, size))
        tmp.replace(target)
        return target.name, True
    except Exception as e:
        log.warning("Лот %s: не удалось сделать %s: %s", flat.get("id"), kind, e)
        return target.name, False


def build_images(flats, plans_dir, size, workers=4):
    """Готовит JPG для всех лотов. Возвращает {flat_id: {"plan": name|None, "floor": name|None}}."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    tasks, result = [], {}
    for f in flats:
        entry = {"plan": plan_filename(f), "floor": floor_filename(f)}
        result[f["id"]] = entry
        for kind in ("plan", "floor"):
            if entry[kind]:
                tasks.append((f, kind, plans_dir / entry[kind], size))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        status = dict(ex.map(lambda t: _job(*t), tasks))

    for entry in result.values():
        for kind in ("plan", "floor"):
            if entry[kind] and not status.get(entry[kind]):
                entry[kind] = None

    ok = sum(1 for v in status.values() if v)
    log.info("Планировки: %d готово, %d с ошибкой", ok, len(status) - ok)
    return result


def cleanup_unused(plans_dir, used_names):
    removed = 0
    for p in plans_dir.glob("*.jpg"):
        if p.name not in used_names:
            p.unlink()
            removed += 1
    if removed:
        log.info("Удалено устаревших картинок: %d", removed)
