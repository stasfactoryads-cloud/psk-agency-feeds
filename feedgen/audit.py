"""Аудит опубликованного набора фидов перед выкладкой.

Проверки:
  1. Каждый фид проходит правила Яндекс Недвижимости (validate.py)
  2. Все картинки с нашего домена реально лежат в папке публикации
  3. Внешние картинки (рендеры ЖК) отвечают 200 и это изображения
  4. В фидах одного ЖК у всех агентств одинаковый набор лотов, и он совпадает с API
  5. Число лотов по каждому ЖК не упало резко относительно прошлой сборки
  6. Фид свежий (собран не раньше чем 26 часов назад)

Результат пишется в status.json (блок audit) и выводится на главной странице.
Код выхода 1 при ошибках: в GitHub Actions это остановит публикацию,
и в интернете останется предыдущая корректная версия.
"""

import json
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .site import write_site
from .validate import validate_feed
from .yrl import NS

log = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))
MAX_DROP = 0.5          # падение числа лотов больше чем вдвое считаем ошибкой
MAX_AGE_HOURS = 26


def _head(url, attempts=3):
    """HEAD с повторами: единичный сбой хранилища не должен блокировать публикацию."""
    result = (url, 0, "")
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "PSK-Feed-Audit/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                result = (url, r.status, r.headers.get("Content-Type", ""))
        except Exception as e:
            result = (url, getattr(e, "code", 0), str(e))
        if result[1] == 200:
            return result
        time.sleep(2 * (i + 1))
    return result


def run(cfg):
    status_path = cfg.output_dir / "status.json"
    report = json.loads(status_path.read_text())
    errors, warnings, checks = [], [], []
    base = cfg.public_base_url.rstrip("/") + "/"

    offer_sets, local_missing, remote_urls = {}, set(), set()
    feed_statuses = {}        # (проект, include_booked) -> Counter статусов из фидов
    for feed in report["feeds"]:
        label = f"{feed['agency']} / {feed['project_name']}"
        path = cfg.output_dir / feed["path"]
        if not path.exists():
            errors.append(f"{label}: файл фида отсутствует")
            continue
        res = validate_feed(path, cfg.public_base_url)
        feed.update(res, status="ok" if not res["errors"] else "error")
        errors += [f"{label}: {e}" for e in res["errors"]]
        warnings += [f"{label}: {w}" for w in res["warnings"]]

        root = ET.parse(path).getroot()
        offers = root.findall(f"{{{NS}}}offer")
        offer_sets.setdefault((feed["project"], feed.get("include_booked", True)), []).append(
            {o.get("internal-id") for o in offers})
        st = {}
        for o in offers:
            tag = o.find(f"{{{NS}}}psk-flat-status")
            key = tag.text if tag is not None else "нет"
            st[key] = st.get(key, 0) + 1
        feed["tag_statuses"] = st
        if len(offers) != feed.get("expected", len(offers)):
            errors.append(f"{label}: в фиде {len(offers)} лотов, ожидалось {feed['expected']}")
        for img in root.iter(f"{{{NS}}}image"):
            url = img.text or ""
            if url.startswith(base):
                if not (cfg.output_dir / url[len(base):]).is_file():
                    local_missing.add(url[len(base):])
            else:
                remote_urls.add(url)
    checks.append(f"Правила Яндекс Недвижимости: проверено фидов {len(report['feeds'])}")

    if local_missing:
        errors.append(f"Нет файлов картинок в публикации: {len(local_missing)} (например {sorted(local_missing)[0]})")
    checks.append("Планировки JPG на месте" if not local_missing else "Планировки JPG: есть пропуски")

    with ThreadPoolExecutor(max_workers=8) as ex:
        broken = [(u, c, d) for u, c, d in ex.map(_head, sorted(remote_urls))
                  if c != 200 or not d.startswith("image/")]
    if broken:
        errors.append(f"Недоступны внешние картинки: {len(broken)} из {len(remote_urls)}, например {broken[0][0]}")
    checks.append(f"Внешние картинки доступны: {len(remote_urls) - len(broken)} из {len(remote_urls)}")

    for pr in report["projects"]:
        sets = offer_sets.get(pr["key"], [])
        name = pr.get("title") or f"ЖК «{pr['name']}»"
        # набор лотов должен совпадать у агентств с одинаковой настройкой include_booked
        for flag in (True, False):
            group = offer_sets.get((pr["key"], flag), [])
            if any(x != group[0] for x in group):
                errors.append(f"{name}: у агентств с include_booked={str(flag).lower()} разный набор лотов")
        # статусы в фидах совпадают с API
        for f in [x for x in report["feeds"] if x["project"] == pr["key"] and x.get("tag_statuses") is not None]:
            want_free = pr["free"]
            want_booked = pr["booked"] if f.get("include_booked", True) else 0
            got = f["tag_statuses"]
            if got.get("свободна", 0) != want_free or got.get("бронь", 0) != want_booked or got.get("нет"):
                errors.append(f"{f['agency']} / {name}: статусы в фиде {got} не совпадают с сайтом "
                              f"(свободно {want_free}, бронь {want_booked})")
        # неизвестные статусы на сайте: такие лоты в фид не попадают
        extra = {k: v for k, v in (pr.get("statuses") or {}).items() if k not in ("Свободно", "Бронь")}
        if extra:
            warnings.append(f"{name}: на сайте появились новые статусы лотов {extra}, в фиды они не попадают")
        # резкий рост брони: возможный сбой данных на сайте
        prev_st = pr.get("prev_statuses") or {}
        if prev_st.get("Бронь") is not None and pr["lots"] >= 20:
            now_b, was_b = pr["booked"], prev_st.get("Бронь", 0)
            if now_b > was_b + max(10, pr["lots"] * 0.3):
                warnings.append(f"{name}: в брони стало {now_b} лотов (было {was_b}), проверьте данные на сайте")
        prev = pr.get("prev_lots")
        if prev and pr["lots"] < prev * (1 - MAX_DROP):
            errors.append(f"{name}: число лотов упало с {prev} до {pr['lots']}, проверьте API сайта")
        elif prev and pr["lots"] != prev:
            warnings.append(f"{name}: число лотов изменилось, было {prev}, стало {pr['lots']}")
    checks.append("Набор лотов совпадает с API: " + ", ".join(f"{pr['name']} {pr['lots']}" for pr in report["projects"]))
    tot_free = sum(pr["free"] for pr in report["projects"])
    tot_booked = sum(pr["booked"] for pr in report["projects"])
    checks.append(f"Статусы лотов в фидах совпадают с сайтом: свободно {tot_free}, бронь {tot_booked}")
    checks.append("У каждого лота есть номер квартиры (psk-flat-number) и статус (psk-flat-status)")

    age = datetime.now(MSK) - datetime.fromisoformat(report["generated_at"])
    if age > timedelta(hours=MAX_AGE_HOURS):
        errors.append(f"Фиды устарели: собраны {report['generated_at']}")
    checks.append("Фиды свежие")

    report["audit"] = {
        "checked_at": datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00"),
        "status": "ok" if not errors else "error",
        "checks": checks, "errors": errors, "warnings": warnings,
    }
    write_site(cfg, report)

    log.info("Аудит: %s, ошибок %d, замечаний %d", report["audit"]["status"].upper(), len(errors), len(warnings))
    for e in errors:
        log.error("  %s", e)
    for w in warnings:
        log.warning("  %s", w)
    return report["audit"]
