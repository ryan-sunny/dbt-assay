"""Does each proven certificate hold on a real run of its model? (validation of `prove` itself)

*** LEAN CHECKS THE PROOF. NOTHING CHECKED THE STATEMENT. *** A certificate is a theorem assay
WRITES from its reading of the model: "the join onto `x` cannot multiply the rows, as long as `id`
is unique in `x`". Lean proves that theorem about the term assay wrote. If assay read the model
wrong (the join's target was a grouped subquery, not the table; the grain came through a dedupe),
Lean proves a true statement about some other query, and the certificate reads proven. Every
wrong verdict the first re-test found was this kind.

So each proven claim is run. For every model with one, its inputs are generated so they MEET the
certificate's premises (a column a premise says is unique gets distinct values; one it says is
never NULL gets none) and are adversarial everywhere else (repeated keys, NULLs, ties), in several
random datasets. The model's own SQL runs in an in-memory DuckDB, and the claim is tested on what
it returns:

* `unique` (a grain): no two output rows share the key, rows with a NULL in it exempt, as dbt's
  `unique` test counts;
* `join` (no fan-out): the rows the model's select produces with this join, against without it,
  every other clause of that select dropped: an inner join may not add rows, a left join must
  keep exactly as many.

A claim that fails on inputs meeting its premises means `prove` stated the wrong theorem: the
certificate reads `contradicted`, never proven, and the case that broke it is kept. Only DuckDB
SQL runs here; a model whose SQL cannot run on generated inputs is `unchecked`, with the reason.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp

HOLDS, CONTRADICTED, UNCHECKED = "holds", "contradicted", "unchecked"
N_DATASETS = 3
N_ROWS = 7

DDL = """
create table if not exists claim_checks (
    model          varchar,
    property       varchar,
    model_checksum varchar,
    premises_key   varchar,        -- the premises the inputs were generated to meet
    status         varchar,        -- holds | contradicted | unchecked
    detail         varchar,
    checked_at     timestamp,
    primary key (model, property)
);
"""


def _pool(kind: str) -> list:
    return {"int": [1, 2, 2, 3], "float": [1.5, 2.25, 2.25], "text": ["a", "b", "b", "ab"],
            "date": ["2026-01-01", "2026-01-02", "2026-01-02"],
            "timestamp": ["2026-01-01 00:00:00", "2026-01-02 00:00:00"],
            "bool": [True, False], "null": [None]}[kind]


def _distinct(kind: str, i: int):
    return {"int": i + 1, "float": i + 0.5, "text": f"k{i}",
            "date": f"2026-02-{i + 1:02d}", "timestamp": f"2026-02-{i + 1:02d} 00:00:00"}.get(kind)


def _norm(s: str) -> str:
    return (s or "").replace('"', "").lower()


def _rows_for(premises: list, rel_of: dict, rng: random.Random):
    """rows_for(full, cols) meeting the premises on that relation."""
    def rows_for(full, cols):
        uid = rel_of.get(_norm(full))
        mine = [p for p in premises if uid and p["relation"] == uid]
        unique = [p["columns"] for p in mine if p["property"] == "unique"]
        notnull = {c.lower() for p in mine if p["property"] == "not_null" for c in p["columns"]}
        # one column of each unique key carries distinct values: then the key is distinct
        distinct_col = {ks[0].lower() for ks in unique if ks}
        out = []
        n = rng.randint(2, N_ROWS)
        order = list(range(n))
        rng.shuffle(order)
        for i in order:
            row = []
            for c, k, _raw in cols:
                if c in distinct_col and _distinct(k, i) is not None:
                    row.append(_distinct(k, i))
                elif c in distinct_col:
                    raise _Unmet(f"`{c}` must be distinct and is {k}")
                else:
                    v = rng.choice(_pool(k))
                    if c not in notnull and rng.random() < 0.25:
                        v = None
                    if c in notnull and v is None:
                        raise _Unmet(f"`{c}` must never be NULL and has no non-NULL value here")
                    row.append(v)
            out.append(row)
        return out
    return rows_for


class _Unmet(Exception):
    """The inputs cannot be generated to meet a premise."""


def _wrap(sql: str) -> str:
    return sql.strip().rstrip(";")


def _unique_violations(con, sql: str, cols: list) -> tuple[int, str]:
    keys = ", ".join(f'"{c}"' for c in cols)
    nn = " and ".join(f'"{c}" is not null' for c in cols)
    got = con.execute(f"select {keys}, count(*) from ({_wrap(sql)}) as __m where {nn} "
                      f"group by {keys} having count(*) > 1 limit 1").fetchall()
    return (1, f"key {got[0][:-1]} appears {got[0][-1]} times") if got else (0, "")


def _join_counts(sql: str, index: int, dialect: str) -> tuple[str, str]:
    """Two statements: the rows the join's own select produces through this join, and through
    the joins before it, every other clause of that select dropped."""
    tree = sqlglot.parse_one(sql, read=dialect)

    def variant(keep_this: bool) -> str:
        t = tree.copy()
        joins = list(t.find_all(exp.Join))
        if index > len(joins):
            raise _Unmet("the join is not where the parse put it")
        j = joins[index - 1]
        sel = j.parent
        if not isinstance(sel, exp.Select):
            raise _Unmet("the join is not in a select")
        k = sel.args["joins"].index(j)
        sel.set("joins", sel.args["joins"][:k + 1] if keep_this else sel.args["joins"][:k])
        for a in ("where", "group", "having", "qualify", "order", "limit", "offset", "distinct",
                  "windows"):
            sel.set(a, None)
        sel.set("expressions", [exp.alias_(exp.Count(this=exp.Star()), "__n")])
        if sel is t:
            return t.sql(dialect=dialect)
        body = sel.copy()
        w = t.args.get("with_") or t.args.get("with")
        if w is not None:
            body.set("with_" if "with_" in body.arg_types else "with", w.copy())
        return body.sql(dialect=dialect)
    return variant(True), variant(False)


def check_model(project, schema, uid: str, sql: str, dialect: str, certs: list,
                seed: int = 13) -> dict:
    """{property: (status, detail)} for one model's proven certificates."""
    import duckdb

    from . import parsecheck
    out: dict = {}
    if (dialect or "duckdb") != "duckdb":
        return {c["property"]: (UNCHECKED, f"runs DuckDB SQL only; this project is {dialect}")
                for c in certs}
    try:
        ins = parsecheck._inputs(project, schema, sql, dialect)
    except Exception as e:                                       # noqa: BLE001
        return {c["property"]: (UNCHECKED, f"not read: {str(e)[:160]}") for c in certs}
    rel_of = {_norm(r): u for u, r in (getattr(schema, "relation", {}) or {}).items() if r}
    read = {rel_of.get(_norm(full)) for full, *_x in ins}
    for c in certs:
        away = sorted({p["relation"] for p in c["premises"]} - read)
        if away:
            out[c["property"]] = (UNCHECKED, ("a premise is about a relation the model does not "
                                             f"read directly ({away[0].split('.')[-1]})"))
            continue
        claim = c.get("claim") or {}
        if claim.get("kind") not in ("unique", "join"):
            out[c["property"]] = (UNCHECKED, "no run check for this kind of claim yet")
            continue
        status, detail = HOLDS, ""
        for n in range(N_DATASETS):
            rng = random.Random(f"{seed}:{uid}:{c['property']}:{n}")
            con = duckdb.connect(":memory:")
            try:
                parsecheck.fill(con, ins, sql, _rows_for(c["premises"], rel_of, rng))
                if claim["kind"] == "unique":
                    bad, why = _unique_violations(con, sql, claim["cols"])
                    if bad:
                        status, detail = CONTRADICTED, f"dataset {n + 1}: {why}"
                        break
                else:
                    with_j, without = _join_counts(sql, claim["index"], dialect)
                    a = con.execute(with_j).fetchone()[0]
                    b = con.execute(without).fetchone()[0]
                    if (a != b) if claim.get("left") else (a > b):
                        status = CONTRADICTED
                        detail = (f"dataset {n + 1}: {a} row(s) through the join against {b} "
                                  f"before it")
                        break
            except _Unmet as e:
                status, detail = UNCHECKED, f"inputs cannot meet the premises: {e}"
                break
            except Exception as e:                               # noqa: BLE001
                status = UNCHECKED
                detail = "the SQL could not run on generated inputs: " + \
                         str(e).splitlines()[0][:200]
                break
            finally:
                con.close()
        if status == HOLDS:
            detail = f"held on {N_DATASETS} generated datasets that meet its premises"
        out[c["property"]] = (status, detail)
    return out


def _premises_key(certs: list) -> str:
    return hashlib.sha256(json.dumps(sorted(p["id"] for c in certs for p in c["premises"]))
                          .encode()).hexdigest()[:16]


def run(project, schema, store, obligations: list, proven: set, *, force: bool = False,
        say=print) -> dict:
    """Check every proven certificate whose model file or premises changed since last time.
    `proven`: the (model, property) pairs Lean proved."""
    store.con.execute(DDL)
    have = {(m, p): (cs, pk) for m, p, cs, pk in store.con.execute(
        "select model, property, model_checksum, premises_key from claim_checks").fetchall()}
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    by_model: dict = {}
    for o in obligations:
        if (o.model, o.prop) not in proven:
            continue
        prem = [{"id": p.id, "relation": p.relation, "columns": list(p.columns),
                 "property": p.prop} for p in o.premises]
        by_model.setdefault(o.model, []).append(
            {"property": o.prop, "claim": o.claim, "premises": prem, "checksum": o.checksum})
    rows, n = [], 0
    now = datetime.now(timezone.utc)
    for uid, certs in sorted(by_model.items()):
        # the inputs meet EVERY premise of the model's proven certificates at once, so all its
        # claims are tested on the same runs
        allp = [p for c in certs for p in c["premises"]]
        key = _premises_key(certs)
        todo = [c for c in certs if force or have.get((uid, c["property"])) != (c["checksum"], key)]
        if not todo:
            continue
        m = project.models.get(uid)
        if m is None or not m.readable:
            continue
        n += len(todo)
        got = check_model(project, schema, uid, m.compiled, dialect,
                          [{**c, "premises": allp} for c in todo])
        for c in todo:
            st, detail = got[c["property"]]
            rows.append((uid, c["property"], c["checksum"], key, st, detail, now))
    if rows:
        store.con.executemany("insert or replace into claim_checks values (?,?,?,?,?,?,?)", rows)
    if n:
        say(f"checked {n} proven claim(s) on runs of their models")
    return {"checked": n}


def stored(store) -> dict:
    """{(model, property): {status, detail, model_checksum}}."""
    try:
        store.con.execute(DDL)
        return {(m, p): {"status": s, "detail": d, "model_checksum": cs}
                for m, p, cs, s, d in store.con.execute(
                    "select model, property, model_checksum, status, detail from claim_checks")
                .fetchall()}
    except Exception:                                            # noqa: BLE001
        return {}
