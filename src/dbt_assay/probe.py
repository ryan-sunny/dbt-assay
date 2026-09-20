"""Counting what the SQL cannot settle, without ever holding a credential.

*** ASSAY NEVER SEES YOUR WAREHOUSE PASSWORD. ***
It shells out to the dbt binary the project already has configured:
`dbt show --inline "<sql>" --output json`. Every adapter, every auth scheme -- key pair, OAuth, IAM,
SSO -- works because assay is not doing the dance. The alternative, parsing profiles.yml and
connecting per-adapter, is a second product and it is the part that would break constantly.

*** WHY THIS EXISTS AT ALL. ***
Grain propagates through the DAG parents-first, and it has no BASE CASE: a model reads a source, the
source declares no key, and propagation stops. Measured on a real project, that left the true key
outside the candidate set for most models, and a judgment cannot pick an option it was never given.
`count(distinct k)` settles it exactly, once, and caches forever.

*** ONE QUERY PER RELATION, WHICH IS ALREADY THE CHEAP SHAPE. ***
count(*) plus per-column counts in a single statement is ONE SCAN however many candidates are
tested. An approximate first pass would cut compute inside that scan but not the I/O that dominates
the bill, so it buys nothing here and is not done.

*** NULLS ARE COUNTED SEPARATELY, BECAUSE count(distinct) IGNORES THEM. ***
A column that is 90% NULL can report distinct == count and look unique. A key needs BOTH no nulls
and no duplicates, so all three numbers are kept and the verdict says which test failed. dbt's own
`unique` test ignores nulls the same way, which is worth knowing when the two disagree.

*** OBSERVED IS NOT DECLARED. ***
`count(distinct k) = count(*)` means unique IN TODAY'S DATA. It is not a constraint and tomorrow's
load can break it. Every row carries its row count and timestamp and the wording never says "is the
key". A probe that ERRORS records `unknown`, never `not_unique`: a read-only role that cannot see a
schema, silently read as a duplicate key, is a guard that cannot see with the sign flipped.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp

# *** A HEURISTIC IS ALLOWED IN TARGETING AND NEVER IN A VERDICT. ***
# Guessing WHAT TO COUNT costs a little compute when it is wrong. Guessing the ANSWER would be
# wrong. So when a relation's children give no structural hint -- a plain `select * from source`
# offers no join key, no partition, no group by -- assay falls back to names that look like
# identifiers, and the COUNT still decides.
ID_LIKE = re.compile(r"(^|_)(id|key|pk|no|num|number|code|uuid|guid)$|^id$", re.IGNORECASE)

DDL = """
create table if not exists observed_keys (
    relation     varchar,
    column_name  varchar,
    row_count    bigint,
    non_null     bigint,
    distinct_ct  bigint,
    status       varchar,     -- unique | has_duplicates | has_nulls | unknown
    detail       varchar,
    observed_at  timestamp,
    via          varchar,     -- dbt-show | loaded
    primary key (relation, column_name)
);
"""


@dataclass
class Target:
    relation: str                 # catalog.schema.table, as compiled SQL spells it
    uid: str
    columns: list[str] = field(default_factory=list)
    why: str = ""


@dataclass
class Observation:
    relation: str
    column: str
    row_count: int | None = None
    non_null: int | None = None
    distinct_ct: int | None = None
    status: str = "unknown"
    detail: str = ""

    @property
    def is_unique_key(self) -> bool:
        return self.status == "unique"


def targets(project, digests, schema, declared, known_grain: dict) -> list[Target]:
    """Relations whose grain nothing can settle, and only the columns their children key on.

    Targeting is GRAPH POSITION, never a folder name: a project with every model in one flat
    directory is probed identically.
    """
    out: list[Target] = []
    for uid in list(project.sources) + list(project.models):
        if uid in declared or uid in known_grain:
            continue                                  # already settled; nothing to buy
        rel = schema.relation.get(uid)
        if not rel:
            continue
        children = (project.sources[uid].children if uid in project.sources
                    else project.models[uid].children)
        # Settling a leaf's grain unblocks nothing, and the probe exists to unblock propagation.
        if not children:
            continue
        wanted: set[str] = set()
        referenced: set[str] = set()
        for ch in children:
            d = digests.get(ch)
            if not d or not d.ok:
                continue
            for j in d.joins:
                if (j.target_relation or "").lower() == rel.lower():
                    wanted.update(j.target_keys)
            for w in d.windows:
                wanted.update(w.partition_columns)
            wanted.update(d.group_by_columns)
            referenced.update(d.referenced_columns)
        have = [c.lower() for c in schema.columns(uid).names]
        have_set = set(have)
        cols = sorted(c for c in wanted if not have_set or c in have_set)
        why = f"{len(children)} models read it; grain unknown"
        if not cols and have:
            cols = [c for c in have if ID_LIKE.search(c)]
            why += "; no structural key hint, so id-like column names were counted"
        if not cols and not have and referenced:
            # A SOURCE usually declares no columns anywhere, so the only evidence of what it holds
            # is what its children read out of it.
            cols = sorted(c for c in referenced if ID_LIKE.search(c))
            why += "; columns inferred from what its children reference"
        if cols:
            out.append(Target(relation=rel, uid=uid, columns=cols[:12], why=why))
    return out


def build_sql(target: Target, dialect: str = "duckdb") -> str:
    """One statement, one scan, three numbers per candidate column."""
    parts = ["count(*) as row_count"]
    for i, c in enumerate(target.columns):
        col = sqlglot.parse_one(c, dialect=dialect).sql(dialect=dialect)
        parts.append(f"count({col}) as nn_{i}")
        parts.append(f"count(distinct {col}) as dc_{i}")
    rel = exp.to_table(target.relation).sql(dialect=dialect)
    return f"select {', '.join(parts)} from {rel}"


def parse_dbt_show(stdout: str) -> dict | None:
    """dbt writes log lines to stdout before the JSON, so the object is found, not assumed."""
    idx = stdout.find("\n{")
    if idx == -1:
        idx = 0 if stdout.lstrip().startswith("{") else -1
    if idx == -1:
        return None
    try:
        return json.loads(stdout[idx:])
    except json.JSONDecodeError:
        # A trailing banner after the object: take the widest prefix that parses.
        for end in range(len(stdout), idx, -1):
            try:
                return json.loads(stdout[idx:end])
            except json.JSONDecodeError:
                continue
    return None


def interpret(target: Target, row: dict) -> list[Observation]:
    n = row.get("row_count")
    out = []
    for i, c in enumerate(target.columns):
        nn, dc = row.get(f"nn_{i}"), row.get(f"dc_{i}")
        o = Observation(target.relation, c, n, nn, dc)
        if nn is None or dc is None or n is None:
            o.status, o.detail = "unknown", "the query did not return counts for this column"
        elif nn < n:
            # NULLs first: `count(distinct)` ignores them, so a mostly-null column can look unique.
            o.status = "has_nulls"
            o.detail = f"{n - nn:,} of {n:,} rows are NULL, so this cannot be a key on its own"
        elif dc < nn:
            o.status = "has_duplicates"
            o.detail = f"{nn:,} rows, {dc:,} distinct: {nn - dc:,} duplicates"
        else:
            o.status = "unique"
            o.detail = f"{n:,} rows, all non-null and distinct, observed today"
        out.append(o)
    return out


def run_via_dbt(target: Target, project_dir: str, profiles_dir: str | None = None,
                dialect: str = "duckdb", timeout: int = 300,
                dbt_bin: str = "dbt") -> tuple[list[Observation], str]:
    """Returns (observations, raw_sql). A failure yields `unknown` rows, never `not unique`.

    `dbt_bin` may carry arguments ("uv run dbt", "poetry run dbt", a venv path), because plenty of
    projects have no bare `dbt` on PATH and failing on that would be a pointless wall.
    """
    sql = build_sql(target, dialect)
    cmd = [*dbt_bin.split(), "show", "--inline", sql, "--output", "json", "--limit", "1"]
    if profiles_dir:
        cmd += ["--profiles-dir", profiles_dir]
    try:
        p = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ([Observation(target.relation, c, status="unknown", detail=str(e)[:200])
                 for c in target.columns], sql)

    data = parse_dbt_show(p.stdout or "")
    rows = (data or {}).get("show") or []
    if not rows:
        why = (p.stderr or p.stdout or "no output")[-300:].strip()
        return ([Observation(target.relation, c, status="unknown", detail=why)
                 for c in target.columns], sql)
    return interpret(target, rows[0]), sql


def write(store, observations: list[Observation], via: str = "dbt-show") -> None:
    store.con.execute(DDL)
    now = datetime.now(timezone.utc)
    store.con.executemany(
        "insert or replace into observed_keys values (?,?,?,?,?,?,?,?,?)",
        [[o.relation, o.column, o.row_count, o.non_null, o.distinct_ct,
          o.status, o.detail, now, via] for o in observations])


def read(store) -> dict[str, dict[str, Observation]]:
    """{relation: {column: Observation}}, for grain propagation to use as a base case."""
    store.con.execute(DDL)
    out: dict[str, dict[str, Observation]] = {}
    for rel, col, n, nn, dc, status, detail, _at, _via in store.con.execute(
            "select relation, column_name, row_count, non_null, distinct_ct, status, detail,"
            " observed_at, via from observed_keys").fetchall():
        out.setdefault(rel.lower(), {})[col] = Observation(rel, col, n, nn, dc, status, detail)
    return out


def sample_sql(relation: str, columns: list[str], n: int = 20, dialect: str = "duckdb") -> str:
    """A handful of real values per column. The feed layer judges the KIND of thing they are.

    *** NO LIMIT CLAUSE. ***
    `dbt show` appends its own, so a statement carrying one renders as `limit 8 limit 5` and dies
    on a parser error -- silently, because a failed sample just looks like an empty table. The row
    count comes from `--limit` instead.
    """
    cols = ", ".join(sqlglot.parse_one(c, dialect=dialect).sql(dialect=dialect) for c in columns)
    rel = exp.to_table(relation).sql(dialect=dialect)
    return f"select {cols} from {rel}"


def profile_sql(relation: str, columns: list[str], dialect: str = "duckdb") -> str:
    """*** HALF THE FEED LAYER IS ARITHMETIC AND IS NEVER ASKED. ***

    A numeric column spiking at -9999, or a date whose maximum sits years in the future, is a
    sentinel found by counting. Only meaning goes to a judgment.
    """
    parts = ["count(*) as row_count"]
    for i, c in enumerate(columns):
        col = sqlglot.parse_one(c, dialect=dialect).sql(dialect=dialect)
        parts += [f"min(try_cast({col} as double)) as min_{i}",
                  f"max(try_cast({col} as double)) as max_{i}",
                  f"count(try_cast({col} as double)) as num_{i}"]
    rel = exp.to_table(relation).sql(dialect=dialect)
    return f"select {', '.join(parts)} from {rel}"


def run_sql(sql: str, project_dir: str, profiles_dir: str | None = None,
            dbt_bin: str = "dbt", limit: int = 50, timeout: int = 300) -> list[dict]:
    """Any read-only statement, through the project's own dbt. assay never holds a credential."""
    cmd = [*dbt_bin.split(), "show", "--inline", sql, "--output", "json", "--limit", str(limit)]
    if profiles_dir:
        cmd += ["--profiles-dir", profiles_dir]
    try:
        p = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return ((parse_dbt_show(p.stdout or "") or {}).get("show") or [])


# Far-future or far-past dates, and the numeric placeholders feeds reach for instead of NULL.
SENTINELS = (-9999, -999, -1, 9999, 99999, -99999, 0.0)


def sentinel_findings(relation: str, columns: list[str], profile: dict) -> list[tuple]:
    """(column, value, why) for numeric extremes that are placeholders, not measurements."""
    out = []
    for i, c in enumerate(columns):
        lo, hi = profile.get(f"min_{i}"), profile.get(f"max_{i}")
        for v in (lo, hi):
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv in SENTINELS and fv not in (0.0,):
                why = ("a placeholder a feed writes instead of NULL; it must be NULLed, "
                       "never clamped")
                out.append((c, fv, why))
    return out


def emit(targets_: list[Target], dialect: str = "duckdb") -> str:
    """The offline path, for anyone who will not let a subprocess touch their warehouse."""
    blocks = []
    for t in targets_:
        blocks.append(f"-- assay probe :: {t.relation}\n-- columns: {', '.join(t.columns)}\n"
                      f"{build_sql(t, dialect)};")
    return "\n\n".join(blocks)
