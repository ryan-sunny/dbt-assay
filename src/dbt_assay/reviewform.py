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
            # *** MY READ READ IDENTICALLY AT 0.92 AND AT 0.10. *** (25.1) The number goes on the
            # card, and so does the line the reading rests on, copied from the SQL.
            if r.get("confidence") is not None:
                c["read"]["confidence"] = round(float(r["confidence"]), 2)
            if r.get("floored"):
                c["read"]["floored"] = True
            for k in ("answer", "reason", "runner_up"):
                if r.get(k):
                    c["read"][k] = str(r[k])
            ro = r.get("rests_on")
            if ro:
                c["read"]["rests_on"] = {k: ro.get(k) for k in ("text", "file", "line", "none")}
            # What "use the agent's reason" puts in the note: the LINE first, because a reason
            # with no locator is what `rule()` refuses from an agent, then the criterion.
            if ro and ro.get("text"):
                where = f"{ro.get('file')}:{ro['line']}" if ro.get("line") else "the compiled SQL"
                c["read"]["note"] = (f"rests on {where}: `{ro['text'][:200]}`. "
                                     f"{r.get('reason') or c['read']['why']}")
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
        if v not in ("agree", "disagree", "unclear", "accept"):
            bad.append(f"{subj.split('.')[-1]} / {q}: verdict is {v or 'empty'}, not recorded")
            continue
        until = str(r.get("until", "") or "").strip()
        if v == "accept" and str(r.get("note", "") or "").rstrip().endswith("It stays because"):
            bad.append(f"{subj.split('.')[-1]} / {q}: the reason was left at the form's draft -- "
                       f"\"It stays because\" with nothing after it. Not recorded.")
            continue
        if v == "accept" and not str(r.get("note", "") or "").strip():
            bad.append(f"{subj.split('.')[-1]} / {q}: accepted with no reason, not recorded. "
                       f"An accept is a waiver with a name on it and needs the waiver's why.")
            continue
        if until:
            try:
                from datetime import date
                date.fromisoformat(until)
            except ValueError:
                bad.append(f"{subj.split('.')[-1]} / {q}: until is {until!r}, not a date")
                continue
        ok.append({"subject": subj, "question": q, "verdict": v,
                   "note": str(r.get("note", "") or ""),
                   "correction": str(r.get("correction", "") or ""),
                   # *** THE FINDING IDS THE CARD COVERED, SO A `disagree` CAN ACTUALLY DISMISS. ***
                   # A verdict is recorded against (subject, question), which is the right grain
                   # for measuring a question and deliberately too coarse to delete evidence with
                   # -- one model carries eight findings of one check. The card knows exactly
                   # which ones it showed, so the dismissal lands on those and no others.
                   "findings": [str(x) for x in (r.get("findings") or []) if x],
                   "until": until if v == "accept" else ""})
    # A total order, so loading the same file twice writes the same rows in the same sequence.
    ok.sort(key=lambda r: (r["subject"], r["question"]))
    return ok, bad


# The settings the form writes that have a legal range, and what that range IS. The message is
# the one a person reads, so it says what the number MEANS rather than quoting a bound.
_RANGES = {
    "gating.min_agreement": (0.0, 1.0, "a rate between 0 and 1, not a percentage"),
    "completeness.row_loss_threshold": (0.0, 1.0,
                                        "the share of the parent LOST, strictly between 0 and 1"),
    "jev.max_spend_usd": (0.0, None, "dollars, and never negative"),
    "gating.min_adjudications": (0.0, None, "a count of human verdicts, and never negative"),
    "cost.usd_per_tb_scanned": (0.0, None, "dollars per TB, and never negative"),
}
_TEXT_SETTINGS = ("cost.engine", "cost.rate_card")


def _bad_setting(path, value) -> str:
    """Why this value cannot be written, or `''`. Checked here so their next run still starts."""
    dotted = ".".join(path)
    if dotted in _TEXT_SETTINGS:
        return "" if isinstance(value, (str, int, float)) else "must be text"
    if dotted not in _RANGES:
        return "the form does not write this key"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return f"must be a number -- {_RANGES[dotted][2]}"
    lo, hi, said = _RANGES[dotted]
    if lo is not None and n < lo:
        return said
    if hi is not None and n > hi:
        return said
    if dotted == "completeness.row_loss_threshold" and not 0.0 < n < 1.0:
        return said
    return ""


def _typed_setting(path, value):
    """`"20"` out of a text box is the integer 20 in a YAML file, not a quoted string."""
    dotted = ".".join(path)
    if dotted in _TEXT_SETTINGS:
        return str(value)
    n = float(value)
    return int(n) if dotted == "gating.min_adjudications" else n


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
        if path[0] not in ("vocab", "explanations", "waivers", "monitoring",
                           "gating", "completeness", "jev", "cost"):
            # An allow-list, not a deny-list. A config editor that accepts an arbitrary path out
            # of a downloaded file is a hole, and the failure is silent: the value lands in
            # somebody's audit.yml under a key nothing reads.
            bad.append(f"`{'.'.join(path)}`: the form does not write this key")
            continue
        if path[0] in ("gating", "completeness", "jev", "cost"):
            # *** A NUMBER OUT OF RANGE HERE BREAKS THEIR NEXT RUN, NOT THIS ONE. ***
            # `Config.from_dict` raises on a bad floor, so an unvalidated box would write a file
            # that refuses to load -- discovered later, by somebody who did not type it.
            problem = _bad_setting(path, value)
            if problem:
                bad.append(f"`{'.'.join(path)}`: {problem}")
                continue
            value = _typed_setting(path, value)
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
        "gap_text": cad.get("gap_text"),
        "floored": bool(cad.get("floored")),
        "min_marts": mon.get("min_marts"),
        "findings": (volume_json or {}).get("monitoring") or [],
        "test_coverage": cov,
        "measured": bool(cad),
    }


# *** THE REST OF audit.yml, WHICH ENDED UP EDITED IN A TEXT EDITOR OR NOT AT ALL. ***
# "i feel like theres other configs and shit that should be accessible here... its all version
# control trackable properly anyway so its like the same thing anyway i reckon." He is right: a
# form that writes `audit.yml` produces the same committed file as an editor does, by a route
# that shows what each number means and what happens if it is wrong.
#
# Every one of these is a number somebody ACTS on, so each carries what assay ships, what is set
# now, and the consequence -- never a bare box with a default in it.
SETTINGS = [
    ("gating.min_adjudications", "min_adjudications", "number", 20,
     "How many HUMAN verdicts a question family needs before it may fail a build.",
     ("Under this, a family cannot gate however high its agreement reads. Agent rulings never "
      "count toward it.")),
    ("gating.min_agreement", "min_agreement", "number", 0.0,
     "The agreement rate a family must reach before it may fail a build.",
     ("Default 0, which is OFF -- a floor set before anything was measured is a guess. "
      "`assay effectiveness` prints the real rates; pick a number from those.")),
    ("completeness.row_loss_threshold", "row_loss_threshold", "number", 0.8,
     "How much of a parent a hop may LOSE before assay reports it.",
     "It is the share DROPPED, so 0.8 means `kept less than a fifth`. Must be between 0 and 1."),
    ("jev.max_spend_usd", "max_spend_usd", "number", 1.0,
     "The most one `assay` invocation may spend on judgment before it stops.",
     ("It stops a runaway mid-run. It is not a budget and it is not a ledger -- `assay cost` "
      "is the ledger.")),
    ("cost.engine", "engine", "text", "",
     "Which warehouse you are billed by: bigquery, snowflake, duckdb.",
     ("Blank reads it from your manifest's dialect. DuckDB on your own disk bills nothing and "
      "says so; MotherDuck speaks the same dialect and does bill.")),
    ("cost.rate_card", "rate_card", "text", "",
     "A NAME for the rate below, stored on every statement it prices.",
     ("Without it a dollar figure has no provenance, and the first time a published rate "
      "moves, every historical total moves with it.")),
    ("cost.usd_per_tb_scanned", "usd_per_tb_scanned", "number", None,
     "What your warehouse charges per TB scanned.",
     ("BigQuery on-demand. Leave it blank and assay still estimates the BYTES and prints no "
      "dollar figure: a number it cannot justify is worse than no number.")),
]


def settings_rows(cfg) -> list:
    """Each setting, what it is now, and what assay ships. Never a bare box with a default in it.

    A form showing `20` in a box cannot be told apart from a form where somebody typed 20, so the
    shipped value is shown BESIDE the box and the box holds only what this project actually set.
    """
    out = []
    for path, key, kind, shipped, what, why in SETTINGS:
        head, _, _tail = path.partition(".")
        if head == "cost":
            cur = (getattr(cfg, "cost", None) or {}).get(key)
        else:
            cur = getattr(cfg, key, None)
        set_here = cur is not None and cur != shipped and cur != ""
        out.append({"path": path.split("."), "dotted": path, "kind": kind,
                    "shipped": shipped, "value": cur if set_here else "",
                    "current": cur, "set_here": set_here, "what": what, "why": why})
    return out


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
    for row in _candidates(store, cfg, findings or [], project):
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
                      "suggested": "", "quoted_means": _quoted_means(row.draft)})
    return {
        "words": words,
        "more_candidates": more,
        "monitoring": monitoring_rows(cfg, volume_json),
        "explanations": _explanation_rows(cfg, findings or [], project),
        "waivers": _waiver_rows(store, cfg, findings or [], project),
        "settings": settings_rows(cfg),
    }


def _quoted_means(draft: str) -> str:
    """The `means:` a suggestion QUOTED from this project, or "". Never one assay wrote: only a
    draft that says it was quoted is read, and the person still has to press the button."""
    if "quoted" not in (draft or ""):
        return ""
    import yaml
    try:
        body = (yaml.safe_load(draft) or {}).get("vocab") or {}
    except Exception:                                            # noqa: BLE001
        return ""
    for v in body.values():
        if isinstance(v, dict) and str(v.get("means") or "").strip():
            return str(v["means"]).strip()
    return ""


def _candidates(store, cfg, findings, project=None) -> list:
    """Vocabulary candidates `suggest` already ranks, with `means:` empty as it always returns."""
    from . import suggest as sug
    if store is None:
        return []
    try:
        firing = {f.check for f in findings}
        return [r for r in sug.build(store, cfg, firing, None, sug.live_pairs(findings), project)
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


def _explanation_rows(cfg, findings, project=None) -> list:
    """One row per named set, then one per mart no set covers, with what is configured now.

    A mart a named set already covers gets no card of its own: forty cards saying the same thing
    is the repetition a set exists to remove.
    """
    have = getattr(cfg, "explanations", None) or {}
    sets = getattr(cfg, "explanation_sets", None) or []
    out, covered = [], set()
    for name, sel, opts in sets:
        out.append({"mart": name, "named": True, "applies_to": _scope_text(sel),
                    "options": [{"name": k, "means": v} for k, v in sorted(opts.items())]})
        if project is not None:
            from .selector import scope_of
            covered |= {project.name_of(u) for u in (scope_of(project, sel) or set())}
    for mart in sorted(({f.subject_name for f in findings} | set(have)) - covered):
        opts = have.get(mart) or {}
        out.append({"mart": mart, "options": [{"name": k, "means": v} for k, v in
                                              sorted(opts.items())]})
    return out[:40]


def _waiver_rows(store, cfg, findings, project=None) -> list:
    """Findings a PERSON called correct and chose to leave, with the reason they gave.

    *** A WAIVER SAYS THE CHECK WAS RIGHT. THESE CAME FROM RULINGS SAYING IT WAS WRONG. ***
    This pane proposed waivers from `disagree` rulings, which records the opposite of what the
    person said: a disagreement is "the check misread the SQL", a waiver is "the check is right
    and I accept it". With `accept` a verdict of its own, the candidates are exactly those, and
    the reason is still not generated -- it is the sentence the person typed when they accepted.

    One row per (model, check), because that is what a waiver covers. Proposed as a named waiver
    scoped to the model, so the decision lands in audit.yml -- which is in git -- and not only in
    a store that is not.
    """
    if store is None:
        return []
    try:
        rows = store.con.execute(
            """select subject, question, decided_by, note, coalesce(until, '') from (
                   select *, row_number() over (partition by subject
                                                order by decided_at desc) rn
                   from adjudications
                   where source = 'human' and subject like '%::finding::%')
               where rn = 1 and verdict = 'accept' order by decided_at""").fetchall()
    except Exception:                                            # noqa: BLE001
        return []
    names = {f.subject: f.subject_name for f in findings}
    today = __import__("datetime").date.today().isoformat()
    out, seen = [], set()
    for subj, check, who, note, until in rows:
        uid = str(subj).split("::finding::")[0]
        model = names.get(uid) or uid.split(".")[-1]
        if (model, check) in seen or (until and str(until) < today):
            continue
        seen.add((model, check))
        if cfg.waived(model, check, project, uid):
            continue                                  # already a waiver: nothing to propose
        out.append({"model": model, "check": check, "reason": note or "", "by": who or "",
                    "until": until or "", "name": f"{model}__{check}"})
    return out[:40]


# --------------------------------------------------------------------------------- the page

_CSS = """/* *** THE SAME PRESS AS THE REPORT. ***
   Paper, ink and rules. The report and the form are two artifacts a person moves between, and
   two houses of type between them would read as two tools. Everything here is the report's
   system, with the additions a form needs: boxes you type into, and a card you rule on. */
:root{
  --paper:#faf8f3; --ink:#1a1714; --ash:#615a52; --faint:#948c81;
  --rule:#cec5b6; --rule2:#e3dbcd;
  --rust:#a8491a; --ember:#d2833a; --iron:#4a443d;
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.55 "Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
display:flex;flex-direction:column;height:100vh;overflow:hidden;
-webkit-font-smoothing:antialiased}
header,footer{flex:0 0 auto}
header{background:var(--paper);border-bottom:3px double var(--ink);padding:14px 26px 0}
h1{margin:0;font-family:Fell,"Iowan Old Style",Georgia,serif;font-weight:400;font-size:25px;
display:flex;align-items:center;gap:11px;flex-wrap:wrap}
/* The mark is the cut itself, so it blends onto the paper rather than sitting on a
   white field of its own. */
h1 > .mark{width:27px;height:auto;flex:none;mix-blend-mode:multiply}
/* *** EVERY WORD BESIDE THE PROJECT NAME WAS SET TOO SMALL TO READ. ***
   13px of an old face at 15px body size is a footnote, and this line carries the counts, the
   version and the manifest stamp -- the things a reader checks before trusting anything under
   them. */
h1 span{font-weight:400;color:var(--ash);font-size:15px;font-style:italic;
font-family:Fell,Georgia,serif}
h1 span.hname{color:var(--ink);font-size:25px;flex:none;font-style:normal}
h1 a{color:var(--rust)}

.tabs{display:flex;gap:0;margin-top:10px;border-bottom:1px solid var(--ink);
align-items:center;flex-wrap:wrap;padding-bottom:0}
.tabs button{background:none;border:0;border-bottom:3px solid transparent;
font-family:Fell,Georgia,serif;font-size:16.5px;letter-spacing:.06em;text-transform:uppercase;
color:var(--ash);padding:7px 15px 6px;cursor:pointer;margin-bottom:-1px}
.tabs button:hover{color:var(--ink)}
.tabs button.on{color:var(--ink);border-bottom-color:var(--ink)}
.tabs button b{font-weight:400;color:var(--ash);margin-left:6px;font-size:13px;
letter-spacing:0;text-transform:none}
/* *** THE ONE CONTROL THE WHOLE FORM EXISTS FOR WAS THE PALEST THING ON THE PAGE. ***
   `download handback.json` was a rust underline at 14px among faint italics: "this shit is like
   hard to see i dont like it". Nothing about it is decoration -- until it is pressed, every
   answer is in browser storage and nowhere else. So it is boxed, in ink weight, and the status
   beside it is ash rather than faint. */
.ident{margin-left:auto;display:flex;align-items:center;gap:16px;flex:none;
font-style:normal}
.ident .count{color:var(--ash);font-size:13.5px;font-family:Fell,Georgia,serif}
.ident button{margin-right:0}
.ident #by{font-size:15px;border-bottom-color:var(--ash)}
.ident button.go{color:var(--rust);border:1px solid var(--rust);padding:4px 12px;
font-size:14.5px;background:#fbf2e9}
.ident button.go:hover:not(:disabled){background:var(--rust);color:var(--paper)}
.ident button#clear{color:var(--ash);font-size:14.5px}
.tabs .count{margin-right:6px;color:var(--faint);font-size:12.5px;
font-family:Fell,Georgia,serif}
.bar{display:flex;align-items:baseline;gap:16px;margin:8px 0 0;flex-wrap:wrap;
padding-bottom:8px}

/* *** MAIN OWNS THE SCROLL, NOT THE DOCUMENT. ***
   Reported from the field with the numbers: the words pane overflowed the document by 6,705px,
   findings by 6,059. The report got this shell and the form did not. */
/* *** THE PAGE IS AS WIDE AS THE WINDOW. ***
   A 1200px column in a 1990px window left 700px of bare paper down the right of every tab, which
   is not a margin, it is the page failing to use the sheet it was given. The gutter is the
   margin; the measure is whatever is left. */
main{padding:18px 34px 26px;flex:1 1 auto;min-height:0;overflow:auto;width:100%}
.pane[hidden]{display:none}

/* ---- the task slip that opens each tab */
.task{border-top:1px solid var(--ink);border-bottom:1px solid var(--ink);
padding:14px 0;margin:0 0 20px}
.taskh{margin:0 0 6px;font-family:Fell,Georgia,serif;font-size:19px;font-weight:400}
.tasklab{font-family:Fell,Georgia,serif;font-size:12px;text-transform:uppercase;
letter-spacing:.1em;color:var(--faint);margin:12px 0 4px}
.taskex{margin:0;white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
background:#f4f1e9;border:0;border-left:2px solid var(--rule);padding:9px 12px;overflow-x:auto}
.measured.dim{color:var(--faint);margin-top:10px}
/* *** ONE PLATE, ON THE LEFT, BIG ENOUGH TO READ. ***
   Set into the slip rather than stacked above it, so it costs the height it occupies and the
   instruction runs around it. */
.taskcut{float:left;height:172px;width:auto;margin:2px 26px 12px 0;mix-blend-mode:multiply}

/* ---- a row you fill in */
.wrow{border:0;border-bottom:1px solid var(--rule2);padding:16px 0;margin:0}
.wgroup{font-family:Fell,Georgia,serif;font-weight:400;font-size:13px;letter-spacing:.1em;
text-transform:uppercase;color:var(--ash);margin:26px 0 0;padding-bottom:6px;
border-bottom:1px solid var(--ink)}
.wrow h3{margin:0 0 2px;font-size:15px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.wrow h3 .tag{margin-left:12px;position:relative;top:-1px}
.wrow .measured{color:var(--ash);font-size:13px;margin:6px 0 10px}
.wrow label{display:block;font-family:Fell,Georgia,serif;font-size:12px;color:var(--faint);
text-transform:uppercase;letter-spacing:.08em;margin:10px 0 2px}
.wrow textarea,.wrow input{width:100%;font:inherit;font-size:14px;padding:5px 2px;
border:0;border-bottom:1px solid var(--rule);background:none;color:var(--ink)}
.wrow textarea:focus,.wrow input:focus{outline:none;border-bottom-color:var(--ink)}
.wrow textarea{min-height:44px;resize:vertical;line-height:1.5}
.flag{display:inline-block;font-family:Fell,Georgia,serif;font-size:11.5px;padding:0 6px;
margin-right:6px;border:1px solid var(--ember);color:var(--ember)}
.flag.error{border-color:var(--rust);color:var(--rust)}
.accept{font-family:Fell,Georgia,serif;font-size:13.5px;padding:2px 0;margin-top:8px;
cursor:pointer;border:0;border-bottom:1px solid var(--rule);background:none;color:var(--rust)}
.accept:hover{border-bottom-color:var(--rust)}
.count{font-variant-numeric:tabular-nums}

button{font-family:Fell,Georgia,serif;font-size:14px;padding:3px 0;border:0;
border-bottom:1px solid var(--rule);background:none;cursor:pointer;color:var(--ash);
margin-right:14px}
button:hover:not(:disabled){color:var(--ink);border-bottom-color:var(--ink)}
/* *** DISABLED HAS TO READ AS A CONTROL THAT IS NOT READY, NOT AS A SMUDGE. ***
   At .35 the download button was the faintest thing on the masthead and read as broken
   styling rather than as "answer something first". */
button:disabled{opacity:.55;cursor:default}
button.go{color:var(--rust);border-bottom-color:var(--rust)}
button.go:hover:not(:disabled){color:var(--rust)}
input[type=text]{font:inherit;font-size:14px;padding:4px 2px;border:0;
border-bottom:1px solid var(--rule);background:none;color:var(--ink)}
input[type=text]:focus{outline:none;border-bottom-color:var(--ink)}

/* ---- the finding card: a ruled entry, not a box */
.card{border:0;border-left:2px solid var(--ink);padding:14px 0 16px 16px;margin:0 0 22px}
.card.done{border-left-color:var(--ember)}
.hd{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}
.hd b{font-family:Fell,Georgia,serif;font-size:18px;font-weight:400}
.tag{font-family:Fell,Georgia,serif;font-size:11.5px;color:var(--ash);
border:1px solid var(--rule);padding:0 6px}
.lbl{font-family:Fell,Georgia,serif;font-size:12px;letter-spacing:.09em;color:var(--faint);
text-transform:uppercase;margin:12px 0 3px}
.q{border-left:2px solid var(--rule);padding-left:12px;margin:0 0 4px}
.claim{font-style:italic}
pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;
background:#f4f1e9;border:0;border-left:2px solid var(--rule);padding:9px 12px;
max-height:340px;overflow:auto}
pre .n{color:var(--faint);user-select:none}
details summary{cursor:pointer;color:var(--rust);font-size:13.5px;font-family:Fell,Georgia,serif}
.ans{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;margin-top:14px;
padding-top:11px;border-top:1px solid var(--rule)}
.ans label{display:inline-flex;gap:6px;align-items:baseline;border:0;padding:0;cursor:pointer;
font-family:Fell,Georgia,serif;font-size:15px;color:var(--ash)}
.ans label:hover{color:var(--ink)}
.ans input{accent-color:var(--ink)}
.ans input:checked+span{color:var(--ink);border-bottom:2px solid var(--ink)}
.ans .note{flex:1;min-width:240px}
.note-none{color:var(--faint);font-style:italic}
.q.dim{color:var(--ash);font-size:13.5px}
code.rests{display:block;white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,
monospace;background:#f4f1e9;border-left:2px solid var(--rule);padding:5px 12px;margin:0 0 4px}
.warn{color:var(--rust)}
.measured{color:var(--ash)}
footer{padding:13px 26px;color:var(--faint);font-size:12px;border-top:3px double var(--ink);
font-family:Fell,Georgia,serif;font-style:italic}
footer code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-style:normal;
font-size:11.5px}
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
    /* How sure, beside the verdict, because a suggestion at 0.22 and one at 0.92 are different
       suggestions -- ONE number, named. A dismissal read below the floor arrives as unclear and
       says so. A reads file written before these fields existed still shows its whole `why`. */
    const sure = c.read.confidence == null ? '' : ' · confidence ' + c.read.confidence.toFixed(2);
    if (c.read.reason) {
      box.append(el('div', {class: 'q', text: c.read.verdict + sure}));
      if (c.read.floored) box.append(el('div', {class: 'q warn',
        text: 'read as ' + c.read.answer + ', too unsure to suggest a dismissal'}));
      box.append(el('div', {class: 'q dim', text: c.read.reason +
        (c.read.runner_up ? ' (next most likely: ' + c.read.runner_up + ')' : '')}));
    } else {
      box.append(el('div', {class: 'q', text: c.read.verdict + sure + ' — ' + c.read.why}));
    }
    const ro = c.read.rests_on;
    if (ro && ro.text) {
      const where = ro.line ? ro.file + ':' + ro.line : 'compiled SQL (no single line in ' +
                    (ro.file || 'the file') + ' carries it)';
      box.append(el('div', {class: 'q dim', text: 'rests on ' + where}));
      box.append(el('code', {class: 'rests', text: ro.text}));
    } else if (ro && ro.none) {
      box.append(el('div', {class: 'q dim', text: 'rests on no single line of the SQL'}));
    }
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
  /* *** `accept`: THE FINDING IS RIGHT, AND IT STAYS. ***
     Without it a correct-but-intended finding had two answers and both were wrong: `agree` left
     it outstanding forever, and `disagree` told a working check it was mistaken. An accept needs
     the reason a waiver needs, and takes the date it lapses. */
  const untilBox = el('input', {type: 'text', class: 'until',
                                placeholder: 'accepted until YYYY-MM-DD (optional)'});
  untilBox.value = a.until || '';
  untilBox.hidden = a.verdict !== 'accept';
  untilBox.oninput = () => {
    answers[c.key] = Object.assign({}, answers[c.key], {until: untilBox.value.trim()}); save();
    emitWaiver();
  };
  for (const v of ['agree', 'disagree', 'unclear', 'accept']) {
    const r = el('input', {type: 'radio', name: 'v-' + c.key, value: v});
    if (a.verdict === v) r.checked = true;
    r.onchange = () => {
      answers[c.key] = Object.assign({}, answers[c.key], {verdict: v});
      untilBox.hidden = v !== 'accept';
      wlab.hidden = v !== 'accept';
      if (v === 'accept' && answers[c.key].write_waiver !== false) writeW.checked = true;
      /* *** AN ACCEPT NEEDS A REASON, AND HALF OF ONE IS ALREADY ON THE CARD. ***
         The finding says what is true; only the reader knows why it stays. So an empty reason
         box is filled with the first half, quoted from the finding, and the second half is left
         for them -- a draft keyed to the evidence, never a reason written for them. */
      if (v === 'accept' && !note.value.trim() && c.findings.length) {
        note.value = c.findings[0].summary.replace(/\.$/, '') + '. It stays because ';
        answers[c.key].note = note.value;
        note.focus();
      }
      box.classList.add('done'); save(); tick(); emitWaiver();
    };
    ans.append(el('label', {title: v === 'accept'
      ? 'correct, and left as it is on purpose: it leaves the open list and counts as the check '
        + 'being right. Needs a reason.' : ''}, [r, el('span', {text: v})]));
  }
  ans.append(untilBox);
  /* *** THE WAIVER, WRITTEN FROM THE CARD, WHERE THE EVIDENCE IS. ***
     An accept records the decision in the store; a waiver puts it in audit.yml, which is in git.
     Deciding on the card and then finding the same finding again on another tab to write it down
     is two trips for one decision. So an accept offers the waiver right here, ticked, built from
     the reason and date already typed above -- one complete named waiver, the same one the
     Waivers tab would write. */
  const wkey = ['waivers', c.model + '__' + c.question].join('\u001f');
  const writeW = el('input', {type: 'checkbox'});
  writeW.checked = a.verdict === 'accept' ? (a.write_waiver !== false) : false;
  const wlab = el('label', {class: 'write'}, [writeW,
    el('span', {text: ' also write this as a waiver in audit.yml'})]);
  wlab.hidden = a.verdict !== 'accept';
  function emitWaiver() {
    const cur = answers[c.key] || {};
    const on = cur.verdict === 'accept' && writeW.checked && (cur.note || '').trim()
               && !(cur.note || '').trim().endsWith('It stays because');
    setEdit(wkey, on ? Object.assign({question: c.question, applies_to: c.model,
                                      reason: cur.note.trim()},
                                     (cur.until || '').trim() ? {until: cur.until.trim()} : {})
                     : null);
  }
  writeW.onchange = () => {
    answers[c.key] = Object.assign({}, answers[c.key], {write_waiver: writeW.checked});
    save(); emitWaiver();
  };
  ans.append(wlab);
  const note = el('input', {type: 'text', class: 'note',
                            placeholder: 'why (optional, and the most useful thing here)'});
  note.value = a.note || '';
  note.oninput = () => {
    answers[c.key] = Object.assign({}, answers[c.key], {note: note.value}); save();
    emitWaiver();
  };
  ans.append(note);
  /* *** THE AGENT'S READING IS ALREADY ON THE CARD, TWO INCHES ABOVE THIS BOX. ***
     "if optional is empty i feel like taking the agents output should be an option as the why."
     Re-typing a reason you just read and agree with is the kind of work a form should not ask
     for. It fills the box and nothing more: the verdict is still the reader's click, and the
     text lands in a field they can edit before it is recorded. */
  const agentWhy = (c.read && (c.read.note || c.read.why)) || (c.agent && c.agent.note) || '';
  if (agentWhy) {
    const use = el('button', {class: 'accept', type: 'button',
                              text: 'use the agent\u2019s reason'});
    use.onclick = () => {
      note.value = agentWhy;
      answers[c.key] = Object.assign({}, answers[c.key], {note: agentWhy});
      save(); tick(); emitWaiver();
    };
    ans.append(use);
  }
  box.append(ans);
  return box;
}

/* *** THE BAR BELONGED TO ONE PANE AND SAT OVER ALL OF THEM. ***
   `page 1 of 12 · 2 of 235 answered` was the FINDINGS pagination, rendered above the Words tab
   while Words showed all 36 of its rows in one scroll. Two lies at once: the controls did nothing
   where they were, and the counts described something you were not looking at. Paging is per
   pane now, and a pane that fits on one page says so by hiding the controls rather than by
   showing disabled ones. */
const PAGES = Object.assign({words: 0, explanations: 0, waivers: 0, monitoring: 0,
                            settings: 0, findings: 0}, SAVED_PAGES);
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
  document.getElementById('pager').style.display = single ? 'none' : '';
  if (!single) {
    document.getElementById('where').textContent =
      'page ' + (p.at + 1) + ' of ' + p.pages + ' · ' + p.tot + ' ' + PANE_NOUN[pane];
    document.getElementById('prev').disabled = p.at === 0;
    document.getElementById('next').disabled = p.at >= p.pages - 1;
  }
}

const PANE_NOUN = {findings: 'to rule on', words: 'words', explanations: 'marts',
                   waivers: 'proposed', monitoring: 'findings', settings: 'settings'};

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
    out.push(Object.assign({subject: c.subject, question: c.question, verdict: a.verdict,
              note: a.note || '', model: c.model, findings: c.findings.map(f => f.id)},
              a.verdict === 'accept' && a.until ? {until: a.until} : {}));
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

/* *** `what is a address?` AND `what is a owner_name?`. ***
   The article was a literal in the template, and every term beginning with a vowel read as
   somebody who does not speak the language. `u` stays `a`: warehouse identifiers starting with
   one are `user`, `unit`, `uuid`, all of which are said "you", and "an user" is the same error
   in the other direction. */
function an(word) {
  return (/^[aeio]/i.test(String(word)) ? 'an ' : 'a ') + word;
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

/* *** ONE PLATE PER TAB, AND NO TWO TABS THE SAME. ***
   Every tab opened with the same cut, so the picture told a reader nothing except that they were
   still in the form -- and on arriving at Settings from Words it read as a page that had not
   changed. The cut is a landmark or it is wallpaper: Words gets the workshop, Settings the
   lettered instruments, Explanations the assayer at his cauldron.

   *** A TAB HAS TO SAY WHAT TO DO BEFORE IT SAYS HOW IT WORKS. ***
   Every one of these tabs opened with a paragraph about the mechanism, and a reader who does not
   already know the tool cannot get a task out of it. Imperative first, then one filled example,
   then why it matters. */
function explainer(task, how, example, why, cut) {
  const box = el('div', {class: 'task'});
  /* The plate floats into the slip. A cut beside the instruction reads as the page being about
     something, where the same cut in a row of its own reads as an ornament somebody added. */
  const src = cut && (D.cuts || {})[cut];
  if (src) box.append(el('img', {class: 'taskcut', src: src, alt: ''}));
  box.append(el('h2', {class: 'taskh', text: task}));
  box.append(el('p', {class: 'measured', text: how}));
  if (example) {
    box.append(el('div', {class: 'tasklab', text: 'one filled in'}));
    box.append(el('pre', {class: 'taskex', text: example}));
  }
  if (why) box.append(el('p', {class: 'measured dim', text: why}));
  return box;
}

function wordsTab(host) {
  const bits = [];
  /* *** IT DESCRIBED THE MECHANISM AND NEVER SAID WHAT TO DO. ***
     "its SO UNCLEAR what you should actually be doing here." The task in the imperative, then
     one worked example filled in, then the consequence. The example is shown ONCE at the top
     rather than repeated as a placeholder on all forty cards. */
  bits.push(explainer(
    'Write one sentence per word, in your own words.',
    'These are words your warehouse uses that assay has no definition for. Say what each one '
    + 'means to somebody on their first day, and what follows from it that the name does not '
    + 'say. Scope it if the word is only true in part of the project.',
    'backorder\n'
    + '  means:      an order placed for stock that has not arrived yet\n'
    + '  implies:    it has no ship date, so anything averaging ship time must exclude it\n'
    + '  applies_to: path:models/marts/orders',
    'Every word you fill in is sent with EVERY judged question about every model it applies to. '
    + 'That is why it improves answers to questions you never wrote -- and why one that is false '
    + 'in part of the project is false in every answer about that part.',
    'workshop'));
  const _p = pageOf('words');
  /* *** FORTY CARDS OF ONE SHAPE, AND THE THING THAT DIFFERED WAS WHY THEY WERE HERE. ***
     Candidates come from different rules -- joined on across many models, named like a key and
     nearly unique, described identically everywhere -- and each card said its reason on its own
     line, forty times. The reason is said once, over the cards it explains, with the count. */
  const reason = w => w.known ? 'already in your vocabulary' : (w.basis || 'a candidate');
  const counts = {};
  for (const w of CTX.words) counts[reason(w)] = (counts[reason(w)] || 0) + 1;
  const page = CTX.words.slice(_p.from, _p.to)
    .map((w, i) => [w, i]).sort((a, b) => (a[0].known === b[0].known ? 0 : a[0].known ? -1 : 1)
      || reason(a[0]).localeCompare(reason(b[0])) || a[1] - b[1]).map(x => x[0]);
  let lastReason = null;
  for (const w of page) {
    if (reason(w) !== lastReason) {
      lastReason = reason(w);
      bits.push(el('h2', {class: 'wgroup', text: lastReason + ' \u00b7 ' + counts[lastReason]}));
    }
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
                     'what is ' + an(w.term) + '?', 1));
    row.append(field('implies', ['vocab', w.term, 'implies'], w.implies,
                     'what does knowing it is ' + an(w.term) + ' tell you?', 1));
    row.append(field('applies_to', ['vocab', w.term, 'applies_to'], w.applies_to,
                     'blank means every model'));
    if (w.quoted_means && !w.means) {
      /* A sentence this project already uses for the word, quoted and cited in `assay suggest`.
         Offered, never filled: it becomes the answer only when somebody presses the button. */
      const q = el('button', {class: 'accept', text: 'use the sentence your models already use'});
      q.title = w.quoted_means;
      q.onclick = () => { setEdit(['vocab', w.term, 'means'].join('\u001f'), w.quoted_means);
                          render(); };
      row.append(el('div', {class: 'measured', text: 'already written in this project: \u201c'
                            + w.quoted_means + '\u201d'}));
      row.append(q);
    }
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

function settingsTab(host) {
  const bits = [explainer(
    'Set the numbers this project is judged by.',
    'These end up in audit.yml under version control, which is where they would end up if you '
    + 'edited the file by hand. Each box is empty unless THIS project set it; what assay ships '
    + 'is beside it.',
    'gating.min_adjudications: 20   # a question family needs 20 human verdicts to gate a build\n'
    + 'cost.usd_per_tb_scanned:  6.25 # so `assay cost` can price what it ran on your warehouse',
    'Nothing here is written until you download the file and run `assay review --load '
    + 'handback.json --apply`, which shows you the diff first.', 'instruments')];
  for (const s of (CTX.settings || [])) {
    const row = el('div', {class: 'wrow'});
    row.append(el('h3', {text: s.dotted}));
    row.append(el('div', {class: 'measured', text: s.what}));
    const shipped = s.shipped == null ? 'nothing' : String(s.shipped);
    row.append(el('div', {class: 'measured', text: s.set_here
      ? 'this project set ' + String(s.current) + '. assay ships ' + shipped + '.'
      : 'not set here, so assay ships ' + shipped + ' and that is what is in force.'}));
    row.append(field(s.kind === 'number' ? 'value (a number)' : 'value',
                     s.path, s.value === '' ? '' : String(s.value), ''));
    row.append(el('div', {class: 'measured dim', text: s.why}));
    bits.push(row);
  }
  if (!(CTX.settings || []).length)
    bits.push(el('p', {class: 'measured', text: 'No settings are exposed here.'}));
  host.replaceChildren(...bits);
}

function explanationsTab(host) {
  const bits = [explainer(
    'Name the kinds of failing row this mart actually has.',
    'When a test fails, assay asks what KIND of row that is. The generic answers are always '
    + 'available; these are yours, added to them. One line each.',
    'orders_fct\n'
    + '  backorder:  the stock had not arrived, so the ship date is legitimately null\n'
    + '  test_order: a row our own QA writes nightly and deletes the next morning',
    'These are the domain knowledge. A failing row somebody can name is a decision; one nobody '
    + 'can name gets ruled `unclear` and measures nothing.', 'assayer')];
  const _p = pageOf('explanations');
  for (const x of CTX.explanations.slice(_p.from, _p.to)) {
    const row = el('div', {class: 'wrow'});
    row.append(el('h3', {text: x.mart}));
    /* A named set covers every model its selector selects, so its options sit one level down,
       under `options`, and the card says what it covers. */
    const base = x.named ? ['explanations', x.mart, 'options'] : ['explanations', x.mart];
    if (x.named) row.append(el('div', {class: 'measured', text: 'covers ' + x.applies_to}));
    for (const o of (x.options || []))
      row.append(field(o.name, [...base, o.name], o.means, '', 1));
    row.append(field('(new option name)', [...base, '__new'], '',
                     'e.g. backorder: an order placed for stock that has not arrived', 1));
    bits.push(row);
  }
  if (!CTX.explanations.length)
    bits.push(el('p', {class: 'measured', text: 'Nothing to adjudicate yet.'}));
  host.replaceChildren(...bits);
}

function waiversTab(host) {
  const bits = [el('p', {class: 'measured', text:
    'Findings somebody ACCEPTED: correct, and left as they are on purpose, with the reason they '
    + 'gave. Tick one to write it into audit.yml as a waiver, so the decision is in git and not '
    + 'only in the store. Nothing here is written until you apply it.'})];
  const _p = pageOf('waivers');
  for (const w of CTX.waivers.slice(_p.from, _p.to)) {
    const row = el('div', {class: 'wrow'});
    /* *** TWO NAMES RUN TOGETHER READ AS ONE NAME. ***
       `water_stream_gauges hop_multiplies_rows` in one weight is a single identifier to anybody
       who does not already know where the model ends. The model is the subject; the check is a
       tag on it, set the way the findings cards already set theirs. */
    const h = el('h3', {text: w.model});
    h.append(el('span', {class: 'tag', text: w.check}));
    row.append(h);
    row.append(el('div', {class: 'measured', text: 'accepted' + (w.by ? ' by ' + w.by : '')
                          + (w.until ? ', until ' + w.until : '') + ': ' + w.reason}));
    /* *** ONE COMPLETE WAIVER, OR NOTHING. ***
       This wrote `waivers.<model>.reason` as loose fields -- no check named, and a shape audit.yml
       cannot load, so applying it broke the next run. A waiver is four fields that only mean
       something together, so it is emitted whole, as a named waiver scoped to the model. */
    const key = ['waivers', w.name].join('\u001f');
    const cur = edits[key] || null;
    const reason = el('textarea', {});
    reason.value = cur ? cur.reason : w.reason;
    const until = el('input', {placeholder: 'YYYY-MM-DD, optional and worth having'});
    until.value = cur ? (cur.until || '') : w.until;
    const on = el('input', {type: 'checkbox'});
    on.checked = !!cur;
    const emit = () => setEdit(key, on.checked && reason.value.trim() ? Object.assign(
      {question: w.check, applies_to: w.model, reason: reason.value.trim()},
      until.value.trim() ? {until: until.value.trim()} : {}) : null);
    on.onchange = emit; reason.oninput = () => { if (on.checked) emit(); };
    until.oninput = () => { if (on.checked) emit(); };
    row.append(el('label', {class: 'write'}, [on, el('span', {text: ' write this waiver'})]));
    row.append(el('div', {}, [el('label', {text: 'reason'}), reason]));
    row.append(el('div', {}, [el('label', {text: 'until'}), until]));
    bits.push(row);
  }
  if (!CTX.waivers.length)
    bits.push(el('p', {class: 'measured', text:
      'Nothing proposed. A waiver assay proposes comes from a finding somebody accepted, with '
      + 'the reason they gave -- it never invents one.'}));
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
  /* *** THE SENTENCE CALLED A p90 GAP "THREE MISSED RUNS", WHICH IT IS NOT. ***
     It is the 90th-percentile gap this project has actually gone between builds, rounded up to
     whole days -- and where that rounds below a day, the one-day floor is what you are reading.
     Reported from the field as "0.0 days isn't a cadence" and "three missed runs at 0.0 days
     apart is 0, not 5, so a floor is being applied silently". Both were true. */
  const gap = m.gap_text || 'not derivable';
  row.append(el('div', {class: 'measured', text:
    'assay measured: across ' + num(m.runs) + ' run(s), 9 gaps in 10 between builds are under '
    + gap + '. Late is longer than this project has normally gone, so the derived threshold is '
    + (m.derived == null ? 'not derivable from that' : m.derived + ' day(s)')
    + (m.floored ? ', which is the one-day floor rather than the measured gap: a threshold '
       + 'cannot be shorter than a day' : '')
    + (m.configured ? '. audit.yml says ' + m.configured : '. Nothing is configured, so the '
       + 'derived number is what is in force') + '.'}));
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
               monitoring: monitoringTab, settings: settingsTab, findings: null};
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
/* The count is how many THIS project has set, not how many exist: a tab reading `7` when
   nothing is configured says the opposite of the truth. */
document.getElementById('n-set').textContent =
  (CTX.settings || []).filter(s => s.set_here).length || '';
document.getElementById('n-find').textContent = D.cards.length || '';
openPane(SAVED_PANE && (SAVED_PANE in PANES) ? SAVED_PANE
         : (CTX.words.length ? 'words' : 'findings'));
"""


def form_html(card_list: list, sql: dict, project: str, generated_at: str, version: str,
              ctx: dict | None = None, report: str = "") -> str:
    """One self-contained file. No server, no fetch, no network."""
    from .assets import CUTS, FAVICON, FONT_CSS, MARK_SVG
    e = html.escape
    ctx = ctx or {"words": [], "explanations": [], "waivers": [], "settings": []}
    blob = json.dumps({"project": project, "cards": card_list, "sql": sql, "no_read": NO_READ,
                       "context": ctx, "cuts": CUTS},
                      separators=(",", ":"), sort_keys=True, default=str)
    # `</script>` inside a model's SQL would end the tag and silently truncate the page. `<!--`
    # opens a comment inside a script element. A dbt model containing either is not exotic.
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")
    withread = sum(1 for c in card_list if c.get("read") or c.get("agent"))
    # *** TWO ARTIFACTS THAT LINK, RATHER THAN ONE THAT HALF-DOES BOTH. ***
    # The report is read-only and shareable; this form owns every box you type into. Without a
    # link the split reads as a missing feature rather than as a decision.
    report_link = (f' &middot; <a href="{e(report)}">the report</a>' if report else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(project)} &middot; assay review</title>
<link rel="icon" href="{FAVICON}">
<style>{FONT_CSS}{_CSS}</style></head><body>
<header>
<h1>{MARK_SVG}<span class="hname">{e(project)}</span><span>{len(card_list)} to rule on &middot; {len(ctx.get("words") or [])} word(s) &middot; {withread} carry a reading &middot;
assay {e(version)} &middot; manifest {e(str(generated_at))}{report_link}</span>
<!-- *** IT MOVED EVERY TIME THE COUNT TEXT CHANGED LENGTH OR A PAGER APPEARED. ***
     On the tab strip it wrapped to a second line on five panes and sat at x=176 on the sixth,
     measured. These belong to the whole form, not to a tab, so they sit on the masthead where
     nothing a tab does can shift them. -->
<span class="ident">
  <span class="count" id="count"></span>
  <input type="text" id="by" placeholder="your name" style="width:140px">
  <button class="go" id="dl">download handback.json</button>
  <button id="clear">clear</button>
</span></h1>
<!-- *** WHAT YOU DO WITH THE WHOLE FORM SITS WITH THE TAB STRIP, NOT INSIDE A TAB. ***
     Your name and the download button used to share a row with the findings pager, so switching
     to a tab that has no pager slid them sideways: "so it doesnt get moved around by the UI when
     switching tabs". They belong to the form, so they hold position on the form's own row. -->
<nav class="tabs">
  <button data-pane="words" class="on">Words<b id="n-words"></b></button>
  <button data-pane="explanations">Explanations<b id="n-expl"></b></button>
  <button data-pane="waivers">Waivers<b id="n-waiv"></b></button>
  <button data-pane="monitoring">Monitoring<b id="n-mon"></b></button>
  <button data-pane="findings">Findings<b id="n-find"></b></button>
  <button data-pane="settings">Settings<b id="n-set"></b></button>
</nav>
<!-- The pager belongs to one pane, so it appears with that pane and nowhere else. -->
<div class="bar" id="pager">
  <button id="prev">&larr; previous</button>
  <span id="where"></span>
  <button id="next">next &rarr;</button>
</div>
</header>
<main>
<div id="p-words" class="pane"></div>
<div id="p-explanations" class="pane" hidden></div>
<div id="p-waivers" class="pane" hidden></div>
<div id="p-monitoring" class="pane" hidden></div>
<div id="p-settings" class="pane" hidden></div>
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
