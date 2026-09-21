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
    -- *** DOES THIS COLUMN ADD IDENTIFYING POWER, OR IS IT CARRIED ALONG. ***
    -- `adds` | `carried` | null when the question was not asked. Counted, not judged: drop the
    -- column from the candidate set, recount the distinct combination, and if the number does not
    -- move the column was determined by the others. That is the whole of minimality and it is two
    -- counts, so a model was being asked a question arithmetic settles exactly.
    minimality   varchar,
    -- *** EVERY OTHER TABLE THAT RECORDS A MEASUREMENT KEEPS ITS SERIES. THIS ONE OVERWROTE. ***
    -- `model_decisions` keys on the version so `effectiveness` and `regress` are possible;
    -- `findings` and `edge_facts` key on `run_id`. `observed_keys` keyed on (relation, column)
    -- with `insert or replace`, so there was exactly one observation per column, ever.
    --
    -- Which means assay could say a key holds TODAY and could never say a key that held last week
    -- has stopped holding -- and that second sentence is the one that matters. A key silently
    -- ceasing to be a key is how a warehouse goes wrong: every count downstream inflates, nothing
    -- errors, and the tests still pass because they were written while it was true.
    primary key (relation, column_name, observed_at)
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
    # "adds" | "carried" | "" when minimality was not counted for this column.
    minimality: str = ""
    observed_at: object = None

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


def migrate(store) -> int:
    """Give an existing `observed_keys` its history back. Returns rows carried over.

    *** duckdb CANNOT ALTER A PRIMARY KEY, SO THIS REBUILDS THE TABLE. ***
    Same shape as `_reshape_adjudications`, and the same rule: every existing row is CARRIED, not
    dropped. A store written before history existed holds one observation per column, and that one
    is real -- it is the first point of the series, and throwing it away would mean the first
    comparison could not happen until two more probes had run.

    Idempotent. It looks at the actual key and does nothing when history is already there.
    """
    store.con.execute(DDL)
    try:
        cols = store.con.execute(
            "select column_name from information_schema.columns "
            "where table_name = 'observed_keys'").fetchall()
    except Exception:                                            # noqa: BLE001
        return 0
    have = {c[0] for c in cols}
    if not have:
        return 0
    # `minimality` may be missing on an old store; add it before anything reads it.
    if "minimality" not in have:
        store.con.execute("alter table observed_keys add column minimality varchar")
    # Is `observed_at` already part of the key? duckdb exposes it through the constraint list.
    try:
        keyed = store.con.execute(
            "select constraint_column_names from duckdb_constraints() "
            "where table_name = 'observed_keys' and constraint_type = 'PRIMARY KEY'").fetchone()
    except Exception:                                            # noqa: BLE001
        keyed = None
    if keyed and "observed_at" in list(keyed[0] or []):
        return 0
    n = store.con.execute("select count(*) from observed_keys").fetchone()[0]
    store.con.execute("""
        create table _ok_hist (
            relation varchar, column_name varchar, row_count bigint, non_null bigint,
            distinct_ct bigint, status varchar, detail varchar, observed_at timestamp,
            via varchar, minimality varchar,
            primary key (relation, column_name, observed_at))""")
    store.con.execute("""
        insert into _ok_hist
        select relation, column_name, row_count, non_null, distinct_ct, status, detail,
               -- A row written before history has a timestamp; one written before `observed_at`
               -- existed at all would be NULL, and NULL cannot sit in a primary key. Such a row
               -- is the oldest thing here by definition, so it is dated as such rather than lost.
               coalesce(observed_at, timestamp '1970-01-01 00:00:00'),
               via, coalesce(minimality, '')
        from observed_keys""")
    store.con.execute("drop table observed_keys")
    store.con.execute("alter table _ok_hist rename to observed_keys")
    return n


def write(store, observations: list[Observation], via: str = "dbt-show") -> None:
    """Append an observation. *** APPEND, NOT REPLACE. ***

    One timestamp for the whole batch, so everything counted in one pass shares an observation and
    the comparison between passes is a comparison between two known moments rather than between
    two rows that happen to sit next to each other.
    """
    store.con.execute(DDL)
    now = datetime.now(timezone.utc)
    # Named columns: a migration appends at the END and a positional insert then writes `via`
    # into whichever column happens to sit there.
    store.con.executemany(
        """insert or replace into observed_keys
           (relation, column_name, row_count, non_null, distinct_ct, status, detail,
            observed_at, via, minimality)
           values (?,?,?,?,?,?,?,?,?,?)""",
        [[o.relation, o.column, o.row_count, o.non_null, o.distinct_ct,
          o.status, o.detail, now, via, o.minimality or ""] for o in observations])


def read(store) -> dict[str, dict[str, Observation]]:
    """{relation: {column: Observation}}, the LATEST observation of each, for grain propagation.

    *** THE LATEST, NOW THAT THERE IS MORE THAN ONE. ***
    Before history existed this was every row, because there was only ever one per column. Reading
    all of them now would hand a caller several observations of one column and no way to tell
    which is live -- the defect `traversal` had when it returned twelve verdicts for four hops.
    """
    store.con.execute(DDL)
    out: dict[str, dict[str, Observation]] = {}
    for rel, col, n, nn, dc, status, detail, at, _via, mini in store.con.execute(
            """select relation, column_name, row_count, non_null, distinct_ct, status, detail,
                      observed_at, via, minimality
               from (select *, row_number() over (partition by relation, column_name
                                                  order by observed_at desc) rn
                     from observed_keys) where rn = 1""").fetchall():
        out.setdefault(rel.lower(), {})[col] = Observation(
            rel, col, n, nn, dc, status, detail, mini or "", at)
    return out


def history(store, relation: str = "", column: str = "") -> list[Observation]:
    """Every observation, oldest first. The series a change is visible in."""
    store.con.execute(DDL)
    where, args = "", []
    if relation:
        where, args = "where lower(relation) = ?", [relation.lower()]
        if column:
            where += " and column_name = ?"
            args.append(column)
    rows = store.con.execute(
        f"""select relation, column_name, row_count, non_null, distinct_ct, status, detail,
                   observed_at, minimality
            from observed_keys {where} order by relation, column_name, observed_at""",
        args).fetchall()
    return [Observation(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8] or "", r[7]) for r in rows]


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


# --------------------------------------------------------------------- what CHANGED, not what is

def changes(store, project=None) -> list:
    """Columns whose observed behavior moved between the last two observations.

    *** "IT HOLDS TODAY" IS THE LESS USEFUL HALF. ***
    A key that silently stops being a key is how a warehouse goes wrong: every count downstream
    inflates, nothing errors, and the tests still pass because they were written while it was
    true. assay could say a key holds now and could not say one had stopped, because the store
    kept exactly one observation per column and overwrote it.

    Two observations is the minimum for a change to exist, so a column with one is absent from
    this rather than reported as stable. An absent comparison is not a clean bill.
    """
    from .checks.structural import Finding
    store.con.execute(DDL)
    rows = store.con.execute("""
        select relation, column_name, status, minimality, row_count, distinct_ct, non_null,
               observed_at,
               row_number() over (partition by relation, column_name
                                  order by observed_at desc) as rn
        from observed_keys
    """).fetchall()
    latest, prior = {}, {}
    for rel, col, status, mini, n, dc, nn, at, rn in rows:
        (latest if rn == 1 else prior if rn == 2 else {}).setdefault(
            (rel, col), (status, mini, n, dc, nn, at))

    out = []
    for key, now in sorted(latest.items()):
        was = prior.get(key)
        if was is None:
            continue                      # one observation. Nothing to compare, and not a pass.
        rel, col = key
        (st_now, mini_now, n_now, dc_now, nn_now, at_now) = now
        (st_was, mini_was, _n, _d, _nn, at_was) = was
        when = f"held at {at_was}, not at {at_now}"

        # *** THE ONE THAT MATTERS. *** A key that was unique and is not.
        if st_was == "unique" and st_now in ("has_duplicates", "has_nulls"):
            out.append(Finding(
                check="key_stopped_holding", subject=f"{rel}.{col}", subject_name=rel,
                file="", base=3,
                summary=f"`{col}` was unique in {rel} and is not any more "
                        f"({dc_now:,} distinct over {n_now:,} rows)",
                detail=("This column was counted unique in an earlier observation and is not in "
                        "the latest. If anything declares it a key, or joins on it expecting one "
                        "row, every count past that join is now inflated and nothing will "
                        "error.\n\n" + when + ".\n\nCounted, not judged. What it cannot tell you "
                        "is WHEN between the two observations it changed, only that it did."),
                evidence={"was": st_was, "now": st_now, "rows": n_now, "distinct": dc_now,
                          "non_null": nn_now, "observed_at": str(at_now),
                          "previously_at": str(at_was)}))

        # The other direction, which is a smaller but real fact: a key you could not declare
        # before, you could now.
        elif st_was in ("has_duplicates", "has_nulls") and st_now == "unique":
            out.append(Finding(
                check="key_started_holding", subject=f"{rel}.{col}", subject_name=rel,
                file="", base=1,
                summary=f"`{col}` is now unique in {rel} and was not before",
                detail=("Not a defect. A uniqueness test on this column would pass today and "
                        "would not have before, so a grain nothing could declare is now "
                        "declarable.\n\n" + when + ".\n\nUnique in today's data is still not a "
                        "constraint. It is a reason to look, not a reason to assert."),
                evidence={"was": st_was, "now": st_now, "rows": n_now, "distinct": dc_now}))

        # *** AND THE MINIMALITY DIRECTION: A COLUMN THAT STARTED CARRYING ITS WEIGHT. ***
        if mini_was == "carried" and mini_now == "adds":
            out.append(Finding(
                check="key_column_started_mattering", subject=f"{rel}.{col}", subject_name=rel,
                file="", base=2,
                summary=f"`{col}` now adds identifying power in {rel} and did not before",
                detail=("Dropping this column from the candidate set used to leave the distinct "
                        "count unchanged, so it was carried along. It does not any more, which "
                        "means the minimal key of this model has GROWN and a uniqueness test "
                        "written without this column is now testing the wrong thing.\n\n"
                        + when + "."),
                evidence={"was": mini_was, "now": mini_now}))
        elif mini_was == "adds" and mini_now == "carried":
            out.append(Finding(
                check="key_column_stopped_mattering", subject=f"{rel}.{col}", subject_name=rel,
                file="", base=1,
                summary=f"`{col}` no longer adds identifying power in {rel}",
                detail=("The other candidates now determine it, so the minimal key has shrunk. "
                        "A test including this column still passes -- a superset of a key is "
                        "unique -- so nothing will fail; the key is simply wider than it needs "
                        "to be.\n\n" + when + "."),
                evidence={"was": mini_was, "now": mini_now}))
    return out
