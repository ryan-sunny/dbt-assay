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
import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp

from . import bulk

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
    -- *** THE SUBPROCESS ROUND TRIP. NEVER A COST BASIS. ***
    -- Measured on the production sweep: 18,000ms of wall clock around a statement the warehouse
    -- ran in 30ms, because 99.7% of it is dbt starting up and parsing a manifest. Snowflake bills
    -- warehouse seconds, so pricing on this is wrong by roughly 600x -- and wrong in the
    -- direction that looks plausible rather than absurd, so nobody catches it.
    wall_ms        integer,
    -- What the ENGINE took, from the adapter response or dbt's own debug log. NULL when nothing
    -- reported one, and a time-priced estimate is then not emitted at all: absent beats 600x.
    exec_ms        integer,
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
    # How many statements shared the dbt invocation this came from. `wall_ms` is that invocation's,
    # so a batched Result's wall clock is the batch's and says so.
    batched: int = 1

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
        # *** THE ENGINE'S OWN TIME, OR NOTHING. ***
        # Falling back to `wall_ms` here is what makes a Snowflake estimate 600x wrong, so there
        # is no fallback: `price` gets None and declines to produce a figure.
        took = res.engine_ms
        led.store.con.execute(DDL)
        led.store.con.execute(
            """insert into warehouse_calls
               (call_id, run_id, caller, relation, statement_kind, dialect, columns_touched,
                column_names, rows_returned, rows_scanned, bytes_estimated, bytes_measured,
                estimate_basis, sampled, sample_rows, wall_ms, exec_ms, usd_estimated,
                rate_card, failed, detail, called_at)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [hashlib.sha1(sql.encode("utf-8")).hexdigest()[:16],
             led.run_id, caller, relation, kind, rate.engine,
             len(cols) or None, json.dumps(cols) if cols else None,
             len(res.rows), scanned, est, measured, basis, bool(sampled), sample_rows,
             res.wall_ms, res.engine_ms,
             # Priced on the measured bytes when there are any: that is the invoice.
             rate.price(measured if measured is not None else est, took), rate.name,
             bool(res.failed), (res.why or "")[:300],
             datetime.now(timezone.utc)])
        led.written += 1
    except Exception:                                            # noqa: BLE001
        led.unrecorded += 1


DBT_OUTPUT = "\ndbt output:\n"


def failure_text(p, keep: int = 600) -> str:
    """What a failed dbt call printed, from BOTH streams.

    *** `stderr or stdout` KEPT THE WRONG ONE. ***
    dbt writes its error to STDOUT ("Encountered an error: ... Could not find profile named
    'sunny_data'"), and `uv run` writes a harmless VIRTUAL_ENV warning to STDERR whenever it is
    started from inside another environment -- which is exactly how assay runs under `uvx` or
    `uv run --project`. A non-empty stderr won, so the one line that said what was wrong was
    thrown away and the warning was printed in its place.
    """
    both = "\n".join(x.strip() for x in (p.stdout or "", p.stderr or "") if x and x.strip())
    rest, _dep = split_warnings(both)
    return (rest or both or "no output")[-keep:].strip()


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STAMP = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d+)?\s")
_DEP_LINE = re.compile(r"^- (\w+): (\d+) occurrence")


def split_warnings(text: str) -> tuple[str, dict]:
    """(dbt's output without its [WARNING] blocks, {deprecation: count}).

    *** THE DEPRECATION SUMMARY PUSHED THE ERROR OUT OF THE MESSAGE. *** (sunny-data, 0.52.4) On
    a full parse dbt prints every deprecation, then a summary, LAST. A failure was reported by
    its tail, so `volume` said "could not reach the warehouse" and quoted only the summary. A
    warning block runs from its `[WARNING]` line to the next timestamped line; dropping them leaves
    the error. The counts come back separately, to be said on their own line.
    """
    kept, deps, in_warning, in_summary = [], {}, False, False
    for line in _ANSI.sub("", text or "").splitlines():
        stamped = bool(_STAMP.match(line))
        if stamped:
            in_warning = "[WARNING]" in line
            in_summary = "DeprecationsSummary" in line
        if in_warning:
            m = _DEP_LINE.match(line.strip()) if in_summary else None
            if m:
                deps[m.group(1)] = deps.get(m.group(1), 0) + int(m.group(2))
            continue
        kept.append(line)
    return "\n".join(kept).strip(), deps


# Said once per process: a command makes many dbt calls and dbt repeats itself on each full parse.
_DEPRECATIONS_SAID: list = []


def say_deprecations(text: str) -> str:
    """Print dbt's deprecation counts to stderr, once. The line, or "" when there were none."""
    _rest, deps = split_warnings(text)
    if not deps or _DEPRECATIONS_SAID:
        return ""
    n = sum(deps.values())
    line = (f"dbt printed {n} deprecation warning{'s' if n != 1 else ''}: "
            + ", ".join(f"{k} ({v})" for k, v in sorted(deps.items(), key=lambda kv: -kv[1]))
            + ". They did not stop assay; `dbt parse --no-partial-parse --show-all-deprecations` "
              "lists each one.")
    _DEPRECATIONS_SAID.append(line)
    import sys
    print(line, file=sys.stderr)
    return line


def profiles_args(profiles_dir: str | None) -> list:
    """`["--profiles-dir", <absolute>]`, or nothing.

    *** EVERY dbt CALL RUNS IN THE PROJECT, SO A RELATIVE PATH WAS READ FROM INSIDE IT. ***
    `--profiles-dir transform`, typed from the directory holding `transform/`, reached dbt as
    `transform` with its working directory already `transform/`, and dbt looked in
    `transform/transform`. Nothing said why. A path a person types is resolved from where they
    typed it, once, here, for every call that hands it to dbt.
    """
    if not profiles_dir:
        return []
    return ["--profiles-dir", os.path.abspath(os.path.expanduser(profiles_dir))]


class WarehouseUnreachable(RuntimeError):
    """dbt could not answer `select 1` here, so nothing this command counts would be real."""


# *** A COMMAND THAT CANNOT REACH THE WAREHOUSE MUST SAY SO, NOT REPORT WHAT IT DID NOT SEE. ***
# `practices` run without `--project-dir`/`--dbt` printed "23 of 23 standard check(s) were NOT
# LOOKED AT" in the shape of a finding about the project, and it was read as one. `backtest
# --compile` failing the same way died with a traceback, which was the right behaviour. So the
# first statement any command sends is preceded, once per (dir, dbt, profiles), by `select 1`; if
# that fails, the command stops with dbt's own words.
_REACHED: dict = {}


def _reach(project_dir: str, profiles_dir: str | None, dbt_bin: str) -> None:
    key = (os.path.abspath(project_dir or "."), profiles_dir or "", dbt_bin)
    if key in _REACHED:
        if _REACHED[key]:
            raise WarehouseUnreachable(_REACHED[key])
        return
    cmd = [*dbt_bin.split(), "show", "--inline", "select 1 as assay_reachable", "--output",
           "json", "--limit", "1", *profiles_args(profiles_dir)]
    if profiles_dir:
        where = profiles_args(profiles_dir)[1]
        if not os.path.isfile(os.path.join(where, "profiles.yml")):
            _REACHED[key] = (f"could not reach the warehouse: --profiles-dir {profiles_dir} is "
                             f"{where}, and there is no profiles.yml there. A relative path is "
                             f"read from the directory assay was run in.")
            raise WarehouseUnreachable(_REACHED[key])
    try:
        p = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, timeout=180,
                           check=False)
        ok = p.returncode == 0 and parse_dbt_show(p.stdout or "") is not None
        say_deprecations((p.stdout or "") + "\n" + (p.stderr or ""))
        why = "" if ok else failure_text(p, 1200)
    except (OSError, subprocess.TimeoutExpired) as e:
        why = str(e)[:600]
    # *** A LOCKED WAREHOUSE IS NOT A WRONG FLAG. *** (sunny-data, 0.52.4) Another job held the
    # DuckDB file, and the advice to pass --project-dir and --dbt was wrong: both were given.
    locked = bool(re.search(r"Could not set lock on file|Conflicting lock is held", why or ""))
    _REACHED[key] = ((f"could not reach the warehouse: it is locked by another process (DuckDB "
                      f"allows one writer; the command that holds it has to finish first)."
                      f"{DBT_OUTPUT}{why}") if locked else
                     (f"could not reach the warehouse: `{dbt_bin} show` in `{project_dir}` "
                      f"failed, so nothing counted here would be real. Pass --project-dir (the "
                      f"dbt project) and --dbt (how dbt runs, e.g. \"uv run dbt\")."
                      f"{DBT_OUTPUT}{why}")
                     if why else "")
    if _REACHED[key]:
        raise WarehouseUnreachable(_REACHED[key])


def _execute(sql: str, project_dir: str, profiles_dir: str | None = None,
             dbt_bin: str = "dbt", limit: int = 50, timeout: int = 300,
             measure: bool = False) -> Result:
    """`dbt show --inline`, timed, with failure separated from emptiness.

    `dbt_bin` may carry arguments ("uv run dbt", "poetry run dbt", a venv path), because plenty of
    projects have no bare `dbt` on PATH and failing on that would be a pointless wall.

    `measure` asks dbt for its adapter's own numbers, which needs JSON logging at debug level --
    slow, and enormous, so it is off unless `cost.measure_bytes` is set.
    """
    _reach(project_dir, profiles_dir, dbt_bin)
    cmd = [*dbt_bin.split(), "show", "--inline", sql, "--output", "json", "--limit", str(limit),
           *profiles_args(profiles_dir)]
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
    say_deprecations((p.stdout or "") + "\n" + (p.stderr or ""))
    if measure:
        # With JSON logging the result object is not on its own line: it is the `preview` field
        # of the ShowNode event, so it needs the other parser.
        rows, resp, secs = parse_dbt_json_logs(p.stdout or "")
        if p.returncode != 0:
            return Result(failed=True, wall_ms=ms,
                          why=failure_text(p))
        return Result(rows=rows, wall_ms=ms, adapter=resp,
                      engine_ms=None if secs is None else int(secs * 1000))
    data = parse_dbt_show(p.stdout or "")
    if p.returncode != 0 or data is None:
        return Result(failed=True, wall_ms=ms,
                      why=failure_text(p))
    # dbt answered. An empty `show` is now an EMPTY TABLE and says so, which is the whole point.
    return Result(rows=list(data.get("show") or []), wall_ms=ms)


def targets(project, digests, schema, declared, known_grain: dict,
            real_columns: dict | None = None) -> list[Target]:
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
        # *** WHAT THE WAREHOUSE SAYS THE RELATION HOLDS, WHERE IT WAS ASKED. ***
        # The manifest may know nothing about a source. `information_schema` always does, and
        # that read scans nothing.
        actual = (real_columns or {}).get(rel.lower())
        if actual:
            have_set = actual
            have = sorted(actual)
        # *** WITH NOTHING TO CHECK AGAINST, `not have_set` LETS EVERY NAME THROUGH. ***
        # Not only the `referenced` fallback below: a join key a child declares is accepted here
        # unchecked too, and a child can join on a name it computed itself. Whenever nothing
        # could say what this relation holds, the candidate list is a guess and says so.
        unverified = not have_set
        cols = sorted(c for c in wanted if not have_set or c in have_set)
        why = f"{len(children)} models read it; grain unknown"
        if not cols and have:
            cols = [c for c in have if ID_LIKE.search(c)]
            why += "; no structural key hint, so id-like column names were counted"
        if not cols and not have and referenced:
            # *** ONLY WHERE NOTHING COULD BE ASKED. ***
            # A source declares no columns anywhere, so absent a catalog the only evidence of
            # what it holds is what its children read out of it -- which is evidence of what the
            # CHILD produces, and 70 of 271 statements on a real warehouse asked for a column
            # that lived only in the child. Where the warehouse was reachable this branch is
            # never taken; where it was not, the guess says it is one.
            cols = sorted(c for c in referenced if ID_LIKE.search(c))
            why += ("; columns GUESSED from what its children reference, because nothing could "
                    "say what this relation actually holds")
        if cols:
            if unverified and "GUESSED" not in why:
                why += ("; columns UNVERIFIED -- nothing could say what this relation holds, so "
                        "a name that exists only in a child may be counted here")
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


# *** 99.7% OF THE SWEEP WAS dbt STARTING UP. ***
# Measured on the production sweep: 5 dbt invocations in 90 seconds, so 18 seconds per statement,
# while dbt's own debug log recorded the warehouse doing the work in 12 to 96 MILLISECONDS. 271
# relations therefore took about eighty minutes to perform roughly twenty seconds of querying.
#
# The two constraints that produce this are both good and both stay. assay never holds a
# credential, so every read goes out through the project's own dbt; and assay does not depend on
# dbt-core, because dbt-core pins adapters and Python versions aggressively and depending on it
# means breaking on every dbt release. Together they rule out `dbtRunner`, which would parse the
# manifest once and invoke many times in one process.
#
# What neither constraint requires is ONE SHELL-OUT PER STATEMENT. These are independent
# aggregates over single relations with no joins, so they union into one statement. 271
# invocations become about 30, and eighty minutes becomes under ten, with the same counts, the
# same credential handling and the same dependencies.
#
# *** THE WIDTH IS BOUNDED, BECAUSE THE FAILURE MODE IS A SHRUG. ***
# A statement too wide hits a parser or planner limit on some engine, and a failed batch today
# looks like `unknown` rows, which reads as "assay could not tell" rather than "assay asked
# badly". So batches are small, and a failed batch is retried in halves the way `rows.py` already
# retries -- the culprit ends up alone and is recorded as unknown, and its neighbours still count.
#
# *** AND ON BIGQUERY THIS BUYS TIME, NEVER MONEY. ***
# A batched statement still scans every column in it. Same split as the cost model: batching helps
# where seconds are billed and does nothing where bytes are.
BATCH_COLUMNS = 340          # total count() expressions in one statement
BATCH_RELATIONS = 12         # relations in one statement, whichever limit binds first


def batch_sql(targets_: list[Target], dialect: str = "duckdb",
              sample_pct: float = 0.0) -> str:
    """One statement counting several relations, each row labelled with the relation it is about.

    `interpret` reads one result row per target, so the label is what lets a batched result be
    split back out. It is a literal rather than a positional guess, because a union does not
    promise an order and reading these back by position is `first_match_pick` with extra steps.
    """
    # *** THE ARMS PAD TO THE WIDEST RELATION IN THIS BATCH, NOT TO THE GLOBAL CAP. ***
    # Every arm of a union must have the same shape. Padding all of them to twelve columns when
    # the batch's widest has three writes nine dead `cast(null)` pairs per arm into a statement
    # that is already the thing being made smaller.
    width = max((len(t.columns) for t in targets_), default=0)
    parts = []
    for t in targets_:
        cols = ["count(*) as row_count"]
        for i in range(width):
            if i < len(t.columns):
                col = sqlglot.parse_one(t.columns[i], dialect=dialect).sql(dialect=dialect)
                cols.append(f"count({col}) as nn_{i}")
                cols.append(f"count(distinct {col}) as dc_{i}")
            else:
                # Every arm of a union must have the same shape, so a relation with fewer
                # candidates pads with NULLs -- which `interpret` already reads as `unknown` for
                # a column that is not there to be asked about.
                cols.append(f"cast(null as bigint) as nn_{i}")
                cols.append(f"cast(null as bigint) as dc_{i}")
        rel = exp.to_table(t.relation).sql(dialect=dialect)
        clause = sample_clause(dialect, sample_pct)
        lit = t.relation.replace("'", "''")
        parts.append(f"select '{lit}' as assay_rel, {', '.join(cols)} from {rel}"
                     + (f" {clause}" if clause else ""))
    return " union all ".join(parts)


def plan_batches(targets_: list[Target]) -> list[list[Target]]:
    """Split targets into statements small enough that no engine refuses one."""
    out: list[list[Target]] = []
    cur: list[Target] = []
    for t in targets_:
        trial = [*cur, t]
        # The real width, which is every arm padded to the widest one in the batch.
        wide = max(len(x.columns) for x in trial)
        if cur and (len(trial) > BATCH_RELATIONS
                    or len(trial) * (1 + wide * 2) > BATCH_COLUMNS):
            out.append(cur)
            cur = []
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def run_batch(targets_: list[Target], project_dir: str, profiles_dir: str | None = None,
              dialect: str = "duckdb", timeout: int = 600, dbt_bin: str = "dbt",
              caller: str = "assay.probe.keys",
              sample_pct: float = 0.0) -> dict:
    """{relation: [Observation]} for a batch, halving on failure until the culprit is alone.

    A relation that still fails alone is recorded as `unknown` with the reason, never as "not
    unique" and never as absent: silence has to be distinguishable from absence, which is the
    rule this whole module is built on.
    """
    if not targets_:
        return {}
    clause = sample_clause(dialect, sample_pct)
    used_pct = sample_pct if clause else 0.0
    sql = batch_sql(targets_, dialect, sample_pct)
    res = _execute(sql, project_dir, profiles_dir, dbt_bin, limit=len(targets_) + 1,
                   timeout=timeout, measure=measuring())
    _record(sql, res, caller=caller, kind="key_scan",
            relation=targets_[0].relation if len(targets_) == 1 else "",
            columns=targets_[0].columns if len(targets_) == 1 else None,
            sampled=bool(used_pct))
    if not res.failed:
        by_rel = {}
        for row in res.rows:
            key = str(row.get("assay_rel") or "")
            if key:
                by_rel[key.lower()] = row
        out = {}
        for t in targets_:
            row = by_rel.get(t.relation.lower())
            out[t.relation] = (interpret(t, row, used_pct) if row is not None else
                               [Observation(t.relation, c, status="unknown",
                                            detail="the batch returned no row for this relation")
                                for c in t.columns])
        return out
    if len(targets_) == 1:
        t = targets_[0]
        # *** ONE BAD COLUMN NAME DISCARDED EVERY GOOD CANDIDATE FOR THAT RELATION. ***
        # Measured on a real sweep: 548 candidate columns produced 242 `unknown` rows, because a
        # statement counting twelve columns fails entirely if one of them does not exist, and the
        # whole relation was then written off. The halving that isolates a bad RELATION works on
        # columns too: keep splitting until the bad name is alone, and everything beside it is
        # counted rather than shrugged at.
        if len(t.columns) > 1:
            mid = len(t.columns) // 2
            out: dict = {t.relation: []}
            for half in (t.columns[:mid], t.columns[mid:]):
                part = Target(relation=t.relation, uid=t.uid, columns=half, why=t.why)
                got = run_batch([part], project_dir, profiles_dir, dialect, timeout, dbt_bin,
                                caller, sample_pct)
                out[t.relation].extend(got.get(t.relation, []))
            return out
        why = res.why or "the query returned no row"
        return {t.relation: [Observation(t.relation, c, status="unknown", detail=why)
                             for c in t.columns]}
    mid = len(targets_) // 2
    out = run_batch(targets_[:mid], project_dir, profiles_dir, dialect, timeout, dbt_bin,
                    caller, sample_pct)
    out.update(run_batch(targets_[mid:], project_dir, profiles_dir, dialect, timeout, dbt_bin,
                         caller, sample_pct))
    return out


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
    bulk.many(store.con,
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


# *** THE BATCHING WENT IN AT ONE CALL SITE, AND EVERY OTHER CALLER KEPT PAYING. ***
# 0.49.0 batched `probe`'s key scans: 271 invocations became 23 and 88 minutes became 10. Every
# other caller got nothing, because the batching lived in `probe.py` and not at the door. Measured
# on `feeds`: 47 seconds per source, of which the warehouse saw 12-96ms. The rest is dbt starting.
#
# `run_many` is the door for a caller holding several independent statements. Their SHAPES differ
# -- a profile, a sample, a count -- so they cannot be unioned as they stand. Each is wrapped so its
# rows come back as one JSON value under a literal label, which a union of any shapes can carry, and
# the labels split the result back out. A label and not a position, because a union does not
# promise an order.
#
# The three rules `run_batch` established hold here too:
#   BOUNDED WIDTH  -- a statement too wide hits an engine limit, and a failed batch is a shrug.
#   ISOLATED FAILURE -- a failed batch is retried in halves, so the one bad statement ends up alone
#                       with ITS error, and its neighbours still answer.
#   LABELLED ROWS  -- every row goes back to the statement that produced it, by name.
#
# *** AND ON BIGQUERY THIS BUYS TIME, NEVER MONEY. ***
# A batched statement scans everything each arm scans. Batching removes dbt startups, which is
# what seconds are billed on; it removes no bytes.
BATCH_STATEMENTS = 12        # statements in one batch
BATCH_ROWS = 60000           # the sum of their row limits, so one batch cannot return a flood
BATCH_CHARS = 200_000        # the statement text, well under any engine's parser limit

# The one expression that turns a whole row into one value, per engine. An engine not here runs
# its statements one at a time, exactly as before, rather than with a guessed-at function.
_ROW_AS_JSON = {
    "duckdb": "to_json(assay_t)",
    "postgres": "row_to_json(assay_t)::text",
    "bigquery": "to_json_string(assay_t)",
    "snowflake": "to_json(object_construct_keep_null(*))",
    "databricks": "to_json(struct(*))",
    "spark": "to_json(struct(*))",
}


@dataclass
class Statement:
    """One statement a caller wants answered, and how the ledger should file it."""
    sql: str
    caller: str = "assay.unattributed"
    kind: str = "metadata"
    limit: int = 50
    relation: str = ""
    columns: list[str] | None = None
    sampled: bool = False
    sample_rows: int | None = None
    timeout: int = 300


def batchable(dialect: str | None) -> bool:
    return (dialect or "").lower() in _ROW_AS_JSON


def wrap_many(stmts: list[Statement], dialect: str) -> str:
    """One statement answering several, each row labelled with the statement it came from."""
    expr = _ROW_AS_JSON[(dialect or "").lower()]
    arms = []
    for i, s in enumerate(stmts):
        inner = s.sql.strip().rstrip(";")
        # `row_number() over ()` inside the arm keeps each statement's own order: its rows are
        # numbered as the arm produced them, and read back in that order.
        arms.append(f"select 's{i}' as assay_stmt, row_number() over () as assay_ord, "
                    f"{expr} as assay_row from (select * from ({inner}) as assay_q "
                    f"limit {int(s.limit)}) as assay_t")
    return " union all ".join(arms)


def unwrap_many(rows: list[dict], n: int) -> list[list[dict]]:
    """Split a wrapped result back into one row list per statement, in each statement's order."""
    out: list[list[tuple]] = [[] for _ in range(n)]
    for r in rows:
        label = str(r.get("assay_stmt") or r.get("ASSAY_STMT") or "")
        if not label.startswith("s"):
            continue
        try:
            i = int(label[1:])
        except ValueError:
            continue
        if not 0 <= i < n:
            continue
        raw = r.get("assay_row", r.get("ASSAY_ROW"))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                continue
        if not isinstance(raw, dict):
            continue
        try:
            ordinal = int(r.get("assay_ord", r.get("ASSAY_ORD")) or 0)
        except (TypeError, ValueError):
            ordinal = 0
        out[i].append((ordinal, raw))
    return [[row for _o, row in sorted(part, key=lambda x: x[0])] for part in out]


def plan_statements(stmts: list[Statement]) -> list[list[int]]:
    """Indexes of `stmts`, grouped into batches no engine should refuse."""
    out: list[list[int]] = []
    cur: list[int] = []
    rows = chars = 0
    for i, s in enumerate(stmts):
        if cur and (len(cur) >= BATCH_STATEMENTS or rows + s.limit > BATCH_ROWS
                    or chars + len(s.sql) > BATCH_CHARS):
            out.append(cur)
            cur, rows, chars = [], 0, 0
        cur.append(i)
        rows += s.limit
        chars += len(s.sql)
    if cur:
        out.append(cur)
    return out


def run_many(stmts: list[Statement], project_dir: str, profiles_dir: str | None = None,
             dbt_bin: str = "dbt", dialect: str = "duckdb") -> list[Result]:
    """One `Result` per statement, in order, from as few dbt invocations as the bounds allow.

    Every `Result` is exactly what `run_sql` would have returned for that statement alone: its
    rows, or `failed` with ITS reason. A statement that fails is isolated by halving and re-run on
    its own, so the reason is the warehouse's reason about that statement, not about a batch.
    """
    if not stmts:
        return []
    if len(stmts) == 1 or not batchable(dialect):
        return [run_sql(s.sql, project_dir, profiles_dir, dbt_bin, limit=s.limit,
                        timeout=s.timeout, caller=s.caller, kind=s.kind, relation=s.relation,
                        columns=s.columns, sampled=s.sampled, sample_rows=s.sample_rows)
                for s in stmts]
    out: list[Result | None] = [None] * len(stmts)
    for idx in plan_statements(stmts):
        _run_group([stmts[i] for i in idx], idx, out, project_dir, profiles_dir, dbt_bin,
                   dialect)
    return [r if r is not None else Result(failed=True, why="not run") for r in out]


def _run_group(group: list[Statement], idx: list[int], out: list, project_dir: str,
               profiles_dir: str | None, dbt_bin: str, dialect: str) -> None:
    if len(group) == 1:
        s = group[0]
        out[idx[0]] = run_sql(s.sql, project_dir, profiles_dir, dbt_bin, limit=s.limit,
                              timeout=s.timeout, caller=s.caller, kind=s.kind,
                              relation=s.relation, columns=s.columns, sampled=s.sampled,
                              sample_rows=s.sample_rows)
        return
    sql = wrap_many(group, dialect)
    timeout = min(sum(s.timeout for s in group), 3 * max(s.timeout for s in group))
    res = _execute(sql, project_dir, profiles_dir, dbt_bin,
                   limit=sum(s.limit for s in group) + 1, timeout=timeout, measure=measuring())
    kinds = {s.kind for s in group}
    # One ledger row per statement SENT, which is the batch. Its caller is the members' shared
    # prefix -- `assay.feeds` for a profile and a sample together -- so `assay cost` still says which
    # command spent it, and its kind says `mixed` rather than naming one member's.
    _record(sql, res, caller=_shared_caller([s.caller for s in group]),
            kind=kinds.pop() if len(kinds) == 1 else "mixed",
            sampled=any(s.sampled for s in group))
    if not res.failed:
        parts = unwrap_many(res.rows, len(group))
        for i, rows in zip(idx, parts):
            out[i] = Result(rows=rows, wall_ms=res.wall_ms, batched=len(group))
        return
    mid = len(group) // 2
    _run_group(group[:mid], idx[:mid], out, project_dir, profiles_dir, dbt_bin, dialect)
    _run_group(group[mid:], idx[mid:], out, project_dir, profiles_dir, dbt_bin, dialect)


def _shared_caller(callers: list[str]) -> str:
    parts = [c.split(".") for c in callers]
    common = []
    for bits in zip(*parts):
        if len(set(bits)) != 1:
            break
        common.append(bits[0])
    return ".".join(common) if len(common) > 1 else "assay.batch"


def many_runner(project_dir: str, profiles_dir: str | None, dbt_bin: str, dialect: str,
                caller: str, kind: str = "metadata"):
    """A `runner(sql, limit)` for modules handed a connection, with `.many([(sql, limit)])`.

    Elementary and practices take a runner rather than a project, so assay never holds a
    credential. `.many` is how they send independent statements together.
    """
    def runner(sql: str, n: int) -> Result:
        return run_sql(sql, project_dir, profiles_dir, dbt_bin, limit=n, caller=caller,
                       kind=kind)

    def many(pairs: list[tuple[str, int]]) -> list[Result]:
        return run_many([Statement(sql, caller=caller, kind=kind, limit=n) for sql, n in pairs],
                        project_dir, profiles_dir, dbt_bin, dialect)
    runner.many = many
    return runner


def ask_many(runner, pairs: list[tuple[str, int]]) -> list[Result]:
    """`runner.many` when the runner has it, else one at a time. Test doubles need not batch."""
    many = getattr(runner, "many", None)
    if many is not None:
        return many(pairs)
    return [runner(sql, n) for sql, n in pairs]


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


# *** THE ESTIMATE COULD NOT PRICE A COLD STORE, WHICH IS WHEN IT IS ASKED FOR. ***
# Measured on production: 271 statements, 38 estimated, 233 not -- 86% unpriced, because a byte
# estimate needs a row count and row counts only existed for relations a previous probe had
# already scanned. So `--dry-run` answered "what will this cost me" with a shrug at exactly the
# moment a new BigQuery user asks it.
#
# Every engine publishes row counts as catalog metadata, and reading them scans nothing:
#   duckdb     duckdb_tables().estimated_size
#   bigquery   INFORMATION_SCHEMA.TABLE_STORAGE.total_rows, and total_logical_bytes, which is a
#              BETTER basis than rows x declared widths because it is what the scan reads
#   snowflake  INFORMATION_SCHEMA.TABLES.ROW_COUNT
#
# One metadata statement, and `estimate_basis` becomes `catalog` rather than `unknown`.
_CATALOG_ROWS = {
    "duckdb": ("select database_name || '.' || schema_name || '.' || table_name as assay_rel, "
               "estimated_size as assay_rows, cast(null as bigint) as assay_bytes "
               "from duckdb_tables()"),
    "snowflake": ("select table_catalog || '.' || table_schema || '.' || table_name "
                  "as assay_rel, row_count as assay_rows, bytes as assay_bytes "
                  "from information_schema.tables where row_count is not null"),
    "bigquery": ("select concat(project_id, '.', table_schema, '.', table_name) as assay_rel, "
                 "total_rows as assay_rows, total_logical_bytes as assay_bytes "
                 "from `region-us`.INFORMATION_SCHEMA.TABLE_STORAGE"),
}


# *** 70 OF 271 STATEMENTS ASKED FOR COLUMNS THAT DO NOT EXIST. ***
# `nhdplus_flowline` has nine columns and assay asked it to count `basin_name`, which lives in a
# CHILD. The hole is in `targets`: where a relation declares no columns anywhere -- which is the
# normal case for a source -- every name a child referenced was accepted unchecked, on the theory
# that what the children read is the only evidence of what the parent holds. It is evidence of
# what the child PRODUCES, and those are different sets.
#
# The warehouse knows. `information_schema.columns` is a metadata read that scans nothing, and it
# is the same trip the row counts already make.
_CATALOG_COLS = {
    "duckdb": ("select table_catalog || '.' || table_schema || '.' || table_name as assay_rel, "
               "column_name as assay_col from information_schema.columns"),
    "snowflake": ("select table_catalog || '.' || table_schema || '.' || table_name as assay_rel, "
                  "column_name as assay_col from information_schema.columns"),
    "postgres": ("select table_catalog || '.' || table_schema || '.' || table_name as assay_rel, "
                 "column_name as assay_col from information_schema.columns"),
}


def catalog_columns(project_dir: str, profiles_dir: str | None = None, dialect: str = "duckdb",
                    dbt_bin: str = "dbt", timeout: int = 180) -> dict:
    """{relation_lower: {column_lower}} from the warehouse. Scans nothing.

    `{}` for an engine assay has no statement for, which leaves the old behaviour in place: a
    guess, said out loud as a guess, rather than a guess dressed as knowledge.
    """
    sql = _CATALOG_COLS.get((dialect or "").lower())
    if not sql:
        return {}
    res = run_sql(sql, project_dir, profiles_dir, dbt_bin, limit=400000, timeout=timeout,
                  caller="assay.probe.catalog", kind="metadata")
    if res.failed:
        return {}
    out: dict = {}
    for row in res.rows:
        rel = str(row.get("assay_rel") or "").strip().lower()
        col = str(row.get("assay_col") or "").strip().lower()
        if rel and col:
            out.setdefault(rel, set()).add(col)
    return out


def catalog_rows(project_dir: str, profiles_dir: str | None = None, dialect: str = "duckdb",
                 dbt_bin: str = "dbt", timeout: int = 120) -> dict:
    """{relation_lower: (rows, bytes_or_None)} from the engine's catalog. Scans nothing.

    An engine assay has no catalog statement for returns `{}`, which reads downstream as "no row
    count", exactly as it did before -- an unpriced statement, said out loud, rather than a
    number built from a guess.
    """
    sql = _CATALOG_ROWS.get((dialect or "").lower())
    if not sql:
        return {}
    res = run_sql(sql, project_dir, profiles_dir, dbt_bin, limit=20000, timeout=timeout,
                  caller="assay.probe.catalog", kind="metadata")
    if res.failed:
        return {}
    out: dict = {}
    for row in res.rows:
        rel = str(row.get("assay_rel") or "").strip()
        if not rel:
            continue
        try:
            n = int(row.get("assay_rows"))
        except (TypeError, ValueError):
            continue
        try:
            by = int(row.get("assay_bytes"))
        except (TypeError, ValueError):
            by = None
        out[rel.lower()] = (n, by)
    return out


def dry_run(targets_: list[Target], store=None, dialect: str = "duckdb",
            rate=None, types: dict | None = None, catalog: dict | None = None) -> dict:
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
    catalog = catalog or {}
    led = _Ledger(store=store, dialect=dialect) if store is not None else None
    out: list[dict] = []
    total_bytes, total_usd, unpriced = 0, 0.0, 0
    for t in targets_:
        cat = catalog.get(t.relation.lower())
        # *** THE CATALOG'S OWN BYTES BEAT rows x DECLARED WIDTHS. ***
        # BigQuery's `total_logical_bytes` is what the scan reads; a width table is a model of it.
        # A relation the catalog sizes directly skips the estimate entirely and says `catalog`.
        if cat and cat[1]:
            est, basis, rows = int(cat[1]), "catalog", cat[0]
        else:
            rows = (cat[0] if cat else None)
            if rows is None and led is not None:
                rows = led.row_count(t.relation)
            est, basis = cost_mod.estimate([c.lower() for c in t.columns],
                                           types.get(t.relation.lower(), {}), rows, rate)
            if est is not None and cat:
                basis = "catalog"
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
