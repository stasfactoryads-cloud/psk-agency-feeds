"""Главная страница публикации: ссылки на фиды по агентствам (аккордеон), статус, результат аудита."""

import html
import json
from datetime import datetime

from .config import Config

STATUS_LABEL = {"ok": "готов", "error": "есть ошибки"}


def write_site(cfg: Config, report):
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    (cfg.output_dir / ".nojekyll").touch()          # GitHub Pages: отдавать файлы как есть
    tmp = cfg.output_dir / "status.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    tmp.replace(cfg.output_dir / "status.json")
    (cfg.output_dir / "index.html").write_text(render_index(report, cfg), encoding="utf-8")


def fmt_ts(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M МСК")
    except (TypeError, ValueError):
        return iso


def _list(items, cls=""):
    return "".join(f'<li class="{cls}">{html.escape(i)}</li>' for i in items)


def copy_text(cfg: Config, agency_name, feeds):
    """Текст для вставки: описание, контакты и ссылки на фиды одного агентства."""
    p = cfg.portal
    lines = [f"Действующие фиды выгрузок объектов недвижимости от застройщика {p.developer}."]
    if p.site:
        lines.append(f"Сайт: {p.site}")
    if p.phone:
        lines.append(f"Единый телефон: {p.phone}")
    if p.manager:
        lines.append(f"{p.manager_role.capitalize()}: {p.manager}")
    lines += ["", f"Ссылки на фиды для «{agency_name}»:"]
    for f in feeds:
        title = f.get("project_title") or f"ЖК «{f['project_name']}»"
        lines.append(f"{title}: {f['url']}")
    if p.support_bot:
        lines += ["", "Если возникли технические проблемы, связанные с работой фидов и инфраструктуры, "
                      f"просим направлять запрос в телеграм-бот {p.support_bot}"]
    return "\n".join(lines)


def render_index(report, cfg: Config):
    e = html.escape
    audit = report.get("audit")
    p = cfg.portal

    groups = {}
    for f in report["feeds"]:
        groups.setdefault(f["slug"], {"name": f["agency"], "feeds": []})["feeds"].append(f)

    sections = ""
    for slug, g in groups.items():
        feeds, name = g["feeds"], g["name"]
        ok = all(f["status"] == "ok" for f in feeds)
        lots = sum(f["offers"] for f in feeds)
        rows = ""
        for f in feeds:
            title = f.get("project_title") or f"ЖК «{f['project_name']}»"
            rows += f"""<tr>
<td class="nw">{e(title)}</td>
<td class="url"><div class="link"><code>{e(f['url'])}</code>
<button type="button" class="copy" data-copy="{e(f['url'])}">Копировать</button></div>
<a href="{e(f['url'])}">открыть XML</a> · <a href="{e(f['preview'])}">превью лотов</a></td>
<td class="num">{f['offers']}</td>
<td><span class="badge {f['status']}">{STATUS_LABEL.get(f['status'], f['status'])}</span></td>
</tr>"""
        sections += f"""<details class="agency" id="{e(slug)}" data-slug="{e(slug)}">
<summary>
<span class="chev" aria-hidden="true"></span>
<span class="who"><b>{e(name)}</b><span class="muted">объектов: {len(feeds)}, лотов: {lots}{'' if feeds[0].get('include_booked', True) else ', без лотов в брони'}</span></span>
<span class="badge {'ok' if ok else 'error'}">{'готов' if ok else 'есть ошибки'}</span>
<button type="button" class="copy all" data-copy="{e(copy_text(cfg, name, feeds), quote=True)}">Скопировать все ссылки</button>
</summary>
<div class="wrap"><table>
<tr><th>Объект</th><th>Ссылка на фид</th><th class="num">Лотов</th><th>Статус</th></tr>
{rows}</table></div>
</details>"""

    lots_total = ", ".join(f"«{e(pr['name'])}» {pr['lots']}" for pr in report.get("projects", []))

    contacts = []
    if p.site:
        contacts.append(f'<a href="{e(p.site)}">{e(p.site)}</a>')
    if p.phone:
        contacts.append(e(p.phone))
    if p.manager:
        contacts.append(f"{e(p.manager_role)}: {e(p.manager)}")
    contacts_html = " · ".join(contacts)
    support_html = ""
    if p.support_bot:
        handle = p.support_bot.lstrip("@")
        support_html = (f'<p class="muted">Технические проблемы с фидами и инфраструктурой: '
                        f'<a href="https://t.me/{e(handle)}">{e(p.support_bot)}</a></p>')

    tf = sum(pr["free"] for pr in report.get("projects", []))
    tb = sum(pr["booked"] for pr in report.get("projects", []))

    def delta(now, was):
        if was is None:
            return '<span class="muted" title="нет данных прошлой сборки">–</span>'
        if now == was:
            return '<span class="muted">0</span>'
        d = now - was
        return f'<span class="{"up" if d > 0 else "down"}">{"+" if d > 0 else "−"}{abs(d)}</span>'

    status_rows = ""
    for pr in report.get("projects", []):
        prev = pr.get("prev") or {}
        status_rows += f"""<tr><td class="nw">{e(pr.get('title') or pr['name'])}</td>
<td class="num">{pr['free']}</td><td class="num">{delta(pr['free'], prev.get('free'))}</td>
<td class="num">{pr['booked']}</td><td class="num">{delta(pr['booked'], prev.get('booked'))}</td>
<td class="num"><b>{pr['free'] + pr['booked']}</b></td></tr>"""
    status_rows += f"""<tr class="total"><td><b>Всего</b></td><td class="num"><b>{tf}</b></td><td></td>
<td class="num"><b>{tb}</b></td><td></td><td class="num"><b>{tf + tb}</b></td></tr>"""
    status_html = f"""<details class="statuses" open><summary><span class="chev" aria-hidden="true"></span>
<span class="who"><b>Статусы квартир</b><span class="muted">данные сайта на момент сборки, изменения с прошлой сборки</span></span></summary>
<div class="wrap"><table class="st"><tr><th>Объект</th><th class="num">Свободно</th><th class="num">Δ</th>
<th class="num">Бронь</th><th class="num">Δ</th><th class="num">Всего в сборке</th></tr>{status_rows}</table></div></details>"""

    if audit:
        audit_html = f"""<details class="audit"><summary><b>Аудит</b>
<span class="badge {audit['status']}">{STATUS_LABEL[audit['status']]}</span>
<span class="muted">проверено {e(fmt_ts(audit['checked_at']))}</span></summary>
<ul>{_list(audit['checks'], 'ok')}{_list(audit['errors'], 'error')}{_list(audit['warnings'], 'warn')}</ul></details>"""
    else:
        audit_html = '<p class="muted">Аудит еще не проводился.</p>'

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Фиды ГК «ПСК» для агентств</title>
<style>
:root{{--bg:#f6f6f6;--card:#fff;--text:#111;--muted:#666;--line:#e2e2e2;--ok:#1a7f37;--err:#b3261e;--warn:#9a6700}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151515;--card:#1f1f1f;--text:#f2f2f2;--muted:#a0a0a0;--line:#333;--ok:#4ac26b;--err:#ff7b72;--warn:#d29922}}}}
*{{box-sizing:border-box}}body{{font:15px/1.5 system-ui,sans-serif;color:var(--text);background:var(--bg);margin:0;padding:24px 16px}}
main{{max-width:1100px;margin:0 auto}}h1{{margin:0 0 4px}}.muted{{color:var(--muted)}}p.muted{{margin:0 0 8px}}a{{color:var(--text)}}
.bar{{display:flex;gap:8px;margin:16px 0 12px;flex-wrap:wrap}}
button{{font:inherit;font-size:13px;padding:4px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--text);cursor:pointer;white-space:nowrap}}
button:hover{{border-color:var(--muted)}}button.done{{border-color:var(--ok);color:var(--ok)}}button.fail{{border-color:var(--err);color:var(--err)}}
details.agency,details.audit,details.statuses{{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:0 0 10px}}
details>summary{{list-style:none;cursor:pointer;display:flex;align-items:center;gap:12px;padding:12px 16px;flex-wrap:wrap}}
details>summary::-webkit-details-marker{{display:none}}
.chev{{width:8px;height:8px;border-right:2px solid var(--muted);border-bottom:2px solid var(--muted);transform:rotate(-45deg);transition:transform .15s;flex:none}}
details[open]>summary .chev{{transform:rotate(45deg)}}
.who{{display:flex;flex-direction:column;flex:1;min-width:180px;line-height:1.3}}.who b{{font-size:16px}}.who .muted{{font-size:13px}}
.wrap{{overflow-x:auto;padding:0 16px 12px}}table{{border-collapse:collapse;width:100%;min-width:760px}}
td,th{{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}th{{font-weight:600;color:var(--muted)}}
.num{{text-align:right}}.nw{{white-space:nowrap}}td.url{{width:100%}}
.link{{display:flex;gap:8px;align-items:center;margin:0 0 4px}}.link code{{flex:1;min-width:0}}
code{{background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:3px 6px;word-break:break-all;font-size:13px}}
.badge{{font-size:13px;padding:1px 8px;border-radius:10px;border:1px solid currentColor;white-space:nowrap}}
.badge.ok,li.ok{{color:var(--ok)}}.badge.error,li.error{{color:var(--err)}}li.warn{{color:var(--warn)}}
details.audit ul{{margin:0;padding:0 16px 12px 36px}}
table.st{{min-width:560px}}tr.total td{{border-top:2px solid var(--line)}}.up{{color:var(--ok)}}.down{{color:var(--err)}}
@media (max-width:640px){{details>summary{{gap:8px}}button.all{{width:100%}}}}
</style></head><body><main>
<h1>Фиды ГК «ПСК» для агентств недвижимости</h1>
<p class="muted">Формат YRL, техтребования Яндекс Недвижимости. Обновляются ежедневно в 12:00 МСК.
Последняя сборка: {e(fmt_ts(report['generated_at']))}. Квартир в фидах: {tf + tb}, из них свободно {tf}, в брони {tb}.</p>
<p class="muted">{contacts_html}</p>
{support_html}
<div class="bar">
<button type="button" id="open-all">Раскрыть все</button>
<button type="button" id="close-all">Свернуть все</button>
</div>
{status_html}
{sections}
{audit_html}
<p class="muted">Данные в JSON: <a href="status.json">status.json</a></p>
</main>
<script>
function flash(btn, text, ok) {{
  const old = btn.dataset.label || btn.textContent;
  btn.dataset.label = old;
  btn.textContent = text; btn.classList.add(ok ? 'done' : 'fail');
  setTimeout(() => {{ btn.textContent = old; btn.classList.remove('done', 'fail'); }}, 1600);
}}
async function copyText(text) {{
  try {{ await navigator.clipboard.writeText(text); return true; }} catch (e) {{}}
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
  ta.remove();
  return ok;
}}
document.querySelectorAll('button.copy').forEach(b => b.addEventListener('click', async ev => {{
  ev.preventDefault(); ev.stopPropagation();           // не раскрывать аккордеон по клику на кнопку
  const ok = await copyText(b.dataset.copy);
  flash(b, ok ? 'Скопировано' : 'Не удалось скопировать', ok);
  if (!ok) window.prompt('Скопируйте текст вручную (Ctrl+C / Cmd+C):', b.dataset.copy);
}}));
const agencies = [...document.querySelectorAll('details.agency')];
const KEY = 'psk-feeds-open';
function saveState() {{
  try {{ localStorage.setItem(KEY, JSON.stringify(agencies.filter(d => d.open).map(d => d.dataset.slug))); }} catch (e) {{}}
}}
try {{
  const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
  if (Array.isArray(saved)) agencies.forEach(d => {{ d.open = saved.includes(d.dataset.slug); }});
}} catch (e) {{}}
if (location.hash) {{ const t = document.querySelector(location.hash); if (t && t.tagName === 'DETAILS') t.open = true; }}
agencies.forEach(d => d.addEventListener('toggle', saveState));
document.getElementById('open-all').addEventListener('click', () => {{ agencies.forEach(d => d.open = true); }});
document.getElementById('close-all').addEventListener('click', () => {{ agencies.forEach(d => d.open = false); }});
</script></body></html>"""
