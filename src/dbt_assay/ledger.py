"""The premise ledger: every fact the rest of assay rests on, what supports it, and what uses it.

*** assay ALREADY REASONED FROM PREMISES AND WROTE NONE OF THEM DOWN. ***
A grain "declared by a test" rested on that test; a `hop_multiplies_rows` finding was dropped
because the parent's key was unique; an `arbitrary_pick` was excused because its last sort key was
declared unique. Each was a fact used and then forgotten, so when the fact stopped being true
nothing moved: a `unique` test that never ran still made a grain "declared", and a key that
started carrying duplicates still held back the fan-out finding it had excused.

A premise is one such statement ("`parcel_id` is unique in `stg_co_parcels_composite`"). The
ledger keeps, for each: every piece of evidence assay has (a declared test WITH ITS LAST ACTUAL
RESULT, a probe count, a judgment), the status that evidence yields, since when, and everything
that rests on it. A dependent of a premise that is not holding says so; one resting on a BROKEN
premise is raised again, with the reason.

Statuses, strongest evidence first, and one false measurement or failed test beats everything:

    broken     measured false, or its test's last result failed
    holding    measured true, or its test ran and passed
    unchecked  declared, but its test never ran or was skipped, or no test results were read
    assumed    only a judgment supports it
    unknown    no evidence at all

Proofs (Lean, later) register as one more kind of dependent: their premises are rows here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

BROKEN, HOLDING, UNCHECKED, ASSUMED, UNKNOWN = ("broken", "holding", "unchecked", "assumed",
                                                "unknown")
STATUSES = (BROKEN, UNCHECKED, ASSUMED, UNKNOWN, HOLDING)   # worst first, for display

# dbt / Elementary test statuses, read as evidence.
_PASS = {"pass", "success"}
_FAIL = {"fail", "error", "runtime error", "warn"}
_SKIP = {"skipped", "skip"}

DDL = """
create table if not exists test_status (
    test_id      varchar,          -- the dbt test's unique_id
    status       varchar,          -- pass | fail | warn | error | skipped, as the build reported
    ran_at       timestamp,        -- when that result was produced
    observed_at  timestamp,        -- when assay read it
    via          varchar,          -- elementary | run_results
    primary key (test_id, ran_at)
);
create table if not exists premises (
    run_id       varchar,
    premise_id   varchar,
    relation     varchar,          -- the model or source, by unique_id
    name         varchar,          -- its name, for reading
    columns      varchar,          -- json list
    property     varchar,          -- unique | not_null | unique_per_batch | max_lateness | ...
    param        varchar,
    status       varchar,          -- broken | holding | unchecked | assumed | unknown
    since        timestamp,        -- the first run of the current status streak
    evidence     varchar,          -- json list of {kind, detail, status, at}
    primary key (run_id, premise_id)
);
create table if not exists premise_uses (
    run_id         varchar,
    premise_id     varchar,
    dependent_kind varchar,        -- grain | held_back | proof | ...
    dependent_id   varchar,        -- what rests on it: a model, a finding's construct, a proof
    model          varchar,        -- the model it is about, by unique_id
    detail         varchar
);
"""


@dataclass
class Evidence:
    kind: str                  # declared | observed | judged | config | none
    detail: str                # the sentence a person reads
    status: str                # what this evidence alone says
    at: str = ""               # when, where there is a when


@dataclass
class Premise:
    relation: str              # unique_id
    name: str
    columns: tuple
    prop: str = "unique"      # the property: unique | not_null | unique_per_batch | max_lateness
    param: str = ""
    evidence: list = field(default_factory=list)
    status: str = UNKNOWN
    since: str = ""

    @property
    def id(self) -> str:
        raw = f"{self.prop}|{self.relation}|{','.join(sorted(self.columns))}|{self.param}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def statement(self) -> str:
        """In words, with names as code spans, the way the page and the terminal show it."""
        cols = ", ".join(f"`{c}`" for c in self.columns)
        if len(self.columns) > 1:
            cols = f"({cols})"
        where = f"`{self.name}`"
        if self.prop == "unique":
            return f"{cols} unique in {where}"
        if self.prop == "not_null":
            return f"{cols} never null in {where}"
        if self.prop == "unique_per_batch":
            return f"{cols} unique within each run's new rows of {where}"
        if self.prop == "max_lateness":
            if str(self.param).startswith("0"):
                return f"no row of {where} arrives after a later {cols} is already loaded"
            return f"no row of {where} arrives more than {self.param} after its {cols}"
        return f"{self.prop}({cols}) in {where}"

    def as_dict(self) -> dict:
        return {"id": self.id, "relation": self.relation, "name": self.name,
                "columns": list(self.columns), "property": self.prop, "param": self.param,
                "statement": self.statement(), "status": self.status, "since": self.since,
                "evidence": [e.__dict__ for e in self.evidence]}


@dataclass
class Use:
    premise_id: str
    kind: str                  # grain | held_back | proof
    dependent: str             # model uid, or "<check>:<model>:<construct>"
    model: str = ""            # the model the dependent is about
    detail: str = ""


@dataclass
class Ledger:
    premises: dict = field(default_factory=dict)       # id -> Premise
    uses: list = field(default_factory=list)
    tests_read: bool = False                           # were any test results available at all

    def get(self, pid: str) -> Premise | None:
        return self.premises.get(pid)

    def use(self, p: Premise, kind: str, dependent: str, model: str = "",
            detail: str = "") -> Premise:
        p = self.premises.setdefault(p.id, p)
        if not any(u.premise_id == p.id and u.kind == kind and u.dependent == dependent
                   for u in self.uses):
            self.uses.append(Use(p.id, kind, dependent, model, detail))
        return p

    def of(self, model: str) -> list:
        """The premises anything about this model rests on, for its pane."""
        ids = {u.premise_id for u in self.uses if u.model == model}
        return [self.premises[i] for i in sorted(ids) if i in self.premises]

    def uses_of(self, pid: str) -> list:
        return [u for u in self.uses if u.premise_id == pid]

    def counts(self) -> dict:
        out = {s: 0 for s in STATUSES}
        for p in self.premises.values():
            out[p.status] = out.get(p.status, 0) + 1
        return out


# ---------------------------------------------------------------------- the ledger in force

# *** ONE LEDGER PER COMPUTATION OF THE FINDINGS, SEEN BY EVERY CHECK THAT RESTS ON A PREMISE. ***
# The checks that hold a finding back on a premise are spread across four modules and called
# through signatures a dozen callers share. `collecting` makes the ledger visible to them for the
# length of one `all_findings`, the same shape as `probe.recording`; outside it `active()` is
# None and every check behaves exactly as it did before the ledger existed.
_ACTIVE: Ledger | None = None
_LAST: Ledger | None = None


class collecting:
    def __init__(self, led: Ledger):
        self.led = led

    def __enter__(self) -> Ledger:
        global _ACTIVE
        self.prev, _ACTIVE = _ACTIVE, self.led
        return self.led

    def __exit__(self, *exc) -> None:
        global _ACTIVE, _LAST
        _ACTIVE, _LAST = self.prev, self.led


def active() -> Ledger | None:
    return _ACTIVE


def last() -> Ledger | None:
    """The ledger of the most recent `all_findings`, for the caller that writes or shows it."""
    return _LAST


# ---------------------------------------------------------------------- evidence sources

def test_status(store) -> dict:
    """{test unique_id: (status, ran_at)}, the LAST result of each test, from what `volume`
    (Elementary) and `check` (run_results.json) recorded. Empty when nothing was ever recorded."""
    if store is None:
        return {}
    try:
        store.con.execute(DDL)
        rows = store.con.execute("""
            select test_id, status, ran_at from (
                select *, row_number() over (partition by test_id
                                             order by ran_at desc nulls last, observed_at desc) rn
                from test_status) where rn = 1""").fetchall()
    except Exception:                                            # noqa: BLE001
        return {}
    return {t: (str(s or "").lower(), str(at or "")[:19]) for t, s, at in rows}


def record_test_status(store, results: dict, via: str) -> int:
    """Keep each test's result. `results` is {test_id: (status, ran_at)}."""
    if store is None or not results:
        return 0
    store.con.execute(DDL)
    rows = [(t, str(s), at or None, via) for t, (s, at) in results.items() if t]
    store.con.executemany(
        "insert or replace into test_status (test_id, status, ran_at, observed_at, via) "
        "values (?, ?, ?, now(), ?)", rows)
    return len(rows)


def run_results_status(target_dir) -> dict:
    """Each test's last result from every `run_results.json` assay can find that came from a build
    or a test run: target/ itself, the copy `onboard --compile` keeps, and one folder down, where
    an orchestrator keeps each invocation's own (dagster-dbt writes `target/<job>-<hash>/`).

    `dbt compile`, `dbt show` and `dbt docs generate` overwrite the file with a result per node
    that says only that it COMPILED (`success`), which is not a run. Read as evidence, that would
    call every never-run test passing, so those files are skipped by their `args.which`."""
    from pathlib import Path
    root = Path(target_dir)
    files = [root / "run_results.json", root / "run_results.before-assay-compile.json"]
    try:
        files += sorted(root.glob("*/run_results.json"))
    except OSError:
        pass
    out: dict = {}
    for p in files:
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        which = str(((d.get("args") or {}).get("which")) or "")
        if which not in ("build", "test"):
            continue
        at = str((d.get("metadata") or {}).get("generated_at") or "")[:19].replace("T", " ")
        for r in d.get("results") or []:
            uid = str(r.get("unique_id", ""))
            if uid.startswith("test.") and (uid not in out or at > out[uid][1]):
                out[uid] = (str(r.get("status") or "").lower(), at)
    return out


def _from_test(status: str) -> str:
    if status in _PASS:
        return HOLDING
    if status in _FAIL:
        return BROKEN
    return UNCHECKED


def _verdict(evidence: list) -> str:
    kinds = {e.status for e in evidence}
    if BROKEN in kinds:
        return BROKEN
    if HOLDING in kinds:
        return HOLDING
    if UNCHECKED in kinds:
        return UNCHECKED
    if ASSUMED in kinds:
        return ASSUMED
    return UNKNOWN


# ---------------------------------------------------------------------- building it

def build(project, schema=None, entries=None, store=None, tests: dict | None = None,
          observed: dict | None = None) -> Ledger:
    """Every premise the checks use, with its evidence and status. Uses are registered by the
    checks themselves (`Ledger.use`) and by `register_grains` here."""
    led = Ledger()
    if tests is None:
        tests = test_status(store) or run_results_status(
            getattr(project, "target_dir", None) or ".")
    led.tests = tests
    led.tests_read = bool(led.tests)
    if observed is None and store is not None:
        try:
            from . import probe as probe_mod
            observed = probe_mod.read(store)
        except Exception:                                        # noqa: BLE001
            observed = {}
    led.observed = observed or {}
    led.project, led.schema = project, schema
    led.judged_grain = {e.uid: e.grain for e in (entries or [])
                        if getattr(e, "grain", None) is not None and e.grain.source == "judged"}
    return led


def unique(led: Ledger, relation: str, columns) -> Premise:
    """The premise that `columns` are unique in `relation` (a unique_id), with all its evidence.
    Returns the ledger's copy when there is one, so evidence is gathered once."""
    project = led.project
    cols = tuple(sorted(c.lower() for c in columns))
    name = project.name_of(relation) if hasattr(project, "name_of") else relation
    p = Premise(relation, name, cols, "unique")
    if p.id in led.premises:
        return led.premises[p.id]
    ev: list = []
    # declared: a unique test, or a unique_combination_of_columns, on exactly these columns
    for t in project.tests:
        if t.tests_model != relation:
            continue
        if t.kind == "unique" and t.column and (t.column.lower(),) == cols:
            pass
        elif (t.kind or "").startswith("unique_combination") and tuple(sorted(
                c.lower() for c in (t.kwargs.get("combination_of_columns") or []))) == cols:
            pass
        else:
            continue
        if not led.tests_read:
            ev.append(Evidence("declared", f"`{t.name}`: no test results were read, so whether "
                               f"it runs is not known", UNCHECKED))
            continue
        got = led.tests.get(t.unique_id)
        if got is None:
            ev.append(Evidence("declared", f"`{t.name}` never ran: no build or Elementary "
                               f"result assay read has it", UNCHECKED))
        else:
            st, at = got
            ev.append(Evidence("declared", f"`{t.name}` last result: {st}", _from_test(st), at))
    # declared, no test: an incremental model's unique_key
    node = ((project.raw or {}).get("nodes") or {}).get(relation) or {}
    uk = (node.get("config") or {}).get("unique_key")
    if uk:
        uk_cols = tuple(sorted(c.lower() for c in ([uk] if isinstance(uk, str) else uk)))
        if uk_cols == cols:
            ev.append(Evidence("config", "declared as this incremental model's `unique_key`, "
                               "with no test behind it", UNCHECKED))
    # observed: probe's latest count on this relation, of exactly these columns. A composite
    # key is stored under its columns joined by ", " (how `--verify` and `probe` write one).
    o = None
    if led.schema is not None:
        rel = (led.schema.relation.get(relation) or "").replace('"', "").lower()
        for k, cand in (led.observed.get(rel) or {}).items():
            if tuple(sorted(x.strip().lower() for x in str(k).split(","))) == cols:
                o = cand
                break
    if o is not None and o.row_count:
        # Duplicates found in a sample are duplicates in the table; uniqueness in a sample is not
        # uniqueness in the table, so only an exact count can hold a premise.
        dup = (o.non_null or 0) - (o.distinct_ct or 0)
        at = str(o.observed_at or "")[:19]
        if dup > 0:
            ev.append(Evidence("observed", f"{dup:,} duplicate value(s) in {o.row_count:,} rows",
                               BROKEN, at))
        elif o.status in ("unique", "has_nulls") and not getattr(o, "sampled", False):
            ev.append(Evidence("observed", f"every one of {o.non_null:,} non-null value(s) "
                               f"distinct", HOLDING, at))
    # judged: the grain a judgment settled on
    g = led.judged_grain.get(relation)
    if g is not None and tuple(sorted(c.lower() for c in g.value)) == cols:
        ev.append(Evidence("judged", f"the judged grain, at {g.confidence or 0:.2f}", ASSUMED))
    p.evidence = ev
    p.status = _verdict(ev)
    return p


def uid_of(project, name: str) -> str:
    """A model's or source's unique_id from the name a check holds."""
    for uid, m in project.models.items():
        if m.name == name:
            return uid
    for uid, sm in project.sources.items():
        if sm.name == name:
            return uid
    return name


def parent_key(led: Ledger, entry, parent_name: str) -> Premise:
    """The premise a join onto `parent_name` cannot fan out on: the parent's declared key when the
    join covers it, else the columns the join is on (what `--verify` counts)."""
    from .relate import declared_keys
    if not hasattr(led, "_declared"):
        led._declared = declared_keys(led.project)
    uid = uid_of(led.project, parent_name)
    pk = led._declared.get(uid) or []
    on = [c.lower() for c in (getattr(entry, "join_keys", {}) or {}).get(parent_name) or []]
    key = pk if pk and on and {c.lower() for c in pk} <= set(on) else (on or pk)
    return unique(led, uid, key)


def hold(check: str, model: str, construct: str, premise_fn, detail: str = "") -> dict | None:
    """A check is about to hold a finding back because a premise is true. Records that it did,
    and returns the finding's `why_it_is_back` evidence when the premise is BROKEN, which means
    the finding is raised after all. None means it stays held back, which is also what happens
    with no ledger in force: every caller outside `all_findings` behaves as it always did.

    `premise_fn(led)` builds the premise, so nothing is looked up when there is no ledger."""
    led = active()
    if led is None:
        return None
    p = premise_fn(led)
    if p is None or not p.columns:
        return None
    p = led.use(p, "held_back", f"{check}:{model}:{construct}", model, detail)
    if p.status != BROKEN:
        return None
    broke = next((e for e in p.evidence if e.status == BROKEN), None)
    return {"premise": p.statement(), "premise_id": p.id, "status": p.status,
            "why": why(p), "broke_on": (broke.at if broke else "")[:10], "held_back": detail}


def rests_on(check: str, model: str, construct: str, premise_fn, detail: str = "") -> None:
    """A finding RAISED on a premise (e.g. `join_fans_out` reading a declared key): recorded so
    the ledger shows what reads it, and never changes the finding."""
    led = active()
    if led is None:
        return
    p = premise_fn(led)
    if p is not None and p.columns:
        led.use(p, "raised_on", f"{check}:{model}:{construct}", model, detail)


def register_grains(led: Ledger, entries=None) -> None:
    """A declared grain rests on its key being unique. (G-A)

    From the project's declared keys, the same ones the inventory makes a declared grain from,
    so a `check` against a fresh store (no inventory built yet) records them too."""
    from .relate import declared_keys
    if not hasattr(led, "_declared"):
        led._declared = declared_keys(led.project)
    for uid, cols in sorted(led._declared.items()):
        if uid in led.project.models and cols:
            led.use(unique(led, uid, cols), "grain", uid, uid, "the grain")


def apply_to_grains(led: Ledger, entries) -> None:
    """A declared grain whose premise is not holding keeps its value, stops being firm, and says
    why ("`unique_stg_x_id` has never run")."""
    for e in entries or []:
        g = getattr(e, "grain", None)
        if g is None or g.source != "declared":
            continue
        g.premise = None
        for u in led.uses:
            if u.kind != "grain" or u.dependent != e.uid:
                continue
            p = led.premises.get(u.premise_id)
            if p is not None:
                g.premise = {"id": p.id, "status": p.status, "why": why(p), "label": label(p)}


def label(p: Premise) -> str:
    """The few words a badge carries about a declared key: what its strongest evidence says."""
    kinds = {e.kind: e for e in p.evidence}
    if p.prop == "max_lateness":
        return {BROKEN: "rows arrive later", HOLDING: "measured within",
                UNCHECKED: "not measured", UNKNOWN: "no arrival column"}.get(p.status, p.status)
    if p.prop == "unique_per_batch":
        if p.status == HOLDING:
            return "by its SQL" if "derived" in kinds else "holding"
        return {BROKEN: "duplicates", UNCHECKED: "table only",
                UNKNOWN: "nothing says"}.get(p.status, p.status)
    if p.status == BROKEN:
        obs = kinds.get("observed")
        return "counted duplicates" if obs is not None and obs.status == BROKEN else "failing"
    if p.status == HOLDING:
        dec = kinds.get("declared")
        return "passing" if dec is not None and dec.status == HOLDING else "counted"
    if p.status == UNCHECKED:
        dec = kinds.get("declared")
        if dec is None:
            return "no test"
        if "no test results were read" in dec.detail:
            return "no results read"
        if "last result" in dec.detail:
            return "skipped"
        return "never ran"
    return p.status


def why(p: Premise) -> str:
    """The evidence behind a premise's status, in one line."""
    got = [e.detail for e in p.evidence if e.status == p.status]
    if got:
        return "; ".join(got)
    return "nothing declares, counts or judges it" if p.status == UNKNOWN else p.status


# ---------------------------------------------------------------------- persistence

def write(store, run_id: str, led: Ledger) -> None:
    """Keep this run's premises, with `since` carried from the previous run's rows."""
    if store is None:
        return
    store.con.execute(DDL)
    prev = {}
    try:
        for pid, status, since in store.con.execute("""
                select premise_id, status, since from premises where run_id = (
                    select run_id from premises p join runs r using (run_id)
                    where run_id <> ? order by r.started_at desc limit 1)""", [run_id]).fetchall():
            prev[pid] = (status, str(since or "")[:19])
    except Exception:                                            # noqa: BLE001
        prev = {}
    now = store.con.execute("select started_at from runs where run_id = ?", [run_id]).fetchone()
    now = str(now[0])[:19] if now and now[0] else ""
    rows, uses = [], []
    for p in led.premises.values():
        before = prev.get(p.id)
        p.since = before[1] if before and before[0] == p.status and before[1] else now
        rows.append((run_id, p.id, p.relation, p.name, json.dumps(list(p.columns)), p.prop,
                     p.param, p.status, p.since or None,
                     json.dumps([e.__dict__ for e in p.evidence])))
    for u in led.uses:
        uses.append((run_id, u.premise_id, u.kind, u.dependent, u.model, u.detail))
    if rows:
        store.con.executemany("insert or replace into premises values (?,?,?,?,?,?,?,?,?,?)", rows)
    if uses:
        store.con.executemany("insert into premise_uses values (?,?,?,?,?,?)", uses)


def changes(store, run_id: str) -> list:
    """(statement, before, after) for every premise whose status moved since the previous run."""
    if store is None:
        return []
    try:
        store.con.execute(DDL)
        return store.con.execute("""
            with cur as (select * from premises where run_id = ?),
                 prv as (select * from premises where run_id = (
                    select run_id from premises p join runs r using (run_id)
                    where run_id <> ? order by r.started_at desc limit 1))
            select cur.name, cur.columns, cur.property, prv.status, cur.status, cur.evidence
            from cur join prv using (premise_id)
            where cur.status <> prv.status
            order by cur.status, cur.name""", [run_id, run_id]).fetchall()
    except Exception:                                            # noqa: BLE001
        return []


def change_lines(rows: list, each: int = 8) -> list:
    """What `check` prints about moved premises. Every one that BROKE is named, up to `each`;
    the rest are counted by where they moved from and to, because the first run after test
    results arrive moves hundreds from `unchecked` to `holding` and a list of them says nothing."""
    broke, rest = [], {}
    for name, cols, prop, before, after, ev in rows:
        if after != BROKEN:
            rest[(before, after)] = rest.get((before, after), 0) + 1
            continue
        p = Premise("", name, tuple(json.loads(cols or "[]")), prop)
        detail = ""
        try:
            detail = next((e["detail"] for e in json.loads(ev or "[]")
                           if e.get("status") == BROKEN), "")
        except (ValueError, TypeError, AttributeError):
            pass
        broke.append(f"[red]broke[/]: {p.statement()} [dim](was {before})"
                     f"{' · ' + detail if detail else ''}[/]")
    out = broke[:each]
    if len(broke) > each:
        out.append(f"[red]{len(broke) - each} more broke[/] [dim](the Guarantees tab lists them)[/]")
    for (before, after), n in sorted(rest.items(), key=lambda kv: -kv[1]):
        colour = "green" if after == HOLDING else "yellow"
        out.append(f"[{colour}]{n} now {after}[/] [dim](were {before})[/]")
    return out


def latest_run(store) -> str | None:
    """The newest run that wrote premises (a full `check`), or None."""
    if store is None:
        return None
    try:
        store.con.execute(DDL)
        got = store.con.execute("""
            select p.run_id from (select distinct run_id from premises) p join runs r using (run_id)
            order by r.started_at desc, p.run_id desc limit 1""").fetchone()
    except Exception:                                            # noqa: BLE001
        return None
    return got[0] if got else None


def stored(store, run_id: str | None = None) -> dict:
    """{premise_id: (status, since)} from a run's rows (the latest by default)."""
    run_id = run_id or latest_run(store)
    if not run_id:
        return {}
    return {pid: (st, str(since or "")[:10]) for pid, st, since in store.con.execute(
        "select premise_id, status, since from premises where run_id = ?", [run_id]).fetchall()}


def moves(store, run_id: str | None = None) -> list:
    """[{id, before, after}] for the premises whose status moved at that run (the latest)."""
    run_id = run_id or latest_run(store)
    if not run_id:
        return []
    try:
        return [{"id": pid, "before": b, "after": a} for pid, b, a in store.con.execute("""
            with cur as (select * from premises where run_id = ?),
                 prv as (select * from premises where run_id = (
                    select run_id from premises p join runs r using (run_id)
                    where run_id <> ? and r.started_at <= (select started_at from runs
                                                           where run_id = ?)
                    order by r.started_at desc limit 1))
            select cur.premise_id, prv.status, cur.status from cur join prv using (premise_id)
            where cur.status <> prv.status order by 1""", [run_id, run_id, run_id]).fetchall()]
    except Exception:                                            # noqa: BLE001
        return []


def to_rows(led: Ledger, store=None) -> list:
    """Every premise as the page and MCP read it: the statement, status, since, evidence, and
    each thing resting on it. `since` comes from the latest `check` that wrote premises, when the
    status then was the status now; otherwise it is blank rather than a wall-clock guess."""
    if led is None:
        return []
    before = stored(store) if store is not None else {}
    names = {u: m.name for u, m in led.project.models.items()}
    out = []
    for p in sorted(led.premises.values(), key=lambda x: (STATUSES.index(x.status), x.name,
                                                           x.columns)):
        d = p.as_dict()
        st = before.get(p.id)
        d["since"] = st[1] if st and st[0] == p.status else ""
        d["label"] = label(p)
        d["why"] = why(p)
        d["uses"] = [{"kind": u.kind, "dependent": u.dependent, "model": u.model,
                      "model_name": names.get(u.model, u.model), "detail": u.detail}
                     for u in led.uses_of(p.id)]
        out.append(d)
    return out
