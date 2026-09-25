"""Put assay's knowledge IN the warehouse, where a dbt model can join to it.

*** A REPORT IS READ ONCE. A TABLE ACCRUES. ***
The output being a dataset is the whole point: a probability per question per model per commit is
something you can chart, diff, and join against your own marts. "Which defect class does this team
keep reintroducing" stops being an impression and becomes a select.

*** SEEDS, BECAUSE THEY WORK EVERYWHERE. ***
CSV seeds land in any warehouse dbt can reach, on any adapter, with no external-table setup, no
staging bucket and no per-adapter code. `dbt seed --select assay` and the knowledge is a real
relation. Parquet is offered too for anyone already reading external files, but it is the
adapter-specific path and is not the default.

The generated schema.yml documents every column, so assay's own tables arrive documented -- which
is the least it can do, given what it says about undocumented models.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

PREFIX = "assay_"

# Only the durable tables. A run's scratch is not knowledge.
TABLES = {
    "inventory": "One row per (model, column, property). What each model IS, with the source of "
                 "every cell: declared, observed, derived or judged.",
    "findings": "One row per (run, check, subject). Contradictions found, structural and judged, "
                "with the blast radius that ranks them.",
    "model_decisions": "One row per ANSWER. The full probability distribution, the state hash, and "
                       "the prompt and model versions that produced it.",
    "adjudications": "One row per human verdict. The labeled set, manufactured by use; nothing "
                     "gates a build until a question has enough of these.",
    "observed_keys": "One row per (relation, column) the probe counted, with the row count and the "
                     "day it was observed. Unique in that day's data is not a constraint.",
    "edge_facts": "One row per DAG edge: what the parent offered, what the child took, what it "
                  "dropped, and what it joined on.",
    # *** WITHOUT THIS, NOTHING DOWNSTREAM CAN TELL WHICH RUN IS THE CURRENT ONE. ***
    # Every other table here is keyed by `run_id` and carries no clock, so a model asking "what is
    # our debt NOW" had to guess. The shipped example guessed with `order by count(*) desc`, and
    # reported from the field: three runs tied at 349 findings, so the guess was an arbitrary pick.
    # Adding `run_id` to that order makes it stable and makes it wrong FOREVER, because the release
    # that made findings honest also made them fewer -- 349 became 234, and a smaller run can never
    # win. A count is not a clock. This is the clock.
    "runs": "One row per assay run: when it started, which assay and which dbt produced it, and "
            "what it could and could not read. The only table here that says which run is CURRENT "
            "-- order by `started_at`, never by how much a run happened to find. A run with a "
            "`scope` was narrowed (`check --check`) and is history, never the current one.",
    # *** WHAT THE FINDINGS REST ON, AND WHAT IS PROVEN. *** So a dashboard or a Dagster asset
    # can ask, in plain SQL, which marts are proven and which premises broke.
    "premises": "One row per (run, premise): a statement about the data something here leans on "
                "(a key unique, a column never null, rows arriving within a lookback), its status "
                "(broken / holding / unchecked / assumed / unknown), since when, and its evidence.",
    "premise_uses": "One row per (run, premise, dependent): the grain, the held-back finding or "
                    "the proof that rests on the premise.",
    "proofs": "One row per (model, property, author): a certificate Lean checked, the rule it "
              "applies, the premises it assumes, or what Lean could not close. Its guarantee is "
              "live: join `premises` for whether they still hold.",
    "conformance": "One row per (construct, engine, version): whether the engine does what "
                   "assay's meaning of SQL says, measured on identical inputs.",
    "claim_checks": "One row per (model, property): whether a proven certificate's claim held "
                    "when the model ran on inputs meeting its premises (holds, contradicted, "
                    "unchecked), for the model file it names.",
}

COLUMN_DOCS = {
    "source": "declared (a human wrote it) | observed (counted) | derived (code) | judged (a model)",
    "confidence": "For a choice or score, distribution concentration. A noul has none, on purpose.",
    "probabilities": "The FULL distribution, not just the winner, so a close call stays visible.",
    "state_hash": "A hash of exactly what was sent. A cache hit whose state moved is a miss.",
    "prompt_version": "Bumped whenever a question's wording changes; a reworded question is a "
                      "different question.",
    "model_version": "What ANSWERED, never what was asked for.",
    "verdict": "agree | disagree | unclear | accept",
    "row_count": "How many rows the probe saw, so an observation can be weighed.",
    "observed_at": "When it was counted. An observation ages; a declaration does not.",
    "weight": "base severity lifted by reach. Arithmetic over the DAG, never a judgment.",
    "scope": "NULL for a full run. Otherwise what the run was narrowed to; `(inferred)` when it "
             "was reconstructed for a run written before the column existed.",
}


@dataclass
class Exported:
    path: Path
    table: str
    rows: int


def _rows(store, table: str):
    try:
        cur = store.con.execute(f"select * from {table}")
    except Exception:                                                   # noqa: BLE001
        return [], []
    return [d[0] for d in cur.description], cur.fetchall()


def to_seeds(store, directory: str | Path) -> list[Exported]:
    """CSV seeds. `dbt seed` then loads them into whatever warehouse the project points at."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for table in TABLES:
        cols, rows = _rows(store, table)
        if not cols:
            continue
        p = d / f"{PREFIX}{table}.csv"
        with p.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow(["" if v is None else v for v in r])
        out.append(Exported(p, table, len(rows)))
    return out


def to_parquet(store, directory: str | Path) -> list[Exported]:
    """The adapter-specific path, for projects already reading external files."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for table in TABLES:
        cols, _ = _rows(store, table)
        if not cols:
            continue
        p = d / f"{PREFIX}{table}.parquet"
        store.con.execute(f"copy (select * from {table}) to '{p}' (format parquet)")
        n = store.con.execute(f"select count(*) from {table}").fetchone()[0]
        out.append(Exported(p, table, n))
    return out


# *** THE DECLARATION, THE STORE AND THE QUERY WERE THREE SPELLINGS OF ONE FACT. ***
# Reported from the field, about this tool. `edge_facts.dropped` is INTEGER in the store, this map
# did not name it, so `assay.yml` declared it `varchar`, dbt honoured the declaration, the seeded
# column came back text, and assay's OWN shipped query died on `sum(dropped)`. Any two of those
# three are fine together; all three is broken.
#
# It also produced a wrong number that looked right: `len()` over a VARCHAR column reported a model
# dropping "65 columns" when the character count was 65 and the real answer, read from the store
# where the type survives, is 794 over 36 hops.
#
# So the types are DERIVED from the store, which knows them exactly, and this map is only the
# override for the few the store cannot settle. A hand-written list of every numeric column is a
# second copy of the schema, and seven columns had already drifted out of it.
_SEED_TYPE = {
    "weight": "double", "confidence": "double", "base": "double",
    "descendants": "integer", "marts": "integer", "input_tokens": "integer",
    "models": "integer", "sources": "integer", "tests": "integer", "edges": "integer",
    "readable": "integer", "unreadable": "integer", "parse_ok": "integer",
    "parse_failed": "integer", "rows": "integer", "n": "integer",
    "decided_at": "timestamp", "started_at": "timestamp", "decided_on": "timestamp",
}


# duckdb's own type names -> what a seed should be declared as. Anything not matched stays
# varchar, which keeps the original argument intact: a column whose type is not obviously a number
# or a time is transported as text rather than guessed at.
_DUCK_TO_SEED = (("bigint", "bigint"), ("hugeint", "bigint"), ("integer", "integer"),
                 ("smallint", "integer"), ("tinyint", "integer"), ("double", "double"),
                 ("float", "double"), ("real", "double"), ("decimal", "double"),
                 ("timestamp", "timestamp"), ("date", "date"), ("boolean", "boolean"))


def seed_type(store, table: str, column: str) -> str:
    """What this column should be declared as in the seed schema.

    The store first, because it holds the truth. `_SEED_TYPE` second, for the handful a caller
    wants to override. varchar last, for anything neither settles.
    """
    try:
        for c, typ, *_ in store.con.execute(f"describe {table}").fetchall():
            if c != column:
                continue
            low = str(typ).lower()
            for needle, seed in _DUCK_TO_SEED:
                if needle in low:
                    return seed
            return _SEED_TYPE.get(column, "varchar")
    except Exception:                                                   # noqa: BLE001
        # A store that cannot describe the table still exports; the declaration falls back rather
        # than the whole command failing over a schema query.
        return _SEED_TYPE.get(column, "varchar")
    return _SEED_TYPE.get(column, "varchar")


def schema_yml(store, exported: list[Exported]) -> str:
    """Document assay's own tables. It has opinions about undocumented models."""
    lines = ["version: 2", "", "seeds:"]
    for e in exported:
        cols, _ = _rows(store, e.table)
        lines.append(f"  - name: {PREFIX}{e.table}")
        lines.append("    description: >")
        lines.append(f"      {TABLES[e.table]}")
        # *** PIN EVERY TYPE, BECAUSE THE SNIFFER FAILS ON OUR OWN TEXT. ***
        # These tables carry the reasoning somebody wrote when they ruled, and a ruling's `note` is
        # prose full of commas, quotes and colons. dbt-duckdb sniffs a seed's dialect, and on a real
        # store it gives up: "It was not possible to automatically detect the CSV parsing dialect".
        # The files are well formed -- Python's csv module reads them with zero ragged rows -- so
        # this is the sniffer guessing, not the export being wrong.
        #
        # IT ONLY BREAKS AT SCALE, which is why it shipped. Eight rulings seeded fine; a hundred and
        # seven did not. `dbt seed` is the last line this command prints, so the instruction has to
        # work on a store somebody has actually used.
        lines.append("    config:")
        lines.append("      column_types:")
        for c in cols:
            lines.append(f"        {c}: {seed_type(store, e.table, c)}")
        lines.append("    columns:")
        for c in cols:
            doc = COLUMN_DOCS.get(c)
            lines.append(f"      - name: {c}")
            if doc:
                lines.append(f"        description: \"{doc}\"")
    return "\n".join(lines) + "\n"


EXAMPLE_SQL = '''\
-- Example: which defect class does this project keep reintroducing?
-- assay's findings are an ordinary relation, so this is just a query.
--
-- It GROUPS BY check_name rather than bucketing into named classes with a CASE, on purpose. A
-- hand-written class list is a second copy of the check list and the second copy is what drifts:
-- reported from the field, a debt model built that way had 219 of 349 rows land in `other` after
-- one release added a check, so 63% of the table said nothing. If you do want classes, add an
-- `else check_name` branch so a new check names itself instead of disappearing.
select
    check_name,
    count(*)                                   as findings,
    count(distinct subject_name)               as models_affected,
    round(avg(weight), 1)                      as mean_weight,
    max(marts)                                 as worst_blast_radius
from {{ ref('assay_findings') }}
group by 1
order by findings desc
'''

# *** QUERIES, NOT MODELS, AND THE REASON IS IN YOUR OWN WAREHOUSE. ***
# Reported from the field: 80% of what `export` writes had no reader -- `model_decisions`,
# `edge_facts` and `observed_keys` loaded on every build and consumed by nothing. The obvious fix
# is to ship models so the relations have readers by construction, and it is the wrong one: on
# that same warehouse an installed package's 30 models are every one of the unreadable models and
# carry 541 columns of unknown provenance. Shipping models means shipping that to somebody else.
#
# So these are queries. Copy one into your project if it earns its place there; nothing enters
# your DAG because assay put it there. Each answers a question the relations can already answer
# and nothing else in the tool surfaces.
EXTRA_SQL = {
    "assay_uncertainty": '''\
-- Which high-reach models rest on answers nobody is sure of?
--
-- `effectiveness` reports agreement per family. It cannot tell you that a model fourteen marts
-- read is carrying six answers that all came back under the gate. Confidence times reach is the
-- ranking, and both numbers are already in the seeds.
--
-- NOTE ON `confidence`: a `choice` stores distribution concentration there. A `noul` stores NULL
-- on purpose, because its ANSWER is the probability. Reading only this column on a noul family
-- finds nothing and concludes the question was never asked.
with live as (
    select decision_key, question, answer, confidence, kind,
           row_number() over (partition by decision_key, question
                              order by decided_at desc) as rn
    from {{ ref('assay_model_decisions') }}
)
-- A decision key is `<model uid>` for a per-model question and `<model uid>::<family>::<id>`
-- for the families that ask per claim or per hop. Matching the bare uid finds only the first
-- kind, which on a real store is a small minority -- this returned ZERO rows before the split.
--
-- LIMIT: `marts` lives only on findings in this export, so a model with no finding has no reach
-- here and is absent. That is a gap in what is exported, not a statement that the model is fine.
, keyed as (
    select split_part(decision_key, '::', 1) as model_uid, confidence
    from live where rn = 1 and confidence is not null
),
reach as (
    select subject, subject_name, max(marts) as marts
    from {{ ref('assay_findings') }} group by 1, 2
)
select
    r.subject_name                                        as model,
    r.marts                                               as marts,
    count(*)                                              as answers,
    round(avg(k.confidence), 2)                           as mean_confidence,
    sum(case when k.confidence < 0.6 then 1 else 0 end)   as under_the_gate
from keyed k
join reach r on r.subject = k.model_uid
group by 1, 2
order by under_the_gate desc, marts desc
''',

    "assay_join_surface": '''\
-- Which models join on the most DIFFERENT keysets, and what do they drop?
--
-- `traverse` shows one model's hops. Nothing sums them. A model reached on eleven distinct
-- keysets over thirty-six hops is carrying a grain risk that no single-hop check can see, and
-- this is exact, off the DAG, free.
select
    child_name                                     as model,
    count(*)                                       as hops,
    count(distinct joined_on)                      as distinct_keysets,
    sum(dropped)                                   as columns_dropped
from {{ ref('assay_edge_facts') }}
where run_id = (select run_id from {{ ref('assay_runs') }}
                where run_id in (select run_id from {{ ref('assay_edge_facts') }})
                order by started_at desc, run_id desc limit 1)
group by 1
order by distinct_keysets desc, hops desc
''',
}


# --------------------------------------------------------------------------------- import

@dataclass
class Imported:
    table: str
    path: Path
    in_file: int
    added: int
    ignored_columns: list


def from_dir(store, directory: str | Path, tables: list[str] | None = None) -> list[Imported]:
    """Load what `to_seeds` or `to_parquet` wrote back into a store. The inverse of export.

    *** THE VERDICTS WERE IN GIT, AND NOTHING COULD READ THEM BACK. ***
    `export` wrote every table as seeds and the field project committed them -- 145 KB of verdicts
    under `transform/seeds/assay/` -- and a fresh checkout still started from an empty store,
    cleared no `min_adjudications` floor, and could never gate. Export was one-way.

    *** ADD, NEVER OVERWRITE, AND MATCH BY NAME. ***
    A row the store already holds is left exactly as it is: the store's own history wins, and an
    import run twice adds nothing the second time. Columns are matched by NAME, because a CSV
    written by an older assay has fewer columns and a positional load writes one field into
    another. Every value is read as text and cast to the store's own type, and the empty string
    `to_seeds` writes for NULL becomes NULL again.
    """
    d = Path(directory)
    store.con.execute("select 1")
    out = []
    for table in tables or list(TABLES):
        if table not in TABLES:
            raise ValueError(f"`{table}` is not a table assay exports. Tables: {sorted(TABLES)}")
        csv_p, pq_p = d / f"{PREFIX}{table}.csv", d / f"{PREFIX}{table}.parquet"
        src = csv_p if csv_p.exists() else pq_p if pq_p.exists() else None
        if src is None:
            continue
        have = store.con.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_name = ? order by ordinal_position", [table]).fetchall()
        if not have:
            continue
        reader = (f"read_csv('{src}', header = true, all_varchar = true)" if src.suffix == ".csv"
                  else f"(select * from read_parquet('{src}'))")
        file_cols = [r[0] for r in store.con.execute(f"describe select * from {reader}")
                     .fetchall()]
        types = dict(have)
        cols = [c for c in file_cols if c in types]
        if not cols:
            continue
        casts = ", ".join(f"try_cast(nullif(cast(\"{c}\" as varchar), '') as {types[c]}) "
                          f"as \"{c}\"" for c in cols)
        n_file = store.con.execute(f"select count(*) from {reader}").fetchone()[0]
        before = store.con.execute(f"select count(*) from {table}").fetchone()[0]
        names = ", ".join(f'"{c}"' for c in cols)
        store.con.execute(
            f"insert or ignore into {table} ({names}) "
            f"select * from (select {casts} from {reader}) "
            f"except select {names} from {table}")
        after = store.con.execute(f"select count(*) from {table}").fetchone()[0]
        out.append(Imported(table, src, n_file, after - before,
                            [c for c in file_cols if c not in types]))
    return out
