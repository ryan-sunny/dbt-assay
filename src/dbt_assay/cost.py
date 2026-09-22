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
