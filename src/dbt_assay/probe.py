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

import hashlib
import json
import re
import subprocess
import time
from contextlib import contextmanager
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
    -- *** A SAMPLED COUNT AND AN EXACT ONE ARE DIFFERENT FACTS UNDER ONE NAME. ***
    -- Without this, `--sample` would write a row indistinguishable from an exact observation,
    -- and every reader downstream -- grain propagation, the drift checks, the page -- would
    -- treat 1% of a table as the whole of it. `sample_pct` is 0 for an exact count, which is
    -- the honest reading: nothing was sampled.
    sampled      boolean,
    sample_pct   double,
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
-- *** WHAT assay SPENT ON THE WAREHOUSE, MIRRORING WHAT `model_calls` RECORDS ABOUT THINKING. ***
-- DuckDB is free. BigQuery bills the bytes of the columns a statement touches and Snowflake bills
-- the warehouse being awake, and assay is about to be shown to people who run both. The first
-- question a BigQuery user asks is what a sweep will cost, and without this table there is no
-- answer to give -- not even afterwards.
--
-- Every row here is written by `_record`, which only two functions call: `run_via_dbt` and
-- `run_sql` are the ONLY places a statement reaches a warehouse (`probe.py` docstring: assay
-- never holds a credential, so every read goes out through the project's own dbt). That is what
-- makes this ledger complete by construction rather than by everybody remembering to log.
--
-- NEVER_PRUNED: it is the record of what was spent.
create table if not exists warehouse_calls (
    -- *** THE STATEMENT'S IDENTITY, NOT THE CALL'S, AND THAT IS THE DIFFERENCE FROM `model_calls`. ***
    -- A content hash of the SQL, so the same statement issued on Monday and on Friday carries one
    -- id and a rerun is identifiable. `model_calls.call_id` is unique per call and is its primary
    -- key; this one repeats by design, which is why this table has NO key at all. A key would
    -- mean `insert or replace`, and two identical statements in one pass would collapse into one
    -- row -- money spent, silently unrecorded. An append-only ledger cannot lose a row that way.
    call_id        varchar,
    -- '' when the command minted no run. `assay probe` opens a store and is not part of a check,
    -- and stamping it with the newest run_id would credit a cost to a run that did not cause it.
    run_id         varchar,
    caller         varchar,     -- 'assay.probe.keys', 'assay.practices.collect', ...
    relation       varchar,     -- '' when the statement spans several, as a union-all batch does
    statement_kind varchar,     -- key_scan | profile | sample | count | metadata
    dialect        varchar,
    columns_touched integer,
    column_names   varchar,     -- json array, so a cost can be attributed to a column later
    rows_returned  bigint,
    rows_scanned   bigint,      -- the relation's KNOWN row count, never a measurement of this run
    bytes_estimated bigint,
    -- *** NULL UNLESS AN ADAPTER GAVE A REAL NUMBER. *** Never the estimate copied across.
    -- A column mixing measured and guessed numbers is the varchar-declared-INTEGER-stored defect
    -- again: two different facts sharing one name, and no reader able to tell which they have.
    bytes_measured bigint,
    estimate_basis varchar,     -- declared_types | adapter | unknown. NOT optional, same reason.
    sampled        boolean,
    sample_rows    bigint,
    wall_ms        integer,
    usd_estimated  double,      -- NULL when nothing configured can justify a number
    rate_card      varchar,     -- WHICH rate produced usd_estimated, stored, never derived later
    -- *** A FAILED STATEMENT AND AN EMPTY ONE ARE NOT THE SAME ROW. ***
    -- `dbt show` returns both as no output, which is how a monitoring cadence query that FAILED
    -- was read as "0 writes recorded" against a table holding 27 days of them.
    failed         boolean,
    detail         varchar,     -- why it failed, when it did
    called_at      timestamp
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
    # *** A SAMPLED RESULT MUST NEVER SATISFY THE CLAIM AN EXACT ONE DOES. ***
    # `count(distinct k) = count(*)` over 1% of a table says nothing about the other 99%: the
    # duplicates are exactly what a sample is likely to miss. Set at the point of CREATION, not
    # at the point of display -- a weaker record that looks like a stronger one because a field
    # did not get set is the defect that stamped 70 verdicts `(unversioned)`.
    sampled: bool = False
    sample_pct: float = 0.0

    @property
    def is_unique_key(self) -> bool:
        """Unique in today's data, counted over ALL of it.

        A sampled observation is never a unique key however the counts came out, because the
        thing it would be asserting is about rows it did not read.
        """
        return self.status == "unique" and not self.sampled


@dataclass
class Result:
    """What one statement did. *** `[]` USED TO MEAN BOTH "IT FAILED" AND "NO ROWS". ***

    `dbt show` reports a broken connection, a missing relation, a syntax error and an empty table
    identically: nothing on stdout. Every caller here read that as an empty result, so a cadence
    query that FAILED was reported as "only 0 writes recorded" against a table holding 27 days of
    them, and a partial dbt-project-evaluator build read as a clean project.

    `elementary.read` already fixed the top-level case with a reachability probe. That probe passes
    and then an individual statement fails silently, which is the same defect one layer down, and
    it exists at every call site rather than in one of them.
    """
    rows: list[dict] = field(default_factory=list)
    failed: bool = False
    why: str = ""
    wall_ms: int = 0
    # What the ADAPTER said, when assay asked for it. `bytes_measured` is filled from here and
    # from nowhere else; `engine_ms` is dbt's own execution time, which excludes its startup and
    # is therefore a truer number than the wall clock around the subprocess.
    adapter: dict = field(default_factory=dict)
    engine_ms: int | None = None

    def __bool__(self):
        """*** DELIBERATELY UNUSABLE, BECAUSE `if not got:` IS THE BUG. ***

        An object is truthy, so a call site left as `if not runner(...)` would silently stop
        firing and the failure path would quietly disappear. Raising here turns every one of them
        into a test failure instead of into a wrong answer delivered with confidence.
        """
        raise TypeError(
            "a probe Result is not a truth value: a failed statement and an empty one are "
            "different facts. Read `.rows` for the data and `.failed` for whether the warehouse "
            "answered at all.")


# *** THE STORE IS AMBIENT, THE CALLER AND THE KIND ARE NOT. ***
# A command installs this once; the two doors below then write a ledger row per statement without
# `practices`, `rows` and `elementary` -- which are handed `probe_mod` and have no store, no config
# and no run -- growing a store argument apiece. What must NOT be ambient is who issued the
# statement and what kind it is: inferring either from the call stack or from the SQL would put a
# heuristic inside a recorded fact, and a heuristic is allowed in targeting and never in a verdict.
_RECORDING: _Ledger | None = None


@dataclass
class _Ledger:
    store: object
    run_id: str = ""
    dialect: str = "duckdb"
    # {relation_lower: {column_lower: declared type}}, from `cost.declared_types`. Empty means no
    # bytes are estimated at all, which is the honest outcome for a project with no catalog.
    types: dict = field(default_factory=dict)
    rate: object = None
    # *** ASKING THE ADAPTER COSTS A DEBUG-LEVEL LOG, SO IT IS ASKED FOR. ***
    # `cost.measure_bytes` in audit.yml. Off by default: the log is slow and enormous, and the
    # estimate needs no warehouse at all.
    measure: bool = False
    written: int = 0
    unrecorded: int = 0            # ledger writes that themselves failed, reported, never silent
    _rows: dict | None = None

    def row_count(self, relation: str) -> int | None:
        """The relation's last observed row count, or None. Never a measurement of this run.

        Read once per command from `observed_keys`: the LATEST observation of each column, then
        the largest count among them. A batch shares a timestamp so the columns agree, and taking
        a max rather than whichever row came back first keeps two runs over one store identical.
        """
        if self._rows is None:
            self._rows = {}
            try:
                self.store.con.execute(DDL)
                for rel, n in self.store.con.execute(
                        """select relation, max(row_count) from
                             (select relation, column_name, row_count,
                                     row_number() over (partition by relation, column_name
                                                        order by observed_at desc) rn
                              from observed_keys) where rn = 1 group by relation""").fetchall():
                    if rel is not None and n is not None:
                        self._rows[str(rel).lower()] = int(n)
            except Exception:                                    # noqa: BLE001
                self._rows = {}
        return self._rows.get((relation or "").lower())


@contextmanager
def recording(store, run_id: str = "", dialect: str = "duckdb",
              types: dict | None = None, rate=None):
    """Record every warehouse statement issued inside this block to `warehouse_calls`.

    Nesting restores the outer ledger rather than clearing it, so a command that opens one around
    a sub-step does not silently stop recording the rest of itself.
    """
    global _RECORDING
    previous = _RECORDING
    _RECORDING = _Ledger(store=store, run_id=run_id or "", dialect=dialect,
                         types=types or {}, rate=rate)
    try:
        yield _RECORDING
    finally:
        _RECORDING = previous


def attach(store) -> None:
    """Start recording, for the lifetime of this store. Called by `Store.__init__`.

    *** COMPLETE BY CONSTRUCTION, NOT BY EVERY COMMAND REMEMBERING TO OPT IN. ***
    Eleven commands can reach a warehouse and more will exist. Wrapping each one is a list that
    goes stale the first time somebody adds the twelfth, and the symptom would be money quietly
    missing from the ledger rather than an error. A statement can only be issued by a process that
    opened a store, so the store is where recording begins.

    What it cannot know yet is the dialect, the column types or the rate: those come from the
    manifest and `audit.yml`, which are loaded later. `enrich` fills them in, and until it does a
    row still records the statement, the caller, the timing and the outcome.
    """
    global _RECORDING
    _RECORDING = _Ledger(store=store)


def detach(store) -> None:
    """Stop recording, if the live ledger is this store's. Called by `Store.close`."""
    global _RECORDING
    if _RECORDING is not None and _RECORDING.store is store:
        _RECORDING = None


def enrich(dialect: str | None = None, types: dict | None = None, rate=None,
           run_id: str | None = None, measure: bool | None = None) -> None:
    """Give the live ledger what the manifest and the config know. A no-op with no ledger."""
    led = _RECORDING
    if led is None:
        return
    if dialect:
        led.dialect = dialect
    if types:
        led.types = types
    if rate is not None:
        led.rate = rate
    if run_id is not None:
        led.run_id = run_id
    if measure is not None:
        led.measure = bool(measure)


def measuring() -> bool:
    """Whether to ask dbt for its adapter's own numbers on the next statement."""
    led = _RECORDING
    return bool(led is not None and led.measure)


def ledger():
    """The live ledger, or None. For a command that wants to report what it recorded."""
    return _RECORDING


def _record(sql: str, res: Result, *, caller: str, kind: str, relation: str = "",
            columns: list[str] | None = None, sampled: bool = False,
            sample_rows: int | None = None) -> None:
    """One ledger row. A failure to record is counted and never raised.

    *** THE LEDGER MUST NOT BE ABLE TO BREAK THE THING IT IS MEASURING. ***
    A locked store or an older schema would otherwise turn cost accounting into an outage of
    `assay check`. It is counted on the ledger instead, so `unrecorded > 0` is visible rather than
    being a quiet gap in the money.
    """
    led = _RECORDING
    if led is None:
        return
    try:
        from . import cost as cost_mod
        rate = led.rate if led.rate is not None else cost_mod.RateCard.from_config({}, led.dialect)
        scanned = led.row_count(relation) if relation else None
        cols = [str(c).lower() for c in (columns or [])]
        if cols and relation:
            est, basis = cost_mod.estimate(cols, led.types.get(relation.lower(), {}),
                                           scanned, rate)
        else:
            # A statement whose column list assay did not build -- `select *`, a union-all batch
            # over many relations -- cannot be sized from the schema, and a number built from the
            # columns it happened to recognise would understate the scan with nothing saying so.
            est, basis = None, "unknown"
        # *** A NUMBER THE WAREHOUSE RETURNED, OR NOTHING. NEVER THE ESTIMATE COPIED ACROSS. ***
        # When the adapter gave one, the basis says `adapter` and the estimate stays in its own
        # column: two figures under two names, so a reader can always tell which they have.
        measured = adapter_bytes(res.adapter)
        if measured is not None:
            basis = "adapter"
        # dbt's own execution time excludes its startup, so it prices a Snowflake second better
        # than the wall clock around the subprocess does.
        took = res.engine_ms if res.engine_ms is not None else res.wall_ms
        led.store.con.execute(DDL)
        led.store.con.execute(
            """insert into warehouse_calls
               (call_id, run_id, caller, relation, statement_kind, dialect, columns_touched,
                column_names, rows_returned, rows_scanned, bytes_estimated, bytes_measured,
                estimate_basis, sampled, sample_rows, wall_ms, usd_estimated, rate_card,
                failed, detail, called_at)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [hashlib.sha1(sql.encode("utf-8")).hexdigest()[:16],
             led.run_id, caller, relation, kind, rate.engine,
             len(cols) or None, json.dumps(cols) if cols else None,
             len(res.rows), scanned, est, measured, basis, bool(sampled), sample_rows,
             res.wall_ms,
             # Priced on the measured bytes when there are any: that is the invoice.
             rate.price(measured if measured is not None else est, took), rate.name,
             bool(res.failed), (res.why or "")[:300],
             datetime.now(timezone.utc)])
        led.written += 1
    except Exception:                                            # noqa: BLE001
        led.unrecorded += 1


def _execute(sql: str, project_dir: str, profiles_dir: str | None = None,
             dbt_bin: str = "dbt", limit: int = 50, timeout: int = 300,
             measure: bool = False) -> Result:
    """`dbt show --inline`, timed, with failure separated from emptiness.

    `dbt_bin` may carry arguments ("uv run dbt", "poetry run dbt", a venv path), because plenty of
    projects have no bare `dbt` on PATH and failing on that would be a pointless wall.

    `measure` asks dbt for its adapter's own numbers, which needs JSON logging at debug level --
    slow, and enormous, so it is off unless `cost.measure_bytes` is set.
    """
    cmd = [*dbt_bin.split(), "show", "--inline", sql, "--output", "json", "--limit", str(limit)]
    if profiles_dir:
        cmd += ["--profiles-dir", profiles_dir]
    if measure:
        cmd += ["--log-format", "json", "--log-level", "debug"]
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        p = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Result(failed=True, why=str(e)[:300], wall_ms=elapsed())
    ms = elapsed()
    if measure:
        # With JSON logging the result object is not on its own line: it is the `preview` field
        # of the ShowNode event, so it needs the other parser.
        rows, resp, secs = parse_dbt_json_logs(p.stdout or "")
        if p.returncode != 0:
            return Result(failed=True, wall_ms=ms,
                          why=(p.stderr or p.stdout or "no output")[-300:].strip())
        return Result(rows=rows, wall_ms=ms, adapter=resp,
                      engine_ms=None if secs is None else int(secs * 1000))
    data = parse_dbt_show(p.stdout or "")
    if p.returncode != 0 or data is None:
        return Result(failed=True, wall_ms=ms,
                      why=(p.stderr or p.stdout or "no output")[-300:].strip())
    # dbt answered. An empty `show` is now an EMPTY TABLE and says so, which is the whole point.
    return Result(rows=list(data.get("show") or []), wall_ms=ms)


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


# *** SAMPLING IS DIALECT-SPECIFIC AND A WRONG CLAUSE READS AS AN EMPTY TABLE. ***
# `dbt show` reports a syntax error the same way it reports no rows, so a sample clause that the
# warehouse does not understand would come back looking like a relation with nothing in it. Each
# engine's own spelling, and an engine assay has no spelling for is not sampled at all -- exact
# is the default and falling back to it is never wrong, only slower.
_SAMPLE_CLAUSE = {
    "duckdb": "using sample {pct}%",
    "bigquery": "tablesample system ({pct} percent)",
    "snowflake": "sample ({pct})",
    "databricks": "tablesample ({pct} percent)",
    "spark": "tablesample ({pct} percent)",
}


def sample_clause(dialect: str, pct: float) -> str:
    """The engine's own sampling syntax, or `''` where assay does not know it."""
    tpl = _SAMPLE_CLAUSE.get((dialect or "").lower())
    if not tpl or not pct or pct <= 0 or pct >= 100:
        return ""
    # Trailing zeros off: `10%` rather than `10.0%`, because two of these dialects parse the
    # percentage as an integer literal.
    text = f"{pct:.4f}".rstrip("0").rstrip(".")
    return tpl.format(pct=text)


def build_sql(target: Target, dialect: str = "duckdb", sample_pct: float = 0.0) -> str:
    """One statement, one scan, three numbers per candidate column.

    *** EXACT IS THE DEFAULT AND SAMPLING IS AN ESCAPE HATCH. ***
    `count(distinct k)` settles a grain exactly, once, and caches forever, and that exactness is
    the whole reason this exists. But a uniqueness check on a billion-row BigQuery table is a
    real bill, so `--sample` exists for people with big warehouses -- and a sampled result is
    evidence, never a settled fact. Every Observation it produces carries `sampled`, set at the
    point of creation rather than at the point of display, because the alternative is 1.2 again:
    a weaker record that looks like a stronger one because a field did not get set.
    """
    parts = ["count(*) as row_count"]
    for i, c in enumerate(target.columns):
        col = sqlglot.parse_one(c, dialect=dialect).sql(dialect=dialect)
        parts.append(f"count({col}) as nn_{i}")
        parts.append(f"count(distinct {col}) as dc_{i}")
    rel = exp.to_table(target.relation).sql(dialect=dialect)
    clause = sample_clause(dialect, sample_pct)
    return f"select {', '.join(parts)} from {rel}" + (f" {clause}" if clause else "")


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


# *** WHETHER dbt CARRIES THE ADAPTER'S OWN NUMBERS: VERIFIED, NOT ASSUMED. ***
# Run against dbt-core 1.11 with dbt-duckdb:
#
#   dbt show --inline "select 1 as n" --output json --log-format json --log-level debug
#
# emits a `Q025 NodeFinished` event carrying `run_result.adapter_response` and
# `run_result.execution_time`. On DuckDB the response is `{_message, code, query_id,
# rows_affected}` -- no bytes, because DuckDB does not bill on bytes and has none to report. The
# BigQuery adapter puts `bytes_processed` and `bytes_billed` on the same object, and Snowflake
# puts `query_id` and `rows_affected`.
#
# So a real engine number IS reachable without a credential, through the project's own dbt. It
# costs a debug-level log, which is slow and enormous, so it is opt-in: `cost.measure_bytes` in
# `audit.yml`. What it buys is `bytes_measured` filled from a number the warehouse returned,
# beside `estimate_basis = 'adapter'` -- and the estimate column left alone, so the two never mix.
_BYTES_KEYS = ("bytes_billed", "bytes_processed", "total_bytes_billed", "total_bytes_processed")


def parse_dbt_json_logs(stdout: str) -> tuple[list[dict], dict, float | None]:
    """(rows, adapter_response, execution_seconds) from `--log-format json` output.

    With JSON logging the result object does not arrive on its own line: it is the `preview`
    field of the `Q041 ShowNode` event. A reader looking for a bare `{` finds a log line instead,
    which is why this is a second parser rather than a flag on the first one.
    """
    rows: list[dict] = []
    resp: dict = {}
    secs: float | None = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = (ev.get("info") or {}).get("code")
        data = ev.get("data") or {}
        if code == "Q041":
            try:
                got = json.loads(data.get("preview") or "[]")
                rows = list(got) if isinstance(got, list) else []
            except (json.JSONDecodeError, TypeError):
                rows = []
        elif code == "Q025":
            rr = data.get("run_result") or {}
            resp = rr.get("adapter_response") or {}
            try:
                secs = float(rr.get("execution_time"))
            except (TypeError, ValueError):
                secs = None
    return rows, resp, secs


def adapter_bytes(resp: dict) -> int | None:
    """Bytes the ADAPTER reported, or None. Never a fallback to anything estimated.

    `bytes_billed` first, because that is the number on the invoice: BigQuery bills a 10MB
    minimum per table, so a scan of 2KB is processed as 2KB and billed as 10MB, and only one of
    those is what it cost.
    """
    for k in _BYTES_KEYS:
        v = (resp or {}).get(k)
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            return n
    return None


def interpret(target: Target, row: dict, sample_pct: float = 0.0) -> list[Observation]:
    n = row.get("row_count")
    sampled = bool(sample_pct)
    over = f" over a {sample_pct:g}% sample" if sampled else ""
    out = []
    for i, c in enumerate(target.columns):
        nn, dc = row.get(f"nn_{i}"), row.get(f"dc_{i}")
        o = Observation(target.relation, c, n, nn, dc,
                        sampled=sampled, sample_pct=float(sample_pct or 0.0))
        if nn is None or dc is None or n is None:
            o.status, o.detail = "unknown", "the query did not return counts for this column"
        elif nn < n:
            # NULLs first: `count(distinct)` ignores them, so a mostly-null column can look unique.
            o.status = "has_nulls"
            o.detail = (f"{n - nn:,} of {n:,} rows are NULL{over}, so this cannot be a key on "
                        f"its own")
        elif dc < nn:
            o.status = "has_duplicates"
            o.detail = f"{nn:,} rows{over}, {dc:,} distinct: {nn - dc:,} duplicates"
        elif sampled:
            # *** THE ONE CASE A SAMPLE CANNOT SETTLE, SO IT SAYS SO IN THE STATUS ITSELF. ***
            # Duplicates are precisely what a sample misses. A sampled pass is a reason to run
            # the exact count, not a substitute for having run it.
            o.status = "unique"
            o.detail = (f"{n:,} sampled rows, all non-null and distinct{over}. NOT settled: "
                        f"duplicates are what a sample misses. Re-run without --sample to "
                        f"count it exactly.")
        else:
            o.status = "unique"
            o.detail = f"{n:,} rows, all non-null and distinct, observed today"
        out.append(o)
    return out


def run_via_dbt(target: Target, project_dir: str, profiles_dir: str | None = None,
                dialect: str = "duckdb", timeout: int = 300,
                dbt_bin: str = "dbt",
                caller: str = "assay.probe.keys",
                sample_pct: float = 0.0) -> tuple[list[Observation], str]:
    """Returns (observations, raw_sql). A failure yields `unknown` rows, never `not unique`.

    One of the two places a statement reaches a warehouse, so one of the two places that records
    what it cost.
    """
    # An engine assay has no sampling syntax for is counted EXACTLY rather than with a clause it
    # guessed at, and the observations say so.
    clause = sample_clause(dialect, sample_pct)
    used_pct = sample_pct if clause else 0.0
    sql = build_sql(target, dialect, sample_pct)
    res = _execute(sql, project_dir, profiles_dir, dbt_bin, limit=1, timeout=timeout,
                   measure=measuring())
    _record(sql, res, caller=caller, kind="key_scan", relation=target.relation,
            columns=target.columns, sampled=bool(used_pct),
            sample_rows=None)
    if res.failed or not res.rows:
        # An aggregate over any table returns exactly one row, so no rows here is a failure and
        # not an empty table -- but the detail now says WHICH, instead of both reading the same.
        why = res.why or "the query returned no row"
        return ([Observation(target.relation, c, status="unknown", detail=why)
                 for c in target.columns], sql)
    return interpret(target, res.rows[0], used_pct), sql


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
    # Same for the sampling pair. An existing row has neither, and NULL reads as `false` and
    # `0.0` through the coalesce in `read` -- which is correct: it was counted exactly.
    if "sampled" not in have:
        store.con.execute("alter table observed_keys add column sampled boolean")
    if "sample_pct" not in have:
        store.con.execute("alter table observed_keys add column sample_pct double")
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
            via varchar, minimality varchar, sampled boolean, sample_pct double,
            primary key (relation, column_name, observed_at))""")
    store.con.execute("""
        insert into _ok_hist
        select relation, column_name, row_count, non_null, distinct_ct, status, detail,
               -- A row written before history has a timestamp; one written before `observed_at`
               -- existed at all would be NULL, and NULL cannot sit in a primary key. Such a row
               -- is the oldest thing here by definition, so it is dated as such rather than lost.
               coalesce(observed_at, timestamp '1970-01-01 00:00:00'),
               via, coalesce(minimality, ''),
               -- Anything written before sampling existed was counted exactly, which is what
               -- these two values say.
               coalesce(sampled, false), coalesce(sample_pct, 0.0)
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
            observed_at, via, minimality, sampled, sample_pct)
           values (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [[o.relation, o.column, o.row_count, o.non_null, o.distinct_ct,
          o.status, o.detail, now, via, o.minimality or "",
          bool(o.sampled), float(o.sample_pct or 0.0)] for o in observations])


def read(store) -> dict[str, dict[str, Observation]]:
    """{relation: {column: Observation}}, the LATEST observation of each, for grain propagation.

    *** THE LATEST, NOW THAT THERE IS MORE THAN ONE. ***
    Before history existed this was every row, because there was only ever one per column. Reading
    all of them now would hand a caller several observations of one column and no way to tell
    which is live -- the defect `traversal` had when it returned twelve verdicts for four hops.
    """
    store.con.execute(DDL)
    out: dict[str, dict[str, Observation]] = {}
    for rel, col, n, nn, dc, status, detail, at, _via, mini, smp, pct in store.con.execute(
            """select relation, column_name, row_count, non_null, distinct_ct, status, detail,
                      observed_at, via, minimality,
                      coalesce(sampled, false), coalesce(sample_pct, 0.0)
               from (select *, row_number() over (partition by relation, column_name
                                                  order by observed_at desc) rn
                     from observed_keys) where rn = 1""").fetchall():
        out.setdefault(rel.lower(), {})[col] = Observation(
            rel, col, n, nn, dc, status, detail, mini or "", at,
            sampled=bool(smp), sample_pct=float(pct or 0.0))
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
                   observed_at, minimality, coalesce(sampled, false), coalesce(sample_pct, 0.0)
            from observed_keys {where} order by relation, column_name, observed_at""",
        args).fetchall()
    return [Observation(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8] or "", r[7],
                        sampled=bool(r[9]), sample_pct=float(r[10] or 0.0)) for r in rows]


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
            dbt_bin: str = "dbt", limit: int = 50, timeout: int = 300, *,
            caller: str = "assay.unattributed", kind: str = "metadata",
            relation: str = "", columns: list[str] | None = None,
            sampled: bool = False, sample_rows: int | None = None) -> Result:
    """Any read-only statement, through the project's own dbt. assay never holds a credential.

    *** RETURNS A `Result`, NOT A LIST, AND THAT IS THE POINT. ***
    It used to return `[]` for a failed statement and `[]` for an empty table. Callers cannot be
    trusted to remember the difference -- none of the thirteen did -- so the return type carries
    it and refuses to be used as a truth value.

    `caller` and `kind` are written into the ledger and have no defaults worth relying on: the
    fallbacks exist so an outside caller records SOMETHING rather than nothing, and every call
    site inside assay passes both.
    """
    res = _execute(sql, project_dir, profiles_dir, dbt_bin, limit=limit, timeout=timeout,
                   measure=measuring())
    _record(sql, res, caller=caller, kind=kind, relation=relation, columns=columns,
            sampled=sampled, sample_rows=sample_rows)
    return res


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


def dry_run(targets_: list[Target], store=None, dialect: str = "duckdb",
            rate=None, types: dict | None = None) -> dict:
    """Every statement a probe would issue, and what it would cost. Executes nothing.

    *** THE ARGUMENT FOR assay TO SOMEBODY WHO PAYS PER QUERY. ***
    A BigQuery user's first question is what this will cost them, and until now assay could not
    answer it at all -- not before the run and not after. It needs no credential and no warehouse,
    which also makes it testable.

    `unpriced` is the count it could not estimate and it is reported rather than dropped: a total
    over the statements assay happened to understand, printed as though it were the total, is the
    partial-estimate failure this module refuses everywhere else.
    """
    from . import cost as cost_mod
    rate = rate or cost_mod.RateCard.from_config({}, dialect)
    types = types or {}
    led = _Ledger(store=store, dialect=dialect) if store is not None else None
    out: list[dict] = []
    total_bytes, total_usd, unpriced = 0, 0.0, 0
    for t in targets_:
        rows = led.row_count(t.relation) if led is not None else None
        est, basis = cost_mod.estimate([c.lower() for c in t.columns],
                                       types.get(t.relation.lower(), {}), rows, rate)
        usd = rate.price(est, None)
        if est is None:
            unpriced += 1
        else:
            total_bytes += est
            total_usd += usd or 0.0
        out.append({"relation": t.relation, "why": t.why, "sql": build_sql(t, dialect),
                    "columns": len(t.columns), "column_names": list(t.columns),
                    "rows": rows, "bytes": est, "usd": usd, "basis": basis})
    return {"statements": out, "bytes": total_bytes if total_bytes else None,
            "usd": total_usd if total_bytes else None, "unpriced": unpriced,
            "rate_card": rate.name, "engine": rate.engine}


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
    # *** A SAMPLED OBSERVATION CANNOT ENTER A DRIFT COMPARISON. ***
    # `key_stopped_holding` says a column WAS unique and is not, which is the finding that
    # matters most here. Comparing last week's exact count against this week's 1% sample would
    # manufacture that finding out of the sampling, and comparing the other way would manufacture
    # `key_started_holding`. A sample is evidence about today, never a point in a series.
    rows = store.con.execute("""
        select relation, column_name, status, minimality, row_count, distinct_ct, non_null,
               observed_at,
               row_number() over (partition by relation, column_name
                                  order by observed_at desc) as rn
        from observed_keys where not coalesce(sampled, false)
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


def order_by_staleness(targets_: list, store, project=None) -> list:
    """Least-recently-observed first, so -n walks the project instead of re-probing the front.

    *** WITHOUT THIS, `-n 8` PROBES THE SAME EIGHT FOREVER. ***
    The order was whatever the manifest yielded, so a bounded probe on a 277-relation project read
    the first eight on every run and the other 269 were never seen. Coverage could not grow, and
    the drift checks -- which need TWO observations of a relation before they can say anything --
    could never reach a second one.

    *** THE TIE-BREAK IS NOT OPTIONAL. ***
    A probe writes one timestamp for the whole batch, so every relation observed in the same pass
    sorts EQUAL. Ties are the normal case here, not the edge case. Without a total order two runs
    against an unchanged store pick different subsets, which is `arbitrary_pick` -- the check this
    tool runs against other people's SQL -- and it is the same defect already fixed twice here:
    the `count(*) desc` run selection, and the page writing different bytes on identical input.

    A relation that FAILED to count still advances, because `observe` records `unknown` rather
    than nothing. If a failure wrote no row its `max(observed_at)` would stay NULL, it would sort
    first forever, and one unreadable relation would starve the whole cycle -- silently, with the
    symptom "probe seems to work but coverage never grows".

    *** AND AMONG THE NEVER-OBSERVED, REACH DECIDES, NOT THE ALPHABET. ***
    Reported from the field: the next eight on a real warehouse ranged from 0 to 18 marts with no
    relation to position, because the order within a tier was the relation name. At 35 passes to
    first coverage that is the alphabet deciding which models are understood in week one and which
    in week five. Blast radius is already counted off the DAG, exact and free, so it costs nothing
    to front-load the models where a key that stops holding actually costs something. The name
    stays as the last key, because a comparison that can tie is not an order.
    """
    def reach(t) -> int:
        if project is None:
            return 0
        try:
            return int(project.blast_radius(t.uid).get("marts", 0) or 0)
        except Exception:                                        # noqa: BLE001
            return 0

    if store is None:
        return sorted(targets_, key=lambda t: (-reach(t), t.relation))
    store.con.execute(DDL)
    try:
        seen = {str(r[0]).lower(): r[1] for r in store.con.execute(
            "select relation, max(observed_at) from observed_keys group by relation").fetchall()}
    except Exception:                                            # noqa: BLE001
        seen = {}
    # Never-observed first, then oldest, then WIDEST REACH, then the name. Four keys, and the
    # last one is what makes it total -- a batch shares a timestamp and reach ties constantly.
    return sorted(targets_, key=lambda t: (seen.get(t.relation.lower()) is not None,
                                           seen.get(t.relation.lower()) or 0,
                                           -reach(t),
                                           t.relation))
