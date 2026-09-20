"""The inventory as something you can look at.

*** dbt DOCS SHOWS YOU LINEAGE. NOTHING SHOWS YOU MEANING. ***
A terminal table is fine for scanning and useless for reading, sending or diffing. This is one
self-contained file: every model, what one row is, what each column does, where each value came
from, and -- the part that matters -- WHO SAID SO. A grain a human declared and one a judgment
reached at 0.53 must never look alike, so they do not.

No JavaScript framework, no build step, no network. It opens from a file:// URL and it can be
committed, so a change in what your warehouse MEANS shows up as a diff.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

SOURCE_COLOUR = {
    "declared": ("#1f6f43", "a human wrote it down"),
    "observed": ("#1d4e89", "the probe counted it"),
    "derived": ("#6b5b1f", "code worked it out"),
    "judged": ("#6d3b8e", "a model answered"),
    "unknown": ("#5a5a5a", "nothing settles it"),
}

CSS = """
:root{--bg:#0d1117;--fg:#e6edf3;--dim:#8b949e;--line:#30363d;--card:#161b22}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Helvetica,Arial,sans-serif}
header{padding:28px 32px 18px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:22px;letter-spacing:-.2px}
.sub{color:var(--dim);margin-top:6px}
.wrap{padding:22px 32px 60px}
input[type=search]{width:100%;max-width:520px;padding:9px 12px;border-radius:7px;
border:1px solid var(--line);background:var(--card);color:var(--fg);font-size:14px}
.counts{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0 26px}
.counts div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
.counts b{display:block;font-size:19px}
.counts span{color:var(--dim);font-size:12px}
details{background:var(--card);border:1px solid var(--line);border-radius:9px;margin-bottom:10px}
summary{cursor:pointer;padding:12px 16px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
summary::-webkit-details-marker{display:none}
.name{font-weight:600}
.grain{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.reach{color:var(--dim);font-size:12px;margin-left:auto}
.body{padding:0 16px 16px}
.desc{color:var(--dim);margin:2px 0 14px}
.warn{margin:2px 0 12px;padding:8px 11px;border-left:3px solid #d29922;background:#2b220c;
 border-radius:0 5px 5px 0;color:#e3b341}
.warn b{color:#f0c674;font-weight:600}
.dflag{font-size:11px;padding:2px 7px;border-radius:99px;background:#2b220c;color:#e3b341;
 border:1px solid #6b4f10}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:500;padding:5px 10px 5px 0;
border-bottom:1px solid var(--line)}
td{padding:5px 10px 5px 0;border-bottom:1px solid #21262d;vertical-align:top}
td.col{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.key{color:#e3b341}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;color:#fff;
white-space:nowrap}
.hidden{display:none}
footer{color:var(--dim);padding:0 32px 40px;font-size:12px;max-width:80ch}
"""

JS = """
const box=document.getElementById('q'),cards=[...document.querySelectorAll('details')];
box.addEventListener('input',()=>{const t=box.value.toLowerCase();
cards.forEach(c=>c.classList.toggle('hidden',t&&!c.dataset.hay.includes(t)));});
"""


def _pill(source: str, confidence=None) -> str:
    colour, why = SOURCE_COLOUR.get(source, SOURCE_COLOUR["unknown"])
    label = source if confidence is None else f"{source} {confidence:.2f}"
    return f'<span class="pill" style="background:{colour}" title="{why}">{label}</span>'


def inventory_html(entries, project_name: str, describe) -> str:
    counts: dict = {}
    for e in entries:
        counts[e.grain.source if e.grain else "unsettled"] = \
            counts.get(e.grain.source if e.grain else "unsettled", 0) + 1
    ncols = sum(len(e.columns) for e in entries)
    roles = sum(1 for e in entries for c in e.columns if c.role)
    drift = sum(1 for e in entries
                if e.doc_conflict and (e.doc_conflict.confidence or 0) >= 0.6)

    cards = []
    for e in sorted(entries, key=lambda x: (-x.marts, x.name)):
        if e.unreadable:
            continue
        grain = ", ".join(e.grain.value) if e.grain and isinstance(e.grain.value, list) else (
            str(e.grain.value) if e.grain else "unsettled")
        rows = []
        for c in e.columns:
            nm = f'<span class="key">{html.escape(c.name)}</span>' if c.in_key \
                else html.escape(c.name)
            role = (_pill("judged", c.role.confidence) + " " + html.escape(str(c.role.value))
                    if c.role else '<span style="color:#484f58">-</span>')
            null = html.escape(str(c.null_meaning.value)) if c.null_meaning else ""
            rows.append(
                f"<tr><td class='col'>{nm}</td><td>{role}</td>"
                f"<td>{html.escape(str(c.provenance.value))}</td><td>{null}</td></tr>")
        # *** THE PROSE WARNING BELONGS BESIDE THE PROSE. ***
        # A reader who opens this page to find out what a model means is exactly the reader who
        # must be told that its description has stopped being true. Put it anywhere else and they
        # read the sentence above it and believe it.
        dc = e.doc_conflict
        flag = (f'<p class="warn">Its description no longer matches its code '
                f'<b>&middot; judged {dc.confidence:.2f}</b></p>'
                if dc and (dc.confidence or 0) >= 0.6 else "")
        hay = " ".join([e.name, e.layer, grain] + [c.name for c in e.columns]).lower()
        cards.append(f"""<details data-hay="{html.escape(hay)}">
<summary><span class="name">{html.escape(e.name)}</span>
<span class="grain">{html.escape(grain)}</span>
{_pill(e.grain.source, e.grain.confidence) if e.grain else _pill("unknown")}
{'<span class="dflag">description drift</span>' if flag else ''}
<span class="reach">{e.descendants} downstream &middot; {e.marts} marts</span></summary>
<div class="body">{flag}<p class="desc">{html.escape(describe(e))}</p>
<table><tr><th>column</th><th>role</th><th>value comes from</th><th>null means</th></tr>
{''.join(rows)}</table></div></details>""")

    chips = "".join(
        f"<div><b>{v}</b><span>grain {k}</span></div>" for k, v in sorted(counts.items()))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(project_name)} &middot; what it means</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head>
<body><header><h1>{html.escape(project_name)}</h1>
<div class="sub">What every model actually is. Generated by assay, {now}.</div></header>
<div class="wrap"><input id="q" type="search" placeholder="filter by model, column or grain">
<div class="counts"><div><b>{len(entries)}</b><span>models</span></div>
<div><b>{ncols:,}</b><span>columns</span></div>
<div><b>{roles:,}</b><span>roles judged</span></div>
{f'<div><b>{drift}</b><span>descriptions drifted</span></div>' if drift else ''}{chips}</div>
{''.join(cards)}</div>
<footer>Every cell says where it came from. <b>declared</b> means a human wrote it down,
<b>observed</b> means the probe counted it, <b>derived</b> means code worked it out from the SQL and
the DAG, and <b>judged</b> carries the probability a model answered with. A judgement is not a
fact, and nothing here pretends otherwise.</footer>
<script>{JS}</script></body></html>"""
