"""Incremental models: what a full refresh hides. (G-D)

*** THE COMPILED SQL IS THE FULL REFRESH, AND THE BUG IS IN THE OTHER BRANCH. ***
`dbt compile` renders `is_incremental()` as false, so every check that reads compiled SQL reads
the model as it builds the FIRST time. The branch that runs every day after -- the filter that
decides which rows are new, and the merge that folds them in -- was never read by anything here.
It is read now from the RAW code: the `{% if is_incremental() %}` block is extracted, `{{ this }}`
replaced by a placeholder, and parsed.

Five checks, Snowflake first (its default strategy is `merge`):

| check | fires when |
|---|---|
| `incremental_merge_without_key` | strategy merge or delete+insert, and no `unique_key` |
| `incremental_key_not_unique` | the `unique_key` is not known to be unique within one run's rows |
| `incremental_filter_without_lookback` | `col > (select max(col) from this)` with no subtraction |
| `microbatch_without_lookback` | microbatch with `lookback: 0`, or rows arriving later than it |
| `incremental_schema_change_ignored` | `on_schema_change` unset or `ignore`, and the columns moved |

Two premises: `unique_per_batch(unique_key)` and `max_lateness(event column, lookback)`. The second
needs to know when a row ARRIVED: a loader column by name (`_loaded_at`, `inserted_at`, ...), or
the `arrival_time_column` judgment where the name does not say, and `assay probe --lateness`
measures `max(arrival - event)`. With no arrival column the premise is `unknown`, and the finding
says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .structural import Finding

# What each adapter does when `incremental_strategy` is not set. An adapter absent here is not
# guessed: its strategy reads `adapter default` and no strategy-dependent check fires on it.
ADAPTER_DEFAULT = {"snowflake": "merge", "bigquery": "merge", "databricks": "merge",
                   "postgres": "append", "redshift": "append"}
KEYED = ("merge", "delete+insert", "insert_overwrite_by_key")
# A column a loader writes when a row lands: the arrival time, by name.
ARRIVAL_NAMES = ("_loaded_at", "loaded_at", "_etl_loaded_at", "inserted_at", "_inserted_at",
                 "_airbyte_extracted_at", "_airbyte_emitted_at", "_fivetran_synced",
                 "_sdc_batched_at", "_sdc_received_at", "_dlt_loaded_at", "ingested_at",
                 "_ingested_at", "received_at", "_received_at", "arrived_at", "load_ts")
THIS = "__assay_this__"


@dataclass
class Incremental:
    uid: str
    name: str
    path: str
    adapter: str
    strategy: str                   # merge | delete+insert | append | microbatch | ...
    strategy_from: str              # config | adapter default | unknown
    unique_key: list = field(default_factory=list)
    on_schema_change: str = ""
    event_time: str = ""
    lookback: int | None = None     # microbatch: batches, dbt's default 1 when unset
    batch_size: str = ""
    block: str = ""                 # the is_incremental() branch, as written
    filter_column: str = ""         # col in `col > (select max(...) from this)`
    filter_sql: str = ""
    filter_lookback: bool = False   # the max side subtracts something
    filter_read: bool = True        # False: a block exists and could not be parsed

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("uid",)}


_BLOCK = re.compile(r"{%-?\s*if\s+is_incremental\(\s*\)\s*-?%}(.*?){%-?\s*(?:endif|else|elif)\b",
                    re.S | re.I)


def _dejinja(text: str) -> str:
    """The block with its Jinja replaced by things sqlglot can parse."""
    t = re.sub(r"{{-?\s*this\s*-?}}", THIS, text)
    t = re.sub(r"{{-?\s*ref\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*['\"]([^'\"]+)['\"]\s*)?\)\s*-?}}",
               lambda m: m.group(2) or m.group(1), t)
    t = re.sub(r"{{-?\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*-?}}",
               r"\1.\2", t)
    t = re.sub(r"{{.*?}}", "__assay_jinja__", t, flags=re.S)
    t = re.sub(r"{%.*?%}", " ", t, flags=re.S)
    t = re.sub(r"{#.*?#}", " ", t, flags=re.S)
    # comments, so a block that opens with one still starts with its `where`
    t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
    t = re.sub(r"--[^\n]*", " ", t)
    return t.strip()


def _reads_this(node) -> bool:
    return any(t.name.lower() == THIS for t in node.find_all(exp.Table))


def _subtracts(node) -> bool:
    """Does this side of the comparison move the high-water mark back by something?"""
    if node.find(exp.Sub) is not None or node.find(exp.DateSub) is not None \
            or node.find(exp.TsOrDsAdd) is not None:
        return True
    for f in node.find_all(exp.Func):
        n = (f.sql_name() or "").lower()
        if n in ("dateadd", "date_add", "timestampadd", "timestamp_add", "timeadd", "date_sub",
                 "timestamp_sub", "datetime_sub", "timestamp_diff"):
            return True
    for f in node.find_all(exp.Anonymous):
        if str(f.name).lower() in ("dateadd", "timestampadd", "timeadd", "date_sub"):
            return True
    return False


def parse_filter(block: str, dialect: str) -> tuple[str, str, bool, bool]:
    """(column, the comparison's sql, lookback?, read?) from an is_incremental() block."""
    body = _dejinja(block)
    if not body:
        return "", "", False, True
    cond = re.sub(r"^\s*(where|and)\s+", "", body, flags=re.I)
    try:
        tree = sqlglot.parse_one(f"select 1 from __assay_t__ where {cond}", dialect=dialect)
    except Exception:                                            # noqa: BLE001
        try:
            tree = sqlglot.parse_one(body, dialect=dialect)
        except Exception:                                        # noqa: BLE001
            return "", "", False, False
    for cmp_ in tree.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE):
        left, right = cmp_.this, cmp_.expression
        # `col > (select max(col) from this)`, either way round
        if isinstance(cmp_, (exp.LT, exp.LTE)):
            left, right = right, left
        if not _reads_this(right) or right.find(exp.Max) is None:
            continue
        col = left.find(exp.Column)
        shown = cmp_.sql(dialect=dialect).replace(THIS, "{{ this }}")
        return (col.name.lower() if col is not None else "", shown,
                _subtracts(right) or _subtracts(left), True)
    return "", "", False, True


def read(project, digests=None) -> dict:
    """{uid: Incremental} for every incremental model of this project."""
    adapter = str(getattr(project, "adapter_type", "") or "").lower()
    dialect = getattr(project, "dialect", "") or None
    out = {}
    for uid, m in project.models.items():
        if m.materialized != "incremental" or getattr(m, "is_installed_package", False):
            continue
        node = ((project.raw or {}).get("nodes") or {}).get(uid) or {}
        cfg = node.get("config") or {}
        strategy = str(cfg.get("incremental_strategy") or "").lower()
        came = "config"
        if not strategy:
            strategy = ADAPTER_DEFAULT.get(adapter, "")
            came = "adapter default" if strategy else "unknown"
        uk = cfg.get("unique_key")
        keys = [uk] if isinstance(uk, str) else list(uk or [])
        keys = [k.strip().lower() for k in keys for k in str(k).split(",") if k.strip()]
        lb = cfg.get("lookback")
        inc = Incremental(uid, m.name, m.path, adapter, strategy, came, keys,
                          str(cfg.get("on_schema_change") or ""),
                          str(cfg.get("event_time") or ""),
                          int(lb) if isinstance(lb, (int, float)) or str(lb).isdigit() else None,
                          str(cfg.get("batch_size") or ""))
        raw = node.get("raw_code") or node.get("raw_sql") or ""
        got = _BLOCK.search(raw)
        if got:
            inc.block = got.group(1).strip()
            (inc.filter_column, inc.filter_sql, inc.filter_lookback,
             inc.filter_read) = parse_filter(inc.block, dialect)
        out[uid] = inc
    return out


# --------------------------------------------------------------------------- premises

def per_batch_premise(led, inc: Incremental, digest_):
    """`unique_per_batch(unique_key)`: the new rows of one run carry each key once.

    Evidence: the SQL itself (a final group by, a one-per-partition dedupe or DISTINCT ON over
    columns inside the key makes it unique by construction), and the table's own uniqueness (a
    failing test or a counted duplicate on the key breaks it; a passing one says the TABLE is
    unique, which a merge would make true even when a batch is not, so it only makes it
    `unchecked`)."""
    from .. import ledger as L
    p = L.Premise(inc.uid, inc.name, tuple(sorted(inc.unique_key)), "unique_per_batch")
    if p.id in led.premises:
        return led.premises[p.id]
    keys = set(p.columns)
    ev = []
    d = digest_
    if d is not None and d.ok:
        grain = [c.lower() for c in (getattr(d, "final_group_by", None) or [])]
        dedupe = [[c.lower() for c in (w.partition_columns or [])] for w in d.windows
                  if w.partition_columns and (d.has_qualify or any(
                      "= 1" in x for x in d.predicates))]
        don = [c.lower() for c in (getattr(d, "distinct_on", None) or [])]
        for label, cols in (("a final group by", grain), ("DISTINCT ON", don),
                            *(("a one-row-per-partition dedupe", c) for c in dedupe)):
            if cols and set(cols) <= keys:
                ev.append(L.Evidence("derived", f"{label} on ({', '.join(cols)}) makes each key "
                                     f"appear once per run", L.HOLDING))
                break
    table = L.unique(led, inc.uid, sorted(keys))
    for e in table.evidence:
        if e.kind == "judged":
            continue
        st = L.BROKEN if e.status == L.BROKEN else L.UNCHECKED
        ev.append(L.Evidence(e.kind, f"the table: {e.detail}"
                             + ("" if st == L.BROKEN else " (a run's own rows are not counted)"),
                             st, e.at))
    p.evidence = [e for e in ev if not (e.kind == "config")]
    p.status = L._verdict(p.evidence) if p.evidence else L.UNKNOWN
    return p


DDL_LATENESS = """
create table if not exists observed_lateness (
    relation          varchar,
    event_column      varchar,
    arrival_column    varchar,
    max_late_seconds  double,       -- max(arrival - event) over the table, in seconds
    rows_read         bigint,
    observed_at       timestamp,
    via               varchar,
    primary key (relation, event_column, observed_at)
);
"""


def arrival_column(inc: Incremental, entry, schema) -> tuple[str, str]:
    """(column, how it was chosen) for when a row arrived, or ("", why none)."""
    cols = [c.name for c in (entry.columns if entry is not None else [])]
    for c in cols:
        if c.lower() in ARRIVAL_NAMES:
            return c, "its name"
    best, p_best = "", 0.0
    for qid, v in sorted(((entry.judged or {}) if entry is not None else {}).items()):
        if not (qid == "arrv" or qid.startswith("arrv__")):
            continue
        if (v or {}).get("answer") != "arrival_time":
            continue
        try:
            p_ = float(((v or {}).get("probabilities") or {}).get("arrival_time", 0) or 0)
        except (TypeError, ValueError):
            p_ = 0.0
        col = str((v or {}).get("context") or "").split(".")[-1]
        if p_ >= 0.6 and col in cols and p_ > p_best:
            best, p_best = col, p_
    if best:
        return best, f"the arrival_time_column judgment, {p_best:.2f}"
    return "", "no column says when a row arrived"


def _seconds(lookback: int | None, batch_size: str) -> float | None:
    per = {"hour": 3600, "day": 86400, "month": 31 * 86400, "year": 366 * 86400}.get(batch_size)
    if lookback is None or per is None:
        return None
    return float(lookback * per)


def lateness_premise(led, inc: Incremental, column: str, allowed_s: float, allowed_txt: str,
                     entry, schema, store):
    """`max_lateness(column) <= allowed`: no row arrives later than the window the filter allows.
    """
    from .. import ledger as L
    p = L.Premise(inc.uid, inc.name, (column,), "max_lateness", allowed_txt)
    if p.id in led.premises:
        return led.premises[p.id]
    arrival, how = arrival_column(inc, entry, schema)
    ev = []
    if not arrival:
        ev = []
        p.evidence, p.status = [L.Evidence("none", f"{how}, so lateness cannot be measured",
                                           L.UNKNOWN)], L.UNKNOWN
        return p
    got = None
    if store is not None:
        try:
            store.con.execute(DDL_LATENESS)
            rel = (schema.relation.get(inc.uid) or "").replace('"', "").lower()
            got = store.con.execute(
                "select max_late_seconds, rows_read, observed_at from observed_lateness "
                "where relation = ? and event_column = ? order by observed_at desc limit 1",
                [rel, column]).fetchone()
        except Exception:                                        # noqa: BLE001
            got = None
    if got is None:
        ev.append(L.Evidence("config", f"arrival is `{arrival}` ({how}); not measured yet: "
                             f"`assay probe --lateness`", L.UNCHECKED))
    else:
        late, n, at = got
        late = float(late or 0)
        ok = late <= allowed_s
        ev.append(L.Evidence("observed", f"the latest row arrived {_human(late)} after its "
                             f"`{column}` ({n or 0:,} rows, arrival `{arrival}`)",
                             L.HOLDING if ok else L.BROKEN, str(at or "")[:19]))
    p.evidence = ev
    p.status = L._verdict(ev)
    return p


def _human(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} second(s)"
    if seconds < 7200:
        return f"{seconds / 60:.0f} minute(s)"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hour(s)"
    return f"{seconds / 86400:.1f} day(s)"


def lateness_sql(relation: str, event: str, arrival: str, dialect: str) -> str:
    """max(arrival - event) in seconds, and the rows read, in the project's own dialect."""
    duck = (f"select max(date_diff('second', cast({event} as timestamp), "
            f"cast({arrival} as timestamp))) as late, count(*) as n from {relation}")
    try:
        return sqlglot.transpile(duck, read="duckdb", write=dialect or "duckdb")[0]
    except Exception:                                            # noqa: BLE001
        return duck


# --------------------------------------------------------------------------- the checks

def _finding(check, inc, entry, summary, detail, evidence, base=2):
    return Finding(check=check, subject=inc.uid, subject_name=inc.name, file=inc.path,
                   summary=summary, detail=detail, base=base, evidence=evidence,
                   descendants=getattr(entry, "descendants", 0) or 0,
                   marts=getattr(entry, "marts", 0) or 0)


def run_all(project, digests, schema, entries, store=None) -> list:
    """Every incremental finding. Registers its premises in the ledger in force."""
    from .. import ledger as L
    incs = read(project, digests)
    by_uid = {e.uid: e for e in entries or []}
    led = L.active()
    out = []
    for uid, inc in sorted(incs.items()):
        e = by_uid.get(uid)
        d = (digests or {}).get(uid)
        # 1. merge without a key
        if inc.strategy in KEYED and not inc.unique_key:
            out.append(_finding(
                "incremental_merge_without_key", inc, e,
                f"`{inc.strategy}` incremental with no `unique_key`",
                (f"`{inc.name}` is incremental with strategy `{inc.strategy}` "
                 f"({inc.strategy_from}) and declares no `unique_key`, so it behaves as append: "
                 f"a rerun, a backfill or an overlapping window inserts the same rows again. "
                 f"Set `unique_key` to the columns that identify a row."),
                {"strategy": inc.strategy, "strategy_from": inc.strategy_from}, base=3))
        # 2. a key nothing makes unique within a run
        if inc.unique_key and inc.strategy in KEYED:
            if led is not None:
                prem = led.use(per_batch_premise(led, inc, d), "raised_on",
                               f"incremental_key_not_unique:{uid}:{','.join(inc.unique_key)}",
                               uid, "the merge folds each key in once")
            else:
                prem = per_batch_premise(L.build(project, schema, entries, store), inc, d)
            if prem.status in (L.BROKEN, L.UNKNOWN):
                what = ("Snowflake refuses the merge (`Duplicate row detected during DML "
                        "action`) or, with `ERROR_ON_NONDETERMINISTIC_MERGE` off, updates the row "
                        "from whichever duplicate it read last")
                out.append(_finding(
                    "incremental_key_not_unique", inc, e,
                    f"nothing makes `unique_key` ({', '.join(inc.unique_key)}) unique within a run",
                    (f"`{inc.name}` merges on ({', '.join(inc.unique_key)}). If one run's new rows "
                     f"carry a key twice, {what}. "
                     + ("A count or a failed test says the key is not unique. "
                        if prem.status == L.BROKEN else
                        "Nothing here says it cannot: no final group by, dedupe or DISTINCT ON "
                        "over the key, and no test or count on it. ")
                     + "Dedupe the new rows on the key before the merge."),
                    {"unique_key": inc.unique_key, "premise": prem.statement(),
                     "premise_status": prem.status, "why": L.why(prem)}, base=3))
        # 3. a high-water mark with no lookback
        if inc.block and inc.filter_column and not inc.filter_lookback:
            prem = None
            if led is not None:
                prem = led.use(lateness_premise(led, inc, inc.filter_column, 0.0, "0 (no lookback)",
                                                e, schema, store),
                               "raised_on", f"incremental_filter_without_lookback:{uid}:"
                                            f"{inc.filter_column}", uid,
                               "the filter reads only rows newer than the newest kept")
            if prem is None or prem.status != L.HOLDING:
                out.append(_finding(
                    "incremental_filter_without_lookback", inc, e,
                    f"the incremental filter on `{inc.filter_column}` has no lookback",
                    (f"`{inc.name}` loads only rows whose `{inc.filter_column}` is later than "
                     f"the newest already in the table. A row that arrives after a newer one -- a "
                     f"late event, a retried load, a backfilled source -- is below the mark and "
                     f"is skipped forever, and no test notices. Subtract a window: "
                     f"`> (select max({inc.filter_column}) - interval '3 days' from {{{{ this }}}})`"
                     f", sized to how late rows really arrive."
                     + (f" {L.why(prem)}." if prem is not None else "")),
                    {"column": inc.filter_column, "filter": inc.filter_sql,
                     **({"premise": prem.statement(), "premise_status": prem.status,
                         "why": L.why(prem)} if prem is not None else {})}))
        # 4. microbatch
        if inc.strategy == "microbatch":
            lb = 1 if inc.lookback is None else inc.lookback
            allowed = _seconds(lb, inc.batch_size)
            prem = None
            if led is not None and inc.event_time and allowed is not None:
                prem = led.use(lateness_premise(led, inc, inc.event_time, allowed,
                                                f"{lb} {inc.batch_size}(s)", e, schema, store),
                               "raised_on", f"microbatch_without_lookback:{uid}:{inc.event_time}",
                               uid, "each run reprocesses only the lookback's batches")
            if lb == 0 or (prem is not None and prem.status == L.BROKEN):
                out.append(_finding(
                    "microbatch_without_lookback", inc, e,
                    "microbatch reprocesses no earlier batch" if lb == 0 else
                    "rows arrive later than the microbatch lookback",
                    (f"`{inc.name}` is microbatch on `{inc.event_time or '?'}` by "
                     f"{inc.batch_size or '?'} with lookback {lb}. A row arriving after its "
                     f"batch was processed and outside the lookback is never loaded. "
                     + (f"{L.why(prem)}. " if prem is not None else "")
                     + "Set `lookback` to cover how late rows arrive."),
                    {"lookback": lb, "batch_size": inc.batch_size, "event_time": inc.event_time,
                     **({"premise": prem.statement(), "premise_status": prem.status,
                         "why": L.why(prem)} if prem is not None else {})}))
        # 5. schema change ignored
        if inc.on_schema_change in ("", "ignore") and store is not None and d is not None \
                and d.ok:
            moved = _columns_moved(store, project.models[uid], d, project)
            if moved:
                added, removed, prev = moved
                out.append(_finding(
                    "incremental_schema_change_ignored", inc, e,
                    "the model's columns changed and `on_schema_change` ignores it",
                    (f"`{inc.name}` is incremental with `on_schema_change: "
                     f"{inc.on_schema_change or 'ignore (the default)'}`, and its columns changed "
                     f"since the last version assay kept"
                     + (f": added {', '.join(added)}" if added else "")
                     + (f"; removed {', '.join(removed)}" if removed else "")
                     + ". The existing table is not altered, so an added column never appears "
                       "and a removed one fills with NULL until a full refresh. Set "
                       "`on_schema_change: append_new_columns` (or `sync_all_columns`), or "
                       "run `--full-refresh` once."),
                    {"added": added, "removed": removed, "previous_version": prev[:12]}))
    return out


def _columns_moved(store, m, d, project):
    """(added, removed, previous checksum) against the newest earlier version `check` kept."""
    from ..parse import digest
    try:
        rows = store.con.execute(
            "select checksum, sql, first_seen from compiled_sql where model = ? "
            "order by first_seen desc", [m.name]).fetchall()
    except Exception:                                            # noqa: BLE001
        return None
    now = m.checksum
    prev = next((r for r in rows if r[0] != now), None)
    if prev is None:
        return None
    try:
        old = digest(prev[1], m.name, dialect=project.dialect)
    except TypeError:
        old = digest(prev[1], m.name)
    if not old.ok:
        return None
    a = [c.lower() for c in d.output_columns]
    b = [c.lower() for c in old.output_columns]
    if "*" in a or "*" in b:
        return None
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    return (added, removed, prev[0]) if (added or removed) else None


def model_rows(project, digests, findings) -> dict:
    """{uid: the incremental section of the model pane}, with what each G-D check flagged."""
    flags: dict = {}
    field_of = {"incremental_merge_without_key": "unique_key",
                "incremental_key_not_unique": "unique_key",
                "incremental_filter_without_lookback": "filter",
                "microbatch_without_lookback": "lookback",
                "incremental_schema_change_ignored": "on_schema_change"}
    for f in findings or []:
        k = field_of.get(getattr(f, "check", None) or (f.get("check") if isinstance(f, dict)
                                                       else None))
        if k:
            sub = f.subject if not isinstance(f, dict) else f.get("subject")
            fid = f.id if not isinstance(f, dict) else f.get("id")
            chk = f.check if not isinstance(f, dict) else f.get("check")
            flags.setdefault(sub, {}).setdefault(k, []).append({"id": fid, "check": chk})
    out = {}
    for uid, inc in read(project, digests).items():
        out[uid] = {**inc.as_dict(), "flags": flags.get(uid, {})}
    return out


def _json(o) -> str:
    return json.dumps(o, sort_keys=True, default=str)


def lateness_targets(project, digests, schema, entries) -> list:
    """[(Incremental, event column, arrival column, how, relation)] that can be measured."""
    by_uid = {e.uid: e for e in entries or []}
    out = []
    for uid, i in sorted(read(project, digests).items()):
        event = i.event_time if i.strategy == "microbatch" else i.filter_column
        if not event:
            continue
        arrival, how = arrival_column(i, by_uid.get(uid), schema)
        rel = (schema.relation.get(uid) or "")
        out.append((i, event, arrival, how, rel))
    return out


def measure(project, digests, schema, entries, store, run, dry_run: bool = False) -> list:
    """Count `max(arrival - event)` for each incremental model that has both, through the
    caller's `run(sql) -> probe.Result`. Returns one row per target, measured or why not."""
    from datetime import datetime, timezone
    store.con.execute(DDL_LATENESS)
    rows = []
    for i, event, arrival, how, rel in lateness_targets(project, digests, schema, entries):
        if not arrival:
            rows.append({"model": i.name, "event": event, "measured": False, "why": how})
            continue
        sql = lateness_sql(rel, event, arrival, getattr(project, "dialect", "duckdb"))
        if dry_run:
            rows.append({"model": i.name, "event": event, "arrival": arrival, "sql": sql,
                         "measured": False, "why": "dry run"})
            continue
        got = run(sql)
        if got.failed or not got.rows:
            rows.append({"model": i.name, "event": event, "arrival": arrival, "measured": False,
                         "why": got.why or "no rows came back"})
            continue
        vals = list(got.rows[0].values())
        try:
            late = float(got.rows[0].get("late", vals[0]) or 0)
            n = int(got.rows[0].get("n", vals[1] if len(vals) > 1 else 0) or 0)
        except (TypeError, ValueError, IndexError):
            rows.append({"model": i.name, "event": event, "arrival": arrival, "measured": False,
                         "why": f"could not read {got.rows[0]}"})
            continue
        store.con.execute("insert or replace into observed_lateness values (?,?,?,?,?,?,?)",
                          [rel.replace('"', "").lower(), event, arrival, late, n,
                           datetime.now(timezone.utc), "dbt-show"])
        rows.append({"model": i.name, "event": event, "arrival": arrival, "arrival_from": how,
                     "measured": True, "max_late_seconds": late, "late": _human(late),
                     "rows": n})
    return rows
