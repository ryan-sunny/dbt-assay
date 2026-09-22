"""A form you fill in at your own pace, so ruling stops costing a turn each.

*** THE NUMBER WOULD NOT MOVE, AND THE INTERACTIVE LOOP IS WHY. ***
0 of 159 models ruled on by a person. The tool was never missing -- `assay review -i` has shipped
for most of this project's life -- and neither was the procedure: an agent walking findings one at
a time, reading the SQL first, is genuinely the right shape for the CALL. It is the wrong shape for
159 of them. One turn per finding is 159 turns, and nobody does 159 turns.

So the reading batches and the answering does not. An agent reads every finding once, offline, and
writes what it found into a form. The form is a single file that opens from `file://` with no
server, no port and nothing running, and it is filled in whenever there is ten minutes -- twenty
cards at a time, closing the tab without losing anything. When it is done the page hands back a
JSON and `assay review --load` records every verdict at once.

*** THE ROUND TRIP IS `probe --emit` / `--load`, WHICH THIS PROJECT ALREADY HAS. ***
`probe` writes the SQL out for you to run through your own dbt and takes the results back, because
assay never holds a credential. Same shape, different reason: assay never holds a verdict it was
not given.

*** ONE CARD PER (SUBJECT, QUESTION), BECAUSE THAT IS WHAT A VERDICT COVERS. ***
A ruling lands on the pair, not on the finding, so one answer clears every finding of that check on
that model. Cards per finding would have asked the same question several times and recorded the
last answer -- 260 findings are 212 pairs here, so 48 of them would have been asked twice.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

# The one thing a card cannot carry and still be honest.
NO_READ = "nothing on this question"

# *** A SEED'S "CODE" IS ITS DATA, AND ONE OF THEM WAS 4.9 MB. ***
# `seed_reaches_nothing` fires on seeds, whose `file` is a .csv. Read whole, two of them made the
# form 7.8 MB for 212 cards -- 91% of the page was two CSVs nobody would scroll. Nothing about a
# seed's 41,000th row helps anybody rule on whether the seed reaches anything.
#
# Capped rather than dropped, and the cap is STATED in the panel. A file that ends early without
# saying so is the same shape as a model that really is that short, and this codebase has found
# that defect in other people's warehouses often enough not to ship it here.
_CSV_ROWS = 20
_MAX_LINES = 900          # the largest real model on the field warehouse is 629 lines
_MAX_CHARS = 90_000       # ...and 46 KB


def _excerpt(name: str, text: str) -> str:
    """The file, or as much of it as is worth reading, always saying which."""
    lines = text.split("\n")
    if name.lower().endswith(".csv") and len(lines) > _CSV_ROWS + 1:
        head = lines[:_CSV_ROWS + 1]
        return ("\n".join(head)
                + f"\n\n-- assay: {_CSV_ROWS} of {len(lines) - 1:,} rows. This is a seed, and "
                  f"the rest of it is not what you are ruling on.")
    if len(lines) > _MAX_LINES or len(text) > _MAX_CHARS:
        keep = lines[:_MAX_LINES]
        return ("\n".join(keep)
                + f"\n\n-- assay: showing {len(keep):,} of {len(lines):,} lines. Open the file "
                  f"for the rest.")
    return text


def cards(findings, store, project_root, reads: dict | None = None) -> tuple[list, dict]:
    """`([card], {path: sql})`, ordered so the ones where a wrong verdict costs most come first.

    *** THE SQL IS STORED ONCE PER FILE, NOT ONCE PER CARD. ***
    159 models carry 212 cards. Embedding each card's SQL inside it would put a dozen models in
    twice and make the page a copy of the warehouse rather than a view of it.
    """
    agent: dict = {}
    if store is not None:
        try:
            for r in store.agent_rulings():
                key = str(r["subject"])
                if "::finding::" in key:
                    agent[key.split("::finding::")[1]] = r            # this exact finding
                else:
                    agent.setdefault(key.split("::")[0], r)           # the whole model
        except Exception:                                        # noqa: BLE001
            agent = {}

    # *** A PAIR SOMEBODY ALREADY RULED ON NEVER APPEARS -- AND ONLY THE PAIR. ***
    # Asking again is not harmless: it invites a second, different answer to the same question,
    # and the store keeps both.
    #
    # The first version also skipped on `store.ruled_subjects()`, which is SUBJECT-level. Ruling
    # `code_contradicts_a_claim` on a model therefore hid every OTHER check on that model, and it
    # hid them by removing the card rather than by marking it -- so a question nobody answered
    # disappeared from the form whose whole job is showing what nobody has answered. Measured:
    # four verdicts made 212 cards become 206 instead of 208.
    #
    # A verdict covers (subject, question). Nothing else may be inferred from it.
    human = set()
    if store is not None:
        try:
            human = store.ruled_pairs()
        except Exception:                                        # noqa: BLE001
            human = set()

    root = Path(project_root)
    by_pair: dict = {}
    for f in findings:
        if (str(f.subject), str(f.check)) in human:
            continue
        key = f"{f.subject}::{f.check}"
        c = by_pair.setdefault(key, {
            "key": key, "subject": str(f.subject), "model": f.subject_name,
            "question": str(f.check), "file": f.file or "", "marts": 0, "descendants": 0,
            "findings": [], "agent": None, "read": None})
        c["marts"] = max(c["marts"], int(f.marts or 0))
        c["descendants"] = max(c["descendants"], int(f.descendants or 0))
        ev = f.evidence or {}
        c["findings"].append({
            "id": f.id, "summary": f.summary or "", "detail": f.detail or "",
            # The quoted sentence, where the check is about one. Every claim family carries it.
            "claim": str(ev.get("claim") or ""),
        })
        a = agent.get(f.id) or agent.get(str(f.subject).split("::")[0])
        if a and not c["agent"]:
            c["agent"] = {
                "verdict": a["verdict"], "note": a["note"],
                # *** WHICH QUESTION THE AGENT WAS ANSWERING. ***
                # A model-level ruling lands on every finding that model has and usually
                # addressed a different one. Passing it off as an answer to THIS question is how
                # a person confirms a reading nobody did.
                "scope": ("this exact finding" if agent.get(f.id) else
                          "the model, so it may be about a different finding"),
            }

    sql: dict = {}
    for c in by_pair.values():
        if c["file"] and c["file"] not in sql:
            p = root / c["file"]
            try:
                sql[c["file"]] = _excerpt(c["file"], p.read_text(errors="replace"))
            except OSError:
                # Named rather than omitted: a card with no code is a card you cannot answer, and
                # a blank panel looks like a model with nothing in it.
                sql[c["file"]] = ""
        r = (reads or {}).get(c["key"])
        if r:
            c["read"] = {"verdict": str(r.get("verdict", "")),
                         "why": str(r.get("why", "") or r.get("note", ""))}
        for fd in c["findings"]:
            fd["detail"] = fd["detail"][:1200]
        c["findings"].sort(key=lambda d: d["id"])

    # A wrong verdict costs the most where the most marts are downstream. Ties break on the key,
    # so two runs over one store produce one order.
    out = sorted(by_pair.values(), key=lambda c: (-c["marts"], -c["descendants"], c["key"]))
    return out, sql


def load(payload) -> tuple[list, list]:
    """`([verdict], [problem])` from what the form handed back.

    *** NOTHING UNANSWERED BECOMES AN ANSWER. ***
    A card with no verdict is a card somebody scrolled past, and the whole premise of a `human`
    row is that a person actually answered it. Rows without one are counted and reported, never
    defaulted and never inferred from a note being present.
    """
    rows = payload.get("verdicts") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return [], ["the file has no `verdicts` list; this is not a form assay wrote"]
    ok, bad = [], []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            bad.append(f"row {i} is not an object")
            continue
        subj, q = str(r.get("subject", "")), str(r.get("question", ""))
        v = str(r.get("verdict", "")).strip().lower()
        if not subj or not q:
            bad.append(f"row {i} names no subject or question")
            continue
        if v not in ("agree", "disagree", "unclear"):
            bad.append(f"{subj.split('.')[-1]} / {q}: verdict is {v or 'empty'}, not recorded")
            continue
        ok.append({"subject": subj, "question": q, "verdict": v,
                   "note": str(r.get("note", "") or ""),
                   "correction": str(r.get("correction", "") or ""),
                   # *** THE FINDING IDS THE CARD COVERED, SO A `disagree` CAN ACTUALLY DISMISS. ***
                   # A verdict is recorded against (subject, question), which is the right grain
                   # for measuring a question and deliberately too coarse to delete evidence with
                   # -- one model carries eight findings of one check. The card knows exactly
                   # which ones it showed, so the dismissal lands on those and no others.
                   "findings": [str(x) for x in (r.get("findings") or []) if x]})
    # A total order, so loading the same file twice writes the same rows in the same sequence.
    ok.sort(key=lambda r: (r["subject"], r["question"]))
    return ok, bad


# --------------------------------------------------------------------------------- the page

_CSS = """
:root{--ink:#16232a;--dim:#6b7a80;--faint:#94a3aa;--line:#dfe6e8;--bg:#fbfcfc;--card:#fff;
--red:#9e2b20;--green:#5a6a2f;--blue:#2b5c7a;--amber:#8a6412}
*{box-sizing:border-box}
body{margin:0;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
color:var(--ink);background:var(--bg)}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);
padding:14px 24px}
h1{margin:0;font-size:17px}
h1 span{font-weight:400;color:var(--dim);font-size:13px;margin-left:8px}
.bar{display:flex;align-items:center;gap:14px;margin-top:8px;flex-wrap:wrap}
.count{font-variant-numeric:tabular-nums}
button{font:inherit;padding:5px 12px;border:1px solid var(--line);background:var(--bg);
border-radius:4px;cursor:pointer}
button:hover:not(:disabled){background:var(--card);border-color:var(--dim)}
button:disabled{opacity:.4;cursor:default}
button.go{background:var(--blue);color:#fff;border-color:var(--blue)}
main{padding:18px 24px 60px;max-width:none}
.card{border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:5px;
background:var(--card);padding:14px 16px;margin:0 0 14px}
.card.done{border-left-color:var(--green)}
.hd{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}
.hd b{font-size:15px}
.tag{font-size:11.5px;color:var(--dim);border:1px solid var(--line);border-radius:3px;
padding:1px 6px}
.lbl{font-size:11px;letter-spacing:.06em;color:var(--faint);text-transform:uppercase;
margin:10px 0 3px}
.q{border-left:2px solid var(--line);padding-left:10px;margin:0 0 4px}
.claim{font-style:italic}
pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;
background:var(--bg);border:1px solid var(--line);border-radius:3px;padding:8px 10px;
max-height:340px;overflow:auto}
pre .n{color:var(--faint);user-select:none}
details summary{cursor:pointer;color:var(--blue);font-size:12.5px}
.ans{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px;
padding-top:10px;border-top:1px solid var(--line)}
.ans label{display:inline-flex;gap:5px;align-items:center;border:1px solid var(--line);
border-radius:4px;padding:4px 10px;cursor:pointer}
.ans label:hover{border-color:var(--dim)}
.ans input:checked+span{font-weight:600}
.ans .note{flex:1;min-width:220px}
input[type=text]{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:4px;
width:100%;background:var(--bg)}
.note-none{color:var(--faint)}
.q.dim{color:var(--dim);font-size:13px}
.warn{color:var(--amber)}
footer{padding:0 24px 40px;color:var(--dim);font-size:12.5px}
"""

_JS = r"""
const D = JSON.parse(document.getElementById('assay-form').textContent);
const PER = 20;
const KEY = 'assay-review:' + D.project;
let answers = {}, page = 0;
try { answers = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { answers = {}; }
try { page = Math.max(0, parseInt(localStorage.getItem(KEY + ':page') || '0', 10) || 0); }
catch (e) { page = 0; }

/* Browser storage is a per-viewer convenience and it can throw or come back empty -- a private
   window, cleared site data, a preview. Every read and write is wrapped, and the page renders
   correctly with none of it: you lose your place, not your ability to answer. */
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(answers)); } catch (e) { /* ignore */ }
  try { localStorage.setItem(KEY + ':page', String(page)); } catch (e) { /* ignore */ }
}
function el(t, a, kids) {
  const n = document.createElement(t);
  for (const k in (a || {})) {
    if (k === 'text') n.textContent = a[k];
    else if (k === 'html') n.innerHTML = a[k];
    else n.setAttribute(k, a[k]);
  }
  for (const c of (kids || [])) if (c) n.append(c);
  return n;
}
const answered = () => Object.values(answers).filter(a => a && a.verdict).length;

function numbered(sql) {
  const p = el('pre', {});
  const lines = (sql || '').split('\n');
  const w = String(lines.length).length;
  lines.forEach((ln, i) => {
    p.append(el('span', {class: 'n', text: String(i + 1).padStart(w, ' ') + '  '}));
    p.append(document.createTextNode(ln + '\n'));
  });
  return p;
}

/* Whole numbers grouped, the same rule the rest of the tool follows. */
const num = n => (n == null ? '' : Number(n).toLocaleString('en-US'));

function card(c) {
  const a = answers[c.key] || {};
  const box = el('div', {class: 'card' + (a.verdict ? ' done' : '')});
  box.append(el('div', {class: 'hd'}, [
    el('b', {text: c.model}),
    el('span', {class: 'tag', text: c.question}),
    el('span', {class: 'tag', text: num(c.marts) + ' marts'}),
    el('span', {class: 'tag', text: c.file}),
  ]));

  box.append(el('div', {class: 'lbl', text: 'what assay found'}));
  /* *** EVERY FINDING ON A CARD IS THE SAME CHECK, SO ITS EXPLANATION IS THE SAME TEXT. ***
     `int_azcc_owners` carries three `arbitrary_pick` findings and drew the identical paragraph
     about `row_number() ... = 1` three times. Once it stopped being hidden behind a click, that
     turned a card into a wall -- and three copies of one sentence is what makes a reader skip
     the sentence. It is said once, after the findings it explains. */
  const seenDetail = new Set();
  for (const f of c.findings) {
    box.append(el('div', {class: 'q', text: f.summary}));
    if (f.claim) box.append(el('div', {class: 'q claim', text: '"' + f.claim + '"'}));
    /* *** THE SUMMARY ALONE IS NOT ENOUGH TO RULE ON, AND IT WAS BEHIND A CLICK. ***
       "the description claims something the code does not do" is a category, not a case. The
       detail is what says WHY, and it was first carried in the data and never drawn, then drawn
       inside a <details>. The reason a thing is on the page is not an appendix to it: a card
       that hides its reasoning is asking for a verdict on a headline, one click cheaper. */
    if (f.detail) seenDetail.add(f.detail);
  }
  for (const d of seenDetail) box.append(el('div', {class: 'q dim', text: d}));

  /* *** ONE SECTION, AND IT SAYS WHO. ***
     This drew two: "an agent said" from rulings in the store, and "my read" from the --reads
     file. Both are an AGENT's reading, and a card could therefore say "nothing on this question"
     directly above a full verdict -- contradicting itself -- while labelling the verdict MY READ
     to a person who had not touched the card yet. The obvious reading of that is "I already
     answered this and disagreed", which is the one thing a review form must never imply.
     Nothing on this page is the reader's until the reader clicks a radio. */
  box.append(el('div', {class: 'lbl', text: 'an agent read this'}));
  if (c.read) {
    box.append(el('div', {class: 'q', text: c.read.verdict + ' — ' + c.read.why}));
  }
  if (c.agent) {
    if (c.read) box.append(el('div', {class: 'lbl', text: 'and a ruling stored on this model'}));
    box.append(el('div', {class: 'q', text: c.agent.verdict + ' — ' + c.agent.note}));
    /* WHICH question it was answering. A model-level ruling lands on every finding that model
       has and usually addressed a different one; passing it off is how somebody confirms a
       reading nobody did. */
    box.append(el('div', {class: 'q' + (c.agent.scope.startsWith('the model') ? ' warn' : ''),
                          text: 'about: ' + c.agent.scope}));
  }
  if (!c.read && !c.agent) {
    box.append(el('div', {class: 'q note-none', text: D.no_read}));
  }

  const sql = D.sql[c.file];
  box.append(el('details', {}, [
    el('summary', {text: sql ? 'the code' : 'the code could not be read'}),
    numbered(sql),
  ]));

  const ans = el('div', {class: 'ans'});
  for (const v of ['agree', 'disagree', 'unclear']) {
    const r = el('input', {type: 'radio', name: 'v-' + c.key, value: v});
    if (a.verdict === v) r.checked = true;
    r.onchange = () => {
      answers[c.key] = Object.assign({}, answers[c.key], {verdict: v});
      box.classList.add('done'); save(); tick();
    };
    ans.append(el('label', {}, [r, el('span', {text: v})]));
  }
  const note = el('input', {type: 'text', class: 'note',
                            placeholder: 'why (optional, and the most useful thing here)'});
  note.value = a.note || '';
  note.oninput = () => {
    answers[c.key] = Object.assign({}, answers[c.key], {note: note.value}); save();
  };
  ans.append(note);
  box.append(ans);
  return box;
}

function tick() {
  const n = answered(), tot = D.cards.length;
  document.getElementById('count').textContent =
    n + ' of ' + tot + ' answered' + (n ? '' : ' — nothing is recorded until you download');
  document.getElementById('dl').disabled = n === 0;
  const pages = Math.max(1, Math.ceil(tot / PER));
  document.getElementById('where').textContent = 'page ' + (page + 1) + ' of ' + pages;
  document.getElementById('prev').disabled = page === 0;
  document.getElementById('next').disabled = page >= pages - 1;
}

function render() {
  const host = document.getElementById('cards');
  host.replaceChildren(...D.cards.slice(page * PER, page * PER + PER).map(card));
  window.scrollTo(0, 0);
  tick();
}

function download() {
  /* file:// cannot write to disk, so the answers leave as a download. No server, no port, and
     nothing to leave running -- the same delivery model every other page here has. */
  const out = [];
  for (const c of D.cards) {
    const a = answers[c.key];
    if (!a || !a.verdict) continue;      // never an answer nobody gave
    out.push({subject: c.subject, question: c.question, verdict: a.verdict,
              note: a.note || '', model: c.model, findings: c.findings.map(f => f.id)});
  }
  out.sort((x, y) => (x.subject + x.question < y.subject + y.question ? -1 : 1));
  const body = JSON.stringify(
    {project: D.project, by: document.getElementById('by').value || '', verdicts: out}, null, 2);
  const url = URL.createObjectURL(new Blob([body], {type: 'application/json'}));
  const a = el('a', {href: url, download: 'verdicts.json'});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

document.getElementById('prev').onclick = () => { page--; save(); render(); };
document.getElementById('next').onclick = () => { page++; save(); render(); };
document.getElementById('dl').onclick = download;
document.getElementById('clear').onclick = () => {
  if (!confirm('Clear every answer on this form? This cannot be undone.')) return;
  answers = {}; save(); render();
};
const pages = Math.max(1, Math.ceil(D.cards.length / PER));
if (page >= pages) page = 0;
render();
"""


def form_html(card_list: list, sql: dict, project: str, generated_at: str, version: str) -> str:
    """One self-contained file. No server, no fetch, no network."""
    e = html.escape
    blob = json.dumps({"project": project, "cards": card_list, "sql": sql, "no_read": NO_READ},
                      separators=(",", ":"), sort_keys=True, default=str)
    # `</script>` inside a model's SQL would end the tag and silently truncate the page. `<!--`
    # opens a comment inside a script element. A dbt model containing either is not exotic.
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")
    withread = sum(1 for c in card_list if c.get("read") or c.get("agent"))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(project)} &middot; assay review</title>
<style>{_CSS}</style></head><body>
<header>
<h1>{e(project)}<span>{len(card_list)} to rule on &middot; {withread} carry a reading &middot;
assay {e(version)} &middot; manifest {e(str(generated_at))}</span></h1>
<div class="bar">
  <button id="prev">&larr; previous</button>
  <span id="where"></span>
  <button id="next">next twenty &rarr;</button>
  <span class="count" id="count"></span>
  <input type="text" id="by" placeholder="your name" style="width:160px">
  <button class="go" id="dl">download verdicts.json</button>
  <button id="clear">clear</button>
</div>
</header>
<main><div id="cards"></div></main>
<footer>
Answers are kept in this browser as you go, so you can close the tab and come back. Nothing is
recorded anywhere until you download the file and run
<code>assay review --load verdicts.json</code>. A card you did not answer is never submitted:
`human` means somebody answered it, and a default would make that false.
<br><br>
Twenty at a time, highest blast radius first. Twenty and stopping is a good session &mdash; the
models with the most marts downstream are the ones where a wrong verdict costs something, and
there is no prize for reaching the end of the list.
</footer>
<script id="assay-form" type="application/json">{blob}</script>
<script>{_JS}</script>
</body></html>
"""
