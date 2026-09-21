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
    "adjudications": "One row per human verdict. The labelled set, manufactured by use; nothing "
                     "gates a build until a question has enough of these.",
    "observed_keys": "One row per (relation, column) the probe counted, with the row count and the "
                     "day it was observed. Unique in that day's data is not a constraint.",
    "edge_facts": "One row per DAG edge: what the parent offered, what the child took, what it "
                  "dropped, and what it joined on.",
}

COLUMN_DOCS = {
    "source": "declared (a human wrote it) | observed (counted) | derived (code) | judged (a model)",
    "confidence": "For a choice or score, distribution concentration. A noul has none, on purpose.",
    "probabilities": "The FULL distribution, not just the winner, so a close call stays visible.",
    "state_hash": "A hash of exactly what was sent. A cache hit whose state moved is a miss.",
    "prompt_version": "Bumped whenever a question's wording changes; a reworded question is a "
                      "different question.",
    "model_version": "What ANSWERED, never what was asked for.",
    "verdict": "agree | disagree | unclear",
    "row_count": "How many rows the probe saw, so an observation can be weighed.",
    "observed_at": "When it was counted. An observation ages; a declaration does not.",
    "weight": "base severity lifted by reach. Arithmetic over the DAG, never a judgment.",
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


# Types for the generated seed schema. Everything not named here is varchar on purpose: a seed is
# a transport format, and a column that arrives as text and is cast where it is used cannot be
# mis-sniffed into a number on one machine and a string on another.
_SEED_TYPE = {
    "weight": "double", "confidence": "double", "base": "double",
    "descendants": "integer", "marts": "integer", "input_tokens": "integer",
    "models": "integer", "sources": "integer", "tests": "integer", "edges": "integer",
    "readable": "integer", "unreadable": "integer", "parse_ok": "integer",
    "parse_failed": "integer", "rows": "integer", "n": "integer",
    "decided_at": "timestamp", "started_at": "timestamp", "decided_on": "timestamp",
}


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
            lines.append(f"        {c}: {_SEED_TYPE.get(c, 'varchar')}")
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
