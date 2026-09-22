"""Read what Elementary already measured. Never rebuild it.

*** VOLUME IS SOMEBODY ELSE'S JOB AND assay SHOULD READ THE ANSWER, NOT REIMPLEMENT IT. ***
`observed_keys` counts uniqueness and nulls on the relations somebody probed. Nothing here tracks
row counts over time, so "this table halved last night" is invisible -- and Elementary, where it is
installed, has been recording exactly that per table per bucket all along. This is the same
treatment `practices` gives dbt-project-evaluator's `fct_` tables: read, join to what assay knows,
and rebuild nothing.

*** THE HALF assay ADDS IS THE HALF ELEMENTARY CANNOT HAVE. ***
Elementary detects with no semantics: "row count fell 41%". assay has the declared grain, the
claims the project makes about itself in its own prose, and the blast radius off the DAG. Neither
tool produces the joined sentence alone.

*** AND AN ABSENT MEASUREMENT IS NOT A PASS -- WHICH IS MOST OF THIS FILE. ***
`practices.py` already carries the law, learned in the field: five `fct_` models of many were
built, and the categories whose tables did not exist were reported as nothing at all. An absent
table and an empty one are not the same fact.

Measured against a real 358-model warehouse, there are FIVE states, not the three that were
expected, and the two nobody predicted are the ones that look most like success:

    package absent          nothing here covers volume, and this report does not either
    never run               Elementary is installed and its models are not built
    one bucket              an anomaly needs two observations
    ABANDONED               the table is full and nothing has written to it since 2026-07-08,
                            which reads identically to a clean bill of health
    STALE FAILURE           a test whose last result is a failure and which has not run since.
                            All twelve open anomaly failures on that warehouse last ran
                            2026-08-04 while the suite ran 2026-09-21: in any Elementary view
                            they are indistinguishable from something failing right now

Nothing in here is specific to one warehouse. The schema is configured, the relation names are
Elementary's own, and every threshold has a default and a config key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Elementary's own relation names. Not ours to choose, and not warehouse-specific.
TEST_RESULTS = "elementary_test_results"
METRICS = "data_monitoring_metrics"
FRESHNESS = "dbt_source_freshness_results"
RELATIONS = (TEST_RESULTS, METRICS, FRESHNESS)

# How long a table may go unwritten before assay stops treating it as current. A monitor that
# ran last quarter is a monitor that is not running; `elementary.stale_after_days` overrides it.
STALE_AFTER_DAYS = 14

ABSENT, NEVER_RUN, ONE_BUCKET, ABANDONED, LIVE, UNREACHABLE = (
    "package_absent", "never_run", "one_bucket", "abandoned", "live", "unreachable")


@dataclass
class Reading:
    """What one Elementary relation could tell us, including that it could tell us nothing."""
    relation: str
    state: str
    rows: int = 0
    newest: datetime | None = None
    age_days: float | None = None
    detail: str = ""
    cadence: object = None            # what THIS relation's own write history says
    threshold_days: int | None = None  # the number that decided `abandoned`, and where it came from

    @property
    def usable(self) -> bool:
        return self.state == LIVE

    def says(self) -> str:
        """One sentence a person can act on. Never 'fine'."""
        if self.state == UNREACHABLE:
            # *** THE SIXTH STATE, FOUND BY SHIPPING THE OTHER FIVE AND RUNNING IT. ***
            # `dbt` was not on the PATH, every statement failed, and this reader called it
            # "package absent" -- announcing that nothing monitors volume, on a warehouse where
            # Elementary was running fine. A tool that cannot reach the warehouse and says the
            # warehouse is empty is the exact defect this module is about.
            return (f"assay could not reach the warehouse, so `{self.relation}` was never read. "
                    f"This is NOT a statement about Elementary: nothing here was measured. "
                    f"Check `--dbt`, `--project-dir` and `--profiles-dir`.")
        if self.state == ABSENT:
            return (f"`{self.relation}` does not exist here, so volume is not measured by this "
                    f"report and nothing in it covers that.")
        if self.state == NEVER_RUN:
            return (f"`{self.relation}` exists and is EMPTY. Elementary is present and its models "
                    f"have not run -- a different fix from installing it.")
        if self.state == ONE_BUCKET:
            return (f"`{self.relation}` holds one observation per table. An anomaly needs two, so "
                    f"there is nothing to compare yet.")
        if self.state == ABANDONED:
            how = f" It was {self.cadence.explain()}." if self.cadence else ""
            return (f"`{self.relation}` holds {self.rows:,} row(s) and nothing has written to it "
                    f"for {self.age_days:.0f} days (newest {self.newest:%Y-%m-%d}), against a "
                    f"threshold of {self.threshold_days}.{how} A monitor that stopped reads "
                    f"exactly like one that finds nothing.")
        return f"`{self.relation}`: {self.rows:,} row(s), newest {self.newest:%Y-%m-%d}."


@dataclass
class Volume:
    """One table's row count over time, as Elementary recorded it."""
    table: str                      # normalised: the last segment, lowercased
    raw: str                        # exactly as Elementary wrote it
    buckets: int
    latest: float | None
    previous: float | None
    at: datetime | None

    @property
    def change(self) -> float | None:
        """Signed fraction. None when there is nothing to compare, which is not zero."""
        if self.previous in (None, 0) or self.latest is None:
            return None
        return (self.latest - self.previous) / self.previous


@dataclass
class TestState:
    """The LATEST result for one Elementary test, and how long ago that was."""
    table: str
    column: str
    kind: str                       # anomaly_detection | schema_change
    sub_type: str
    status: str
    at: datetime | None
    age_days: float | None = None

    @property
    def open_failure(self) -> bool:
        return str(self.status).lower() in ("fail", "error")


@dataclass
class Report:
    readings: list = field(default_factory=list)
    volumes: list = field(default_factory=list)
    tests: list = field(default_factory=list)
    schema: str = ""
    stale_after_days: int = STALE_AFTER_DAYS

    def reading(self, relation: str) -> Reading | None:
        return next((r for r in self.readings if r.relation == relation), None)

    @property
    def reachable(self) -> bool:
        return not any(r.state == UNREACHABLE for r in self.readings)

    @property
    def installed(self) -> bool:
        """Present in some form. False when absent -- and NOT true when unreachable, because
        nothing was measured and an unmeasured thing is not an installed one."""
        return self.reachable and any(r.state != ABSENT for r in self.readings)

    def open_failures(self) -> list:
        return [t for t in self.tests if t.open_failure]

    def stale_failures(self) -> list:
        """*** THE STATE NOBODY PREDICTED. ***
        A test whose last result is a failure and which has not run since. Its age is the whole
        point: twelve on a real warehouse, all last run seven weeks before the suite last ran.
        """
        return [t for t in self.open_failures()
                if t.age_days is not None and t.age_days > self.stale_after_days]


# *** ONE ROW PER KEY, COMPUTED WHERE THE DATA IS. ***
# These tables accrue one row per test per run and one per table per bucket, so they grow without
# bound. Collapsing in SQL means assay reads hundreds of rows instead of tens of thousands, and
# means a row limit can never change the answer. Plain ANSI window functions: every warehouse dbt
# supports has them.
_LATEST_TEST = """
select table_name, column_name, test_type, test_sub_type, status, detected_at
from (
  select t.*, row_number() over (
      partition by table_name, coalesce(column_name, ''), coalesce(test_short_name, ''),
                   coalesce(test_sub_type, '')
      order by detected_at desc) as assay_rn
  from {schema}.{rel} as t
  where test_type is not null and test_type <> 'dbt_test'
) as ranked
where assay_rn = 1
"""

# The last two BUCKETS per table is all a movement needs, plus how many there are so "one bucket"
# stays visible -- an anomaly needs two observations and one is no answer rather than a smaller one.
#
# *** ONE BUCKET IS WRITTEN MANY TIMES, AND COMPARING TWO ROWS COMPARES TWO COPIES. ***
# Measured: `buyer_leads_enriched` holds NINE rows for bucket 2026-07-05, identical but for
# `updated_at` -- Elementary re-records the open bucket on every run. The first version of this
# query took "the last two rows by bucket_end" and therefore compared one bucket against itself,
# reporting a 0% move on a table that had changed and, where duplicates straddled a boundary in
# an arbitrary order, movements of +2061% that never happened. That is the `arbitrary_pick` defect
# this tool checks other people's SQL for, in assay's own reader.
#
# So: collapse to one row per (table, bucket) on the newest `updated_at` FIRST, then rank buckets.
_LATEST_VOLUME = """
select full_table_name, bucket_end, metric_value, assay_rn, assay_buckets
from (
  select per_bucket.full_table_name, per_bucket.bucket_end, per_bucket.metric_value,
         row_number() over (partition by per_bucket.full_table_name
                            order by per_bucket.bucket_end desc) as assay_rn,
         count(*) over (partition by per_bucket.full_table_name) as assay_buckets
  from (
    select m.full_table_name, m.bucket_end, m.metric_value,
           row_number() over (partition by m.full_table_name, m.bucket_end
                              order by m.updated_at desc, m.metric_value desc) as assay_dup
    from {schema}.{rel} as m
    where m.metric_name = 'row_count' and m.column_name is null
      and m.bucket_end is not null
  ) as per_bucket
  where per_bucket.assay_dup = 1
) as ranked
where assay_rn <= 2
"""


def _query(schema: str, rel: str) -> str:
    if rel == TEST_RESULTS:
        return _LATEST_TEST.format(schema=schema, rel=rel)
    if rel == METRICS:
        return _LATEST_VOLUME.format(schema=schema, rel=rel)
    return f"select * from {schema}.{rel}"


def _norm(full_name: str) -> str:
    """`SUNNY.MAIN_WATER.WATER_WELLS` -> `water_wells`.

    Elementary writes a fully-qualified, upper-cased name and assay keys on the model's own name.
    The last segment is the relation; nothing else in the string is ours to interpret.
    """
    return str(full_name or "").split(".")[-1].strip().strip('"').lower()


def _as_dt(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if not v:
        return None
    text = str(v).replace("Z", "").replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            # Naive on purpose, and matched to `_age`: Elementary writes naive local timestamps
            # and an aware value here would raise on every subtraction rather than compare.
            return datetime.strptime(text, fmt)          # noqa: DTZ007
        except ValueError:
            continue
    return None


def _age(at: datetime | None, now: datetime | None = None) -> float | None:
    """Days since. Naive on purpose: Elementary writes naive local timestamps, and mixing an
    aware `now` with a naive `detected_at` raises rather than comparing."""
    if at is None:
        return None
    return max(0.0, ((now or datetime.now()) - at) / timedelta(days=1))   # noqa: DTZ005


def read(runner, schema: str, *, stale_after_days: int = STALE_AFTER_DAYS,
         now: datetime | None = None, limit: int = 20000, fallback=None) -> Report:
    """Every Elementary relation assay reads, and what each one could say.

    `runner(sql, limit) -> list[dict]` is the caller's connection -- `probe.run_sql` bound to their
    project, so assay never holds a credential. The same shape `practices` uses.
    """
    rep = Report(schema=schema, stale_after_days=stale_after_days)
    # *** CAN assay REACH THE WAREHOUSE AT ALL? ***
    # Every failure of `dbt show` comes back as `[]`, so without this a broken connection is
    # indistinguishable from a package that is not installed -- and the report would announce
    # that nothing watches volume while Elementary ran happily an hour ago. One trivial statement
    # settles it before anything is concluded from an empty result.
    if not runner("select 1 as assay_reachable", 1):
        rep.readings = [Reading(rel, UNREACHABLE) for rel in RELATIONS]
        return rep
    for rel in RELATIONS:
        # *** AN ABSENT TABLE AND AN EMPTY ONE ARE NOT THE SAME FACT, AND `dbt show` HIDES IT. ***
        # A failed statement and an empty result both come back as `[]`, so a reader that selects
        # rows and finds none cannot tell a missing package from a package that has not run --
        # and would report the first as the second, or worse, as nothing at all. That is the
        # failure `practices` found in the field with dbt-project-evaluator's `fct_` models.
        #
        # `select count(*)` separates them: one row on an empty table, nothing at all when the
        # relation does not exist. One cheap statement, and the distinction is exact rather than
        # inferred.
        counted = runner(f"select count(*) as n from {schema}.{rel}", 1)
        if not counted:
            rep.readings.append(Reading(rel, ABSENT))
            continue
        try:
            n = int(next(iter(counted[0].values())))
        except (TypeError, ValueError, IndexError, AttributeError):
            n = 0
        if n == 0:
            rep.readings.append(Reading(rel, NEVER_RUN))
            continue
        rows = runner(_query(schema, rel), limit) or []
        if not rows:
            rep.readings.append(Reading(rel, NEVER_RUN, rows=0,
                                        detail=f"{n:,} row(s) counted and none readable"))
            continue
        # *** A LIMIT THAT TRUNCATES A HISTORY CHANGES THE ANSWER SILENTLY. ***
        # Found by running it: `elementary_test_results` came back as exactly 20,000 rows of
        # 20,737, and "the latest result per test" computed over a truncated history can pick a
        # row that is not the latest. The queries below collapse to one row per key IN SQL, so
        # the limit is a safety net rather than a sampler -- and if one is ever hit anyway, the
        # count says so rather than the reader quietly answering from part of the table.
        truncated = len(rows) >= limit
        newest = max((_as_dt(r.get("detected_at") or r.get("created_at")
                             or r.get("updated_at") or r.get("bucket_end")) for r in rows),
                     default=None)
        age = _age(newest, now)
        # *** EACH MONITOR GETS ITS OWN THRESHOLD, FROM ITS OWN HISTORY. ***
        # A source refreshed hourly and one refreshed monthly cannot share a number, and the first
        # version gave them one. What is late for this relation is longer than this relation has
        # normally gone between writes; the project's build cadence is only the fallback for one
        # without enough history of its own.
        own = _cadence_of(write_history(runner, schema, rel), f"`{rel}`'s own write history")
        limit_days = own.derived_staleness_days
        if limit_days is None and fallback is not None:
            limit_days = fallback.derived_staleness_days
        if limit_days is None:
            limit_days = stale_after_days
        state = LIVE
        if age is not None and age > limit_days:
            state = ABANDONED
        rep.readings.append(Reading(
            rel, state, rows=n, newest=newest, age_days=age, cadence=own,
            threshold_days=limit_days,
            detail=(f"read {len(rows):,} of {n:,} row(s): the limit was reached, so this is "
                    f"part of the table" if truncated else "")))
        if rel == METRICS:
            rep.volumes = _volumes(rows, now)
        elif rel == TEST_RESULTS:
            rep.tests = _tests(rows, now)
    _mark_one_bucket(rep)
    return rep


def _mark_one_bucket(rep: Report) -> None:
    """An anomaly needs two observations, and one is not a smaller answer -- it is no answer."""
    r = rep.reading(METRICS)
    if r is None or r.state != LIVE or not rep.volumes:
        return
    if all(v.buckets < 2 for v in rep.volumes):
        r.state = ONE_BUCKET


def _volumes(rows: list, now) -> list:
    """The last two buckets per table, as the query returned them, with the TRUE bucket count.

    The count comes from SQL rather than from how many rows arrived, so "this table has only one
    observation" stays a fact about the warehouse instead of an artefact of the query.
    """
    by: dict = {}
    for r in rows:
        key = str(r.get("full_table_name") or "")
        at = _as_dt(r.get("bucket_end") or r.get("created_at"))
        try:
            val = float(r.get("metric_value"))
        except (TypeError, ValueError):
            continue
        try:
            total = int(r.get("assay_buckets") or 0)
        except (TypeError, ValueError):
            total = 0
        rank = r.get("assay_rn")
        by.setdefault(key, {"total": total, "rows": []})["rows"].append((rank, at, val))
        by[key]["total"] = max(by[key]["total"], total)
    out = []
    for raw, got in by.items():
        pairs = sorted(got["rows"], key=lambda t: (t[0] if t[0] is not None else 99))
        if not pairs:
            continue
        latest = pairs[0]
        prev = pairs[1] if len(pairs) > 1 else None
        out.append(Volume(table=_norm(raw), raw=raw,
                          buckets=got["total"] or len(pairs),
                          latest=latest[2], previous=prev[2] if prev else None, at=latest[1]))
    out.sort(key=lambda v: v.table)
    return out


def _tests(rows: list, now) -> list:
    """The LATEST result per test.

    *** HISTORY IS NOT A BACKLOG. ***
    These tables accrue one row per test per run. Counting every `fail` ever recorded turns 3,030
    anomaly rows into a 346-item queue, when the number of tests currently failing is twelve. The
    rule is the one `live_decisions` already settled on: newest wins, per key, with no exceptions.
    """
    out = []
    for r in rows:
        kind = str(r.get("test_type") or "")
        if kind in ("", "dbt_test"):
            continue                    # dbt's own tests; assay reads those from the manifest
        at = _as_dt(r.get("detected_at") or r.get("created_at"))
        out.append(TestState(table=_norm(r.get("table_name")), column=str(r.get("column_name") or ""),
                             kind=kind, sub_type=str(r.get("test_sub_type") or ""),
                             status=str(r.get("status") or ""), at=at, age_days=_age(at, now)))
    out.sort(key=lambda t: (t.table, t.column, t.sub_type))
    return out


# ------------------------------------------------------------------ what assay says about it

def claim_subjects(rep: Report, project, store, threshold: float = 0.10) -> list:
    """(model uid, Volume, claim row) for every movement worth asking about.

    *** CODE NARROWS BEFORE ANYTHING IS ASKED, AS EVERYWHERE ELSE HERE. ***
    A table with no movement, no claim, or no model cannot contradict anything, and asking is
    paying to be told so. What survives is a counted movement, a sentence the project wrote about
    itself, and a model whose blast radius is arithmetic.
    """
    if project is None or store is None:
        return []
    by_name = {m.name.lower(): u for u, m in project.models.items()}
    claims: dict = {}
    for r in (store.claims(checkable_only=False) or []):
        claims.setdefault(str(r["subject"]).split("::")[0], []).append(r)
    out = []
    for v in rep.volumes:
        change = v.change
        if change is None or abs(change) < threshold:
            continue
        uid = by_name.get(v.table)
        if uid is None:
            continue
        for c in claims.get(uid, []):
            out.append((uid, v, c))
    # Biggest blast radius first: a limit should spend itself where being wrong costs most.
    out.sort(key=lambda t: (-project.blast_radius(t[0])["marts"], t[0], t[2]["claim_id"]))
    return out


def claim_state(project, uid: str, vol: Volume, claim: dict, entry=None,
                vocab: dict | None = None) -> dict:
    """The smallest state that can answer it: the movement, the claim, and what one row is.

    Deliberately not the model's SQL. Measured rule in this codebase: a claim alone read 0.96 and
    the same claim plus one correct extra sentence read 0.47.
    """
    m = project.models.get(uid)
    radius = project.blast_radius(uid)
    state = {
        "model": m.name if m else uid,
        "movement": {
            "row_count_was": int(vol.previous) if vol.previous is not None else None,
            "row_count_is": int(vol.latest) if vol.latest is not None else None,
            "change_percent": round((vol.change or 0) * 100, 1),
            "observations": vol.buckets,
            "counted_by": "elementary, not by assay and not by a judgment",
        },
        "the_project_says": claim.get("text", ""),
        "it_says_it_in": claim.get("source_ref") or claim.get("source_kind") or "",
        "what_one_row_of_this_model_is": (
            entry.grain.value if entry is not None and getattr(entry, "grain", None) else None),
        "models_downstream": radius["descendants"],
        "marts_downstream": radius["marts"],
    }
    if vocab:
        state["vocabulary"] = vocab
    return {k: v for k, v in state.items() if v not in (None, "", [], {})}


def unwatched(rep: Report, project, min_marts: int = 1) -> list:
    """Models with real reach that no volume history covers.

    *** THIS IS THE FINDING assay CAN HONESTLY MAKE ABOUT SOMEBODY ELSE'S TOOL. ***
    Not "your data is wrong" -- Elementary's results are Elementary's, and ingesting them as assay
    findings would corrupt the one number measuring the loop. This is about the MONITORING: a
    model that matters and is watched by nothing. That is assay's to say, because only assay knows
    what rests on it.
    """
    if project is None:
        return []
    watched = {v.table for v in rep.volumes}
    out = []
    for uid, m in project.models.items():
        if m.name.lower() in watched or getattr(m, "is_installed_package", False):
            continue
        radius = project.blast_radius(uid)
        if radius["marts"] < min_marts:
            continue
        out.append((uid, m.name, radius["descendants"], radius["marts"]))
    out.sort(key=lambda r: (-r[3], -r[2], r[1]))
    return out


# ----------------------------------------------------------------- monitoring as a contract

# Invocations closer together than this are one build, not two. A `dbt run` followed by a
# `dbt test` minutes later is one pipeline run by any reading a person would give it.
SESSION_HOURS = 6

INVOCATIONS = "dbt_invocations"
DBT_TESTS = "dbt_tests"

# *** assay ASSERTS THE CONTRACT. IT NEVER BECOMES THE MONITOR. ***
# The moment assay measures a row count or a freshness itself, it is a second monitoring tool with
# a second opinion, and you have the two-inboxes problem this whole module exists to avoid. What
# it checks is that a monitor EXISTS, is CURRENT, and COVERS what matters. Everything measured
# stays Elementary's.
MONITORING_CHECKS = (
    "monitor_declared_but_never_run",
    "monitor_ran_then_stopped",
    "volume_is_not_being_watched",
    "test_declared_but_never_run",
    "test_skipped_rather_than_passed",
)


@dataclass
class Cadence:
    """How often something actually gets written, measured from its own history.

    *** "LATE" IS LONGER THAN IT NORMALLY GOES, AND NOTHING HERE MULTIPLIES BY A NUMBER. ***
    The first version took the median gap and multiplied it by three. Three was invented -- the
    same failure as a guessed ledger ceiling, one layer up, because a made-up multiplier is a made-
    up threshold however it is dressed. What "late" means is answerable from the data: the 90th
    percentile of the gaps this thing has actually gone between writes. It has been quiet that
    long before and carried on; longer than that has not happened while it was healthy.

    p90 rather than the maximum, because one historical outage should not license another.
    """
    writes: int = 0
    gaps: list = field(default_factory=list)          # days between consecutive writes
    newest: datetime | None = None
    source: str = ""                                  # where the history came from

    @property
    def days_spanned(self) -> float:
        return sum(self.gaps)

    @property
    def normal_gap_days(self) -> float | None:
        """The 90th percentile gap. None when there is not enough history to have one."""
        if len(self.gaps) < 3:
            return None
        import math
        ordered = sorted(self.gaps)
        # *** THE RANK ROUNDS UP, AND ON A SHORT HISTORY THAT MATTERS MORE THAN THE PERCENTILE. ***
        # Nearest-rank p90 over eight gaps of [1,1,1,1,1,1,1,6] returns 1, so a relation that has
        # quietly gone six days before would be called late at two -- crying wolf, which is half
        # the failure a guessed number has. Rounding the rank up returns 6 there, and on a long
        # history still steps below a single outage: 18 gaps of one day and one of seventy-three
        # returns one day, so an outage does not license another.
        i = min(len(ordered) - 1, math.ceil(0.9 * (len(ordered) - 1)))
        return ordered[i]

    @property
    def derived_staleness_days(self) -> int | None:
        """The threshold, in whole days, or None when it cannot be derived.

        None is the honest answer and not a default: the field warehouse's freshness table was
        written on exactly ONE day -- it did not decay, it ran once -- so nothing about its own
        history can say what late means for it.
        """
        p90 = self.normal_gap_days
        if p90 is None:
            return None
        import math
        return max(1, math.ceil(p90))

    def explain(self) -> str:
        p90 = self.normal_gap_days
        if p90 is None:
            return (f"only {self.writes} write(s) recorded, which is not enough to say what a "
                    f"normal gap is")
        return (f"written {self.writes:,} time(s) over {self.days_spanned:.0f} days; 9 gaps in 10 "
                f"are under {p90:.1f} day(s), from {self.source}")


def _cadence_of(stamps: list, source: str) -> Cadence:
    stamps = sorted({d for d in stamps if d})
    gaps = [(stamps[i + 1] - stamps[i]) / timedelta(days=1) for i in range(len(stamps) - 1)]
    return Cadence(writes=len(stamps), gaps=gaps,
                   newest=stamps[-1] if stamps else None, source=source)


def write_history(runner, schema: str, rel: str, limit: int = 5000) -> list:
    """The distinct DAYS this relation was written on.

    Days rather than timestamps: one build writes a table several times within minutes, and gaps
    between those describe how fast a job runs rather than how often it runs. Cheap -- one grouped
    statement, a few hundred rows at most.
    """
    col = {TEST_RESULTS: "detected_at", METRICS: "created_at",
           FRESHNESS: "created_at"}.get(rel, "created_at")
    rows = runner(f"select distinct cast({col} as date) as assay_day from {schema}.{rel} "
                  f"where {col} is not null", limit) or []
    return [d for d in (_as_dt(r.get("assay_day")) for r in rows) if d]


def build_cadence(runner, schema: str, limit: int = 5000) -> Cadence:
    """How often this project runs dbt at all, as the fallback for a monitor with no history.

    *** ONE PIPELINE RUN ISSUES MANY INVOCATIONS. ***
    Measured: 1,592 invocations over 79 days, sixteen on one day, so the gap between invocations
    describes how fast dbt runs back-to-back rather than how often this project builds. Days,
    for the same reason `write_history` uses them.
    """
    rows = runner(f"select distinct cast(run_started_at as date) as assay_day "
                  f"from {schema}.{INVOCATIONS} where run_started_at is not null", limit) or []
    return _cadence_of([d for d in (_as_dt(r.get("assay_day")) for r in rows) if d],
                       "this project's build cadence")


def test_coverage(runner, schema: str, limit: int = 40000) -> dict:
    """Declared tests against tests that have ever produced a result.

    *** A TEST THAT NEVER RAN AND A TEST THAT PASSED LOOK THE SAME FROM A SUMMARY. ***
    And a SKIPPED result is neither: dbt skips a test whose model failed upstream, so a green
    summary can contain a test that has not evaluated your data in months. Measured on a real
    warehouse: 1,317 tests declared, 858 with any result -- 459 that have never fired -- and 1,587
    skipped results.
    """
    declared = runner(f"select count(*) as n from {schema}.{DBT_TESTS}", 1)
    ran = runner(f"select count(distinct test_unique_id) as n from {schema}.{TEST_RESULTS} "
                 f"where test_type = 'dbt_test'", 1)
    skipped = runner(f"select count(*) as n from {schema}.{TEST_RESULTS} "
                     f"where test_type = 'dbt_test' and status = 'skipped'", 1)

    def one(rows):
        try:
            return int(next(iter(rows[0].values())))
        except (IndexError, TypeError, ValueError, AttributeError):
            return None
    return {"declared": one(declared) if declared else None,
            "ever_ran": one(ran) if ran else None,
            "skipped_results": one(skipped) if skipped else None}


def monitoring_findings(rep: Report, project, cad: Cadence | None = None,
                        coverage: dict | None = None, min_marts: int = 1) -> list:
    """What is wrong with the MONITORING, which is assay's to say.

    Never what is wrong with the data -- that is Elementary's, and ingesting its results as assay
    findings would corrupt the one number measuring the loop.
    """
    from .checks.structural import Finding
    out = []

    for r in rep.readings:
        if r.state == NEVER_RUN:
            out.append(Finding(
                check="monitor_declared_but_never_run", subject="", subject_name="", file="",
                summary=f"`{r.relation}` exists and is empty: the monitor is configured and has "
                        f"never produced a result",
                detail=("Installed is not built, and both tools go quiet the same way. This is "
                        "the `fct_sources_without_freshness` case one layer out: a check somebody "
                        "turned on, that has never said anything, and whose silence reads as a "
                        "clean bill."),
                base=2, evidence={"relation": r.relation, "rows": 0}))
        elif r.state == ABANDONED:
            out.append(Finding(
                check="monitor_ran_then_stopped", subject="", subject_name="", file="",
                summary=f"`{r.relation}` has not been written to for {r.age_days:.0f} days, and "
                        f"a stopped monitor reads exactly like one that finds nothing",
                detail=(f"Newest row {r.newest:%Y-%m-%d}, {r.rows:,} row(s) in the table. "
                        f"Late after {r.threshold_days} day(s)"
                        + (f" -- {r.cadence.explain()}" if r.cadence else "")
                        + ". Nothing here is multiplied by an invented number: late is longer "
                          "than this relation has normally gone between writes."),
                base=3, evidence={"relation": r.relation, "age_days": round(r.age_days or 0, 1),
                                  "newest": str(r.newest),
                                  "threshold_days": r.threshold_days,
                                  "derived_from": (r.cadence.source if r.cadence else "")}))

    # *** ONE FINDING WITH A COUNT, NOT ONE PER MODEL. ***
    # The first version emitted a finding per unwatched model: 232 rows on a real warehouse, which
    # is a wall rather than a report, and would have swamped every other finding in `assay check`.
    # Nobody rules on "add monitoring" 232 times either -- it is one decision about coverage. The
    # models are carried in the evidence, ranked by reach, so the ordering survives.
    gaps = unwatched(rep, project, min_marts)
    if gaps:
        watched = len(rep.volumes)
        top = [{"model": n, "marts": m, "models_downstream": d} for _u, n, d, m in gaps[:15]]
        out.append(Finding(
            check="volume_is_not_being_watched", subject="", subject_name="", file="",
            summary=f"{len(gaps):,} model(s) with a mart downstream have no row-count history",
            detail=(f"{watched:,} relation(s) are watched. assay does not measure volume and does "
                    f"not intend to -- this says only that nobody else is either, which is a "
                    f"coverage fact and not a data one. Worst by reach: "
                    + ", ".join(f"{t['model']} ({t['marts']} marts)" for t in top[:5]) + "."),
            base=2, descendants=max((d for _u, _n, d, _m in gaps), default=0),
            marts=max((m for _u, _n, _d, m in gaps), default=0),
            evidence={"unwatched": len(gaps), "watched": watched, "worst_by_reach": top}))

    cov = coverage or {}
    declared, ran = cov.get("declared"), cov.get("ever_ran")
    if declared and ran is not None and declared > ran:
        out.append(Finding(
            check="test_declared_but_never_run", subject="", subject_name="", file="",
            summary=f"{declared - ran:,} of {declared:,} declared test(s) have never produced a "
                    f"result",
            detail=("A test that never ran and a test that passed are indistinguishable in a "
                    "summary, and only one of them has looked at your data."),
            base=2, evidence={"declared": declared, "ever_ran": ran}))
    if cov.get("skipped_results"):
        out.append(Finding(
            check="test_skipped_rather_than_passed", subject="", subject_name="", file="",
            summary=f"{cov['skipped_results']:,} test result(s) are SKIPPED, which is not a pass",
            detail=("dbt skips a test whose model failed upstream. A green run can contain a "
                    "test that has not evaluated your data in months."),
            base=1, evidence={"skipped": cov["skipped_results"]}))
    return out
