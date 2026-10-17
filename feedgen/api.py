"""Клиент API psk-info.ru: список лотов, карточка лота, карточка проекта."""

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

PAGE_SIZE = 200
PAGE_PAUSE = 0.4              # пауза между страницами списка: не бьем по сайту пачками
DETAIL_FIELDS = ("floor_ceiling_height", "features", "projectutpslide_set")
DETAIL_MAX_AGE_DAYS = 7       # карточка лота обновляется раз в неделю
USER_AGENT = "PSK-Agency-Feeds/1.0"


def get_json(url, retries=6, timeout=90):
    """GET JSON с повторами и нарастающими паузами: разовые сбои сети не должны ронять сборку."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # сеть, 5xx, битый JSON
            last_err = e
            log.warning("API: попытка %d из %d не удалась (%s): %s", attempt + 1, retries, type(e).__name__, url[:80])
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"API недоступно: {url}: {last_err}")


def get_bytes(url, retries=3, timeout=60):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Не удалось скачать {url}: {last_err}")


def fetch_all_flats(api_base):
    """Все лоты сайта. Фильтр по проекту в API не работает, фильтруем сами."""
    flats, offset = [], 0
    while True:
        data = get_json(f"{api_base}/flats/?format=json&limit={PAGE_SIZE}&offset={offset}")
        flats.extend(data.get("results", []))
        offset += PAGE_SIZE
        if offset >= data.get("count", 0) or not data.get("results"):
            break
        time.sleep(PAGE_PAUSE)
    log.info("API: загружено %d лотов", len(flats))
    return flats


def select_project_flats(flats, project):
    result = []
    for f in flats:
        if f.get("project") != project.api_id:
            continue
        if f.get("status") not in project.include_statuses:
            continue
        if not f.get("show_price", True) or not f.get("price"):
            continue
        result.append(f)
    return result


_projects_cache = {}


def status_counts(flats, project):
    """Все статусы лотов проекта на сайте (включая те, что не берем в фид): {статус: число}."""
    counts = {}
    for f in flats:
        if f.get("project") == project.api_id:
            counts[f.get("status")] = counts.get(f.get("status"), 0) + 1
    return counts


def fetch_project(api_base, project_id):
    """Карточка проекта. Список проектов один на всех, поэтому запрашиваем его один раз за сборку."""
    if api_base not in _projects_cache:
        data = get_json(f"{api_base}/projects/?format=json")
        _projects_cache[api_base] = data.get("results", data) if isinstance(data, dict) else data
    for p in _projects_cache[api_base]:
        if p.get("id") == project_id:
            return p
    raise RuntimeError(f"Проект {project_id} не найден в API")


def fetch_details(api_base, flat_ids, workers=4, cache=None, now=None):
    """Карточки лотов (потолки, балконы, преимущества проекта) с кешем.

    cache: словарь {id лота: {"at": время, "d": нужные поля}}, дополняется на месте.
    Свежие записи берутся из кеша, на сайт уходят только устаревшие и новые лоты.
    Ошибка по одному лоту не роняет сборку: берется старая запись из кеша, если она есть.
    """
    cache = cache if cache is not None else {}
    now = now or time.time()
    max_age = DETAIL_MAX_AGE_DAYS * 86400
    todo = [fid for fid in flat_ids
            if str(fid) not in cache or now - cache[str(fid)].get("at", 0) > max_age]
    log.info("Карточки лотов: из кеша %d, запросить %d", len(flat_ids) - len(todo), len(todo))

    def one(fid):
        try:
            data = get_json(f"{api_base}/flats/{fid}/?format=json", retries=3, timeout=30)
            return fid, {"at": now, "d": {k: data.get(k) for k in DETAIL_FIELDS}}
        except Exception as e:
            log.warning("Карточка лота %s недоступна: %s", fid, e)
            return fid, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fid, entry in ex.map(one, todo):
            if entry:
                cache[str(fid)] = entry

    return {fid: (cache.get(str(fid)) or {}).get("d") for fid in flat_ids}
