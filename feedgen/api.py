"""Клиент API psk-info.ru: список лотов, карточка лота, карточка проекта."""

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

PAGE_SIZE = 500
USER_AGENT = "PSK-Agency-Feeds/1.0"


def get_json(url, retries=3, timeout=60):
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
            time.sleep(2 * (attempt + 1))
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


def fetch_project(api_base, project_id):
    data = get_json(f"{api_base}/projects/?format=json")
    items = data.get("results", data) if isinstance(data, dict) else data
    for p in items:
        if p.get("id") == project_id:
            return p
    raise RuntimeError(f"Проект {project_id} не найден в API")


def fetch_details(api_base, flat_ids, workers=8):
    """Карточки лотов (потолки, балконы, преимущества проекта). Ошибка по одному лоту не роняет сборку."""
    def one(fid):
        try:
            return fid, get_json(f"{api_base}/flats/{fid}/?format=json", retries=2, timeout=30)
        except Exception as e:
            log.warning("Карточка лота %s недоступна: %s", fid, e)
            return fid, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, flat_ids))
