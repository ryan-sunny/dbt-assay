"""The explorer: one self-contained file holding everything assay knows, with tabs.

*** WHY ONE FILE AND NOT A DIRECTORY, WHICH IS NOT A STYLE PREFERENCE. ***
Browsers block `fetch` and XHR on `file://` URLs. A directory of HTML plus JSON that loads a
model when you click it cannot be opened by double-clicking it -- that option does not exist
locally, so an artifact you open from disk must carry its data inside it. The moment lazy loading
is wanted, a server is required; there is no middle rung. Measured at roughly 24 KB per model, so
a 358-model warehouse is about 9 MB, which opens instantly, and a 2,000-model one would not.

*** SO THE DATA SOURCE IS ONE SWAPPABLE LINE. ***
Everything expensive here -- the tables, the filtering, the model detail -- reads from one object
called DATA. Embedded, that object is parsed out of a script tag. Served, it would be one `fetch`.
Building the file therefore builds most of the server, and the decision to add `assay serve` can
be made later for a fraction of this work rather than a rewrite.

*** AND IT STAYS DETERMINISTIC. ***
No wall clock, no random, and every array arrives pre-sorted from `explore.assemble`. Two runs
against an unchanged store write identical bytes. What changed is that the FILE being identical
no longer means the RENDERED page is, because script decides what is shown -- which is why the
sorting lives in the assembly layer where a test can assert it, and not in the browser.
"""
from __future__ import annotations

import html
import json

CSS = """
:root{--ink:#16232a;--dim:#6b7a80;--faint:#94a3aa;--line:#dfe6e8;--bg:#fbfcfc;
--card:#fff;--red:#9e2b20;--green:#5a6a2f;--blue:#2b5c7a;--amber:#8a6412}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
color:var(--ink);background:var(--bg)}
header{padding:18px 24px 0;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0;font-size:19px;font-weight:650;letter-spacing:-.01em}
h1 span{font-weight:400;color:var(--dim);font-size:14px;margin-left:8px}
.sub{color:var(--dim);font-size:12.5px;margin:3px 0 14px}
nav{display:flex;gap:2px;flex-wrap:wrap}
nav button{appearance:none;border:1px solid transparent;border-bottom:none;background:none;
font:inherit;font-size:13px;color:var(--dim);padding:7px 13px;cursor:pointer;
border-radius:5px 5px 0 0;margin-bottom:-1px}
nav button:hover{color:var(--ink);background:var(--bg)}
nav button[aria-selected=true]{color:var(--ink);font-weight:600;background:var(--bg);
border-color:var(--line);border-bottom:1px solid var(--bg)}
nav button b{font-weight:500;color:var(--faint);margin-left:5px;font-size:11.5px}
main{padding:18px 24px 60px;max-width:1500px}
.panel[hidden]{display:none}
.bar{display:flex;gap:9px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
input[type=search],select{font:inherit;font-size:13px;padding:6px 9px;border:1px solid var(--line);
border-radius:6px;background:var(--card);color:var(--ink);min-width:150px}
input[type=search]{min-width:290px}
.count{color:var(--dim);font-size:12.5px}
table{border-collapse:collapse;width:100%;background:var(--card);font-size:13px}
th{text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:var(--faint);padding:7px 9px;border-bottom:1px solid var(--line);position:sticky;top:0;
background:var(--card);cursor:pointer;white-space:nowrap}
th:hover{color:var(--ink)}
td{padding:6px 9px;border-bottom:1px solid #f0f4f5;vertical-align:top}
tr.pick{cursor:pointer}
tr.pick:hover td{background:#f4f8f9}
tr.on td{background:#eef5f8}
.n{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.wrap2{display:grid;grid-template-columns:minmax(260px,1fr) minmax(0,2.1fr);gap:16px;
align-items:start}
.list{max-height:78vh;overflow:auto;border:1px solid var(--line);border-radius:8px}
.detail{border:1px solid var(--line);border-radius:8px;background:var(--card);padding:16px 18px;
max-height:78vh;overflow:auto}
.detail h2{margin:0 0 2px;font-size:17px}
.detail h3{margin:20px 0 7px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:var(--faint);font-weight:600}
.detail .path{color:var(--dim);font-size:12px;margin-bottom:10px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;font-size:13px}
.kv dt{color:var(--dim)}
.kv dd{margin:0}
.pill{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:9px;
border:1px solid var(--line);color:var(--dim);background:var(--bg);white-space:nowrap}
.pill.declared{color:var(--green);border-color:#cfd9b8}
.pill.derived{color:var(--blue);border-color:#c3d6e0}
.pill.judged{color:var(--amber);border-color:#e2d3ab}
.pill.observed{color:var(--blue);border-color:#c3d6e0}
.pill.bad{color:var(--red);border-color:#e3c4c0}
.pill.on{color:var(--green);border-color:#cfd9b8}
.note{color:var(--dim);font-size:12.5px;margin:8px 0 0;max-width:none}
.empty{color:var(--faint);padding:14px 9px;font-size:13px}
.prose{white-space:pre-wrap;font-size:13px;color:#33454d;margin:0}
details{margin:5px 0}
summary{cursor:pointer;color:var(--dim);font-size:12.5px}
pre{background:#f6f9fa;border:1px solid var(--line);border-radius:6px;padding:9px 11px;
overflow:auto;font-size:11.5px;margin:6px 0;white-space:pre-wrap;word-break:break-word}
.tot{color:var(--faint)}
.bad{color:var(--red);font-weight:600}
.ok{color:var(--green)}
.low{color:var(--amber);font-weight:600}
a.lk{color:var(--blue);text-decoration:none;border-bottom:1px dotted #b9ccd6}
a.lk:hover{border-bottom-style:solid}
button.back{appearance:none;border:1px solid var(--line);background:var(--card);font:inherit;
font-size:12.5px;color:var(--blue);padding:4px 10px;border-radius:6px;cursor:pointer;
margin:0 8px 10px 0}
button.back:hover{background:#f2f7f9}
.crumb{color:var(--dim);font-size:13px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
.chips.flat{margin:6px 0 0}
.chip{appearance:none;font:inherit;font-size:12px;border:1px solid var(--line);background:var(--card);
border-radius:6px;padding:3px 9px;cursor:pointer;display:inline-flex;gap:7px;align-items:center;
color:var(--dim)}
.chip:hover{border-color:#b9ccd6}
.chip.on{border-color:var(--blue);color:var(--ink);background:#eef5f8}
.chip b{font-weight:650;color:var(--ink)}
.chips.flat .chip{cursor:default;color:var(--dim);background:var(--bg)}
details.strip{margin:0 0 14px;border:1px solid var(--line);border-radius:8px;background:var(--card);
padding:9px 12px}
details.strip summary{color:var(--ink);font-size:13px}
.quote{margin:4px 0 8px;padding-left:11px;border-left:2px solid var(--line);color:#2b3c44;
font-size:13.5px}
.opt{border:1px solid var(--line);border-radius:7px;padding:9px 12px;margin:7px 0;
background:var(--card)}
.optname{font-weight:650;color:var(--ink);font-size:12.5px;margin-bottom:3px}
.kv.sub{margin:2px 0 6px 0;padding-left:10px;border-left:1px solid var(--line)}
.sub{margin:3px 0}
.linwrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card);
padding:10px;position:relative}
.pop{position:fixed;z-index:50;width:360px;max-width:calc(100vw - 24px);max-height:70vh;
overflow:auto;background:var(--card);border:1px solid #b9ccd6;border-radius:8px;
box-shadow:0 6px 20px rgba(22,35,42,.16);padding:12px 14px 10px}
.pop .popname{font-weight:650;font-size:13px;padding-right:18px}
.pop .path{font-size:11.5px;margin:2px 0 6px}
.pop .prose{font-size:12.5px}
.pop .kv{margin-top:8px;font-size:12.5px}
.pop h3{margin:12px 0 4px}
.pop .bar{margin:10px 0 0}
.popx{position:absolute;top:6px;right:8px;appearance:none;border:none;background:none;
font-size:17px;line-height:1;color:var(--faint);cursor:pointer;padding:0 2px}
.popx:hover{color:var(--ink)}
.bandlist{margin:8px 0}
svg.lin{display:block}
svg.lin rect{fill:#fff;stroke:var(--line);stroke-width:1}
svg.lin .foc rect{fill:#eef5f8;stroke:var(--blue);stroke-width:1.5}
svg.lin .bt{font:600 12px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--ink)}
svg.lin .bs{font:11px -apple-system,BlinkMacSystemFont,sans-serif;fill:var(--dim)}
svg.lin .ln{fill:none;stroke:#c4d0d5;stroke-width:1.3}
svg.lin .ln.drv{stroke:var(--blue);stroke-width:2}
svg.lin .ln.nb{stroke:#c9a227;stroke-width:2;stroke-dasharray:5 3}
label.chk{display:inline-flex;gap:6px;align-items:center;font-size:12.5px;color:var(--dim);
cursor:pointer;user-select:none;white-space:nowrap}
label.chk:hover{color:var(--ink)}
label.chk input{margin:0;cursor:pointer}
svg.lin .par.nb rect{stroke:#c9a227}
button.back.on{border-color:var(--blue);background:#eef5f8;color:var(--ink)}
svg.lin marker path{fill:#c4d0d5}
svg.lin .clk{cursor:pointer}
svg.lin .clk:hover rect{stroke:var(--blue)}
footer{color:var(--faint);font-size:12px;padding:18px 24px;border-top:1px solid var(--line)}
"""

JS = r"""
const $ = (s, r) => (r || document).querySelector(s);
const el = (t, a, kids) => { const n = document.createElement(t);
  for (const k in (a || {})) { if (k === 'text') n.textContent = a[k];
    else if (k === 'html') n.innerHTML = a[k]; else if (a[k] != null) n.setAttribute(k, a[k]); }
  for (const c of (kids || [])) n.append(c); return n; };
const num = n => (n == null ? '' : Number(n).toLocaleString('en-US'));
const pct = x => (x == null ? '' : Math.round(x * 100) + '%');

/* A Fact with its provenance. A value with no source is a rumour, so the pill is never dropped. */
function fact(f) {
  if (!f) return el('span', {class: 'tot', text: 'not settled'});
  const v = Array.isArray(f.value) ? f.value.join(', ') : String(f.value);
  const s = el('span', {});
  s.append(el('span', {text: v + ' '}));
  s.append(el('span', {class: 'pill ' + (f.source || ''),
    text: f.source + (f.confidence != null ? ' @' + f.confidence.toFixed(2) : '')}));
  if (f.resting_on) s.append(el('span', {class: 'pill bad', text: 'rests on a premise'}));
  return s;
}

/* One sortable, filterable table. Sorting is a VIEW, never a mutation of DATA: the embedded
   arrives sorted from the assembly layer and that order is what the file's determinism rests on. */
function grid(rows, cols, opts) {
  opts = opts || {};
  let sort = opts.sort || null, dir = opts.dir || 1, q = '';
  const search = el('input', {type: 'search', placeholder: opts.placeholder || 'filter...'});
  const count = el('span', {class: 'count'});
  const bar = el('div', {class: 'bar'}, [search, count]);
  for (const extra of (opts.controls || [])) bar.append(extra);
  const head = el('tr', {}, cols.map(c => {
    const th = el('th', {text: c.label, class: c.n ? 'n' : ''});
    th.onclick = () => { if (sort === c.key) dir = -dir; else { sort = c.key; dir = 1; } draw(); };
    return th;
  }));
  const body = el('tbody');
  const table = el('table', {}, [el('thead', {}, [head]), body]);
  const host = el('div', {}, [bar, el('div', {class: opts.scroll ? 'list' : '', }, [table])]);

  function draw() {
    let view = rows;
    if (q) { const t = q.toLowerCase();
      view = rows.filter(r => (opts.text ? opts.text(r) : JSON.stringify(r)).toLowerCase().includes(t)); }
    if (opts.where) view = view.filter(opts.where);
    if (sort) { const c = cols.find(x => x.key === sort);
      view = view.slice().sort((a, b) => {
        const x = c.val(a), y = c.val(b);
        if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
        return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
      }); }
    /* A cap with no notice reads as "that is all of them". It says how many it is not showing. */
    const cap = opts.cap || 1200, shown = view.slice(0, cap);
    count.textContent = view.length === rows.length
      ? num(rows.length) + ' row(s)' + (view.length > cap ? ', showing ' + num(cap) : '')
      : num(view.length) + ' of ' + num(rows.length) + (view.length > cap ? ', showing ' + num(cap) : '');
    body.replaceChildren(...shown.map(r => {
      const tr = el('tr', {class: opts.pick ? 'pick' : ''},
        cols.map(c => el('td', {class: (c.n ? 'n ' : '') + (c.mono ? 'mono' : '')},
          [c.cell ? c.cell(r) : el('span', {text: c.val(r) == null ? '' : String(c.val(r))})])));
      if (opts.pick) tr.onclick = () => { body.querySelectorAll('tr.on').forEach(x => x.classList.remove('on'));
        tr.classList.add('on'); opts.pick(r); };
      return tr;
    }));
    if (!shown.length) body.replaceChildren(el('tr', {}, [el('td', {class: 'empty',
      colspan: cols.length, text: opts.emptyText || 'nothing matches'})]));
  }
  search.oninput = () => { q = search.value; draw(); };
  draw();
  host.redraw = draw;
  return host;
}

function section(title, node) {
  const h = el('h3', {text: title});
  return el('div', {}, node ? [h, node] : [h]);
}

function kv(pairs) {
  const d = el('dl', {class: 'kv'});
  for (const [k, v] of pairs) { if (v == null) continue;
    d.append(el('dt', {text: k})); d.append(el('dd', {}, [typeof v === 'string' ? el('span', {text: v}) : v])); }
  return d;
}
"""


def explorer_html(data: dict, record_html: str) -> str:
    """The whole thing: one file, embedded data, tabs.

    `record_html` is the existing report, folded in as a tab rather than rewritten. It is the one
    surface with an argument to make rather than a table to show, and it keeps making it.
    """
    e = html.escape
    meta = data["meta"]

    # *** `</script>` INSIDE THE DATA WOULD END THE TAG AND SILENTLY TRUNCATE THE FILE. ***
    # A dbt model that contains that string in a comment is not exotic. Escaping the sequence is
    # what stops a warehouse's own content from breaking the page that describes it. `<!--` too:
    # it opens a comment inside a script element in the HTML parser.
    # *** THE RECORD TRAVELS INSIDE THE BLOB, NOT AS MARKUP. ***
    # It is a whole document with its own <style>, so dropping it into a div would clobber this
    # page's styles with its own. It goes into an isolated iframe instead, and the only safe way
    # to carry a document through HTML is as a JSON string -- json encoding already escapes
    # everything, where hand-escaping a nested document is how a page silently truncates.
    data = {**data, "record": record_html}
    blob = json.dumps(data, separators=(",", ":"), sort_keys=True, default=str)
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")

    counts = {
        "models": len(data["models"]), "edges": len(data["edges"]),
        "claims": len(data["claims"]), "findings": len(data["findings"]),
        "decisions": len(data["decisions"]), "questions": len(data["questions"]),
        "unreadable": len(data["unreadable"]),
    }
    # *** THE OVERVIEW IS THE WAY IN, NOT THE LAST TAB. ***
    # It is the only surface here with an argument to make rather than a table to show, and a
    # person opening this file has not yet picked a model to look at. Landing on 358 rows asks
    # them to choose before they have been told anything.
    tabs = [
        ("understood", "Overview", None),
        ("models", "Models", counts["models"]),
        ("chain", "The chain", counts["edges"]),
        ("claims", "Claims", counts["claims"]),
        ("findings", "Findings", counts["findings"]),
        ("answers", "Answers", counts["decisions"]),
        ("questions", "Questions", counts["questions"]),
        ("config", "Config", None),
    ]
    nav = "".join(
        f'<button role="tab" data-tab="{t}" aria-selected="{"true" if i == 0 else "false"}">'
        f'{e(label)}{f"<b>{n:,}</b>" if n is not None else ""}</button>'
        for i, (t, label, n) in enumerate(tabs))
    panels = "".join(f'<div class="panel" id="p-{t}"{"" if i == 0 else " hidden"}></div>'
                     for i, (t, _l, _n) in enumerate(tabs))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(meta['project'])} &middot; assay</title>
<style>{CSS}</style></head><body>
<header>
<h1>{e(meta['project'])}<span>everything assay knows</span></h1>
<div class="sub">{meta['models']} models &middot; {meta['sources']} sources &middot;
manifest generated {e(str(meta['generated_at']))} &middot; assay {e(meta['version'])}</div>
<nav role="tablist">{nav}</nav>
</header>
<main>{panels}</main>
<footer>
Self-contained and deterministic: it carries the manifest's own <b>generated_at</b> and never a
wall clock, so a rerun against an unchanged store writes an identical file. Every array is sorted
in the assembly layer, not in the browser, so the sort is a fact about the file rather than about
the machine that opened it.
</footer>

<!-- THE ONE SWAPPABLE LINE. Embedded here; a server would make this a fetch and nothing
     below would change. -->
<script id="assay-data" type="application/json">{blob}</script>
<script>
const DATA = JSON.parse(document.getElementById('assay-data').textContent);
{JS}
{_VIEWS}
</script>
</body></html>"""


# --------------------------------------------------------------------------------- the tab views

_VIEWS = r"""
const M = DATA.models, BY_UID = Object.fromEntries(M.map(m => [m.uid, m]));
const BY_NAME = Object.fromEntries(M.map(m => [m.name, m]));
const CLAIM = Object.fromEntries(DATA.claims.map(c => [c.id, c]));
const FIND = Object.fromEntries(DATA.findings.map(f => [f.id, f]));
const EDGES_IN = {}, EDGES_OUT = {};
for (const e of DATA.edges) {
  (EDGES_IN[e.child] = EDGES_IN[e.child] || []).push(e);
  (EDGES_OUT[e.parent] = EDGES_OUT[e.parent] || []).push(e);
}

/* *** THE TABS WERE EIGHT ISLANDS. *** A model name is the one thing every table has in common,
   so every one of them is a way back to that model. This is the cheapest thing on the page and
   the one that turns separate lists into somewhere you can actually move around. */
/* *** A MODEL FROM AN INSTALLED PACKAGE IS IN THE MANIFEST AND IS NOT YOURS. ***
   30 of 358 here, every one unreadable, carrying 541 columns of unknown provenance, all diluting
   numbers about the project somebody actually wrote. The split is by OWNER and never by whether
   the model parsed: a package's model failing to parse is not your problem, and one of YOURS
   failing to parse is the "you did not compile" signal -- which a filter on `unreadable` would
   have hidden, and which is the whole reason not to write that filter. */
/* *** AN EMPTY COLUMN ON EVERY ROW IS NOT A RESULT, IT IS A QUESTION NOBODY ASKED. ***
   Every one of 5,656 columns on the field warehouse had no role, because `assay columns` has
   never been run there. Printing "not settled" 5,656 times says the same thing as a check that
   found nothing, which is the failure this whole tool is built to name. So the column is dropped
   and the reason is said once, with the command that would fill it. */
const HAS_ROLES = M.some(m => (m.columns || []).some(c => c.role));
const PACKAGED = M.filter(m => !m.yours).length;
let showPackaged = false;
const PKG_BOXES = [];
function packageFilter(redraw) {
  if (!PACKAGED) return null;
  const cb = el('input', {type: 'checkbox'});
  cb.checked = showPackaged;
  cb.onchange = () => { showPackaged = cb.checked;
    for (const b of PKG_BOXES) { b.box.checked = showPackaged; b.redraw(); } };
  PKG_BOXES.push({box: cb, redraw: redraw});
  return el('label', {class: 'chk'}, [cb, el('span',
    {text: 'include ' + PACKAGED + ' model(s) from installed packages'})]);
}
const mine = m => showPackaged || m.yours;

const GO = {};
/* Mark the row for `name` as the selected one, and scroll it into view, WITHOUT filtering. If it
   is not in the current view -- filtered out, or past the cap -- nothing happens, which is
   correct: the detail already shows the model and the list is only an index into it. */
function highlight(panel, name) {
  const host = document.querySelector(panel); if (!host) return;
  host.querySelectorAll('tbody tr.on').forEach(r => r.classList.remove('on'));
  for (const tr of host.querySelectorAll('tbody tr')) {
    const first = tr.querySelector('td');
    if (first && first.textContent.trim() === name) {
      tr.classList.add('on');
      if (tr.scrollIntoView) tr.scrollIntoView({block: 'nearest'});
      return;
    }
  }
}
function link(name, where) {
  if (!BY_NAME[name]) return el('span', {class: 'mono', text: name || ''});
  const a = el('a', {class: 'mono lk', href: '#', text: name});
  a.onclick = ev => { ev.preventDefault(); ev.stopPropagation();
    open(where || 'models'); (GO[where || 'models'] || (() => {}))(name); };
  return a;
}

/* A grouped front door. *** NO TAB OPENS ON A FLAT LIST OF EVERYTHING. ***
   5,794 claims in one scroll is not more information than 358 models in one scroll, it is less:
   the first screen tells you nothing about the shape of what is there and gives you nowhere
   obvious to click. So the summary is the view, and the rows are one click in, already filtered. */
function drill(opts) {
  const host = el('div');
  const head = el('div');
  const body = el('div');
  const back = el('button', {class: 'back', text: '\u2190 all ' + opts.noun});
  back.onclick = () => showGroups();

  /* *** ONE BAR, IN THE SAME PLACE, WHICHEVER VIEW YOU ARE IN. ***
     The first version put the view switch in the group bar and a breadcrumb above the rows, so
     flipping between two views changed the furniture as well as the table and you could not flip
     back from where you had landed. Reported from the field: "I'd greatly prefer that dropdown to
     remain so it's flipping between the two, rather than different UI." The switch lives in the
     bar in every state; only DRILLING into one group adds a way back, because that is the one
     move the switch cannot undo. */
  function showGroups() {
    if (opts.onGroups) opts.onGroups();
    head.replaceChildren(el('p', {class: 'note', text: opts.blurb}));
    body.replaceChildren(grid(opts.groups, opts.groupCols, {
      placeholder: opts.groupFilter || 'filter...', pick: g => showRows(g, true),
      sort: opts.groupSort, dir: opts.groupDir || -1, cap: 800,
      controls: opts.controls || [], text: opts.groupText}));
  }
  function showRows(g, drilled) {
    head.replaceChildren(el('p', {class: 'note', text: opts.blurb}));
    const extra = (opts.controls || []).slice();
    if (drilled) {
      extra.push(back);
      extra.push(el('span', {class: 'crumb', text: opts.label(g)}));
    }
    body.replaceChildren(grid(opts.rowsOf(g), opts.rowCols, {
      placeholder: 'filter...', cap: 2000, sort: opts.rowSort, dir: opts.rowDir || 1,
      controls: extra, text: opts.rowText, emptyText: 'nothing here'}));
  }
  host.append(head, body);
  showGroups();
  host.showGroups = showGroups;
  host.showRows = showRows;
  return host;
}

function conf(x) {
  if (x == null) return el('span', {class: 'tot', text: ''});
  /* Under the 0.6 gate an answer reports nothing as a finding, so the number is the point. */
  return el('span', {class: x < 0.6 ? 'low' : '', text: x.toFixed(2)});
}

/* ------------------------------------------------------------------- the drawn lineage

   *** YOU NEVER DRAW 573 HOPS. *** That is the whole graph. A drawing is always ONE model's
   neighbourhood, and measured on a 358-model warehouse those are small: median 3 boxes, p95 12,
   max 37. So three bands and straight lines, no graph algorithm, no force layout, no hairball.

   The edge label goes ON the parent box rather than on the line. With eight parents converging
   on one focus, labels on the lines overlap into mush; on the boxes they never can.

   Past BAND_MAX in a band it degrades to a list with one bracket, because 36 boxes with 36
   converging lines is the hairball this exists to avoid. That is 14 models of 358 on the parent
   side and 6 on the child side, and you can see it coming.                                    */
const BAND_MAX = 9, BW = 210, BH = 66, GAPX = 16, BANDY = 152, MINW = 860;
/* *** A BOX 148 WIDE HOLDING 22 MONOSPACE CHARACTERS IS A BOX THAT OVERFLOWS. ***
   Reported from the field with a screenshot: `int_az_parcel_sections` painted through its own
   border and then through the right edge of the drawing. Truncating by character count guesses
   at the font metrics; a clip path does not, so the box is the boundary whatever renders it.
   `MINW` keeps a two-box drawing filling its panel instead of huddling in the corner. */
const CLIP = 'boxclip';

function svg(tag, attrs, kids) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in (attrs || {})) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  for (const c of (kids || [])) n.append(c);
  return n;
}
function svgText(x, y, s, cls) {
  const t = svg('text', {x: x, y: y, class: cls || ''}); t.textContent = s; return t;
}

function edgeNote(e) {
  const bits = [];
  if (e.kind) bits.push(e.kind);
  if (e.driving) bits.push('drives');
  if (e.union_arm) bits.push('union');
  if ((e.joined_on || []).length) bits.push('on ' + e.joined_on.join('+'));
  if (e.dropped) bits.push(e.dropped + ' dropped');
  if (e.row_loss && e.row_loss.length === 2 && e.row_loss[0])
    bits.push(pct(e.row_loss[1] / e.row_loss[0]) + ' kept');
  return bits.join(' · ');
}

function box(x, y, title, sub, cls, onclick) {
  const g = svg('g', {class: 'box ' + (cls || ''), transform: `translate(${x},${y})`});
  g.append(svg('rect', {width: BW, height: BH, rx: 5}));
  /* Two layers, and both are needed. The ellipsis says "there is more here", which a hard cut
     does not -- a name sliced mid-character just looks like a rendering fault. The clip is the
     backstop that makes the box the boundary whatever font actually renders it. */
  const cut = (t, n) => (t.length > n ? t.slice(0, n - 1) + '\u2026' : t);
  const inner = svg('g', {'clip-path': 'url(#' + CLIP + ')'});
  inner.append(svgText(11, 25, cut(title, 26), 'bt'));
  if (sub) inner.append(svgText(11, 45, cut(sub, 34), 'bs'));
  g.append(inner);
  /* `Node.append` returns undefined, so chaining `.textContent` off it sets a property on
     nothing. The hover title is how a truncated name stays readable, so it is built first. */
  const tt = svg('title', {});
  tt.textContent = title + (sub ? '\n' + sub : '');
  g.append(tt);
  if (onclick) { g.classList.add('clk'); g.onclick = onclick; }
  return g;
}

function bandList(title, edges, other) {
  /* The degradation. A table, with the same facts the boxes would have carried. */
  return el('div', {class: 'bandlist'}, [
    el('p', {class: 'note', text: title}),
    grid(edges, [
      {key: 'n', label: 'model', mono: 1, val: e => other(e), cell: e => link(other(e), 'chain')},
      {key: 'j', label: 'edge', val: e => edgeNote(e)},
    ], {placeholder: 'filter...', cap: 60})]);
}

/* *** CLICKING A NODE MUST NOT DRIVE THE SEARCH BOX. ***
   It used to jump to that model by typing its name into the filter, which left the list showing
   one row and the box full of text you then had to clear by hand to get the graph back.
   Reported from the field: "clicking a node adds it to the search thing and just FUCKS the ui...
   ideally if you're clicking a node it's just a popup right there with the relevant info."

   So a click opens a card where the node is, and going there is a deliberate second click. */
/* *** A CARD INSIDE A SCROLLING BOX GETS CUT BY THE SCROLLING BOX. ***
   It was absolutely positioned inside `.linwrap`, which needs `overflow-x: auto` for a wide
   graph, so a node near the left edge had half its card clipped away. Reported from the field
   with a screenshot: "it gets cut off bruh it needs to fit in the window".

   `position: fixed` escapes every ancestor's overflow, and then the only thing that can cut it
   is the viewport -- which is what this clamps against. Pure, so it is tested as arithmetic
   rather than by hoping a browser agrees. */
function clampToViewport(w, h, cx, below, vw, vh, pad) {
  pad = pad == null ? 12 : pad;
  const left = Math.max(pad, Math.min(cx - w / 2, vw - w - pad));
  // Below the node by preference; above it when that would run off the bottom. If it fits in
  // neither, pin to the top and let the card scroll inside itself.
  let top = below;
  if (top + h + pad > vh) top = Math.max(pad, below - h - 8);
  if (top + h + pad > vh) top = pad;
  return {left: Math.round(left), top: Math.round(top)};
}

function dismissCards() {
  document.querySelectorAll('.pop').forEach(n => n.remove());
}

function nodeCard(node, name, e) {
  dismissCards();
  const m = BY_NAME[name];
  const pop = el('div', {class: 'pop'});
  const close = el('button', {class: 'popx', text: '\u00d7'});
  close.onclick = ev => { ev.stopPropagation(); pop.remove(); };
  pop.append(close);
  pop.append(el('div', {class: 'popname mono', text: name}));
  if (!m) {
    pop.append(el('p', {class: 'note', text: 'A source, or a relation outside this project. '
      + 'assay knows it only as the other end of this hop.'}));
  } else {
    pop.append(el('div', {class: 'path mono', text: m.path}));
    if (m.description) pop.append(el('p', {class: 'prose', text: m.description.slice(0, 260)
      + (m.description.length > 260 ? '\u2026' : '')}));
    pop.append(kv([
      ['grain', fact(m.grain)],
      ['reads', String((EDGES_IN[m.uid] || []).length)],
      ['read by', String((EDGES_OUT[m.uid] || []).length)],
      ['reach', m.marts + ' mart(s)'],
      ['findings', el('span', {class: m.findings.length ? 'bad' : 'tot',
                               text: String(m.findings.length)})],
      ['claims', String(m.claims.length)],
    ]));
  }
  if (e) pop.append(section('this hop', el('p', {class: 'prose', text: edgeNote(e) || 'no join'})));
  const row = el('div', {class: 'bar'});
  if (m) {
    const go = el('button', {class: 'back', text: 'centre the graph here'});
    go.onclick = ev => { ev.stopPropagation(); pop.remove(); GO.chain(name); };
    const go2 = el('button', {class: 'back', text: 'open in Models'});
    go2.onclick = ev => { ev.stopPropagation(); pop.remove(); open('models'); GO.models(name); };
    row.append(go, go2);
  }
  pop.append(row);
  /* On the BODY, not in the drawing: nothing an ancestor does with overflow can clip it. */
  document.body.append(pop);
  const r = node.getBoundingClientRect
    ? node.getBoundingClientRect() : {left: 0, right: 0, bottom: 0, width: 0};
  const box = pop.getBoundingClientRect ? pop.getBoundingClientRect() : {width: 340, height: 300};
  const at = clampToViewport(box.width || 340, box.height || 300,
                             (r.left + r.right) / 2, r.bottom + 8,
                             window.innerWidth || 1200, window.innerHeight || 800);
  pop.style.left = at.left + 'px';
  pop.style.top = at.top + 'px';
  return pop;
}

function lineage(m) {
  const ins = (EDGES_IN[m.uid] || []).slice(), outs = (EDGES_OUT[m.uid] || []).slice();
  /* Driving parents first: the driving edge is the spine, and everything else hangs off it.
     Then by name, so the picture is the same on every machine and in every rerun. */
  ins.sort((a, b) => (b.driving - a.driving) || a.parent_name.localeCompare(b.parent_name));
  outs.sort((a, b) => a.child_name.localeCompare(b.child_name));

  const host = el('div');
  const wrap = el('div', {class: 'linwrap'});
  const drawIn = ins.length <= BAND_MAX, drawOut = outs.length <= BAND_MAX;
  const nTop = drawIn ? ins.length : 0, nBot = drawOut ? outs.length : 0;
  const cols = Math.max(nTop, nBot, 1);
  const W = Math.max(cols * (BW + GAPX) + GAPX, MINW);
  const H = BANDY * 2 + BH + 30;
  const s = svg('svg', {class: 'lin', width: W, height: H, viewBox: `0 0 ${W} ${H}`});
  s.append(svg('defs', {}, [
    svg('marker', {id: 'ah', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7,
        markerHeight: 7, orient: 'auto'}, [svg('path', {d: 'M0,0 L8,4 L0,8 z'})]),
    svg('clipPath', {id: CLIP}, [svg('rect', {width: BW - 6, height: BH})])]));

  const fx = (W - BW) / 2, fy = BANDY;
  const rowFor = (i, n) => (W - (n * (BW + GAPX) - GAPX)) / 2 + i * (BW + GAPX);

  if (drawIn) ins.forEach((e, i) => {
    const x = rowFor(i, ins.length);
    s.append(svg('path', {class: 'ln' + (e.driving ? ' drv' : '') + (why(e) ? ' nb' : ''),
      'marker-end': 'url(#ah)',
      d: `M${x + BW / 2},${BH} C${x + BW / 2},${BH + 40} ${fx + BW / 2},${fy - 40} ${fx + BW / 2},${fy - 6}`}));
    s.append(box(x, 0, e.parent_name, edgeNote(e), 'par' + (why(e) ? ' nb' : ''),
                 ev => { ev.stopPropagation();
                   nodeCard(ev.currentTarget, e.parent_name, e); }));
  });
  if (drawOut) outs.forEach((e, i) => {
    const x = rowFor(i, outs.length);
    s.append(svg('path', {class: 'ln', 'marker-end': 'url(#ah)',
      d: `M${fx + BW / 2},${fy + BH} C${fx + BW / 2},${fy + BH + 40} ${x + BW / 2},${BANDY * 2 - 40} ${x + BW / 2},${BANDY * 2 - 6}`}));
    s.append(box(x, BANDY * 2, e.child_name, edgeNote(e), 'chi',
                 ev => { ev.stopPropagation();
                   nodeCard(ev.currentTarget, e.child_name, e); }));
  });
  const g = m.grain ? (Array.isArray(m.grain.value) ? m.grain.value.join(', ') : String(m.grain.value)) : 'grain not settled';
  s.append(box(fx, fy, m.name, g, 'foc'));

  if (!drawIn && ins.length)
    host.append(bandList(ins.length + ' parents, too many to draw. The same facts as a list:',
                         ins, e => e.parent_name));
  wrap.append(s);
  /* Clicking the canvas anywhere but a node dismisses the card, which is what people expect and
     is also the only way out on a touch device. */
  wrap.onclick = () => dismissCards();
  host.append(wrap);
  if (!drawOut && outs.length)
    host.append(bandList(outs.length + ' children, too many to draw:', outs, e => e.child_name));
  if (!ins.length && !outs.length)
    host.append(el('p', {class: 'empty', text: 'This model has no edges recorded: nothing reads it and it reads nothing that assay could resolve.'}));
  return host;
}

/* ------------------------------------------------------------------------------- Models */
function modelsTab(host) {
  const detail = el('div', {class: 'detail'});
  const list = grid(M, [
    {key: 'name', label: 'model', mono: 1, val: m => m.name},
    {key: 'layer', label: 'layer', val: m => m.layer},
    {key: 'marts', label: 'marts', n: 1, val: m => m.marts},
    {key: 'findings', label: 'find', n: 1, val: m => m.findings.length,
     cell: m => el('span', {class: m.findings.length ? 'bad' : 'tot',
                            text: String(m.findings.length || 0)})},
  ], {placeholder: 'filter models, paths, descriptions...', scroll: 1, pick: m => show(m),
      where: m => mine(m), controls: [packageFilter(() => list.redraw())].filter(Boolean),
      text: m => [m.name, m.path, m.layer, m.description].join(' ')});

  function show(m) {
    const d = detail; d.replaceChildren();
    d.append(el('h2', {text: m.name}));
    d.append(el('div', {class: 'path mono', text: m.path}));
    if (m.unreadable) d.append(el('p', {class: 'pill bad',
      text: 'assay could not read this model, so it is absent from everything below. That is not a pass.'}));
    if (m.description) d.append(el('p', {class: 'prose', text: m.description}));

    d.append(section('what one row is', kv([
      ['grain', fact(m.grain)],
      ['the SQL says', m.derived_grain.length
        ? el('span', {class: 'mono', text: m.derived_grain.join(', ')})
        : el('span', {class: 'tot', text: 'nothing settles it'})],
      ['materialized', m.materialized],
      ['reach', el('span', {text: m.marts + ' mart(s), ' + m.descendants + ' descendant(s)'})],
    ])));

    const colCols = [
      {key: 'name', label: 'column', mono: 1, val: c => c.name,
       cell: c => { const s = el('span', {}); s.append(el('span', {text: c.name + ' '}));
         if (c.in_key) s.append(el('span', {class: 'pill on', text: 'key'})); return s; }},
    ];
    if (HAS_ROLES) colCols.push(
      {key: 'role', label: 'role', val: c => c.role && c.role.value, cell: c => fact(c.role)});
    colCols.push(
      {key: 'prov', label: 'came from', val: c => c.provenance && c.provenance.value,
       cell: c => fact(c.provenance)},
      {key: 'why', label: 'how assay knows', val: c => (c.provenance || {}).note || '',
       cell: c => el('span', {class: 'tot', text: (c.provenance || {}).note || ''})});
    const colsBox = el('div', {}, [grid(m.columns, colCols,
      {placeholder: 'filter columns...', cap: 400, emptyText: 'no columns known'})]);
    if (!HAS_ROLES) colsBox.append(el('p', {class: 'note',
      text: 'No column has a role yet, on any model: nothing has asked. `assay columns` answers '
        + 'what job each column does -- identifier, measure, qualifier, timestamp -- and it is '
        + 'what several grain findings need before they can fire at all.'}));
    d.append(section('columns (' + m.columns.length + ')', colsBox));

    const ins = EDGES_IN[m.uid] || [], outs = EDGES_OUT[m.uid] || [];
    const lin = el('button', {class: 'back', text: 'see it drawn →'});
    lin.onclick = () => { open('chain'); GO.chain(m.name); };
    d.append(section('reads ' + ins.length + ', read by ' + outs.length, el('div', {}, [
      lin,
      grid(ins.concat(outs), [
        {key: 'dir', label: '', val: e => e.child === m.uid ? 'reads' : 'read by',
         cell: e => el('span', {class: 'pill', text: e.child === m.uid ? 'reads' : 'read by'})},
        {key: 'other', label: 'model', mono: 1,
         val: e => e.child === m.uid ? e.parent_name : e.child_name,
         cell: e => link(e.child === m.uid ? e.parent_name : e.child_name, 'chain')},
        {key: 'edge', label: 'edge', val: e => edgeNote(e)},
        {key: 'dropped', label: 'dropped', n: 1, val: e => e.dropped,
         cell: e => { if (!e.dropped) return el('span', {class: 'tot', text: '0'});
           const t = el('details'); t.append(el('summary', {text: String(e.dropped)}));
           t.append(el('pre', {text: (e.dropped_cols || []).join('\n')})); return t; }},
      ], {placeholder: 'filter hops...', cap: 200,
          emptyText: 'no edges: a leaf that nothing reads'})])));

    const fs = m.findings.map(i => FIND[i]).filter(Boolean);
    d.append(section('findings (' + fs.length + ')', fs.length ? grid(fs, [
      {key: 'check', label: 'check', mono: 1, val: f => f.check},
      {key: 'what', label: 'what', val: f => f.summary},
      {key: 'ruled', label: 'ruled', val: f => f.ruled_finding ? 1 : 0,
       cell: f => el('span', {class: 'pill ' + (f.ruled_finding ? 'on' : ''),
         text: f.ruled_finding ? 'read by a person'
           : (f.ruled_model ? 'model ruled, not this' : 'unread')})},
    ], {placeholder: 'filter findings...', cap: 200})
      : el('p', {class: 'empty', text: 'nothing found on this model'})));

    const cs = m.claims.map(i => CLAIM[i]).filter(Boolean);
    const bad = cs.filter(c => c.contradicted != null).length;
    d.append(section('claims (' + cs.length + (bad ? ', ' + bad + ' contradicted' : '') + ')',
      cs.length ? grid(cs, [
        {key: 'text', label: 'claim', val: c => c.text},
        {key: 'where', label: 'written', mono: 1, val: c => c.source_ref},
        {key: 'v', label: 'the code', val: c => c.contradicted == null ? -1 : c.contradicted,
         cell: c => c.contradicted == null ? el('span', {class: 'tot', text: 'not contradicted'})
           : el('span', {class: 'pill bad', text: 'contradicts @' + c.contradicted.toFixed(2)})},
      ], {placeholder: 'filter claims...', cap: 300})
        : el('p', {class: 'empty', text: 'no claims extracted for this model'})));

    const ds = m.decisions.map(i => DATA.decisions[i]).filter(Boolean);
    d.append(section('answers (' + ds.length + ')', ds.length ? grid(ds, [
      {key: 'q', label: 'question', mono: 1, val: a => a.question},
      {key: 'a', label: 'answered', val: a => a.answer},
      {key: 'c', label: 'conf', n: 1, val: a => a.confidence, cell: a => conf(a.confidence)},
      {key: 'r', label: 'next best', val: a => a.runner_up && a.runner_up[0],
       cell: a => a.runner_up ? el('span', {class: 'tot',
         text: a.runner_up[0] + ' ' + a.runner_up[1].toFixed(2)}) : el('span')},
      {key: 'ctx', label: 'about', val: a => a.context},
    ], {placeholder: 'filter answers...', cap: 400})
      : el('p', {class: 'empty', text: 'nothing has been asked about this model'})));
  }

  host.replaceChildren(el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a model. Everything assay knows about it is here: what one row is and who settled that, every column with its role and where its value came from, every hop in and out, what the project claims about it, and every answer ever given.'}));
  GO.models = name => { const m = BY_NAME[name]; if (!m) return;
    show(m);
    highlight('#p-models', name); };
  const first = $('tbody tr', list); if (first) first.click();
}

/* *** "209 OF 573 WORTH A LOOK" IS NOT A SIGNAL, IT IS THE TABLE. ***
   163 of those 209 came from one rule of mine: "a driving edge joining on nothing". A driving
   edge IS the FROM clause. Of course it joins on nothing. It was flagging the normal case, which
   is how a list of exceptions becomes a list.

   Measured on 573 real hops before rewriting it -- dropped columns p75 12, p90 24, max 233; a
   join carrying no resolvable key 46; judged fan-outs 13 -- and every threshold below is a number
   off that distribution rather than a guess. A hop with nothing to say returns "" and is silent. */
function why(e) {
  const out = [];
  if (!e.driving && !e.union_arm && !(e.joined_on || []).length)
    out.push((e.kind || 'a') + ' join carrying no key assay could resolve');
  if (e.dropped > 60) out.push('drops ' + e.dropped + ' columns');
  if (e.row_loss && e.row_loss.length === 2 && e.row_loss[0] &&
      e.row_loss[1] / e.row_loss[0] < 0.5)
    out.push('keeps only ' + pct(e.row_loss[1] / e.row_loss[0]) + ' of the parent');
  return out.join(' \u00b7 ');
}
const NOTABLE_BY_CHILD = {};
for (const e of DATA.edges) if (why(e))
  (NOTABLE_BY_CHILD[e.child] = NOTABLE_BY_CHILD[e.child] || []).push(e);

/* --------------------------------------------------------------------------- The chain */
function chainTab(host) {
  const detail = el('div', {class: 'detail'});
  const withEdges = M.filter(m => (EDGES_IN[m.uid] || []).length || (EDGES_OUT[m.uid] || []).length);
  const nOf = m => (NOTABLE_BY_CHILD[m.uid] || []).length;
  let onlyNotable = false;

  /* *** THE OBSERVABILITY BELONGS IN THE PAGE, NOT BEHIND A DISCLOSURE TRIANGLE. ***
     It was a collapsed `<details>` above the list, which is the same shape as every
     absence-reads-as-nothing defect in this codebase: a closed summary and a check that found
     nothing look identical. So the count sits in the model list as a column you can sort on, the
     edges are drawn differently, and each model's own notable hops are named under its drawing. */
  /* A checkbox, because it is a toggle. A button whose label flips between two sentences makes
     you read it to find out which state you are in; a checkbox shows you. */
  const cb = el('input', {type: 'checkbox'});
  const toggle = el('label', {class: 'chk'}, [cb, el('span',
    {text: 'only the ' + Object.keys(NOTABLE_BY_CHILD).length
           + ' models with something notable'})]);
  function paintToggle() { cb.checked = onlyNotable; }
  cb.onchange = () => { onlyNotable = cb.checked; list.redraw(); };
  paintToggle();

  const list = grid(withEdges, [
    {key: 'name', label: 'model', mono: 1, val: m => m.name},
    {key: 'in', label: 'reads', n: 1, val: m => (EDGES_IN[m.uid] || []).length},
    {key: 'out', label: 'read by', n: 1, val: m => (EDGES_OUT[m.uid] || []).length},
    {key: 'note', label: 'notable', n: 1, val: m => nOf(m),
     cell: m => el('span', {class: nOf(m) ? 'low' : 'tot', text: nOf(m) ? String(nOf(m)) : ''})},
  ], {placeholder: 'filter models...', scroll: 1, pick: m => show(m), sort: 'name',
      where: m => mine(m) && (!onlyNotable || nOf(m)),
      controls: [toggle, packageFilter(() => list.redraw())].filter(Boolean),
      text: m => m.name + ' ' + m.path});

  function show(m) {
    const mine = NOTABLE_BY_CHILD[m.uid] || [];
    const d = detail; d.replaceChildren();
    d.append(el('h2', {text: m.name}));
    d.append(el('div', {class: 'path mono', text: m.path}));
    d.append(lineage(m));
    if (mine.length) {
      d.append(section('worth a look on this model (' + mine.length + ')', grid(mine, [
        {key: 'parent', label: 'from', mono: 1, val: e => e.parent_name,
         cell: e => link(e.parent_name, 'chain')},
        {key: 'why', label: 'why', val: e => why(e),
         cell: e => el('span', {class: 'low', text: why(e)})},
        {key: 'on', label: 'joined on', mono: 1, val: e => (e.joined_on || []).join(', '),
         cell: e => (e.joined_on || []).length
           ? el('span', {class: 'mono', text: e.joined_on.join(', ')})
           : el('span', {class: 'tot', text: 'nothing resolvable'})},
      ], {placeholder: 'filter...', cap: 60})));
    }
    const all = (EDGES_IN[m.uid] || []).concat(EDGES_OUT[m.uid] || []);
    d.append(section('every hop, in and out (' + all.length + ')', grid(all, [
      {key: 'dir', label: '', val: e => e.child === m.uid ? 'reads' : 'read by',
       cell: e => el('span', {class: 'pill', text: e.child === m.uid ? 'reads' : 'read by'})},
      {key: 'other', label: 'model', mono: 1,
       val: e => e.child === m.uid ? e.parent_name : e.child_name,
       cell: e => link(e.child === m.uid ? e.parent_name : e.child_name, 'chain')},
      {key: 'edge', label: 'edge', val: e => edgeNote(e)},
      {key: 'dropped', label: 'dropped', n: 1, val: e => e.dropped,
       cell: e => { if (!e.dropped) return el('span', {class: 'tot', text: '0'});
         const t = el('details'); t.append(el('summary', {text: String(e.dropped)}));
         t.append(el('pre', {text: (e.dropped_cols || []).join('\n')})); return t; }},
    ], {placeholder: 'filter hops...', cap: 200, emptyText: 'no edges'})));
  }

  const n = DATA.edges.filter(e => why(e)).length;
  host.replaceChildren(
    el('p', {class: 'note', text: 'Every hop in the DAG, drawn one neighbourhood at a time. '
      + n + ' of ' + DATA.edges.length + ' hops carry something worth a look: a join with no key '
      + 'assay could resolve, an unusually large column drop, or most of the parent lost. Those '
      + 'are counted in the notable column and named under each drawing.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a model to see its lineage drawn: what feeds it, what it feeds, and what each edge carries and drops. A drawing is always one neighbourhood, never the whole DAG.'}));
  /* *** THE DETAIL IS AUTHORITATIVE; THE LIST IS AN INDEX. ***
     Going to a model used to work by TYPING ITS NAME INTO THE FILTER, which left the list showing
     one row and the box full of text somebody had to clear by hand before they could see anything
     else. Show the model, then highlight its row if it happens to be on screen. Nothing the user
     typed is touched. */
  GO.chain = name => { const m = BY_NAME[name]; if (!m) return;
    show(m);
    highlight('#p-chain', name); };
  const first = $('tbody tr', list); if (first) first.click();
}

/* ------------------------------------------------------------------------------- Claims */
function claimsTab(host) {
  const byModel = {};
  for (const c of DATA.claims) {
    const g = byModel[c.subject_name] = byModel[c.subject_name] ||
      {model: c.subject_name, rows: [], bad: 0,
       // *** THE GROUP IS A MODEL, SO IT SHOULD SAY WHAT THE MODEL IS. ***
       // A list of 349 names and two counts makes you click to find out whether you care.
       desc: ((BY_NAME[c.subject_name] || {}).description || '').split('\n')[0]};
    g.rows.push(c); if (c.contradicted != null) g.bad++;
  }
  const groups = Object.values(byModel).sort((a, b) => a.model.localeCompare(b.model));
  const rowCols = [
    {key: 'model', label: 'model', mono: 1, val: c => c.subject_name,
     cell: c => link(c.subject_name)},
    {key: 'text', label: 'claim', val: c => c.text},
    {key: 'kind', label: 'kind', val: c => c.kind,
     cell: c => el('span', {class: 'pill', text: c.kind || 'unclassified'})},
    {key: 'from', label: 'written', mono: 1, val: c => c.source_ref},
    {key: 'v', label: 'the code', val: c => c.contradicted == null ? -1 : c.contradicted,
     cell: c => c.contradicted == null ? el('span', {class: 'tot', text: 'not contradicted'})
       : el('span', {class: 'pill bad', text: 'contradicts @' + c.contradicted.toFixed(2)})},
  ];
  const contradicted = DATA.claims.filter(c => c.contradicted != null);

  /* *** A BUTTON FLOATING ABOVE THE TABLE IS A ROW OF FURNITURE, NOT A CONTROL. ***
     The same note from the field as the eleven check chips, one tab over: it spends a row of the
     page on something the filter bar already has room for. The select says which of the two
     things you are looking at, and adds nothing above the table. */
  const view = el('select');
  const opt = (v, t) => { const o = document.createElement('option'); o.value = v;
    o.textContent = t; return o; };
  view.append(opt('by_model', 'all ' + num(DATA.claims.length) + ' claims, by model'));
  view.append(opt('contradicted', 'the ' + contradicted.length + ' contradicted, every model'));
  view.onchange = () => { if (view.value === 'contradicted')
    d.showRows({rows: contradicted, model: 'every model'}, false); else d.showGroups(); };

  const d = drill({
    controls: [view], onGroups: () => { view.value = 'by_model'; },
    noun: 'models', groups: groups, groupSort: 'bad',
    blurb: 'Every sentence this project says about itself, extracted from descriptions and SQL '
      + 'comments, grouped by the model it is about. A claim with no verdict was never asked, '
      + 'which is not the same as supported.',
    groupFilter: 'filter models...',
    groupText: g => g.model + ' ' + (g.desc || ''),
    label: g => g.model + ' · ' + g.rows.length + ' claim(s)',
    groupCols: [
      {key: 'model', label: 'model', mono: 1, val: g => g.model},
      {key: 'desc', label: 'what it is', val: g => g.desc,
       cell: g => g.desc ? el('span', {text: g.desc.slice(0, 150)})
         : el('span', {class: 'tot', text: 'no description'})},
      {key: 'n', label: 'claims', n: 1, val: g => g.rows.length},
      {key: 'bad', label: 'contradicted', n: 1, val: g => g.bad,
       cell: g => el('span', {class: g.bad ? 'bad' : 'tot', text: String(g.bad)})},
    ],
    rowsOf: g => g.rows, rowCols: rowCols, rowSort: 'v', rowDir: -1,
    rowText: c => [c.text, c.source_ref, c.kind].join(' '),
  });
  host.replaceChildren(d);
}

/* ----------------------------------------------------------------------------- Findings */
function findingsTab(host) {
  const detail = el('div', {class: 'detail'});
  let only = null;
  const byCheck = {};
  for (const f of DATA.findings) {
    const g = byCheck[f.check] = byCheck[f.check] ||
      {check: f.check, n: 0, marts: 0, ruled: 0, rests_on: f.rests_on};
    g.n++; g.marts = Math.max(g.marts, f.marts); g.ruled += f.ruled_finding ? 1 : 0;
  }
  const checks = Object.values(byCheck).sort((a, b) => b.n - a.n);

  /* *** ELEVEN CHIPS, EACH CARRYING A NAME, A COUNT AND "0 read", IS A WALL OF TEXT. ***
     Reported from the field with a screenshot: two rows of controls above the thing they filter,
     spending more space than the table. A select says the same thing in one line and sits in the
     filter bar that already exists, so nothing is added above the list at all. */
  const pickCheck = el('select');
  const opt = (v, t) => { const o = document.createElement('option'); o.value = v; o.textContent = t;
    return o; };
  pickCheck.append(opt('', 'every check \u00b7 ' + DATA.findings.length));
  for (const c of checks)
    pickCheck.append(opt(c.check, c.check + ' \u00b7 ' + c.n + (c.ruled ? ' \u00b7 ' + c.ruled + ' read' : '')));
  pickCheck.onchange = () => { only = pickCheck.value || null; list.redraw(); };

  const list = grid(DATA.findings, [
    {key: 'check', label: 'check', mono: 1, val: f => f.check},
    {key: 'model', label: 'model', mono: 1, val: f => f.model, cell: f => link(f.model)},
    {key: 'w', label: 'weight', n: 1, val: f => f.weight,
     cell: f => el('span', {text: f.weight.toFixed(1)})},
    {key: 'marts', label: 'marts', n: 1, val: f => f.marts},
  ], {placeholder: 'filter findings...', scroll: 1, sort: 'w', dir: -1, pick: f => show(f),
      where: f => !only || f.check === only, controls: [pickCheck],
      text: f => [f.check, f.model, f.summary].join(' ')});


  function show(f) {
    detail.replaceChildren(
      el('h2', {text: f.check}),
      el('div', {class: 'path'}, [link(f.model), el('span', {class: 'mono tot',
        text: '  ·  ' + (f.file || '')})]),
      el('p', {class: 'prose', text: f.summary}),
      section('what it means', el('p', {class: 'prose', text: f.detail || ''})),
      section('severity', kv([
        ['weight', String(f.weight)],
        ['marts downstream', String(f.marts)],
        ['descendants', String(f.descendants)],
        ['rests on', f.rests_on
          ? el('span', {class: 'pill judged', text: f.rests_on})
          : el('span', {class: 'pill declared', text: 'structural: a parser decided it'})],
        ['ruled', el('span', {class: 'pill ' + (f.ruled_finding ? 'on' : ''),
          text: f.ruled_finding ? 'a person read this finding'
            : (f.ruled_model ? 'a person ruled on this model, but not on this finding'
                             : 'nobody has read it')})],
      ])),
      section('evidence', kvAny(f.evidence)),
      section('rule on it', el('p', {class: 'mono prose',
        text: "assay review -i\nrule(finding='" + f.id + "', verdict=..., why=...)"})));
  }

  host.replaceChildren(
    el('p', {class: 'note', text: 'Ranked by weight, which is the base severity lifted by reach: '
      + 'the same defect on a leaf and on a model nine marts read are not the same finding.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a finding.'}));
  const first = $('tbody tr', list); if (first) first.click();
}

/* ------------------------------------------------------------------------------ Answers */
function answersTab(host) {
  const fam = {};
  for (const a of DATA.decisions) {
    const p = a.question.split('__')[0];
    const g = fam[p] = fam[p] || {prefix: p, rows: [], sum: 0, n: 0, low: 0, versions: {}};
    g.rows.push(a);
    if (a.confidence != null) { g.sum += a.confidence; g.n++; if (a.confidence < 0.6) g.low++; }
    if (a.prompt_version) g.versions[a.prompt_version] = 1;
  }
  const qByPrefix = {};
  for (const q of DATA.questions) if (q.id_prefix) qByPrefix[q.id_prefix] = q.name;
  const groups = Object.values(fam).sort((a, b) => b.rows.length - a.rows.length);

  host.replaceChildren(drill({
    noun: 'question families', groups: groups, groupSort: 'n', groupFilter: 'filter families...',
    blurb: 'The live answer to every question asked about this project: one row per subject and '
      + 'question, the latest. Grouped by the question that asked it, because 8,449 answers '
      + 'sorted by id is a filing cabinet. Below 0.60 nothing is reported as a finding, so the '
      + 'low column is where the model is telling you it cannot tell.',
    groupText: g => g.prefix + ' ' + (qByPrefix[g.prefix] || ''),
    label: g => (qByPrefix[g.prefix] || g.prefix) + ' · ' + g.rows.length + ' answer(s)',
    groupCols: [
      {key: 'fam', label: 'family', mono: 1, val: g => qByPrefix[g.prefix] || g.prefix,
       cell: g => { const s = el('span', {});
         s.append(el('span', {class: 'mono', text: qByPrefix[g.prefix] || g.prefix}));
         if (!qByPrefix[g.prefix]) s.append(el('span', {class: 'pill',
           text: 'no bank claims ' + g.prefix}));
         return s; }},
      {key: 'n', label: 'answers', n: 1, val: g => g.rows.length},
      {key: 'mean', label: 'mean conf', n: 1, val: g => g.n ? g.sum / g.n : null,
       cell: g => conf(g.n ? g.sum / g.n : null)},
      {key: 'low', label: 'under 0.60', n: 1, val: g => g.low,
       cell: g => el('span', {class: g.low ? 'low' : 'tot', text: String(g.low)})},
      {key: 'v', label: 'versions', mono: 1, val: g => Object.keys(g.versions).sort().join(', ')},
    ],
    rowsOf: g => g.rows,
    rowCols: [
      {key: 'q', label: 'question', mono: 1, val: a => a.question},
      {key: 'ctx', label: 'subject', val: a => a.context || a.key,
       cell: a => { const n = (a.key || '').split('.').pop().split('::')[0];
         return BY_NAME[n] ? link(n) : el('span', {text: a.context || a.key}); }},
      {key: 'about', label: 'about', val: a => a.context},
      {key: 'a', label: 'answered', val: a => a.answer},
      {key: 'c', label: 'conf', n: 1, val: a => a.confidence, cell: a => conf(a.confidence)},
      {key: 'r', label: 'next best', val: a => a.runner_up && a.runner_up[0],
       cell: a => a.runner_up ? el('span', {class: 'tot',
         text: a.runner_up[0] + ' ' + a.runner_up[1].toFixed(2)}) : el('span')},
      {key: 'v', label: 'version', mono: 1, val: a => a.prompt_version},
    ],
    rowSort: 'c', rowDir: 1,
    rowText: a => [a.question, a.key, a.context, a.answer, a.prompt_version].join(' '),
  }));
}

/* *** THE THINGS YOU CONFIGURE BY HAND WERE THE ONES DUMPED AS RAW JSON. ***
   The vocabulary, the question text and the per-check policy are hand-written and hand-
   maintained, so they are exactly what a person opens this page to read, and they were the only
   parts rendered as a `<pre>` blob. These render whatever shape the value happens to be. */
function kvAny(v, depth) {
  depth = depth || 0;
  if (v == null || v === '') return el('span', {class: 'tot', text: '—'});
  if (Array.isArray(v)) {
    if (!v.length) return el('span', {class: 'tot', text: 'none'});
    if (v.every(x => typeof x !== 'object'))
      return el('div', {class: 'chips flat'}, v.map(x => el('span', {class: 'chip mono',
        text: String(x)})));
    return el('div', {}, v.map(x => el('div', {class: 'sub'}, [kvAny(x, depth + 1)])));
  }
  if (typeof v === 'object') {
    const d = el('dl', {class: 'kv' + (depth ? ' sub' : '')});
    for (const k of Object.keys(v).sort()) {
      d.append(el('dt', {text: k}));
      d.append(el('dd', {}, [kvAny(v[k], depth + 1)]));
    }
    return d;
  }
  if (typeof v === 'string' && v.length > 90)
    return el('p', {class: 'prose', text: v});
  return el('span', {text: String(v)});
}

/* ---------------------------------------------------------------------------- Questions */
function questionsTab(host) {
  const detail = el('div', {class: 'detail'});
  const byFam = {};
  for (const a of DATA.adjudications) {
    const k = a.family || ''; byFam[k] = byFam[k] || {human: 0, all: 0};
    byFam[k].all++; if (a.source === 'human') byFam[k].human++;
  }
  const asked = {};
  for (const d of DATA.decisions) { const p = d.question.split('__')[0];
    asked[p] = (asked[p] || 0) + 1; }

  const list = grid(DATA.questions, [
    {key: 'name', label: 'family', mono: 1, val: q => q.name},
    {key: 'v', label: 'version', mono: 1, val: q => q.prompt_version},
    {key: 'asked', label: 'asked', n: 1, val: q => asked[q.id_prefix] || 0},
    {key: 'human', label: 'human', n: 1, val: q => (byFam[q.name] || {}).human || 0,
     cell: q => { const n = (byFam[q.name] || {}).human || 0;
       return el('span', {class: n ? 'ok' : 'tot', text: String(n)}); }},
  ], {placeholder: 'filter questions...', scroll: 1, sort: 'name', pick: q => show(q),
      text: q => [q.name, q.id_prefix, q.prompt_version, JSON.stringify(q.instructions)].join(' ')});

  function show(q) {
    const n = (byFam[q.name] || {}).human || 0;
    const d = detail; d.replaceChildren();
    d.append(el('h2', {text: q.name}));
    d.append(el('div', {class: 'path mono',
      text: [q.id_prefix, q.prompt_version, q.kind, q.origin].filter(Boolean).join('  ·  ')}));

    d.append(section('how many verdicts, and whose', kv([
      ['asked on this project', num(asked[q.id_prefix] || 0) + ' subject(s)'],
      ['human verdicts', el('span', {class: 'pill ' + (n ? 'on' : 'bad'), text: String(n)})],
      ['all verdicts', String((byFam[q.name] || {}).all || 0)],
    ])));
    d.append(el('p', {class: 'note', text: 'Only a human verdict counts toward min_adjudications. '
      + 'An agent ruling is evidence and never authority: it cannot gate a build, satisfy the '
      + 'verdict floor, anchor the regression check, or move the ruled-on number.'}));

    /* The question as it is SENT, as prose rather than as a JSON object. */
    const ins = q.instructions || {};
    const qs = el('div');
    if (ins.question) qs.append(el('p', {class: 'quote', text: ins.question}));
    for (const k of Object.keys(ins).sort()) {
      if (k === 'question') continue;
      qs.append(el('p', {class: 'note'}, [el('b', {text: k + ': '}),
                                          el('span', {text: String(ins[k])})]));
    }
    d.append(section('what it asks', qs));

    /* Every option it may return, each one a block: the name, what it means, what it is NOT for,
       and the examples. This is the text `effectiveness` measures agreement against, so it is
       the text a person needs when a version number moves. */
    const crit = q.criteria || {};
    const opts = el('div');
    for (const name of Object.keys(crit).sort()) {
      const c = crit[name], blk = el('div', {class: 'opt'});
      blk.append(el('div', {class: 'optname mono', text: name}));
      if (typeof c === 'string') { blk.append(el('p', {class: 'prose', text: c})); }
      else {
        if (c.what) blk.append(el('p', {class: 'prose', text: c.what}));
        if (c.not_for) blk.append(el('p', {class: 'note'},
          [el('b', {text: 'not for: '}), el('span', {text: String(c.not_for)})]));
        for (const k of Object.keys(c).sort()) {
          if (k === 'what' || k === 'not_for' || k === 'examples') continue;
          blk.append(el('p', {class: 'note'},
            [el('b', {text: k + ': '}), el('span', {text: String(c[k])})]));
        }
        if ((c.examples || []).length)
          blk.append(el('div', {class: 'chips flat'},
            c.examples.map(x => el('span', {class: 'chip mono', text: String(x)}))));
      }
      opts.append(blk);
    }
    d.append(section('the ' + Object.keys(crit).length + ' answers it may give', opts));
  }

  host.replaceChildren(
    el('p', {class: 'note', text: 'Every question assay will ask, in full. The text is the thing '
      + 'being measured, so it sits next to the measurement: agreement is reported per '
      + 'prompt_version and a version number on its own tells a reader nothing about what '
      + 'changed.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a question to read its instructions and every option, exactly as they are sent.'}));
  const first = $('tbody tr', list); if (first) first.click();
}

/* ------------------------------------------------------------------------------- Config */
function configTab(host) {
  const c = DATA.config || {}, bits = [];
  bits.push(el('p', {class: 'note', text: 'What was actually resolved, which is not always what '
    + 'the file says. Everything under here you wrote by hand.'}));

  const scalars = Object.entries(c).filter(([, v]) => typeof v !== 'object' || v === null);
  if (scalars.length) bits.push(section('resolved', kv(scalars.map(([k, v]) => [k, String(v)]))));

  /* *** THE VOCABULARY IS THE POINT OF THE WHOLE CONFIG AND IT WAS A JSON BLOB. ***
     Fifteen terms written once made `traverse` flag wdid joins without anyone writing a water
     question. Knowledge written once reaching questions nobody wrote is the promise, so it gets
     a table you can read rather than a pre block you skim past. */
  if (c.vocab && Object.keys(c.vocab).length) {
    const rows = Object.keys(c.vocab).sort().map(k => ({term: k, def: c.vocab[k]}));
    bits.push(section('vocabulary (' + rows.length + ' term(s), sent with every question)',
      grid(rows, [
        {key: 'term', label: 'term', mono: 1, val: r => r.term},
        {key: 'def', label: 'what it means here', val: r => JSON.stringify(r.def),
         cell: r => kvAny(r.def)},
      ], {placeholder: 'filter terms...', cap: 400})));
  }

  if (c.questions && Object.keys(c.questions).length) {
    const rows = Object.keys(c.questions).sort().map(k => ({q: k, v: c.questions[k]}));
    bits.push(section('per-check policy (' + rows.length + ')', grid(rows, [
      {key: 'q', label: 'check', mono: 1, val: r => r.q},
      {key: 'v', label: 'configured', val: r => JSON.stringify(r.v), cell: r => kvAny(r.v)},
    ], {placeholder: 'filter...', cap: 400})));
  }

  if (c.waivers && Object.keys(c.waivers).length) {
    const rows = [];
    for (const m of Object.keys(c.waivers).sort())
      for (const w of [].concat(c.waivers[m])) rows.push({model: m, w: w});
    bits.push(section('waivers (' + rows.length + ')', grid(rows, [
      {key: 'model', label: 'model', mono: 1, val: r => r.model, cell: r => link(r.model)},
      {key: 'w', label: 'waived, and why', val: r => JSON.stringify(r.w), cell: r => kvAny(r.w)},
    ], {placeholder: 'filter waivers...', cap: 400})));
    bits.push(el('p', {class: 'note', text: 'A waived finding never reaches the findings table, '
      + 'so nothing above counts it. A waiver whose justification is "looks fine" is how a real '
      + 'finding gets silenced, which is why the reason is required and is shown here.'}));
  }

  for (const k of ['practices', 'explanations']) {
    if (c[k] && Object.keys(c[k]).length) bits.push(section(k, kvAny(c[k])));
  }

  if (DATA.runs.length) bits.push(section('runs recorded (' + DATA.runs.length + ')',
    grid(DATA.runs, [
      {key: 'run', label: 'run', mono: 1, val: r => r.run_id},
      {key: 'when', label: 'started', mono: 1, val: r => r.started_at || ''},
      {key: 'av', label: 'assay', mono: 1, val: r => r.assay_version},
      {key: 'dv', label: 'dbt', mono: 1, val: r => r.dbt_version},
      {key: 'm', label: 'models', n: 1, val: r => r.models},
      {key: 'ok', label: 'readable', n: 1, val: r => r.readable},
      {key: 'no', label: 'unreadable', n: 1, val: r => r.unreadable,
       cell: r => el('span', {class: r.unreadable ? 'bad' : 'tot', text: String(r.unreadable)})},
    ], {placeholder: 'filter runs...', sort: 'when', dir: -1})));

  if (DATA.unreadable.length) {
    bits.push(section('what assay could NOT read (' + DATA.unreadable.length + ')',
      grid(DATA.unreadable, [
        {key: 'name', label: 'model', mono: 1, val: u => u.name},
        {key: 'path', label: 'path', mono: 1, val: u => u.path},
        {key: 'why', label: 'why', val: u => u.why},
      ], {placeholder: 'filter...', cap: 500})));
    bits.push(el('p', {class: 'note', text: 'A model absent from every table in this file because '
      + 'its SQL would not parse looks identical, from outside, to a model with nothing wrong '
      + 'with it. That is why it is named here. An absent audit is never a pass.'}));
  }
  host.replaceChildren(...bits);
}

/* --------------------------------------------------------------------------- Understood */
function understoodTab(host) {
  /* An iframe, because the record is a whole document with its own stylesheet and this page has
     one too. srcdoc is same-origin, so the height can follow its content instead of guessing. */
  const f = el('iframe', {style: 'width:100%;border:1px solid var(--line);border-radius:8px;'
    + 'background:#fff;height:80vh', title: 'the record'});
  host.replaceChildren(
    el('p', {class: 'note', text: 'The record: the one surface here with an argument to make '
      + 'rather than a table to show. It is also what `assay page --plain` writes on its own, and '
      + 'what `record.html` holds in the data artifact, small enough to commit and to read a diff '
      + 'of.'}),
    f);
  f.srcdoc = DATA.record || '';
  f.onload = () => { try {
    const h = f.contentDocument.body.scrollHeight;
    if (h > 100) f.style.height = (h + 24) + 'px';
  } catch (e) { /* height stays at the default; nothing here depends on it */ } };
}

/* ---------------------------------------------------------------------------------- tabs */
const VIEWS = {models: modelsTab, chain: chainTab, claims: claimsTab, findings: findingsTab,
               answers: answersTab, questions: questionsTab, config: configTab,
               understood: understoodTab};
const built = {};
function open(name) {
  document.querySelectorAll('nav button').forEach(b =>
    b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('.panel').forEach(p => { p.hidden = p.id !== 'p-' + name; });
  const host = document.getElementById('p-' + name);
  /* Built once, on first open. A 358-model warehouse renders eight tabs' worth of tables in well
     under a second, but there is no reason to render seven of them nobody has looked at. */
  if (!built[name]) { built[name] = 1; VIEWS[name](host); }
  /* *** replaceState THROWS ON file:// IN SOME BROWSERS, AND file:// IS THE POINT. ***
     A SecurityError here would abort `open` after the panels were swapped but before anything
     was built, so a tab would go blank. The hash is a convenience; the tab is not. */
  try { if (location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name); }
  catch (e) { /* no deep link, and every tab still works */ }
}
/* *** A FIXED CARD MUST NOT OUTLIVE THE THING IT POINTS AT. ***
   It sits on the body, so nothing removes it when the panel under it changes. Scrolling moves
   the node out from under it; switching tabs replaces everything it described. Both dismiss it,
   as do Escape and a click anywhere else -- the last one being the only way out on a touch
   device, where there is no canvas to click. */
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') dismissCards(); });
document.addEventListener('click', ev => {
  if (!ev.target.closest || !ev.target.closest('.pop')) dismissCards();
}, true);
document.addEventListener('scroll', () => dismissCards(), true);
window.addEventListener('resize', () => dismissCards());

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => { dismissCards(); open(b.dataset.tab); }; });
open(VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : 'understood');
"""
