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


# *** A FILE THAT DIFFS ACCRUES. A SERVER SHOWS YOU TODAY AND FORGETS. ***
# Same argument as findings-becoming-a-table rather than findings-becoming-a-report. So this is
# one file, no network, no build step, and DETERMINISTIC: it carries the manifest's own
# `generated_at` and never a wall clock, because a page that churns on every run cannot be
# committed and a page that cannot be committed cannot show you what moved.
# *** ONE RECORD STYLE, AND IT IS THE SOBER ONE. ***
# There used to be a second, colourful stylesheet for the default page. Once the record moved
# inside the explorer as a tab it became the only styled surface on a dense page of plain tables,
# which reads as an accident rather than as emphasis. A half-applied theme is worse than none.
PLAIN_CSS = """
:root{--deep:#ffffff; --blue:#ffffff; --sky:#5c6670;
  --gold:#1a1a1a; --amber:#8a6d1f; --ink:#1a1a1a;
  --barn:#8c1d18; --maroon:#8c1d18; --cream:#ffffff; --sage:#3f6b3f;}
*{box-sizing:border-box}
body{margin:0;color:#1a1a1a;background:#fff;
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:36px 24px 60px}
h1{margin:0;font-size:24px;font-weight:700;letter-spacing:-.3px;color:#1a1a1a}
h1 .small{display:block;font-size:14px;font-weight:400;color:#5c6670;margin-top:4px}
h2{font-size:11px;letter-spacing:1.3px;text-transform:uppercase;color:#5c6670;
  margin:32px 0 10px;font-weight:700;border-bottom:1px solid #d8dcdf;padding-bottom:6px}
.sub{color:#5c6670;margin-top:6px;font-size:13px}
.hero{display:flex;gap:16px;flex-wrap:wrap;margin:18px 0 4px}
.big{flex:1 1 300px;background:#fff;color:#1a1a1a;border:1px solid #d8dcdf;border-radius:4px;
  padding:16px 18px;box-shadow:none}
.big .n{font-size:40px;line-height:1;font-weight:700;color:#1a1a1a;letter-spacing:-1px}
.big .of{font-size:15px;color:#5c6670;font-weight:400}
.big .lab{font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:#5c6670;
  font-weight:700;margin-bottom:6px}
.big p{margin:10px 0 0;font-size:12.5px;color:#41494f;line-height:1.5}
.card{background:#fff;color:#1a1a1a;border:1px solid #d8dcdf;border-radius:4px;padding:12px 14px;
  box-shadow:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.stat{background:#fff;color:#1a1a1a;border:1px solid #d8dcdf;border-radius:4px;padding:11px 13px;
  box-shadow:none}
.stat b{display:block;font-size:22px;line-height:1.2;color:#1a1a1a;font-weight:700}
.stat span{font-size:12px;color:#5c6670}
.stat em{display:block;font-style:normal;font-size:11.5px;color:#7a838a;margin-top:4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#5c6670;
  padding:0 10px 6px 0;border-bottom:1px solid #b9c0c5}
td{padding:6px 10px 6px 0;border-bottom:1px solid #edeff1;vertical-align:top}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.bar{display:inline-block;height:8px;border-radius:2px;background:#8c1d18;vertical-align:middle}
.bar.ok{background:#3f6b3f} .bar.mid{background:#8a6d1f}
.tag{display:inline-block;padding:1px 7px;border-radius:3px;font-size:11px;font-weight:600;
  border:1px solid #b9c0c5;background:#f4f5f6;color:#41494f;white-space:nowrap}
.t-bad{background:#fdf0ef;color:#8c1d18;border-color:#e5c3c0}
.t-mid{background:#fdf7e8;color:#7a6415;border-color:#e6d9ae}
.t-ok{background:#f0f5f0;color:#2f5730;border-color:#c3d6c4}
.note{color:#5c6670;font-size:12.5px;margin:8px 0 0;max-width:80ch}
footer{color:#7a838a;font-size:11.5px;margin-top:34px;max-width:82ch;line-height:1.6;
  border-top:1px solid #d8dcdf;padding-top:14px}
footer b{color:#41494f}
a{color:#1a5fa8}
.empty{color:#7a838a;font-style:italic}
"""


def _pct(x) -> str:
    return "&mdash;" if x is None else f"{x:.0%}"


def _red(n) -> str:
    """A zero is not news and must not look like one. A non-zero is."""
    return f'<b style="color:#9e2b20">{n}</b>' if n else ""


def _tag(level: str, text: str) -> str:
    return f'<span class="tag t-{level}">{html.escape(text)}</span>'


def _bar(share: float, width: int = 96) -> str:
    cls = "ok" if share >= 0.8 else ("mid" if share >= 0.5 else "")
    return f'<span class="bar {cls}" style="width:{max(3, int(share * width))}px"></span>'


def page_html(data: dict) -> str:
    """The one question the rest of the tool is built to answer: is this warehouse understood?

    Not a dashboard of metrics. The ruled-on number goes first and largest because everything else
    on the page is downstream of whether anybody has actually read any of it -- and it is the only
    figure a release cannot improve, so a good release makes it look worse.
    """
    e = html.escape
    ruled, total = data["ruled"], data["findings_total"]
    share = (ruled / total) if total else 0.0

    eff = "".join(
        f"<tr><td class='mono'>{e(r['family'])}</td>"
        f"<td class='mono' style='color:#6b7a80'>{e(r['prompt_version'])}</td>"
        f"<td class='n'>{r['n']}</td>"
        f"<td class='n'>{_bar(r['agreement']) if r['agreement'] is not None else ''} "
        f"{_pct(r['agreement'])}</td>"
        f"<td class='n'>{r['unclear'] or ''}</td>"
        f"<td class='n'>{_red(r['open_disagreements'])}</td>"
        f"</tr>" for r in data["effectiveness"])

    by_check = "".join(
        f"<tr><td class='mono'>{e(c)}</td><td class='n'>{n}</td>"
        f"<td class='n'>{m}</td>"
        f"<td>{_tag('bad', f'0 of {n}') if not r else _tag('ok', f'{r} of {n}')}</td>"
        f"</tr>" for c, n, m, r in data["by_check"])

    comp = "".join(
        f"<tr><td>{e(label)}</td><td class='n'>"
        f"{_red(n) or chr(60) + 'span style=' + chr(34) + 'color:#6b7a80' + chr(34) + chr(62) + '0</span>'}"
        f"</td><td style='color:#4a5b63'>{e(why)}</td></tr>"
        for label, n, why in data["completeness"])

    top = "".join(
        f"<tr><td class='mono'>{e(f['check'])}</td>"
        f"<td class='mono'>{e(f['model'])}</td>"
        f"<td>{e(f['summary'][:130])}</td>"
        f"<td class='n'>{f['marts'] or ''}</td></tr>" for f in data["top_findings"])

    cl = data.get("claims") or {}
    claims_html = (
        f"<div class='grid'>"
        f"<div class='stat'><b>{cl['total']}</b><span>claims extracted</span>"
        f"<em>sentences from your own descriptions and comments</em></div>"
        f"<div class='stat'><b>{cl['supported']}</b><span>the code supports</span>"
        f"<em>still true after every edit so far</em></div>"
        f"<div class='stat'><b>{_red(cl['contradicted']) or 0}</b><span>the code contradicts</span>"
        f"<em>prose that stopped being true</em></div></div>"
        if cl.get("total") else
        "<div class='card empty'>No claims extracted yet. "
        "<span class='mono'>assay claims --extract</span> turns this project's own prose into "
        "claims, and <span class='mono'>assay verify</span> checks each one against the code. "
        "Nothing here is a pass &mdash; it has not been asked.</div>")

    moved = data.get("moved") or {}
    moved_html = (
        f"<div class='grid'>"
        f"<div class='stat'><b>{len(moved.get('new', []))}</b><span>appeared</span>"
        f"<em>since the previous recorded run</em></div>"
        f"<div class='stat'><b>{len(moved.get('gone', []))}</b><span>went away</span>"
        f"<em>fixed, or the check stopped seeing it</em></div>"
        f"<div class='stat'><b>{moved.get('same', 0)}</b><span>unchanged</span>"
        f"<em>still true and still unread unless ruled</em></div></div>"
        if moved else
        "<div class='card'>Only one run is recorded, so nothing can have moved yet. "
        "Run <span class='mono'>assay check</span> again after your next change.</div>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(data['project'])} &middot; assay</title>
<style>{PLAIN_CSS}</style></head><body>
<div class="wrap">
<h1>Is this warehouse understood?<span class="small">and by whom</span></h1>
<div class="sub">{e(data['project'])} &middot; {data['models']} models &middot;
manifest generated {e(data['generated_at'])}</div>

<div class="hero">
  <div class="big">
    <div class="lab">findings a person has ruled on</div>
    <div><span class="n">{ruled}</span> <span class="of">of {total}</span></div>
    <p>The only number here a release cannot improve. A sharper check finds more, a fuller
    state raises a confidence, the DAG moves the blast radius &mdash; none of that moves this,
    because it moves when somebody reads SQL and at no other time.
    <b>A good release makes it look worse.</b> That is the design working.</p>
  </div>
  <div class="big" style="flex:1 1 230px">
    <div class="lab">and by whom</div>
    <div><span class="n" style="color:#5a6a2f">{data['agent_rulings']}</span></div>
    <p>agent rulings, kept apart. They triage what a person should read first. They gate nothing,
    satisfy no verdict floor, anchor no regression check, and cannot move the number on the left.
    An agent able to raise it would destroy the property that makes it worth printing.</p>
  </div>
</div>
<p class="note">{share:.0%} of what assay currently sees has been read by a
person.</p>

<h2>Did the questions get better?</h2>
<div class="card">
<table><thead><tr><th>family</th><th>version</th><th class="n">ruled</th>
<th class="n">agreed</th><th class="n">unclear</th><th class="n">open</th></tr></thead>
<tbody>{eff or '<tr><td colspan="6" class="empty">No verdicts recorded yet, so nothing here '
                'can be measured. assay review -i is one keypress each, and until somebody '
                'presses one no question may fail a build.</td></tr>'}</tbody></table>
</div>
<p class="note">A verdict is about a <em>version</em> of a question, so agreement is per version.
Unclear is never in the denominator: disagreement means the criteria are wrong, unclear means the
state does not carry what the question asks. Reword an option to fix one, add a field to fix the
other.</p>

<h2>What is wrong, and how far it reaches</h2>
<div class="card">
<table><thead><tr><th>check</th><th class="n">findings</th><th class="n">deepest reach</th>
<th>read by a person</th></tr></thead><tbody>{by_check}</tbody></table>
</div>

<h2>What is one row of this?</h2>
<div class="grid">
  <div class="stat"><b>{data['grain']['declared']}</b><span>declared by a test</span>
  <em>a person wrote it down; the strongest kind</em></div>
  <div class="stat"><b>{data['grain']['derived']}</b><span>worked out from the SQL</span>
  <em>code settled it from a group by or a dedup</em></div>
  <div class="stat"><b>{data['grain']['judged']}</b><span>judged</span>
  <em>a model answered where code could not</em></div>
  <div class="stat"><b>{_red(data['grain']['none']) or data['grain']['none']}</b>
  <span>nothing settles it</span>
  <em>nobody knows what one row of these is</em></div>
</div>
<p class="note">Responsibility number one, and the one every other answer rests on. A grain a
person declared and one a judgement reached at 0.53 are not the same fact, so they are not counted
together. <b>{data['no_unique_test']}</b> model(s) have no uniqueness test at all.</p>

<h2>The findings themselves</h2>
<div class="card">
<table><thead><tr><th>check</th><th>model</th><th>what</th><th class="n">marts</th></tr></thead>
<tbody>{top}</tbody></table>
</div>
<p class="note">The {data['shown']} with the widest reach, of {data['findings_total']}. Severity is
arithmetic: the same defect on a leaf and on a model nine marts read are not the same finding.</p>

<h2>What this project claims about itself</h2>
{claims_html}

<h2>Do we have all of it?</h2>
<div class="card">
<table><thead><tr><th>coverage</th><th class="n">n</th><th>meaning</th></tr></thead>
<tbody>{comp}</tbody></table>
</div>
{data['not_counted_note']}
<p class="note">Coverage of what this project itself declares. assay can say a column is 99% its
default; it cannot say whether that is bad. The first is a fact about code and rows, the second is
a ruling.</p>

<h2>What moved</h2>
{moved_html}

<footer>
This file is self-contained and deterministic: it carries the manifest's own
<b>generated_at</b> and never a wall clock, so a rerun that changes nothing produces an identical
file. Commit it. A file that diffs accrues; a server shows you today and forgets.<br><br>
Written by <b>assay {e(data['version'])}</b>.
</footer>
</div></body></html>
"""
