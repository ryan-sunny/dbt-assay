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
    tabs = [
        ("models", "Models", counts["models"]),
        ("chain", "The chain", counts["edges"]),
        ("claims", "Claims", counts["claims"]),
        ("findings", "Findings", counts["findings"]),
        ("answers", "Answers", counts["decisions"]),
        ("questions", "Questions", counts["questions"]),
        ("config", "Config", None),
        ("understood", "Understood", None),
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

/* ---------------------------------------------------------------------------- Models, the front door */
function modelsTab(host) {
  const detail = el('div', {class: 'detail'});
  const list = grid(M, [
    {key: 'name', label: 'model', mono: 1, val: m => m.name},
    {key: 'layer', label: 'layer', val: m => m.layer},
    {key: 'marts', label: 'marts', n: 1, val: m => m.marts},
    {key: 'findings', label: 'find', n: 1, val: m => m.findings.length,
     cell: m => el('span', {text: m.findings.length || '', class: m.findings.length ? '' : 'tot'})},
  ], {placeholder: 'filter models, paths, descriptions...', scroll: 1, pick: m => show(m),
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
      ['the SQL says', m.derived_grain.length ? el('span', {class: 'mono', text: m.derived_grain.join(', ')}) : el('span', {class: 'tot', text: 'nothing settles it'})],
      ['materialized', m.materialized],
      ['reach', el('span', {text: m.marts + ' mart(s), ' + m.descendants + ' descendant(s)'})],
    ])));

    d.append(section('columns (' + m.columns.length + ')', grid(m.columns, [
      {key: 'name', label: 'column', mono: 1, val: c => c.name,
       cell: c => { const s = el('span', {}); s.append(el('span', {text: c.name + ' '}));
         if (c.in_key) s.append(el('span', {class: 'pill on', text: 'key'})); return s; }},
      {key: 'role', label: 'role', val: c => c.role && c.role.value, cell: c => fact(c.role)},
      {key: 'prov', label: 'came from', val: c => c.provenance && c.provenance.value,
       cell: c => fact(c.provenance)},
    ], {placeholder: 'filter columns...', cap: 400, emptyText: 'no columns known'})));

    const hops = DATA.edges.filter(x => x.child === m.uid);
    d.append(section('what it reads, hop by hop (' + hops.length + ')', hops.length ? grid(hops, [
      {key: 'p', label: 'parent', mono: 1, val: h => h.parent_name},
      {key: 'kind', label: 'join', val: h => h.kind || '',
       cell: h => { const s = el('span', {});
         if (h.kind) s.append(el('span', {class: 'pill', text: h.kind}));
         if (h.driving) s.append(el('span', {class: 'pill on', text: 'drives'}));
         if (h.union_arm) s.append(el('span', {class: 'pill', text: 'union arm'}));
         if (h.unique_key) s.append(el('span', {class: 'pill declared', text: 'unique key'}));
         return s; }},
      {key: 'on', label: 'on', mono: 1, val: h => (h.joined_on || []).join(', ')},
      {key: 'carried', label: 'carried', n: 1, val: h => h.carried},
      {key: 'dropped', label: 'dropped', n: 1, val: h => h.dropped,
       cell: h => { if (!h.dropped) return el('span', {class: 'tot', text: '0'});
         const det = el('details'); det.append(el('summary', {text: String(h.dropped)}));
         det.append(el('pre', {text: (h.dropped_cols || []).join('\n')})); return det; }},
    ], {placeholder: 'filter hops...', cap: 200}) : el('p', {class: 'empty', text: 'reads nothing; this is a leaf'})));

    if (m.read_by.length) d.append(section('read by (' + m.read_by.length + ')',
      el('p', {class: 'mono prose', text: m.read_by.join(', ')})));

    const fs = m.findings.map(i => FIND[i]).filter(Boolean);
    d.append(section('findings (' + fs.length + ')', fs.length ? grid(fs, [
      {key: 'check', label: 'check', mono: 1, val: f => f.check},
      {key: 'what', label: 'what', val: f => f.summary},
      {key: 'ruled', label: 'ruled', val: f => f.ruled_finding ? 1 : 0,
       cell: f => el('span', {class: 'pill ' + (f.ruled_finding ? 'on' : ''),
         text: f.ruled_finding ? 'a person read this' : (f.ruled_model ? 'model ruled, not this finding' : 'unread')})},
    ], {placeholder: 'filter findings...', cap: 200}) : el('p', {class: 'empty', text: 'nothing found on this model'})));

    const cs = m.claims.map(i => CLAIM[i]).filter(Boolean);
    d.append(section('what this project claims about it (' + cs.length + ')', cs.length ? grid(cs, [
      {key: 'text', label: 'claim', val: c => c.text},
      {key: 'where', label: 'written', mono: 1, val: c => c.source_ref},
      {key: 'v', label: 'the code', val: c => c.contradicted == null ? 0 : c.contradicted,
       cell: c => c.contradicted == null
         ? el('span', {class: 'tot', text: 'not contradicted'})
         : el('span', {class: 'pill bad', text: 'contradicts @' + c.contradicted.toFixed(2)})},
    ], {placeholder: 'filter claims...', cap: 300}) : el('p', {class: 'empty', text: 'no claims extracted for this model'})));

    const ds = m.decisions.map(i => DATA.decisions[i]).filter(Boolean);
    d.append(section('every answer given about it (' + ds.length + ')', ds.length ? grid(ds, [
      {key: 'q', label: 'question', mono: 1, val: a => a.question},
      {key: 'a', label: 'answered', val: a => a.answer},
      {key: 'c', label: 'conf', n: 1, val: a => a.confidence,
       cell: a => el('span', {text: a.confidence == null ? '' : a.confidence.toFixed(2)})},
      {key: 'r', label: 'next best', val: a => a.runner_up && a.runner_up[0],
       cell: a => a.runner_up ? el('span', {class: 'tot',
         text: a.runner_up[0] + ' ' + a.runner_up[1].toFixed(2)}) : el('span')},
      {key: 'ctx', label: 'about', val: a => a.context},
    ], {placeholder: 'filter answers...', cap: 400}) : el('p', {class: 'empty', text: 'nothing has been asked about this model'})));
  }

  host.replaceChildren(el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a model. Everything assay knows about it is here: what one row is and who settled that, every column with its role and where its value came from, every hop in and out, what the project claims about it, and every answer ever given.'}));
  if (M.length) { const first = $('tbody tr', list); if (first) first.click(); }
}

/* ------------------------------------------------------------------------------------ The chain */
function chainTab(host) {
  const rows = DATA.edges;
  host.replaceChildren(
    el('p', {class: 'note', text: 'Every hop in the DAG and what it carries. The dropped columns are the answer to "why does this mart not have that field", and no other surface shows them.'}),
    grid(rows, [
      {key: 'child', label: 'child', mono: 1, val: h => h.child_name},
      {key: 'parent', label: 'parent', mono: 1, val: h => h.parent_name},
      {key: 'kind', label: 'join', val: h => h.kind || '',
       cell: h => { const s = el('span', {});
         if (h.kind) s.append(el('span', {class: 'pill', text: h.kind}));
         if (h.driving) s.append(el('span', {class: 'pill on', text: 'drives'}));
         if (h.union_arm) s.append(el('span', {class: 'pill', text: 'union arm'}));
         return s; }},
      {key: 'on', label: 'joined on', mono: 1, val: h => (h.joined_on || []).join(', ')},
      {key: 'avail', label: 'available', n: 1, val: h => h.available},
      {key: 'carried', label: 'carried', n: 1, val: h => h.carried},
      {key: 'dropped', label: 'dropped', n: 1, val: h => h.dropped,
       cell: h => { if (!h.dropped) return el('span', {class: 'tot', text: '0'});
         const det = el('details'); det.append(el('summary', {text: String(h.dropped)}));
         det.append(el('pre', {text: (h.dropped_cols || []).join('\n')})); return det; }},
      {key: 'loss', label: 'rows kept', n: 1,
       val: h => (h.row_loss && h.row_loss.length === 2 && h.row_loss[0]) ? h.row_loss[1] / h.row_loss[0] : null,
       cell: h => (h.row_loss && h.row_loss.length === 2 && h.row_loss[0])
         ? el('span', {text: pct(h.row_loss[1] / h.row_loss[0]) + ' (' + num(h.row_loss[1]) + ' of ' + num(h.row_loss[0]) + ')'})
         : el('span', {class: 'tot', text: 'not counted'})},
    ], {placeholder: 'filter by model, parent, column...', sort: 'child',
        text: h => [h.child_name, h.parent_name, (h.joined_on || []).join(' '), (h.dropped_cols || []).join(' ')].join(' '),
        cap: 2000}));
}

/* ----------------------------------------------------------------------------------- Claims */
function claimsTab(host) {
  host.replaceChildren(
    el('p', {class: 'note', text: 'Every sentence this project says about itself, extracted from descriptions and SQL comments, with where it was written and what the code said back. A claim with no verdict was never asked, which is not the same as supported.'}),
    grid(DATA.claims, [
      {key: 'model', label: 'model', mono: 1, val: c => c.subject_name},
      {key: 'text', label: 'claim', val: c => c.text},
      {key: 'kind', label: 'kind', val: c => c.kind,
       cell: c => el('span', {class: 'pill', text: c.kind || 'unclassified'})},
      {key: 'from', label: 'written', mono: 1, val: c => c.source_ref},
      {key: 'v', label: 'the code', val: c => c.contradicted == null ? -1 : c.contradicted,
       cell: c => c.contradicted == null
         ? el('span', {class: 'tot', text: 'not contradicted'})
         : el('span', {class: 'pill bad', text: 'contradicts @' + c.contradicted.toFixed(2)})},
    ], {placeholder: 'filter claims...', sort: 'model', cap: 2000,
        text: c => [c.subject_name, c.text, c.source_ref, c.kind].join(' ')}));
}

/* --------------------------------------------------------------------------------- Findings */
function findingsTab(host) {
  const detail = el('div', {class: 'detail'});
  const list = grid(DATA.findings, [
    {key: 'check', label: 'check', mono: 1, val: f => f.check},
    {key: 'model', label: 'model', mono: 1, val: f => f.model},
    {key: 'w', label: 'weight', n: 1, val: f => f.weight,
     cell: f => el('span', {text: f.weight.toFixed(1)})},
    {key: 'marts', label: 'marts', n: 1, val: f => f.marts},
  ], {placeholder: 'filter findings...', scroll: 1, sort: 'w', dir: -1, pick: f => show(f),
      text: f => [f.check, f.model, f.summary].join(' ')});

  function show(f) {
    detail.replaceChildren(
      el('h2', {text: f.check}),
      el('div', {class: 'path mono', text: f.model + '  ·  ' + (f.file || '')}),
      el('p', {class: 'prose', text: f.summary}),
      section('what it means', el('p', {class: 'prose', text: f.detail || ''})),
      section('severity', kv([
        ['weight', String(f.weight)],
        ['marts downstream', String(f.marts)],
        ['descendants', String(f.descendants)],
        ['rests on', f.rests_on ? el('span', {class: 'pill judged', text: f.rests_on})
          : el('span', {class: 'pill declared', text: 'structural: a parser decided it'})],
        ['ruled', el('span', {class: 'pill ' + (f.ruled_finding ? 'on' : ''),
          text: f.ruled_finding ? 'a person read this finding'
            : (f.ruled_model ? 'a person ruled on this model, but not on this finding' : 'nobody has read it')})],
      ])),
      section('evidence', el('pre', {text: JSON.stringify(f.evidence, null, 2)})),
      section('rule on it', el('p', {class: 'mono prose',
        text: "assay review -i\nrule(finding='" + f.id + "', verdict=..., why=...)"})));
  }

  host.replaceChildren(el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a finding. Ranked by weight, which is the base severity lifted by reach: the same defect on a leaf and on a model nine marts read are not the same finding.'}));
  const first = $('tbody tr', list); if (first) first.click();
}

/* ---------------------------------------------------------------------------------- Answers */
function answersTab(host) {
  host.replaceChildren(
    el('p', {class: 'note', text: 'The live answer to every question asked about this project: one row per subject and question, the latest. Every version is kept in the store because that is what makes effectiveness possible, and serving all of them at once is how traversal once reported twelve verdicts for four hops.'}),
    grid(DATA.decisions, [
      {key: 'q', label: 'question', mono: 1, val: a => a.question},
      {key: 'key', label: 'about', mono: 1, val: a => a.key},
      {key: 'ctx', label: 'subject', val: a => a.context},
      {key: 'a', label: 'answered', val: a => a.answer},
      {key: 'c', label: 'conf', n: 1, val: a => a.confidence,
       cell: a => el('span', {text: a.confidence == null ? '' : a.confidence.toFixed(2)})},
      {key: 'r', label: 'next best', val: a => a.runner_up && a.runner_up[0],
       cell: a => a.runner_up ? el('span', {class: 'tot',
         text: a.runner_up[0] + ' ' + a.runner_up[1].toFixed(2)}) : el('span')},
      {key: 'v', label: 'version', mono: 1, val: a => a.prompt_version},
    ], {placeholder: 'filter answers...', sort: 'q', cap: 2000,
        text: a => [a.question, a.key, a.context, a.answer, a.prompt_version].join(' ')}));
}

/* -------------------------------------------------------------------------------- Questions */
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
       return el('span', {class: n ? '' : 'tot', text: String(n)}); }},
  ], {placeholder: 'filter questions...', scroll: 1, sort: 'name', pick: q => show(q),
      text: q => [q.name, q.id_prefix, q.prompt_version, JSON.stringify(q.instructions)].join(' ')});

  function show(q) {
    const n = (byFam[q.name] || {}).human || 0;
    detail.replaceChildren(
      el('h2', {text: q.name}),
      el('div', {class: 'path mono', text: q.id_prefix + '  ·  ' + q.prompt_version + '  ·  ' + q.kind}),
      section('how many verdicts, and whose', kv([
        ['asked on this project', num(asked[q.id_prefix] || 0) + ' subject(s)'],
        ['human verdicts', el('span', {class: 'pill ' + (n ? 'on' : 'bad'), text: String(n)})],
        ['all verdicts', String((byFam[q.name] || {}).all || 0)],
      ])),
      el('p', {class: 'note', text: 'Only a human verdict counts toward min_adjudications. An agent ruling is evidence and never authority: it cannot gate a build, satisfy the verdict floor, anchor the regression check, or move the ruled-on number.'}),
      section('what it asks', el('pre', {text: JSON.stringify(q.instructions, null, 2)})),
      section('the options, and how each is described', el('pre', {text: JSON.stringify(q.criteria, null, 2)})));
  }

  host.replaceChildren(
    el('p', {class: 'note', text: 'Every question assay will ask, in full. The text is the thing being measured, so it sits next to the measurement: agreement is reported per prompt_version and a version number on its own tells a reader nothing about what changed.'}),
    el('div', {class: 'wrap2'}, [list, detail]));
  detail.append(el('p', {class: 'empty', text: 'Pick a question to read its instructions and every option, exactly as they are sent.'}));
  const first = $('tbody tr', list); if (first) first.click();
}

/* ----------------------------------------------------------------------------------- Config */
function configTab(host) {
  const c = DATA.config, bits = [];
  bits.push(el('p', {class: 'note', text: 'What was actually resolved, which is not always what the file says.'}));
  bits.push(section('resolved', kv(Object.entries(c)
    .filter(([, v]) => typeof v !== 'object')
    .map(([k, v]) => [k, String(v)]))));
  for (const k of ['vocab', 'questions', 'practices', 'waivers', 'explanations']) {
    if (c[k] && Object.keys(c[k]).length)
      bits.push(section(k, el('pre', {text: JSON.stringify(c[k], null, 2)})));
  }
  if (DATA.runs.length) bits.push(section('runs recorded', grid(DATA.runs, [
    {key: 'run', label: 'run', mono: 1, val: r => r.run_id},
    {key: 'av', label: 'assay', mono: 1, val: r => r.assay_version},
    {key: 'dv', label: 'dbt', mono: 1, val: r => r.dbt_version},
    {key: 'm', label: 'models', n: 1, val: r => r.models},
    {key: 'ok', label: 'readable', n: 1, val: r => r.readable},
    {key: 'no', label: 'unreadable', n: 1, val: r => r.unreadable},
  ], {placeholder: 'filter runs...'})));
  if (DATA.unreadable.length) {
    bits.push(section('what assay could NOT read (' + DATA.unreadable.length + ')', grid(DATA.unreadable, [
      {key: 'name', label: 'model', mono: 1, val: u => u.name},
      {key: 'path', label: 'path', mono: 1, val: u => u.path},
      {key: 'why', label: 'why', val: u => u.why},
    ], {placeholder: 'filter...', cap: 500})));
    bits.push(el('p', {class: 'note', text: 'A model absent from every table in this file because its SQL would not parse looks identical, from outside, to a model with nothing wrong with it. That is why it is named here. An absent audit is never a pass.'}));
  }
  host.replaceChildren(...bits);
}

/* -------------------------------------------------------------------------------- Understood */
function understoodTab(host) {
  /* An iframe, because the record is a whole document with its own stylesheet and this page has
     one too. srcdoc is same-origin, so the height can follow its content instead of guessing. */
  const f = el('iframe', {style: 'width:100%;border:1px solid var(--line);border-radius:8px;' +
                                 'background:#fff;height:80vh', title: 'the record'});
  host.replaceChildren(
    el('p', {class: 'note', text: 'The record, unchanged: the one surface here with an argument to make rather than a table to show. It is also what `assay page --plain` writes on its own, small enough to commit and to hand to somebody.'}),
    f);
  f.srcdoc = DATA.record || '';
  f.onload = () => { try {
    const h = f.contentDocument.body.scrollHeight;
    if (h > 100) f.style.height = (h + 24) + 'px';
  } catch (e) { /* height stays at the default; nothing here depends on it */ } };
}

/* ------------------------------------------------------------------------------------- tabs */
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
document.querySelectorAll('nav button').forEach(b => { b.onclick = () => open(b.dataset.tab); });
open(VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : 'models');
"""
