"""`assay prove`: per-model certificates, checked by Lean. (L2)

*** A CERTIFICATE IS A THEOREM ABOUT THIS MODEL, AND LEAN CHECKS IT. ***
For each model, assay reads the parts of its parsed structure a rule applies to -- each join and
the keys it covers, each dedupe and what it keeps, the grain and how it is carried, an incremental
model's merge -- and writes a theorem stating the property for THIS model, whose hypotheses are
the model's premises, named `p_<premise_id>` after their rows in the ledger. The proof applies a
rule proven once in the shipped library. Lean then checks it; nothing here decides it is proven.

What a certificate says: "for every input satisfying these premises, this model cannot do X".
The premises are ledger rows, so the guarantee is live: it stands while they hold and reads
"guarantee lost" the run one breaks, without Lean running again. Two things it does not say, each
its own premise: that assay's parse IS the SQL (`parse_faithful`, checked by the round trip here
and proven per model in L4), and that the engine behaves as the model of SQL says (L4's
conformance suite). The page says "proven from the parsed structure" until both hold.

A goal Lean cannot close is reported with what is missing. When what is missing is a premise --
the join covers none of the parent's declared key, a dedupe has no unique last sort key -- that
is the recommendation.

Files are build output: `target/assay/lean/`, regenerated every run, never beside the models.
A model is re-proved only when its checksum changes; otherwise its premises are re-read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ledger as L

PROVEN, NOT_PROVEN, NOT_ATTEMPTED = "proven", "not_proven", "not_attempted"

DDL = """
create table if not exists proofs (
    model          varchar,        -- unique_id
    model_name     varchar,
    model_checksum varchar,        -- the file the certificate is about
    property       varchar,        -- no_fanout:<parent> | pick:<n> | grain | incremental
    statement      varchar,        -- the property in words
    theorem        varchar,        -- the certificate's name in Lean
    rule           varchar,        -- the shipped theorem it applies
    premises       varchar,        -- json list of {id, relation, name, columns, property, param}
    status         varchar,        -- proven | not_proven | not_attempted
    detail         varchar,        -- Lean's error and remaining goal, or what is missing
    missing        varchar,        -- the missing premise, in words, when that is what failed
    written_by     varchar,        -- assay | agent
    source         varchar,        -- the Lean text (kept whole for an agent's proof)
    lean_version   varchar,
    proved_at      timestamp,
    primary key (model, property, written_by)
);
"""


@dataclass
class Obligation:
    model: str
    name: str
    checksum: str
    prop: str
    statement: str
    rule: str = ""
    premises: list = field(default_factory=list)      # ledger Premises, in hypothesis order
    lean: str = ""                                    # the theorem, when one can be written
    status: str = NOT_ATTEMPTED
    detail: str = ""
    missing: str = ""

    @property
    def theorem(self) -> str:
        return _ident(f"{self.name}__{self.prop}")


def _ident(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return out if out[:1].isalpha() else "m_" + out


def _lean_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lean_list(xs) -> str:
    return "[" + ", ".join(_lean_str(str(x)) for x in xs) + "]"


def _hyp(p: L.Premise) -> str:
    return f"p_{p.id}"


def _all_notnull(cols: list, hyps: list[str], table: str) -> str:
    """A proof of `∀ c ∈ cols, NotNull c table` from one hypothesis per column."""
    if not cols:
        return "(fun _ h => by simp at h)"
    cases = " <;> ".join(["rcases hc with " + " | ".join(["rfl"] * len(cols))])
    return (f"(fun c hc => by simp only [List.mem_cons, List.not_mem_nil, or_false] at hc; "
            f"{cases} <;> assumption)") if len(cols) > 1 else \
        f"(fun c hc => by simp only [List.mem_cons, List.not_mem_nil, or_false] at hc; " \
        f"subst hc; exact {hyps[0]})"


# ------------------------------------------------------------------------ the obligations

def obligations(project, digests, schema, entries, led: L.Ledger,
                store=None) -> list[Obligation]:
    from .relate import declared_keys
    declared = declared_keys(project)
    by_uid = {e.uid: e for e in entries or []}
    out: list[Obligation] = []
    for uid, m in sorted(project.models.items()):
        if getattr(m, "is_installed_package", False):
            continue
        d = digests.get(uid)
        e = by_uid.get(uid)
        if d is None or not d.ok or e is None:
            continue
        cs = m.checksum or ""
        out += _joins(uid, m, cs, e, led, declared)
        out += _picks(uid, m, cs, d, led, declared)
        g = _grain(uid, m, cs, d, e, led, declared)
        if g is not None:
            out.append(g)
        i = _incremental(uid, m, cs, d, e, led, project, schema, digests, store)
        if i is not None:
            out.append(i)
    return out


def _joins(uid, m, cs, e, led, declared) -> list[Obligation]:
    out = []
    for parent, cols in sorted((e.join_keys or {}).items()):
        puid = L.uid_of(led.project, parent)
        rk = [c.lower() for c in cols]
        pk = [c.lower() for c in declared.get(puid) or []]
        us = pk or rk
        kind = (e.join_kind or {}).get(parent, "").upper()
        left = kind.startswith("LEFT")
        o = Obligation(uid, m.name, cs, f"no_fanout:{parent}",
                       f"a {'left ' if left else ''}join onto `{parent}` cannot multiply "
                       f"`{m.name}`'s rows" if not left else
                       f"the left join onto `{parent}` keeps exactly `{m.name}`'s left rows",
                       "left_join_preserves_rows" if left else "inner_join_no_fanout")
        prem = L.unique(led, puid, us)
        o.premises = [prem]
        rkl = _lean_list(rk)
        concl = (f"(leftJoin lk {rkl} L R).length = L.length" if left
                 else f"(innerJoin lk {rkl} L R).length ≤ L.length")
        o.lean = (f"theorem {o.theorem} (lk : List String) (L R : Table)\n"
                  f"    ({_hyp(prem)} : Unique {_lean_list(prem.columns)} R) :\n"
                  f"    {concl} :=\n"
                  f"  {o.rule} (us := {_lean_list(prem.columns)}) (by decide) {_hyp(prem)}\n")
        if pk and not set(pk) <= set(rk):
            o.missing = (f"the join is on ({', '.join(rk)}) and `{parent}`'s declared key is "
                         f"({', '.join(pk)}): join on {', '.join(sorted(set(pk) - set(rk)))} too, "
                         f"or make ({', '.join(rk)}) unique in `{parent}`")
        out.append(o)
    return out


def _picks(uid, m, cs, d, led, declared) -> list[Obligation]:
    out = []
    n = 0
    for w in d.windows:
        if not w.partition_columns or not w.order_sql:
            continue
        if not (d.has_qualify or any("rn" in p_.lower() or "= 1" in p_ for p_ in d.predicates)):
            continue
        n += 1
        part = list(w.part_keys or [])
        ordk = [k for k in (w.order_keys or []) if k]
        o = Obligation(uid, m.name, cs, f"pick:{n}",
                       f"the dedupe on ({', '.join(part)}) keeps the same rows in any input order")
        kept = w.kept_columns
        if kept is not None and part and set(kept) <= set(part) | set(ordk):
            o.rule = "pick_is_order_independent"
            o.statement = (f"the dedupe on ({', '.join(part)}) keeps only its keys, so it is the "
                           f"same in any input order")
            o.lean = (f"theorem {o.theorem} (o : KeyOrder) (t t' : Table) (h : t.Perm t') :\n"
                      f"    ((pick o {_lean_list(part)} {_lean_list(ordk)} t).map "
                      f"(keyOf {_lean_list(kept)})).Perm\n"
                      f"      ((pick o {_lean_list(part)} {_lean_list(ordk)} t').map "
                      f"(keyOf {_lean_list(kept)})) :=\n"
                      f"  pick_is_order_independent o (by decide) h\n")
            out.append(o)
            continue
        # a sort key declared unique in the model or a parent: the order is total
        near = [uid, *(led.project.models[uid].parents or [])]
        k = next((k for k in ordk if any(declared.get(r) == [k] for r in near)), None)
        if k is None:
            o.rule = "pick_total_on_unique_key"
            o.missing = ("no sort key is declared unique in this model or a parent: add one as "
                         "the last ORDER BY key, or keep only the partition and order keys")
            out.append(o)
            continue
        rel = next(r for r in near if declared.get(r) == [k])
        pu, pn = L.unique(led, rel, [k]), L.not_null(led, rel, k)
        o.rule = "pick_total_on_unique_key"
        o.premises = [pu, pn]
        o.lean = (f"theorem {o.theorem} (o : KeyOrder) (t t' : Table) (h : t.Perm t')\n"
                  f"    ({_hyp(pu)} : Unique [{_lean_str(k)}] t) "
                  f"({_hyp(pn)} : NotNull {_lean_str(k)} t) :\n"
                  f"    (pick o {_lean_list(part)} {_lean_list(ordk)} t).Perm "
                  f"(pick o {_lean_list(part)} {_lean_list(ordk)} t') :=\n"
                  f"  pick_total_on_unique_key o (total_of_unique (by decide) {_hyp(pu)} "
                  f"{_hyp(pn)}) h\n")
        out.append(o)
    return out


def _grain(uid, m, cs, d, e, led, declared) -> Obligation | None:
    g = [c.lower() for c in (getattr(d, "final_group_by", None) or [])]
    top = [c.lower() for c in (d.group_by_columns or [])]
    if not g and d.group_by and len(top) == len(d.group_by) and not d.windows:
        g = top                                   # the outermost select's own group by
    if g:
        o = Obligation(uid, m.name, cs, "grain",
                       f"`{m.name}` is one row per ({', '.join(g)}): its final group by",
                       "group_by_unique")
        o.lean = (f"theorem {o.theorem} (t : Table) : Unique {_lean_list(g)} "
                  f"(groupBy {_lean_list(g)} t) :=\n  group_by_unique _ t\n")
        return o
    grain = getattr(e, "grain", None)
    drivers = sorted(getattr(e, "driving_parents", None) or [])
    if grain is None or grain.source not in ("declared", "derived", "observed") \
            or len(drivers) != 1 or not grain.value:
        return None
    gcols = [c.lower() for c in grain.value]
    driver = L.uid_of(led.project, drivers[0])
    joins = sorted((p, cols) for p, cols in (e.join_keys or {}).items() if p != drivers[0])
    o = Obligation(uid, m.name, cs, "grain",
                   f"`{m.name}` stays one row per ({', '.join(gcols)}) through its joins",
                   "grain_through_join")
    pg = L.unique(led, driver, gcols)
    pns = [L.not_null(led, driver, c) for c in gcols]
    prem = [pg, *pns]
    hyps = [f"({_hyp(pg)} : Unique {_lean_list(gcols)} L)"]
    hyps += [f"({_hyp(p)} : NotNull {_lean_str(c)} L)" for p, c in zip(pns, gcols)]
    term, nn = _hyp(pg), _all_notnull(gcols, [_hyp(p) for p in pns], "L")
    table = "L"
    rs = []
    for i, (parent, cols) in enumerate(joins, 1):
        puid = L.uid_of(led.project, parent)
        pk = [c.lower() for c in declared.get(puid) or []]
        rk = [c.lower() for c in cols]
        us = pk or rk
        pu = L.unique(led, puid, us)
        prem.append(pu)
        rs.append(f"R{i}")
        hyps.append(f"({_hyp(pu)} : Unique {_lean_list(us)} R{i})")
        left = (e.join_kind or {}).get(parent, "").upper().startswith("LEFT")
        op = "leftJoin" if left else "innerJoin"
        rule = "grain_through_left_join" if left else "grain_through_join"
        carry = "notnull_all_through_left_join" if left else "notnull_all_through_join"
        term = f"({rule} (lk := lk{i}) (rk := {_lean_list(rk)}) {term} {nn} (by decide) {_hyp(pu)})"
        nn = f"({carry} (lk := lk{i}) (rk := {_lean_list(rk)}) (R := R{i}) {nn})"
        table = f"({op} lk{i} {_lean_list(rk)} {table} R{i})"
        if pk and not set(pk) <= set(rk):
            o.missing = (f"the join onto `{parent}` covers none of its declared key "
                         f"({', '.join(pk)}): the grain cannot be carried through it")
    o.premises = prem
    lks = " ".join(f"lk{i}" for i in range(1, len(joins) + 1))
    binders = f"(L {' '.join(rs)} : Table)" if rs else "(L : Table)"
    o.lean = (f"theorem {o.theorem} {('(' + lks + ' : List String) ') if lks else ''}{binders}"
              f" (p : Row → Bool)\n    " + "\n    ".join(hyps) +
              f" :\n    Unique {_lean_list(gcols)} (filterT p {table}) :=\n"
              f"  filter_preserves_unique p {term}\n")
    return o


def _incremental(uid, m, cs, d, e, led, project, schema, digests,
                 store=None) -> Obligation | None:
    from .checks import incremental as inc
    i = inc.read(project, digests).get(uid) if m.materialized == "incremental" else None
    if i is None or not i.unique_key or i.strategy not in inc.KEYED:
        return None
    o = Obligation(uid, m.name, cs, "incremental",
                   f"an incremental run of `{m.name}` equals its full refresh",
                   "incremental_equals_full_refresh")
    if d.group_by or d.windows or d.distinct or getattr(d, "union_members", None):
        o.missing = ("the model aggregates, dedupes or unions, so a run's rows are not computed "
                     "row by row: the equivalence is proven only for row-by-row models")
        return o
    event = i.event_time if i.strategy == "microbatch" else i.filter_column
    if not event and i.block:
        o.missing = "the incremental filter is not a high-water mark assay can read"
        return o
    keys = sorted(i.unique_key)
    pu = L.unique(led, uid, keys)
    pns = [L.not_null(led, uid, k) for k in keys]
    allowed = 0.0 if not i.filter_lookback else float("inf")
    late = inc.lateness_premise(led, i, event or keys[0], allowed,
                                "0 (no lookback)" if not i.filter_lookback else "its lookback",
                                e, schema, store)
    led.premises.setdefault(late.id, late)
    o.premises = [late, pu, *pns]
    S = "((Sold ++ Snew).map g)"
    hyps = [f"({_hyp(late)} : ∀ r ∈ Snew, sel r = true)",
            f"({_hyp(pu)} : Unique {_lean_list(keys)} {S})",
            *[f"({_hyp(p)} : NotNull {_lean_str(k)} {S})" for p, k in zip(pns, keys)]]
    o.lean = (f"theorem {o.theorem} (g : Row → Row) (sel : Row → Bool) (Sold Snew : Table)\n    "
              + "\n    ".join(hyps) +
              f" :\n    (mergeByKey {_lean_list(keys)} (Sold.map g) "
              f"(((Sold ++ Snew).filter sel).map g)).Perm {S} :=\n"
              f"  incremental_equals_full_refresh g {_lean_list(keys)} sel Sold Snew "
              f"{_hyp(late)} {_hyp(pu)} {_all_notnull(keys, [_hyp(p) for p in pns], S)}\n")
    return o


# ------------------------------------------------------------------------ running Lean

def workdir(target_dir) -> Path:
    return (Path(target_dir) / "assay" / "lean").resolve()


def write_project(target_dir, obls: list[Obligation]) -> dict:
    """target/assay/lean: a Lake project requiring the compiled library, one file per model."""
    from . import toolchain
    root = workdir(target_dir)
    (root / "Models").mkdir(parents=True, exist_ok=True)
    lib = toolchain.library()
    (root / "lakefile.toml").write_text(
        'name = "assay_models"\n\n[[require]]\nname = "assay"\n'
        f'path = {json.dumps(str(lib))}\n\n[[lean_lib]]\nname = "Models"\n')
    (root / "lean-toolchain").write_text((lib / "lean-toolchain").read_text()
                                         if (lib / "lean-toolchain").exists() else "")
    files: dict = {}
    by_model: dict = {}
    for o in obls:
        if o.lean:
            by_model.setdefault(o.name, []).append(o)
    for name, os_ in sorted(by_model.items()):
        mod = _ident(name)
        body = ["import Assay", "open Assay", "",
                f"/-! Certificates for `{name}`, generated by `assay prove`. -/", ""]
        for o in os_:
            body.append(f"/-- {o.statement} -/")
            body.append(o.lean)
        f = root / "Models" / f"{mod}.lean"
        f.write_text("\n".join(body) + "\n")
        files[mod] = (f, os_)
    return files


def _lean_env(lake: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(Path(lake).parent) + os.pathsep + env.get("PATH", "")
    return env


def check_files(target_dir, files: dict, lake: str, jobs: int = 0) -> None:
    """Check each model's file with Lean, and mark each certificate by the errors in it."""
    root = workdir(target_dir)
    # Resolve and build the dependency ONCE, before anything runs in parallel: every
    # `lake env` below reads the manifest this writes, and racing to write it fails them all.
    b = subprocess.run([lake, "build", "Assay"], cwd=root, capture_output=True, text=True,
                       env=_lean_env(lake), timeout=1800, check=False)
    if b.returncode != 0:
        raise RuntimeError("the Lean library would not load for the certificates:\n"
                           + (b.stdout + b.stderr)[-1500:])

    def one(item):
        mod, (f, os_) = item
        r = subprocess.run([lake, "env", "lean", str(Path(f).resolve())], cwd=root, capture_output=True,
                           text=True, env=_lean_env(lake), timeout=600, check=False)
        return mod, f, os_, r.stdout + r.stderr, r.returncode

    with ThreadPoolExecutor(max_workers=jobs or max(1, (os.cpu_count() or 2) - 1)) as ex:
        for _mod, f, os_, out, rc in ex.map(one, sorted(files.items())):
            _mark(f, os_, out, rc)


def _mark(f: Path, os_: list, out: str, rc: int = 0) -> None:
    """Each certificate's status from Lean's own messages, matched by the line ranges.

    *** NOTHING IS PROVEN BY DEFAULT. *** An error Lean reports outside every certificate (the
    library would not load, the file would not parse), or a failed run with no error attributed
    to any certificate, fails every certificate in the file. A certificate is proven only when
    Lean ran to completion on the file and reported nothing against it."""
    text = f.read_text().splitlines()
    starts = {}
    for i, line in enumerate(text, 1):
        m = re.match(r"theorem (\S+)", line)
        if m:
            starts[m.group(1)] = i
    order = sorted(starts.items(), key=lambda kv: kv[1])
    errs: dict = {}
    for m in re.finditer(rf"{re.escape(f.name)}:(\d+):\d+: error: (.*?)(?=\n\S+\.lean:\d+:\d+:|\Z)",
                         out, re.S):
        line = int(m.group(1))
        owner = None
        for name, start in order:
            if start <= line:
                owner = name
        if owner:
            errs.setdefault(owner, []).append(m.group(2).strip())
    sorry = "declaration uses 'sorry'" in out
    stray = [m.group(0)[:300] for m in re.finditer(r"error: .*", out)]
    attributed = sum(len(v) for v in errs.values())
    whole_file = (rc != 0 and attributed == 0) or len(stray) > attributed
    for o in os_:
        if whole_file:
            o.status = NOT_PROVEN
            o.detail = "Lean did not check this file: " + (out.strip()[-800:] or f"exit {rc}")
        elif o.theorem in errs:
            o.status = NOT_PROVEN
            o.detail = "\n".join(errs[o.theorem])[:1500]
        elif sorry:
            o.status = NOT_PROVEN
            o.detail = "a proof used sorry"
        else:
            o.status = PROVEN
            o.detail = f"checked by Lean against {o.rule}"


# ------------------------------------------------------------------------ the store

def write(store, obls: list[Obligation], lean_version: str) -> None:
    store.con.execute(DDL)
    now = datetime.now(timezone.utc)
    rows = []
    for o in obls:
        prem = [{"id": p.id, "relation": p.relation, "name": p.name, "columns": list(p.columns),
                 "property": p.prop, "param": p.param} for p in o.premises]
        rows.append((o.model, o.name, o.checksum, o.prop, o.statement, o.theorem, o.rule,
                     json.dumps(prem), o.status, o.detail, o.missing, "assay", o.lean,
                     lean_version, now))
    if rows:
        store.con.executemany(
            "insert or replace into proofs values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def stored(store) -> list[dict]:
    """Every certificate, the agent's where one exists for a property, else assay's."""
    try:
        store.con.execute(DDL)
        rows = store.con.execute("""
            select model, model_name, model_checksum, property, statement, theorem, rule,
                   premises, status, detail, missing, written_by, lean_version, proved_at
            from (select *, row_number() over (partition by model, property
                                               order by (written_by = 'agent') desc,
                                                        proved_at desc) rn
                  from proofs) where rn = 1
            order by model_name, property""").fetchall()
    except Exception:                                            # noqa: BLE001
        return []
    keys = ("model", "model_name", "model_checksum", "property", "statement", "theorem", "rule",
            "premises", "status", "detail", "missing", "written_by", "lean_version", "proved_at")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["premises"] = json.loads(d["premises"] or "[]")
        d["proved_at"] = str(d["proved_at"] or "")[:19]
        out.append(d)
    return out


def engine_of(project) -> str:
    return (getattr(project, "adapter_type", "") or "duckdb").lower()


def with_guarantees(rows: list[dict], led: L.Ledger, project, store=None) -> list[dict]:
    """Each certificate with its premises' statuses NOW and what that makes the guarantee.

    `stale` when the model's file changed since it was proved; `lost` when a premise broke;
    `conditional` when a premise is not holding but none is broken; `holding` otherwise."""
    for r in rows:
        prem = []
        for p in r["premises"]:
            got = led.premises.get(p["id"]) if led is not None else None
            if got is None and led is not None:
                got = _rebuild(led, p)
            st = got.status if got is not None else L.UNKNOWN
            prem.append({**p, "status": st, "statement": got.statement() if got else "",
                         "why": L.why(got) if got else "",
                         "label": L.label(got) if got else st})
        r["premises"] = prem
        m = project.models.get(r["model"]) if project is not None else None
        now = getattr(m, "checksum", "") if m is not None else ""
        if r["status"] != PROVEN:
            r["guarantee"] = r["status"]
        elif now and r["model_checksum"] and now != r["model_checksum"]:
            r["guarantee"] = "stale"
        elif any(p["status"] == L.BROKEN for p in prem):
            r["guarantee"] = "lost"
        elif all(p["status"] == L.HOLDING for p in prem):
            r["guarantee"] = "holding"
        else:
            r["guarantee"] = "conditional"
        broke = next((p for p in prem if p["status"] == L.BROKEN), None)
        r["lost_because"] = (f"{broke['statement']} broke: {broke['why']}" if broke else "")
        # L4: does this project's engine do what the rule's constructs mean?
        from .conformance import RULE_CONSTRUCTS, status_of
        eng = engine_of(project)
        r["engine"] = [{"construct": c, "engine": eng,
                        **dict(zip(("status", "detail"), status_of(store, c, eng)))}
                       for c in RULE_CONSTRUCTS.get(r.get("rule") or "", [])] \
            if store is not None else []
        bad = [e for e in r["engine"] if e["status"] == L.BROKEN]
        r["engine_note"] = (f"{eng} differs from assay's meaning of {bad[0]['construct']}: "
                            f"{bad[0]['detail']}" if bad else
                            f"not yet measured on {eng}" if any(e["status"] != L.HOLDING
                                                                for e in r["engine"]) else "")
    return rows


def _rebuild(led: L.Ledger, p: dict):
    try:
        if p["property"] == "unique":
            return L.unique(led, p["relation"], p["columns"])
        if p["property"] == "not_null":
            return L.not_null(led, p["relation"], p["columns"][0])
    except Exception:                                            # noqa: BLE001
        return None
    return None


def register(led: L.Ledger, rows: list[dict]) -> None:
    """A certificate is a dependent of each premise it names, like a grain or a held-back
    finding: the Guarantees tab lists it under what it rests on."""
    for r in rows:
        if r.get("status") != PROVEN:
            continue
        for p in r["premises"]:
            got = led.premises.get(p["id"]) or _rebuild(led, p)
            if got is not None:
                led.use(got, "proof", f"proof:{r['model']}:{r['property']}", r["model"],
                        r["statement"])


# ------------------------------------------------------------------------ one call

def run(project, digests, schema, entries, store, target_dir, *, force: bool = False,
        select: set | None = None, say=print) -> dict:
    """Write, check and record every certificate. Models whose checksum has a stored result are
    not re-proved unless `force`; their premises are re-read from the ledger regardless."""
    from . import toolchain
    from .proofs import LEAN_VERSION
    led = L.build(project, schema, entries, store)
    L.register_grains(led, entries)
    obls = obligations(project, digests, schema, entries, led, store)
    if select is not None:
        obls = [o for o in obls if o.model in select]
    have = {(r["model"], r["property"]): r for r in stored(store)} if store is not None else {}
    def fresh_needed(o) -> bool:
        prev = have.get((o.model, o.prop))
        return force or prev is None or prev.get("model_checksum") != o.checksum \
            or not o.lean
    todo = [o for o in obls if fresh_needed(o)]
    fresh = [o for o in todo if o.lean]
    skipped = len(obls) - len(todo)
    for o in todo:
        if not o.lean:
            o.status = NOT_ATTEMPTED
            o.detail = o.missing or "no rule applies to this structure"
    if fresh:
        lake = toolchain.lake_for_build()
        if lake is None:
            raise RuntimeError("no Lean toolchain: run `assay prove --setup`")
        toolchain.build_library(say=lambda *_a: None)
        files = write_project(target_dir, fresh)
        say(f"checking {len(fresh)} certificate(s) in {len(files)} model file(s) with Lean "
            f"{LEAN_VERSION}")
        check_files(target_dir, files, lake)
        for o in fresh:
            if o.status == NOT_PROVEN and o.missing:
                o.detail = f"{o.missing}\n\nLean: {o.detail}"
    if store is not None:
        write(store, todo, LEAN_VERSION)
    rows = with_guarantees(stored(store), led, project, store) if store is not None else []
    return {"certificates": len(obls), "checked_now": len(fresh), "reused": skipped,
            "rows": rows, "written_to": str(workdir(target_dir))}
