"""Главная страница публикации: прямые ссылки на фиды, статус, результат аудита."""

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
    (cfg.output_dir / "index.html").write_text(render_index(report), encoding="utf-8")


def fmt_ts(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M МСК")
    except (TypeError, ValueError):
        return iso


def _list(items, cls=""):
    return "".join(f'<li class="{cls}">{html.escape(i)}</li>' for i in items)


def render_index(report):
    e = html.escape
    audit = report.get("audit")
    agencies = {}
    for f in report["feeds"]:
        agencies.setdefault(f["agency"], []).append(f)
    rows = ""
    for agency, feeds in agencies.items():
        for i, f in enumerate(feeds):
            first = f'<td rowspan="{len(feeds)}"><b>{e(agency)}</b></td>' if i == 0 else ""
            rows += f"""<tr class="{'grp' if i == 0 else ''}">{first}
<td class="nw">{e(f.get('project_title') or 'ЖК «' + f['project_name'] + '»')}</td>
<td class="url"><div class="link"><code>{e(f['url'])}</code>
<button type="button" data-url="{e(f['url'])}">Копировать</button></div>
<a href="{e(f['url'])}">открыть XML</a> · <a href="{e(f['preview'])}">превью лотов</a></td>
<td class="num">{f['offers']}</td>
<td><span class="badge {f['status']}">{STATUS_LABEL.get(f['status'], f['status'])}</span></td>
</tr>"""
    lots = ", ".join(f"«{e(p['name'])}» {p['lots']}" for p in report.get("projects", []))

    if audit:
        audit_html = f"""<section><h2>Аудит <span class="badge {audit['status']}">{STATUS_LABEL[audit['status']]}</span></h2>
<p class="muted">Проверено: {e(fmt_ts(audit['checked_at']))}</p>
<ul>{_list(audit['checks'], 'ok')}{_list(audit['errors'], 'error')}{_list(audit['warnings'], 'warn')}</ul></section>"""
    else:
        audit_html = '<section><h2>Аудит</h2><p class="muted">Еще не проводился.</p></section>'

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Фиды ГК «ПСК» для агентств</title>
<style>
:root{{--bg:#f6f6f6;--card:#fff;--text:#111;--muted:#666;--line:#e2e2e2;--ok:#1a7f37;--err:#b3261e;--warn:#9a6700}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151515;--card:#1f1f1f;--text:#f2f2f2;--muted:#a0a0a0;--line:#333;--ok:#4ac26b;--err:#ff7b72;--warn:#d29922}}}}
*{{box-sizing:border-box}}body{{font:15px/1.5 system-ui,sans-serif;color:var(--text);background:var(--bg);margin:0;padding:24px 16px}}
main{{max-width:1100px;margin:0 auto}}section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:0 0 16px}}
h1{{margin:0 0 4px}}h2{{margin:0 0 8px;font-size:18px}}.muted{{color:var(--muted);margin:0 0 16px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}
th{{font-weight:600;color:var(--muted)}}.num{{text-align:right}}a{{color:var(--text)}}
.wrap{{overflow-x:auto}}tr.grp td{{border-top:2px solid var(--line)}}
table{{min-width:820px}}.nw{{white-space:nowrap}}td.url{{width:100%}}.link code{{flex:1;min-width:0}}.link{{display:flex;gap:8px;align-items:center;margin:0 0 4px}}
code{{background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:3px 6px;word-break:break-all;font-size:13px}}
button{{font:inherit;font-size:13px;padding:3px 10px;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--text);cursor:pointer;white-space:nowrap}}
.badge{{font-size:13px;padding:1px 8px;border-radius:10px;border:1px solid currentColor}}.badge.ok,li.ok{{color:var(--ok)}}.badge.error,li.error{{color:var(--err)}}li.warn{{color:var(--warn)}}
ul{{margin:0;padding-left:20px}}
</style></head><body><main>
<h1>Фиды ГК «ПСК» для агентств недвижимости</h1>
<p class="muted">Формат YRL, техтребования Яндекс Недвижимости. Обновляются ежедневно в 12:00 МСК.
Последняя сборка: {e(fmt_ts(report['generated_at']))}. Лотов в продаже: {lots}.</p>
<section><h2>Прямые ссылки</h2><div class="wrap"><table>
<tr><th>Агентство</th><th>ЖК</th><th>Ссылка на фид</th><th class="num">Лотов</th><th>Статус</th></tr>
{rows}</table></div></section>
{audit_html}
<p class="muted">Данные в JSON: <a href="status.json">status.json</a></p>
</main>
<script>
document.querySelectorAll('button[data-url]').forEach(b=>b.addEventListener('click',async()=>{{
  try{{await navigator.clipboard.writeText(b.dataset.url);b.textContent='Скопировано'}}catch(e){{b.textContent='Выделите вручную'}}
  setTimeout(()=>b.textContent='Копировать',1500)}}));
</script></body></html>"""
