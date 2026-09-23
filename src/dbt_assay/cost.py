"""What the judged tier actually cost, read off the calls that were made.

*** THE UNIT WITH A PRICE IS THE CALL, AND EVERY TOTAL HERE GOES THROUGH IT. ***
`model_decisions` is one row per ANSWER and carries its call's token count on every one of them,
because a batch of eight questions about one state is one call and eight rows. Summing that column
therefore counts a batched call once per answer. It is not a small error: on the field store it
reads 101,163,351 tokens against 31,426,560 spent, and $4.25 against $1.32. Three separate figures
in an earlier spec came from it, and one of them was written down as the number to check the first
implementation against.

So nothing in this module sums `model_decisions.input_tokens`, and a test asserts the same of the
rest of the codebase.

*** A DOLLAR TOTAL IS BUILT FROM INTEGERS AND MULTIPLIED ONCE. ***
`sum(usd)` over a DOUBLE column depends on the order rows come back in, so the same store can
print two different lifetime totals on two runs. Tokens are integers and exact: they are summed
per RATE, multiplied once, and added in a fixed order. Two runs over one store print the same
number because they cannot do anything else.

*** AN ABSENT MEASUREMENT IS NEVER AN AVERAGE. ***
A call the provider returned no usage for holds NULL, is excluded from the total, and is counted
in the report. Output tokens are shown and never priced: Jev does not bill them, and multiplying
them by anything would be inventing a rate.
"""
from __future__ import annotations

from collections import defaultdict

# One call's facts, as every reader here wants them.
_CALLS = """
    select m.call_id, m.caller, m.model_name, m.input_tokens, m.output_tokens,
           m.usd_per_input_token, m.id_source,
           coalesce(min(d.decided_at), m.called_at) as at
    from model_calls m
    left join model_decisions d on d.call_id = m.call_id
    group by m.call_id, m.caller, m.model_name, m.input_tokens, m.output_tokens,
             m.usd_per_input_token, m.id_source, m.called_at
"""


def _families(store) -> dict:
    """{call_id: family}, or `(mixed)` when one call carried questions from several.

    A call's cost is one number and cannot be attributed twice. Where a batch spans families the
    honest answer is that this money is not attributable to one of them, said out loud, rather
    than a split nobody measured.
    """
    try:
        from .contracts import family_index, family_of
        index = family_index()
    except Exception:                                            # noqa: BLE001
        return {}
    rows = store.con.execute(
        "select call_id, question from model_decisions "
        "where call_id is not null and call_id <> '' group by 1, 2").fetchall()
    # The banks are read ONCE, by `family_index`, and the prefix rule still lives in `family_of`.
    # Resolving each of 2,289 distinct question ids against its own fresh disk read took 31
    # seconds, and reimplementing the split here is how three shipped questions came to resolve
    # to a neighbouring family.
    known: dict = {}
    seen: dict = defaultdict(set)
    for call_id, question in rows:
        if question not in known:
            try:
                known[question] = family_of(question, index)
            except Exception:                                    # noqa: BLE001
                known[question] = None
        seen[call_id].add(known[question] or "(unclaimed)")
    return {c: (next(iter(f)) if len(f) == 1 else "(mixed)") for c, f in seen.items()}


def _money(groups: dict) -> float:
    """{rate: tokens} -> dollars. Integers first, one multiply each, added in rate order."""
    return sum(tok * rate for rate, tok in sorted(groups.items()))


def ledger(store, since: str | None = None) -> dict:
    """The whole ledger: one pass, every breakdown, and what it could not measure."""
    store.con.execute("select 1 from model_calls limit 1")
    rows = store.con.execute(_CALLS).fetchall()
    if since:
        rows = [r for r in rows if r[7] is not None and str(r[7])[:10] >= since]
    fams = _families(store)

    by: dict = {"caller": defaultdict(lambda: defaultdict(int)),
                "family": defaultdict(lambda: defaultdict(int)),
                "day": defaultdict(lambda: defaultdict(int))}
    calls_by: dict = {"caller": defaultdict(int), "family": defaultdict(int),
                      "day": defaultdict(int)}
    total: dict = defaultdict(int)
    out_tokens, out_calls, no_usage = 0, 0, 0
    id_source: dict = defaultdict(int)
    rates: set = set()

    for call_id, caller, _model, tok, out_tok, rate, src, at in rows:
        id_source[src or "(unknown)"] += 1
        if out_tok is not None:
            out_tokens += int(out_tok)
            out_calls += 1
        keys = {"caller": caller or "(unknown)",
                "family": fams.get(call_id, "(no questions recorded)"),
                "day": str(at)[:10] if at is not None else "(undated)"}
        for axis, key in keys.items():
            calls_by[axis][key] += 1
        if tok is None:
            no_usage += 1
            continue
        r = rate if rate is not None else 0.0
        rates.add(r)
        total[r] += int(tok)
        for axis, key in keys.items():
            by[axis][key][r] += int(tok)

    def rollup(axis: str) -> list:
        out = [(k, calls_by[axis][k], sum(g.values()), _money(g)) for k, g in by[axis].items()]
        # a key with calls but no measured usage still appears, with zero tokens and no dollars
        out += [(k, n, 0, 0.0) for k, n in calls_by[axis].items() if k not in by[axis]]
        return sorted(out, key=lambda r: (-r[3], r[0]))

    return {
        "usd": _money(total),
        "input_tokens": sum(total.values()),
        "calls": len(rows),
        "output_tokens": out_tokens,
        "output_calls": out_calls,
        "calls_without_usage": no_usage,
        "rates": sorted(rates),
        "id_source": dict(id_source),
        "by_caller": rollup("caller"),
        "by_family": rollup("family"),
        "by_day": sorted(rollup("day"), key=lambda r: r[0], reverse=True),
        "since": since,
    }


# ===================================================================== the warehouse, not the model
#
# *** ONE TABLE FOR WHAT assay SPENT ON THINKING, ONE FOR WHAT IT SPENT ON THE WAREHOUSE. ***
# `model_calls` above prices a call to Jev. `warehouse_calls` prices a statement sent through the
# project's own dbt. The symmetry is the point: the same tab renders both, and a user on BigQuery
# can be told what a sweep will cost before it runs.
#
# *** THE COST MODEL IS AN ENGINE PROPERTY AND assay HAS TO KNOW WHICH ONE IT IS UNDER. ***
# `probe.build_sql` batches every candidate column into one statement, which is one scan on DuckDB
# and one warehouse-second on Snowflake and therefore right on both. On BigQuery the bill is the
# bytes of the columns touched, so batching twenty columns bills for twenty columns and saves
# nothing. Neither design is wrong; what would be wrong is a single number printed as though the
# engine did not matter.
#
# *** A NUMBER assay CANNOT JUSTIFY IS WORSE THAN NO NUMBER. ***
# Published rates move. The rate lives in `audit.yml` and the NAME of the rate that produced a
# figure is stored on the row, so a price change cannot silently rewrite what was already spent --
# the argument `model_calls.usd_per_input_token` already makes.

from dataclasses import dataclass

# *** VENDOR-PUBLISHED SIZES, NOT MEASUREMENTS. ***
# BigQuery prices a scan as rows x sum of the declared column widths, and publishes the width of
# every type (cloud.google.com/bigquery/pricing, "data size calculation"). That is what makes an
# estimate possible without a credential: assay builds the SQL, so it knows the exact column list,
# and the manifest declares the types.
_BIGQUERY_WIDTHS = {
    "int64": 8, "integer": 8, "int": 8, "smallint": 8, "bigint": 8, "tinyint": 8, "byteint": 8,
    "float64": 8, "float": 8, "double": 8, "real": 8,
    "numeric": 16, "decimal": 16, "bignumeric": 32, "bigdecimal": 32,
    "bool": 1, "boolean": 1,
    "date": 8, "datetime": 8, "time": 8, "timestamp": 8,
    "interval": 16, "geography": 16,
}

# Everything else. Snowflake and DuckDB do not bill on bytes, so these size a scan rather than
# price one, and the dollars come from wall time and a credit rate instead.
_GENERIC_WIDTHS = {
    "boolean": 1, "bool": 1, "tinyint": 1, "utinyint": 1,
    "smallint": 2, "usmallint": 2,
    "int": 4, "integer": 4, "uinteger": 4,
    "bigint": 8, "ubigint": 8, "hugeint": 16, "int64": 8, "number": 16,
    "float": 4, "real": 4, "float4": 4, "double": 8, "float8": 8, "float64": 8,
    "decimal": 16, "numeric": 16,
    "date": 4, "time": 8, "timestamp": 8, "timestamptz": 8, "timestamp_ntz": 8,
    "timestamp_ltz": 8, "timestamp_tz": 8, "datetime": 8,
    "uuid": 16, "interval": 16,
}

# *** A VARIABLE-WIDTH TYPE HAS NO WIDTH, AND THIS IS THE GUESS THAT SAYS SO. ***
# BigQuery bills a STRING as 2 bytes plus its UTF-8 length, which is a property of the DATA and not
# of the schema. assay has no credential and cannot measure it, so it assumes a nominal length,
# says so in `estimate_basis`, and lets `audit.yml` move the number. It is deliberately not small:
# an estimate that understates a bill is the one that costs somebody money.
NOMINAL_VARIABLE_BYTES = 32
_VARIABLE = ("string", "varchar", "char", "text", "bytes", "blob", "json", "variant", "object",
             "bpchar", "nvarchar", "character")
# Nothing here can be sized from its name alone, so a relation containing one is not estimated.
_UNSIZEABLE = ("array", "struct", "map", "record", "union", "list", "geometry")
USD_PER_TB = 1_000_000_000_000


def normalize_type(declared: str) -> str:
    """`DECIMAL(18,2)` -> `decimal`, `ARRAY<STRING>` -> `array`. Lowercase, no parameters."""
    t = (declared or "").strip().lower()
    for cut in ("(", "<", "["):
        if cut in t:
            t = t.split(cut, 1)[0]
    return t.strip()


def width_of(declared: str, dialect: str = "duckdb",
             nominal_variable_bytes: int = NOMINAL_VARIABLE_BYTES) -> int | None:
    """Nominal bytes for one value of this type, or None when the name cannot say.

    None is the answer for a type assay does not recognise and for a nested type, and it is not a
    zero. A caller that treated it as one would print a smaller bill than the real one.
    """
    t = normalize_type(declared)
    if not t or t in _UNSIZEABLE:
        return None
    if t in _VARIABLE:
        # BigQuery's two-byte length prefix. Elsewhere it is noise against a 32-byte nominal.
        return nominal_variable_bytes + (2 if dialect == "bigquery" else 0)
    table = _BIGQUERY_WIDTHS if dialect == "bigquery" else _GENERIC_WIDTHS
    return table.get(t)


def declared_types(project, schema) -> dict:
    """{relation_lower: {column_lower: declared type}}, from the catalog then the manifest.

    *** THE CATALOG IS WHAT THE WAREHOUSE SAID; THE MANIFEST IS WHAT SOMEBODY WROTE DOWN. ***
    `catalog.json` is generated by `dbt docs generate` against the real warehouse, so it wins where
    it exists. A project that has never run it gets whatever its YAML declares, which is usually
    nothing -- and then no bytes are estimated, which is the honest outcome rather than a number
    built out of an empty dict.
    """
    out: dict = {}

    def put(rel: str, cols: dict) -> None:
        if not rel or not cols:
            return
        out.setdefault(rel.lower(), {}).update(cols)

    raw = getattr(project, "raw", {}) or {}
    for bucket in ("nodes", "sources"):
        for uid, node in (raw.get(bucket) or {}).items():
            rel = (getattr(schema, "relation", {}) or {}).get(uid)
            declared = {str(c.get("name") or n).lower(): str(c.get("data_type") or "")
                        for n, c in (node.get("columns") or {}).items()
                        if (c or {}).get("data_type")}
            put(rel, declared)
    catalog = getattr(schema, "catalog", None) or {}
    for bucket in ("nodes", "sources"):
        for uid, entry in (catalog.get(bucket) or {}).items():
            rel = (getattr(schema, "relation", {}) or {}).get(uid)
            observed = {str(c.get("name") or n).lower(): str(c.get("type") or "")
                        for n, c in (entry.get("columns") or {}).items()
                        if (c or {}).get("type")}
            put(rel, observed)
    return out


@dataclass
class RateCard:
    """What an engine charges, and the NAME of the thing that said so.

    The name is stored on every row it prices. Without it, `usd_estimated` is a number with no
    provenance, and the first time a published rate moves every historical total moves with it.
    """
    engine: str = "duckdb"
    name: str = ""
    usd_per_tb_scanned: float | None = None
    usd_per_credit: float | None = None
    credits_per_hour: float | None = None
    nominal_variable_bytes: int = NOMINAL_VARIABLE_BYTES

    @classmethod
    def from_config(cls, cost_cfg: dict | None, dialect: str = "duckdb") -> RateCard:
        c = dict(cost_cfg or {})
        engine = str(c.get("engine") or dialect or "duckdb").lower()
        name = str(c.get("rate_card") or "")
        tb = c.get("usd_per_tb_scanned")
        credit = c.get("usd_per_credit")
        per_hour = c.get("credits_per_hour")
        if not name and engine == "duckdb" and tb is None and credit is None:
            # *** A LOCAL DUCKDB FILE BILLS NOTHING, AND THE LABEL CARRIES THE ASSUMPTION. ***
            # MotherDuck speaks the same dialect and does bill. The zero is named rather than
            # anonymous, so a reader can see which claim produced it and override `cost.engine`.
            name = "duckdb.local"
        return cls(engine=engine, name=name,
                   usd_per_tb_scanned=None if tb is None else float(tb),
                   usd_per_credit=None if credit is None else float(credit),
                   credits_per_hour=None if per_hour is None else float(per_hour),
                   nominal_variable_bytes=int(c.get("nominal_string_bytes")
                                              or NOMINAL_VARIABLE_BYTES))

    def price(self, bytes_estimated: int | None, exec_ms: int | None = None) -> float | None:
        """Dollars, or None when nothing configured can justify a number.

        None is not zero. A BigQuery user who has not set a rate gets no dollar figure at all,
        because the alternative is assay inventing one and printing it next to a real measurement.

        *** `exec_ms` IS THE ENGINE'S OWN TIME AND THE SUBPROCESS CLOCK IS NOT ALLOWED NEAR IT. ***
        Measured on a production sweep: 18,000ms of wall clock around a statement the warehouse
        ran in 30ms, because 99.7% of a `dbt show` is dbt starting up. Snowflake bills warehouse
        seconds, so a credit estimate built on the wall clock is wrong by about 600x -- and wrong
        in the direction that looks plausible, which is the direction nobody checks. With no
        engine time, a time-priced card returns None and the surfaces print nothing.
        """
        if self.name == "duckdb.local" and self.usd_per_tb_scanned is None:
            return 0.0
        if self.usd_per_tb_scanned is not None and bytes_estimated is not None:
            return (bytes_estimated / USD_PER_TB) * self.usd_per_tb_scanned
        # Snowflake bills the warehouse being awake, so engine time IS the measurement here.
        if self.usd_per_credit is not None and self.credits_per_hour and exec_ms is not None:
            return (exec_ms / 3_600_000.0) * self.credits_per_hour * self.usd_per_credit
        return None


def estimate(columns: list[str], types: dict, rows: int | None,
             rate: RateCard) -> tuple[int | None, str]:
    """(bytes_estimated, estimate_basis) for one statement. Never a partial number.

    *** A COLUMN WHOSE TYPE IS UNKNOWN MAKES THE WHOLE ESTIMATE UNKNOWN. ***
    Summing the columns it could size and calling the result an estimate would understate the scan
    by exactly the part it could not see, and nothing on the row would say so. That is the same
    failure as a column mixing measured and guessed numbers, which is why `estimate_basis` exists
    and why it is never optional.
    """
    if rows is None or not columns:
        return None, "unknown"
    total = 0
    for c in columns:
        w = width_of(types.get(str(c).lower(), ""), rate.engine, rate.nominal_variable_bytes)
        if w is None:
            return None, "unknown"
        total += w
    return int(total) * int(rows), "declared_types"


def warehouse_ledger(store, since: str | None = None, caller: str | None = None) -> dict:
    """What the warehouse cost, from `warehouse_calls`. The mirror of `ledger` above.

    *** SUMMED THE SAME WAY, FOR THE SAME REASON. ***
    Bytes are integers and exact. Dollars are a DOUBLE and `sum(usd)` over one depends on the
    order the rows come back in, so the same store can print two different totals on two runs.
    The dollars are summed in a fixed order here, which is what makes two runs agree.

    A failed statement is counted and kept out of the money: it is a statement that did not do
    what it was sent to do, and averaging it into a cost per relation would hide it.
    """
    from . import probe as probe_mod
    store.con.execute(probe_mod.DDL)
    where, args = [], []
    if since:
        where.append("cast(called_at as date) >= ?")
        args.append(since)
    if caller:
        where.append("caller = ?")
        args.append(caller)
    sql = ("select caller, statement_kind, relation, bytes_estimated, bytes_measured, "
           "usd_estimated, wall_ms, failed, estimate_basis, rate_card, "
           "cast(called_at as date) as day, exec_ms from warehouse_calls")
    if where:
        sql += " where " + " and ".join(where)
    rows = store.con.execute(sql, args).fetchall()

    by_caller: dict = defaultdict(lambda: [0, 0, 0.0, 0, 0])      # calls, bytes, usd, ms, failed
    by_kind: dict = defaultdict(lambda: [0, 0, 0.0, 0, 0])
    by_day: dict = defaultdict(lambda: [0, 0, 0.0, 0, 0])
    total_bytes = total_ms = failed = unestimated = 0
    # *** THE TWO CLOCKS ARE NEVER ADDED TOGETHER AND NEVER PRINTED UNDER ONE NAME. ***
    # Measured on the baseline sweep: 5,256 seconds of subprocess wall clock against about 20
    # seconds of actual querying -- 263x. Bytes got the honesty rule and time did not, and it is
    # the number that becomes a Snowflake bill the moment somebody sets `cost.engine`.
    total_exec_ms = 0
    timed = 0
    measured = 0
    usd_terms: list = []
    bases: dict = defaultdict(int)
    cards: set = set()

    for caller_, kind, _rel, est, meas, usd, ms, bad, basis, card, day, ex in rows:
        if ex is not None:
            total_exec_ms += int(ex)
            timed += 1
        bases[basis or "unknown"] += 1
        if card:
            cards.add(card)
        if meas is not None:
            measured += 1
        if est is None:
            unestimated += 1
        total_bytes += int(est or 0)
        total_ms += int(ms or 0)
        if bad:
            failed += 1
        else:
            usd_terms.append(float(usd or 0.0))
        for bucket, key in ((by_caller, caller_ or "(unattributed)"),
                            (by_kind, kind or "(unkinded)"), (by_day, str(day))):
            b = bucket[key]
            b[0] += 1
            b[1] += int(est or 0)
            b[2] += float(usd or 0.0) if not bad else 0.0
            b[3] += int(ms or 0)
            b[4] += 1 if bad else 0

    def rollup(bucket: dict, by_key: bool = False) -> list:
        out = [(k, v[0], v[1], round(v[2], 10), v[3], v[4]) for k, v in bucket.items()]
        return sorted(out, key=(lambda r: r[0]) if by_key else (lambda r: (-r[2], r[0])),
                      reverse=by_key)

    return {
        "calls": len(rows),
        "bytes_estimated": total_bytes or None,
        "bytes_measured_calls": measured,
        "usd": sum(sorted(usd_terms)) if usd_terms else None,
        # The subprocess round trip. Reported so a slow sweep is visible, NEVER as warehouse time.
        "wall_ms": total_ms,
        # What the engine itself took, summed over the statements that reported one.
        "exec_ms": total_exec_ms if timed else None,
        "timed_calls": timed,
        "failed": failed,
        "unestimated": unestimated,
        "estimate_basis": dict(bases),
        "rate_cards": sorted(cards),
        "by_caller": rollup(by_caller),
        "by_kind": rollup(by_kind),
        "by_day": rollup(by_day, by_key=True),
        "since": since,
    }
