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
        out += _joins(uid, m, cs, e, led, declared, d)
        out += _picks(uid, m, cs, d, led, declared)
        g = _grain(uid, m, cs, d, e, led, declared)
        if g is not None:
            out.append(g)
        i = _incremental(uid, m, cs, d, e, led, project, schema, digests, store)
        if i is not None:
            out.append(i)
    return out


def _target_name(led, j) -> tuple[str, str | None]:
    """(what to call a join's target, its unique_id when it is a model or source)."""
    if j.target_relation:
        rel = j.target_relation.replace('"', "").lower()
        uid = next((u for u, r in (led.schema.relation or {}).items()
                    if (r or "").replace('"', "").lower() == rel), None) \
            if led.schema is not None else None
        name = led.project.name_of(uid) if uid else rel.split(".")[-1]
        return name, uid
    return (j.target_cte or j.target_alias or "a subquery"), None


def _join_step(led, declared, j, i: int, used: set | None = None):
    """How one join is carried in a certificate: a dict with the right-hand table term, the
    uniqueness proof term, the hypotheses and premises it needs, and what to call it; or a
    string saying why no rule applies. (L1: the target's own grouping, dedupe or filter first,
    the base table's key only when the join reads the table itself.)"""
    kind = (j.kind or "").upper()
    if j.lateral:
        return "a lateral join: no rule states its row count yet"
    if kind in ("CROSS", "RIGHT", "FULL"):
        return f"a {kind} join: no rule states its row count yet"
    if not j.equi:
        return ("the join condition is not key equality (a spatial, range or computed join): "
                "no row-count rule applies to it")
    rk = list(j.equi_keys)
    name, uid = _target_name(led, j)
    R = f"R{i}"
    if j.target_unique:
        keys = [k.lower() for k in j.target_unique]
        if j.target_unique_by == "group_by":
            return {"name": name, "rk": rk, "left": kind == "LEFT", "keys": keys, "premises": [],
                    "hyps": [], "right": f"(groupBy {_lean_list(keys)} {R})",
                    "unique": f"(group_by_unique {_lean_list(keys)} {R})",
                    "why": f"grouped by ({', '.join(keys)})", "o": False}
        order = [k.lower() for k in (j.target_order or [])]
        return {"name": name, "rk": rk, "left": kind == "LEFT", "keys": keys, "premises": [],
                "hyps": [], "right": f"(pick o {_lean_list(keys)} {_lean_list(order)} {R})",
                "unique": f"(pick_unique o {_lean_list(keys)} {_lean_list(order)} {R})",
                "why": f"one row per ({', '.join(keys)})", "o": True}
    base_uid, filtered = uid, False
    if base_uid is None and j.target_filter_of:
        base_uid = L.uid_of(led.project, j.target_filter_of)
        filtered = base_uid in led.project.models or base_uid in led.project.sources
        if not filtered:
            base_uid = None
    if base_uid is None:
        return ("the join reads a CTE or subquery built from several relations, and nothing "
                "states what it is unique on")
    pk = [c.lower() for c in declared.get(base_uid) or []]
    us = pk or rk
    prem = L.unique(led, base_uid, us)
    base_name = led.project.name_of(base_uid)
    # Two joins onto one relation assume one premise about two tables: one hypothesis each.
    h = _hyp(prem)
    if used is not None:
        if h in used:
            h = f"{h}_{i}"
        used.add(h)
    right = f"(filterT p{i} {R})" if filtered else R
    unique = f"(filter_preserves_unique p{i} {h})" if filtered else h
    return {"name": base_name if not filtered else f"{name} (a filter of {base_name})", "rk": rk,
            "left": kind == "LEFT", "keys": us, "premises": [prem],
            "hyps": [f"({h} : Unique {_lean_list(us)} {R})"], "right": right,
            "unique": unique, "why": "", "o": False, "filtered": filtered, "pk": pk,
            "base": base_name}


def _joins(uid, m, cs, e, led, declared, d=None) -> list[Obligation]:
    out, seen = [], {}
    for i, j in enumerate(d.joins if d is not None else [], 1):
        name, _u = _target_name(led, j)
        seen[name] = seen.get(name, 0) + 1
        prop = f"no_fanout:{name}" + (f"#{seen[name]}" if seen[name] > 1 else "")
        step = _join_step(led, declared, j, 1)
        left = (j.kind or "").upper() == "LEFT"
        stmt = (f"the left join onto `{name}` keeps exactly `{m.name}`'s left rows" if left
                else f"a join onto `{name}` cannot multiply `{m.name}`'s rows")
        if isinstance(step, str):
            o = Obligation(uid, m.name, cs, prop, stmt)
            o.missing = step
            out.append(o)
            continue
        rkl = _lean_list(step["rk"])
        rule = "left_join_preserves_rows" if left else "inner_join_no_fanout"
        o = Obligation(uid, m.name, cs, prop,
                       stmt + (f", {step['why']}" if step["why"] else ""), rule)
        o.premises = step["premises"]
        concl = (f"(leftJoin lk {rkl} L {step['right']}).length = L.length" if left
                 else f"(innerJoin lk {rkl} L {step['right']}).length ≤ L.length")
        binders = "(lk : List String) (L R1 : Table)" + (" (o : KeyOrder)" if step["o"] else "") \
            + (" (p1 : Row → Bool)" if step.get("filtered") else "")
        o.lean = (f"theorem {o.theorem} {binders}" + "".join("\n    " + h for h in step["hyps"])
                  + f" :\n    {concl} :=\n"
                  f"  {rule} (us := {_lean_list(step['keys'])}) (by decide) {step['unique']}\n")
        if not set(step["keys"]) <= set(step["rk"]):
            what = (f"`{step.get('base', name)}`'s declared key is" if step.get("pk")
                    else "it is unique on")
            o.missing = (f"the join is on ({', '.join(step['rk'])}) and {what} "
                         f"({', '.join(step['keys'])}): join on "
                         f"{', '.join(sorted(set(step['keys']) - set(step['rk'])))} too")
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
    """The grain, proven: by the model's own group by or dedupe when it has one (no premise),
    otherwise carried from its one driving relation through each join, and only when the model
    passes rows through (no grouping, dedupe or union of its own to carry it past)."""
    grain = getattr(e, "grain", None)
    gcols = [c.lower() for c in (grain.value if grain is not None and grain.value else [])]
    own, how, order = getattr(d, "own_unique", (None, "", [])) or (None, "", [])
    g = [c.lower() for c in (getattr(d, "final_group_by", None) or [])]
    if own is None and g:
        own, how, order = g, "group_by", []
    if own:
        own = [c.lower() for c in own]
        target = gcols if gcols and set(own) <= set(gcols) else own
        if how == "group_by":
            base, proof = f"(groupBy {_lean_list(own)} t)", f"(group_by_unique {_lean_list(own)} t)"
            words, binders = "its group by", "(t : Table)"
        else:
            base = f"(pick o {_lean_list(own)} {_lean_list(order)} t)"
            proof = f"(pick_unique o {_lean_list(own)} {_lean_list(order)} t)"
            words = "its DISTINCT ON" if how == "distinct_on" else "its row_number() = 1 dedupe"
            binders = "(o : KeyOrder) (t : Table)"
        o = Obligation(uid, m.name, cs, "grain",
                       f"`{m.name}` is one row per ({', '.join(target)}): {words}",
                       "group_by_unique" if how == "group_by" else "pick_unique")
        if target == own:
            o.lean = (f"theorem {o.theorem} {binders} : Unique {_lean_list(own)} {base} :=\n"
                      f"  {proof}\n")
        else:
            o.lean = (f"theorem {o.theorem} {binders} : Unique {_lean_list(target)} {base} :=\n"
                      f"  Unique.mono (by decide) {proof}\n")
        return o
    drivers = sorted(getattr(e, "driving_parents", None) or [])
    if grain is None or grain.source not in ("declared", "derived", "observed") \
            or len(drivers) != 1 or not gcols:
        return None
    o = Obligation(uid, m.name, cs, "grain",
                   f"`{m.name}` stays one row per ({', '.join(gcols)}) through its joins",
                   "grain_through_join")
    if d.group_by or d.windows or d.distinct or getattr(d, "union_members", None) \
            or getattr(d, "distinct_on", None):
        o.missing = ("the model groups, dedupes or unions on something other than its grain, so "
                     "the grain is not carried row by row from one relation")
        return o
    fu, fhow, forder = getattr(d, "from_unique", (None, "", [])) or (None, "", [])
    fsrc = getattr(d, "from_sources", []) or []
    extra = []
    if fu and {c.lower() for c in fu} == set(gcols) and len(fsrc) == 1:
        # *** THE MODEL READS A CTE ALREADY ONE ROW PER ITS GRAIN. *** (L1) The grain holds by
        # that CTE's own group by or dedupe; only "never null" is assumed, of the table under it.
        keys = [c.lower() for c in fu]
        src = L.uid_of(led.project, fsrc[0])
        pns = [L.not_null(led, src, c) for c in gcols]
        prem = list(pns)
        hyps = [f"({_hyp(p)} : NotNull {_lean_str(c)} T)" for p, c in zip(pns, gcols)]
        nn0 = _all_notnull(gcols, [_hyp(p) for p in pns], "T")
        if fhow == "group_by":
            base = f"(groupBy {_lean_list(keys)} T)"
            term = f"(Unique.mono (by decide) (group_by_unique {_lean_list(keys)} T))"
            nn = f"(notnull_all_group_by (by decide) {nn0})"
        else:
            order = [c.lower() for c in forder]
            base = f"(pick o {_lean_list(keys)} {_lean_list(order)} T)"
            term = (f"(Unique.mono (by decide) (pick_unique o {_lean_list(keys)} "
                    f"{_lean_list(order)} T))")
            nn = f"(notnull_all_pick o {nn0})"
            extra.append("(o : KeyOrder)")
        o.statement = (f"`{m.name}` stays one row per ({', '.join(gcols)}): the CTE it reads is "
                       f"one row per it, and its joins keep that")
        table, rs = base, ["T"]
    else:
        driver = L.uid_of(led.project, drivers[0])
        pg = L.unique(led, driver, gcols)
        pns = [L.not_null(led, driver, c) for c in gcols]
        prem = [pg, *pns]
        hyps = [f"({_hyp(pg)} : Unique {_lean_list(gcols)} L)"]
        hyps += [f"({_hyp(p)} : NotNull {_lean_str(c)} L)" for p, c in zip(pns, gcols)]
        term, nn = _hyp(pg), _all_notnull(gcols, [_hyp(p) for p in pns], "L")
        table, rs = "L", ["L"]
    used = {_hyp(p) for p in prem}
    for i, j in enumerate(d.joins or [], 1):
        step = _join_step(led, declared, j, i, used)
        if isinstance(step, str):
            o.missing = f"a join cannot be carried: {step}"
            return o
        rs.append(f"R{i}")
        prem += step["premises"]
        hyps += step["hyps"]
        if step["o"] and "(o : KeyOrder)" not in extra:
            extra.append("(o : KeyOrder)")
        if step.get("filtered"):
            extra.append(f"(p{i} : Row → Bool)")
        rk = _lean_list(step["rk"])
        rule = "grain_through_left_join" if step["left"] else "grain_through_join"
        carry = "notnull_all_through_left_join" if step["left"] else "notnull_all_through_join"
        op = "leftJoin" if step["left"] else "innerJoin"
        term = (f"({rule} (lk := lk{i}) (rk := {rk}) (us := {_lean_list(step['keys'])}) {term} "
                f"{nn} (by decide) {step['unique']})")
        nn = f"({carry} (lk := lk{i}) (rk := {rk}) (R := {step['right']}) {nn})"
        table = f"({op} lk{i} {rk} {table} {step['right']})"
        if not set(step["keys"]) <= set(step["rk"]):
            o.missing = (f"the join onto `{step['name']}` covers none of what it is unique on "
                         f"({', '.join(step['keys'])}): the grain cannot be carried through it")
    o.premises = prem
    n_joins = len(rs) - 1
    lks = " ".join(f"lk{i}" for i in range(1, n_joins + 1))
    binders = f"({' '.join(rs)} : Table)"
    o.lean = (f"theorem {o.theorem} {('(' + lks + ' : List String) ') if lks else ''}{binders}"
              f" {' '.join(extra)} (p : Row → Bool)\n    " + "\n    ".join(hyps) +
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
                         out, re.DOTALL):
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
            # *** A GUARANTEE IS LOST ONLY IF SOMEBODY ASSERTED IT. *** (L1) A key nothing
            # declared, taken from the join's own columns and counted duplicated, never held:
            # the property does not hold here, often on purpose (a join to many readings, then a
            # group by). Only a declared or configured key that stopped holding is "lost".
            broke_p = [led.premises.get(p["id"]) for p in prem if p["status"] == L.BROKEN]
            declared_ = any(x is not None and any(ev.kind in ("declared", "config")
                                                  for ev in x.evidence) for x in broke_p)
            r["guarantee"] = "lost" if declared_ else "refuted"
        elif all(p["status"] == L.HOLDING for p in prem):
            r["guarantee"] = "holding"
        else:
            r["guarantee"] = "conditional"
        broke = next((p for p in prem if p["status"] == L.BROKEN), None)
        r["lost_because"] = ((f"{broke['statement']} broke: {broke['why']}"
                              if r["guarantee"] == "lost" else
                              f"{broke['statement']} is not so: {broke['why']}")
                             if broke else "")
        # L4: does this project's engine do what the rule's constructs mean?
        from .conformance import CONFORMS, DIFFERS, RULE_CONSTRUCTS, status_of
        eng = engine_of(project)
        r["engine"] = [{"construct": c, "engine": eng,
                        **dict(zip(("status", "detail"), status_of(store, c, eng)))}
                       for c in RULE_CONSTRUCTS.get(r.get("rule") or "", [])] \
            if store is not None else []
        bad = [e for e in r["engine"] if e["status"] == DIFFERS]
        r["engine_note"] = (f"{eng} differs from assay's meaning of {bad[0]['construct']}: "
                            f"{bad[0]['detail']}" if bad else
                            f"not yet measured on {eng}" if any(e["status"] != CONFORMS
                                                                for e in r["engine"]) else "")
    # *** A JOIN THAT MULTIPLIES, THEN GROUPED BACK, IS NOT THE ONE TO ACT ON. *** (sunny-data
    # feedback L9) Four of seven "do not hold" joined onto readings and grouped by the model's
    # grain afterwards, on purpose; the three left were a real duplication. Where the model's own
    # grain is proven by its group by, the fan-out is gone from its output: "regrouped".
    regrouped = {r["model"] for r in rows if r["property"] == "grain"
                 and r.get("rule") == "group_by_unique"
                 and r["guarantee"] in ("holding", "conditional")}
    for r in rows:
        if r["guarantee"] == "refuted" and r["property"].startswith("no_fanout") \
                and r["model"] in regrouped:
            r["guarantee"] = "regrouped"
    # *** `status` IS THE VERDICT. *** (L8) It said `proven` beside `guarantee: refuted`, so a
    # consumer filtering on it counted seven joins that multiply rows as proven. `lean_checked`
    # keeps what Lean did; `status` says what holds.
    for r in rows:
        r["lean_checked"] = r["status"] == PROVEN
        r["status"] = VERDICT.get(r["guarantee"], r["guarantee"])
    return rows


# guarantee -> status. Proven means it holds for every input its premises allow.
VERDICT = {"holding": PROVEN, "conditional": PROVEN, "refuted": "does_not_hold",
           "regrouped": "regrouped", "lost": "lost", "stale": "stale",
           "not_proven": "not_proven", "not_attempted": "not_attempted"}


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
