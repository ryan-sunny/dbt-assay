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


def load_config(payload) -> tuple:
    """`([Change], [problem])` for what they wrote under Words, Explanations and Waivers.

    *** A PROPOSAL, NEVER A WRITE. ***
    These come back as changes to `audit.yml`, which is their file: hand-written, commented, in
    git. `assay review --load` shows them as a diff and writes nothing; `--apply` writes them, in
    place, without touching a comment or reordering a key.

    An empty value is a box somebody cleared, not an instruction to delete a term -- removing a
    word from the vocabulary is a decision with project-wide reach and it is not made by a blank
    text box.
    """
    from .configpatch import Change
    rows = payload.get("config") if isinstance(payload, dict) else None
    if not rows:
        return [], []
    if not isinstance(rows, list):
        return [], ["`config` is not a list; this is not a form assay wrote"]
    out, bad = [], []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            bad.append(f"config row {i} is not an object")
            continue
        path = [str(x) for x in (r.get("path") or []) if str(x).strip()]
        value = r.get("value")
        if not path:
            bad.append(f"config row {i} names no key")
            continue
        if path[0] not in ("vocab", "explanations", "waivers", "monitoring"):
            # The form writes these three. Anything else came from somewhere else, and a config
            # editor that accepts an arbitrary path from a downloaded file is a hole.
            bad.append(f"`{'.'.join(path)}`: the form only writes vocab, explanations, waivers "
                       f"and monitoring")
            continue
        if path[-1] == "__new":
            bad.append(f"`{'.'.join(path[:-1])}`: give the new option a name, not `__new`")
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue                     # a cleared box is not a deletion
        out.append(Change(path=path, value=value))
    out.sort(key=lambda c: c.dotted)
    return out, bad


# ------------------------------------------------------------------------- the context

# *** THE TOOL FORBIDS THE AGENT FROM WRITING THESE, AND GAVE THE PERSON NOWHERE TO WRITE THEM. ***
# `suggestions()` returns every `means:` and `implies:` EMPTY and the agent skill says in capitals
# to leave them empty, because a definition written from a model name looks exactly like one
# somebody chose and then rides along with every judged question forever. So the only legitimate
# author is the person -- and the only place they could write one was hand-editing audit.yml,
# while they sat in this form typing sentences about their own warehouse.
#
# The `note` on a disagree card routinely IS the definition: "this isn't a building, it's a
# diversion point". There was nowhere for that sentence to become a term.
#
# Three sections, and they are the three parts of audit.yml that are pure domain knowledge:
#   words         -- reaches every judged answer about every model it applies to
#   explanations  -- config.py calls this "the part of the file worth maintaining" in its own
#                    comment, and nothing has ever let anybody maintain it
#   waivers       -- `suggest` already finds the reason sitting inside a ruling


def monitoring_rows(cfg, volume_json: dict | None) -> dict:
    """What `assay volume` measured, so a person can see the derived threshold and disagree.

    *** A NUMBER SOMEBODY GUESSES IS A NUMBER THAT CRIES WOLF OR STAYS QUIET FOR A QUARTER. ***
    `max_staleness_days` decides whether a monitor reads as stopped. It is DERIVED from how often
    this project actually runs dbt, and the form shows the cadence it came from beside the number,
    so changing it is a disagreement with a measurement rather than a guess replacing a default.

    Read from `assay volume --json` rather than from a live connection: emitting the form stays
    free, the way `--reads` already works.
    """
    mon = getattr(cfg, "monitoring", None) or {}
    configured = (mon.get("source_freshness") or {}).get("max_staleness_days")
    cad = (volume_json or {}).get("cadence") or {}
    cov = (volume_json or {}).get("test_coverage") or {}
    return {
        "configured": configured,
        "derived": cad.get("derived_staleness_days"),
        "in_use": cad.get("in_use_days") or configured,
        "runs": cad.get("runs"),
        "median_gap_days": cad.get("median_gap_days"),
        "min_marts": mon.get("min_marts"),
        "findings": (volume_json or {}).get("monitoring") or [],
        "test_coverage": cov,
        "measured": bool(cad),
    }


def context(store, project, cfg, findings=None, volume_json: dict | None = None) -> dict:
    """Everything a person could define here, with what assay measured beside it.

    *** ASSAY FILLS WHAT IT MEASURED AND LEAVES THE SENTENCE EMPTY. ***
    The same split as MY READ on a finding card: which models use the word, where they sit, what
    the lint thinks, and a suggested scope -- all of it checkable. The `means:` is the person's.
    """
    from .lint import lint_vocab

    issues: dict = {}
    for i in lint_vocab(getattr(cfg, "vocab", None) or {}, project):
        issues.setdefault(i.question.split(".", 1)[-1], []).append(
            {"level": i.level, "rule": i.rule, "detail": i.detail})

    models = list(getattr(project, "models", {}).values()) if project else []
    words = []
    for term, body in sorted((getattr(cfg, "vocab", None) or {}).items()):
        body = body if isinstance(body, dict) else {"means": str(body)}
        words.append({
            "term": term, "known": True,
            "means": body.get("means", ""), "implies": body.get("implies", ""),
            "applies_to": _scope_text(body.get("applies_to")),
            "issues": issues.get(term, []),
            "used_by": _used_by(term, models),
            "suggested": _suggested_scope(issues.get(term, []), project),
        })
    have = {w["term"] for w in words}
    # *** A TAB WITH SIXTY BOXES IS A TAB SOMEBODY CLOSES. ***
    # Every term they already have is shown, because those are the ones that are already reaching
    # every answer. Candidates are ranked by how often this warehouse joins on them, and the top
    # twenty is a sitting; the rest are still in `assay suggest --section vocab`.
    more = 0
    for row in _candidates(store, cfg, findings or []):
        if row.key in have:
            continue
        have.add(row.key)
        if len(words) - sum(1 for w in words if w["known"]) >= 20:
            more += 1
            continue
        words.append({"term": row.key, "known": False, "means": "", "implies": "",
                      "applies_to": "", "issues": [],
                      "used_by": _used_by(row.key, models),
                      "measured": list(row.measured or []), "basis": row.basis or "",
                      "suggested": ""})
    return {
        "words": words,
        "more_candidates": more,
        "monitoring": monitoring_rows(cfg, volume_json),
        "explanations": _explanation_rows(cfg, findings or []),
        "waivers": _waiver_rows(store, cfg, findings or []),
    }


def _candidates(store, cfg, findings) -> list:
    """Vocabulary candidates `suggest` already ranks, with `means:` empty as it always returns."""
    from . import suggest as sug
    if store is None:
        return []
    try:
        firing = {f.check for f in findings}
        return [r for r in sug.build(store, cfg, firing, None, sug.live_pairs(findings))
                if r.section == "vocab"]
    except Exception:                                            # noqa: BLE001
        return []


def _scope_text(sel) -> str:
    if not sel:
        return ""
    if isinstance(sel, dict):
        keep, drop = sel.get("select", ""), sel.get("exclude", "")
        return f"{keep}  MINUS  {drop}" if drop else str(keep)
    return str(sel)


def _used_by(term: str, models: list) -> dict:
    """Which models actually say this word. The measurement the person checks the scope against."""
    word, spaced = str(term).lower(), str(term).replace("_", " ").lower()
    hits = [m for m in models
            if word in " ".join([m.name, m.description or "", " ".join(m.columns or {}),
                                 m.compiled or ""]).lower()
            or spaced in (m.description or "").lower()]
    dirs: dict = {}
    for m in hits:
        dirs[m.path.rsplit("/", 1)[0]] = dirs.get(m.path.rsplit("/", 1)[0], 0) + 1
    return {"models": len(hits), "of": len(models),
            "directories": sorted(dirs.items(), key=lambda kv: -kv[1])[:4],
            "examples": [m.name for m in hits[:5]]}


def _suggested_scope(issues: list, project=None) -> str:
    """The selector the lint already wrote, pulled out so a button can accept it.

    *** AND ONLY FROM THE RULE THAT WRITES A REAL ONE. ***
    `asserts_law_everywhere` ends with the PLACEHOLDER `applies_to: "path:models/..."`, and the
    first version of this lifted that out and offered it as a button. Accepting it would have
    scoped the term to a path with a literal ellipsis in it -- matching nothing, reading as
    configured. Third time this shape has appeared in two days, so the suggestion is now resolved
    against the project before it is offered at all.
    """
    import re
    for i in issues:
        if i.get("rule") != "narrower_than_where_it_is_sent":
            continue
        m = re.search(r"`applies_to: (.+?)`\.", i.get("detail", ""))
        if not m:
            continue
        found = m.group(1)
        if project is None:
            return found
        from .selector import resolve
        for expr in re.findall(r'"([^"]+)"', found):
            try:
                if not resolve(project, expr):
                    return ""              # a suggestion that matches nothing is not a suggestion
            except Exception:                                    # noqa: BLE001
                return ""
        return found
    return ""


def _explanation_rows(cfg, findings) -> list:
    """One row per mart that has failing-row adjudication to do, with what is configured now."""
    have = getattr(cfg, "explanations", None) or {}
    out = []
    for mart in sorted({f.subject_name for f in findings} | set(have)):
        opts = have.get(mart) or {}
        out.append({"mart": mart, "options": [{"name": k, "means": v} for k, v in
                                              sorted(opts.items())]})
    return out[:40]


def _waiver_rows(store, cfg, findings) -> list:
    """Findings somebody already said were fine, with the reason they gave.

    A waiver written from a ruling is the one kind assay can propose honestly: the reason is not
    generated, it is the sentence the person typed when they disagreed.
    """
    if store is None:
        return []
    try:
        ruled = store.agent_rulings() + [dict(r) for r in []]
    except Exception:                                            # noqa: BLE001
        return []
    waived = getattr(cfg, "waivers", None) or {}
    out = []
    for r in ruled:
        if str(r.get("verdict")) != "disagree" or not (r.get("note") or "").strip():
            continue
        model = str(r.get("subject", "")).split("::")[0].split(".")[-1]
        if model in waived:
            continue
        out.append({"model": model, "check": r.get("family") or r.get("question") or "",
                    "reason": r["note"], "source": r.get("source", "")})
    return out[:40]


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
.tabs{display:flex;gap:2px;margin-top:10px;border-bottom:1px solid var(--line)}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;font:inherit;
font-size:13px;color:var(--dim);padding:7px 13px;cursor:pointer}
.tabs button.on{color:var(--ink);border-bottom-color:var(--ink);font-weight:600}
.tabs button b{font-weight:500;color:var(--faint);margin-left:5px;font-size:11.5px}
.wrow{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:14px 16px;
margin:0 0 12px}
.wrow h3{margin:0 0 2px;font-size:14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.wrow .measured{color:var(--dim);font-size:12.5px;margin:6px 0 10px}
.wrow label{display:block;font-size:11.5px;color:var(--faint);text-transform:uppercase;
letter-spacing:.04em;margin:8px 0 3px}
.wrow textarea,.wrow input{width:100%;font:inherit;font-size:13px;padding:6px 9px;
border:1px solid var(--line);border-radius:4px;background:var(--bg)}
.wrow textarea{min-height:46px;resize:vertical}
.flag{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:9px;margin-right:6px;
background:#fdf3e0;color:var(--amber)}
.flag.error{background:#fbeceb;color:var(--red)}
.accept{font-size:12px;padding:3px 9px;margin-top:6px;cursor:pointer;border:1px solid var(--line);
border-radius:4px;background:var(--bg)}
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
let answers = {};
try { answers = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { answers = {}; }
/* Where you were, per pane and which pane. Restoring one number put you on page 6 of a tab that
   has two, which is how the single-bar version behaved when you came back to it. */
let SAVED_PAGES = {}, SAVED_PANE = '';
try { SAVED_PAGES = JSON.parse(localStorage.getItem(KEY + ':pages') || '{}') || {}; }
catch (e) { SAVED_PAGES = {}; }
try { SAVED_PANE = localStorage.getItem(KEY + ':pane') || ''; } catch (e) { SAVED_PANE = ''; }

/* Browser storage is a per-viewer convenience and it can throw or come back empty -- a private
   window, cleared site data, a preview. Every read and write is wrapped, and the page renders
   correctly with none of it: you lose your place, not your ability to answer. */
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(answers)); } catch (e) { /* ignore */ }
  try { localStorage.setItem(KEY + ':pages', JSON.stringify(PAGES)); } catch (e) { /* ignore */ }
  try { localStorage.setItem(KEY + ':pane', pane); } catch (e) { /* ignore */ }
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

/* *** THE BAR BELONGED TO ONE PANE AND SAT OVER ALL OF THEM. ***
   `page 1 of 12 · 2 of 235 answered` was the FINDINGS pagination, rendered above the Words tab
   while Words showed all 36 of its rows in one scroll. Two lies at once: the controls did nothing
   where they were, and the counts described something you were not looking at. Paging is per
   pane now, and a pane that fits on one page says so by hiding the controls rather than by
   showing disabled ones. */
const PAGES = Object.assign({words: 0, explanations: 0, waivers: 0, monitoring: 0, findings: 0},
                           SAVED_PAGES);
let pane = 'findings';

function paneItems(name) {
  if (name === 'findings') return D.cards;
  if (name === 'words') return CTX.words || [];
  if (name === 'explanations') return CTX.explanations || [];
  if (name === 'waivers') return CTX.waivers || [];
  if (name === 'monitoring') return ((CTX.monitoring || {}).findings) || [];
  return [];
}

function pageOf(name) {
  const tot = paneItems(name).length;
  const pages = Math.max(1, Math.ceil(tot / PER));
  const at = Math.min(Math.max(0, PAGES[name] || 0), pages - 1);
  PAGES[name] = at;
  return {at, pages, tot, from: at * PER, to: at * PER + PER};
}

function tick() {
  const n = answered(), tot = D.cards.length;
  const edits = Object.keys(edits_() || {}).length;
  const p = pageOf(pane);
  /* The count describes the pane you are on. On Findings that is verdicts; everywhere else it is
     boxes you have filled, because nothing on those panes is a verdict. */
  document.getElementById('count').textContent = pane === 'findings'
    ? n + ' of ' + tot + ' answered' + (n ? '' : ' — nothing is recorded until you download')
    : (edits ? edits + ' box(es) filled across the form' : 'nothing filled yet');
  document.getElementById('dl').disabled = n === 0 && edits === 0;
  const single = p.pages <= 1;
  for (const id of ['prev', 'next', 'where']) {
    const el_ = document.getElementById(id);
    el_.style.display = single ? 'none' : '';
  }
  if (!single) {
    document.getElementById('where').textContent =
      'page ' + (p.at + 1) + ' of ' + p.pages + ' · ' + p.tot + ' ' + PANE_NOUN[pane];
    document.getElementById('prev').disabled = p.at === 0;
    document.getElementById('next').disabled = p.at >= p.pages - 1;
  }
}

const PANE_NOUN = {findings: 'to rule on', words: 'words', explanations: 'marts',
                   waivers: 'proposed', monitoring: 'findings'};

function edits_() { return (typeof edits === 'undefined') ? {} : edits; }

function render() {
  const p = pageOf('findings');
  const host = document.getElementById('cards');
  host.replaceChildren(...D.cards.slice(p.from, p.to).map(card));
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
    {project: D.project, by: document.getElementById('by').value || '', verdicts: out,
     config: configChanges()}, null, 2);
  const url = URL.createObjectURL(new Blob([body], {type: 'application/json'}));
  const a = el('a', {href: url, download: 'handback.json'});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ------------------------------------------------------------------ words, and the rest

   *** THE ONLY SURFACE WHERE A PERSON IS ALREADY TYPING SENTENCES ABOUT THEIR OWN WAREHOUSE. ***
   `suggestions()` returns every `means:` empty and the agent is told to leave it empty, because a
   definition written from a model name looks exactly like one somebody chose and then rides along
   with every judged question forever. So the sentence has to be typed by a person, and until now
   the only place to type it was a hand-edited audit.yml.

   Nothing here is a verdict and nothing here is written by this page. Every box becomes a PROPOSED
   change against audit.yml, shown as a diff by `assay review --load` and written only with
   `--apply`. Their comments and key order are theirs.
*/
const CTX = D.context || {words: [], explanations: [], waivers: []};
let edits = answers.__config || {};

function setEdit(path, value) {
  if (value === null || value === '') delete edits[path]; else edits[path] = value;
  answers.__config = edits; save(); tick();
}

function configChanges() {
  /* [{path, value, why}] -- identifiers and the sentence, never anything assay measured. */
  return Object.keys(edits).sort().map(k => ({path: k.split('\u001f'), value: edits[k]}));
}

function field(label, pathParts, current, placeholder, big) {
  const key = pathParts.join('\u001f');
  const box = el(big ? 'textarea' : 'input', {placeholder: placeholder || ''});
  box.value = key in edits ? edits[key] : (current || '');
  box.oninput = () => setEdit(key, box.value.trim() === (current || '').trim()
                              ? null : box.value.trim());
  const wrap = el('div', {});
  wrap.append(el('label', {text: label}), box);
  return wrap;
}

function wordsTab(host) {
  const bits = [];
  bits.push(el('p', {class: 'measured', text:
    'A word here is sent with EVERY judged question about every model it applies to, which is why '
    + 'one that is false in part of the project is false in every answer about that part. assay '
    + 'filled in what it measured; the sentence is yours.'}));
  const _p = pageOf('words');
  for (const w of CTX.words.slice(_p.from, _p.to)) {
    const row = el('div', {class: 'wrow'});
    const head = el('h3', {text: w.term});
    row.append(head);
    for (const i of (w.issues || []))
      row.append(el('span', {class: 'flag' + (i.level === 'error' ? ' error' : ''),
                             text: i.rule.replace(/_/g, ' ')}));
    const u = w.used_by || {};
    const where = (u.directories || []).map(d => d[0] + ' (' + d[1] + ')').join(', ');
    row.append(el('div', {class: 'measured', text:
      (w.known ? '' : 'not in your vocabulary yet. ')
      + 'assay measured: ' + (u.models || 0) + ' of ' + (u.of || 0) + ' model(s) name this word'
      + (where ? ', under ' + where : '')
      + ((u.examples || []).length ? '  e.g. ' + u.examples.join(', ') : '')}));
    row.append(field('means', ['vocab', w.term, 'means'], w.means,
                     'the sentence you would say to a new engineer on their first day', 1));
    row.append(field('implies', ['vocab', w.term, 'implies'], w.implies,
                     'what follows from it that the name does not say', 1));
    row.append(field('applies_to', ['vocab', w.term, 'applies_to'], w.applies_to,
                     'blank means every model. e.g. path:models/marts'));
    if (w.suggested) {
      const b = el('button', {class: 'accept', text: 'use ' + w.suggested});
      b.onclick = () => { setEdit(['vocab', w.term, 'applies_to'].join('\u001f'), w.suggested);
                          render(); };
      row.append(b);
    }
    bits.push(row);
  }
  if (!CTX.words.length)
    bits.push(el('p', {class: 'measured', text:
      'No vocabulary and no candidates. `assay suggest --section vocab` ranks them by how often '
      + 'this warehouse joins on them.'}));
  host.replaceChildren(...bits);
}

function explanationsTab(host) {
  const bits = [el('p', {class: 'measured', text:
    'Options for the failing-row family, per mart. These ARE the domain knowledge: the generic '
    + 'set is always available and these are added to it. One line each, saying what that kind of '
    + 'failing row actually is here.'})];
  const _p = pageOf('explanations');
  for (const x of CTX.explanations.slice(_p.from, _p.to)) {
    const row = el('div', {class: 'wrow'});
    row.append(el('h3', {text: x.mart}));
    for (const o of (x.options || []))
      row.append(field(o.name, ['explanations', x.mart, o.name], o.means, '', 1));
    row.append(field('(new option name)', ['explanations', x.mart, '__new'], '',
                     'e.g. backorder: an order placed for stock that has not arrived', 1));
    bits.push(row);
  }
  if (!CTX.explanations.length)
    bits.push(el('p', {class: 'measured', text: 'Nothing to adjudicate yet.'}));
  host.replaceChildren(...bits);
}

function waiversTab(host) {
  const bits = [el('p', {class: 'measured', text:
    'Findings somebody already said were fine, with the reason THEY gave. A waiver needs a reason '
    + 'and an expiry is worth having; nothing here is written until you apply it.'})];
  const _p = pageOf('waivers');
  for (const w of CTX.waivers.slice(_p.from, _p.to)) {
    const row = el('div', {class: 'wrow'});
    row.append(el('h3', {text: w.model + '  ' + w.check}));
    row.append(el('div', {class: 'measured', text: 'they said: ' + w.reason}));
    row.append(field('reason', ['waivers', w.model, 'reason'], w.reason, '', 1));
    row.append(field('until', ['waivers', w.model, 'until'], '', 'YYYY-MM-DD, optional'));
    bits.push(row);
  }
  if (!CTX.waivers.length)
    bits.push(el('p', {class: 'measured', text:
      'Nothing proposed. A waiver assay proposes comes from a reason already written in a '
      + 'ruling -- it never invents one.'}));
  host.replaceChildren(...bits);
}

function monitoringTab(host) {
  const m = CTX.monitoring || {};
  const bits = [el('p', {class: 'measured', text:
    'assay asserts that a monitor EXISTS, is CURRENT and COVERS what matters. It never measures '
    + 'volume or freshness itself -- that would be a second monitoring tool with a second '
    + 'opinion. Everything below is about the monitoring, never about your data.'})];
  if (!m.measured) {
    bits.push(block2('Nothing measured yet',
      'Run `assay volume --json > volume.json` and emit the form with '
      + '`--monitoring volume.json`, and the derived threshold and the coverage appear here.'));
    host.replaceChildren(...bits); return;
  }
  const row = el('div', {class: 'wrow'});
  row.append(el('h3', {text: 'how stale is too stale'}));
  row.append(el('div', {class: 'measured', text:
    'assay measured: this project runs dbt every ' + (m.median_gap_days || 0).toFixed(1)
    + ' day(s), across ' + num(m.runs) + ' run(s). Three missed runs is '
    + (m.derived == null ? 'not derivable from that' : m.derived + ' day(s)')
    + (m.configured ? '; audit.yml says ' + m.configured : '; nothing is configured, so the '
       + 'derived number is what is used') + '.'}));
  row.append(field('max_staleness_days',
                   ['monitoring', 'source_freshness', 'max_staleness_days'],
                   m.configured == null ? '' : String(m.configured),
                   m.derived == null ? '' : 'leave blank to keep using the derived ' + m.derived));
  row.append(field('min_marts', ['monitoring', 'min_marts'],
                   m.min_marts == null ? '' : String(m.min_marts),
                   'a model with fewer marts downstream is not reported as unwatched'));
  bits.push(row);

  const cov = m.test_coverage || {};
  if (cov.declared)
    bits.push(block2('what your tests are doing',
      num(cov.declared) + ' test(s) declared, ' + num(cov.ever_ran) + ' have ever produced a '
      + 'result, ' + num(cov.skipped_results) + ' result(s) are SKIPPED. A test that never ran '
      + 'and a test that passed look the same in a summary, and only one has read your data.'));

  for (const f of (m.findings || [])) {
    const b = el('div', {class: 'wrow'});
    b.append(el('h3', {text: f.check.replace(/_/g, ' ')}));
    b.append(el('div', {class: 'measured', text: f.summary}));
    bits.push(b);
  }
  host.replaceChildren(...bits);
}

function block2(title, text) {
  const b = el('div', {class: 'wrow'});
  b.append(el('h3', {text: title}));
  b.append(el('div', {class: 'measured', text: text}));
  return b;
}

const PANES = {words: wordsTab, explanations: explanationsTab, waivers: waiversTab,
               monitoring: monitoringTab, findings: null};
function drawPane(name) {
  if (PANES[name]) PANES[name](document.getElementById('p-' + name));
  else render();
}

function openPane(name) {
  pane = name;
  document.querySelectorAll('.tabs button').forEach(b =>
    b.classList.toggle('on', b.dataset.pane === name));
  document.querySelectorAll('.pane').forEach(p => { p.hidden = p.id !== 'p-' + name; });
  drawPane(name);
  tick();
}
document.querySelectorAll('.tabs button').forEach(b => {
  b.onclick = () => openPane(b.dataset.pane);
});

function step(by) {
  PAGES[pane] = (PAGES[pane] || 0) + by;
  pageOf(pane);                       // clamps
  save();
  drawPane(pane);
  window.scrollTo(0, 0);
  tick();
}
document.getElementById('prev').onclick = () => step(-1);
document.getElementById('next').onclick = () => step(1);
document.getElementById('dl').onclick = download;
document.getElementById('clear').onclick = () => {
  if (!confirm('Clear every answer on this form? This cannot be undone.')) return;
  answers = {}; save(); render();
};
pageOf('findings');
document.getElementById('n-words').textContent = CTX.words.length || '';
document.getElementById('n-expl').textContent = CTX.explanations.length || '';
document.getElementById('n-waiv').textContent = CTX.waivers.length || '';
document.getElementById('n-mon').textContent = ((CTX.monitoring || {}).findings || []).length || '';
document.getElementById('n-find').textContent = D.cards.length || '';
openPane(SAVED_PANE && (SAVED_PANE in PANES) ? SAVED_PANE
         : (CTX.words.length ? 'words' : 'findings'));
"""


def form_html(card_list: list, sql: dict, project: str, generated_at: str, version: str,
              ctx: dict | None = None) -> str:
    """One self-contained file. No server, no fetch, no network."""
    e = html.escape
    ctx = ctx or {"words": [], "explanations": [], "waivers": []}
    blob = json.dumps({"project": project, "cards": card_list, "sql": sql, "no_read": NO_READ,
                       "context": ctx},
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
<h1>{e(project)}<span>{len(card_list)} to rule on &middot; {len(ctx.get("words") or [])} word(s) &middot; {withread} carry a reading &middot;
assay {e(version)} &middot; manifest {e(str(generated_at))}</span></h1>
<nav class="tabs">
  <button data-pane="words" class="on">Words<b id="n-words"></b></button>
  <button data-pane="explanations">Explanations<b id="n-expl"></b></button>
  <button data-pane="waivers">Waivers<b id="n-waiv"></b></button>
  <button data-pane="monitoring">Monitoring<b id="n-mon"></b></button>
  <button data-pane="findings">Findings<b id="n-find"></b></button>
</nav>
<div class="bar">
  <button id="prev">&larr; previous</button>
  <span id="where"></span>
  <button id="next">next &rarr;</button>
  <span class="count" id="count"></span>
  <input type="text" id="by" placeholder="your name" style="width:160px">
  <button class="go" id="dl">download handback.json</button>
  <button id="clear">clear</button>
</div>
</header>
<main>
<div id="p-words" class="pane"></div>
<div id="p-explanations" class="pane" hidden></div>
<div id="p-waivers" class="pane" hidden></div>
<div id="p-monitoring" class="pane" hidden></div>
<div id="p-findings" class="pane" hidden><div id="cards"></div></div>
</main>
<footer>
Answers are kept in this browser as you go, so you can close the tab and come back. Nothing is
recorded anywhere until you download the file and run
<code>assay review --load handback.json</code>. Verdicts are recorded; anything you wrote under
Words, Explanations or Waivers is shown to you as a diff against <code>audit.yml</code> and
written only when you add <code>--apply</code>. A card you did not answer is never submitted:
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
