"""Findings as DATA, in DuckDB. Not a report.

*** THE OUTPUT IS A DATASET, WHICH IS THE POINT. ***
A report is read once. A table accrues. One row per (run, check, subject) means a probability or a
count can be trended across commits, a defect class can be counted across the project, and "which
mistake does this team keep making" becomes a query rather than an impression.

Coverage is stored beside the findings ON PURPOSE. A run that could only read 295 of 357 models has
not audited the project, and a findings count divorced from its denominator is the same lie as a
guard that scanned nothing and passed.
"""
from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .jev import DDL as JEV_DDL

DDL = JEV_DDL + """
create table if not exists runs (
    run_id       varchar primary key,
    started_at   timestamp,
    project      varchar,
    dbt_version  varchar,
    target_dir   varchar,
    assay_version varchar,
    host         varchar,
    models       integer,
    sources      integer,
    tests        integer,
    edges        integer,
    readable     integer,
    unreadable   integer,
    parse_ok     integer,
    parse_failed integer
);
create table if not exists findings (
    run_id       varchar,
    check_name   varchar,
    subject      varchar,
    subject_name varchar,
    file         varchar,
    summary      varchar,
    detail       varchar,
    base         integer,
    weight       double,
    descendants  integer,
    marts        integer,
    evidence     varchar,
    primary key (run_id, check_name, subject, summary)
);
create table if not exists adjudications (
    -- *** THE LABELLED SET, MANUFACTURED BY USE. ***
    -- Every human verdict recorded here is one row of evidence about a QUESTION, not just about a
    -- finding. It is the only thing that ever earns a question the right to fail a build, which is
    -- why config refuses to gate below min_adjudications. A tool without this loop ships flag-only
    -- forever and gets muted.
    subject      varchar,     -- model unique_id, or model::column
    question     varchar,     -- the question id, e.g. role__amount
    family       varchar,     -- the bank entry, e.g. column_role
    answered     varchar,     -- what assay said
    verdict      varchar,     -- agree | disagree | unclear
    correction   varchar,     -- what it should have been, when known
    note         varchar,
    decided_by   varchar,
    decided_at   timestamp,
    primary key (subject, question)
);
create table if not exists edge_facts (
    run_id varchar, parent varchar, child varchar, parent_name varchar, child_name varchar,
    available integer, carried integer, dropped integer, joined_on varchar, dropped_cols varchar,
    primary key (run_id, parent, child)
);
create table if not exists unreadable (
    run_id varchar, subject varchar, subject_name varchar, file varchar, reason varchar,
    primary key (run_id, subject)
);
"""


class Store:
    def __init__(self, path: str | Path = "assay.duckdb"):
        self.path = str(path)
        self.con = duckdb.connect(self.path)
        self.con.execute(DDL)

    def close(self) -> None:
        self.con.close()

    def write_run(self, run_id: str, project, coverage: dict, parse_ok: int, parse_failed: int,
                  target_dir: str, version: str) -> None:
        self.con.execute(
            """insert or replace into runs values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [run_id, datetime.now(timezone.utc), project.project_name, project.dbt_version,
             target_dir, version, platform.node(),
             coverage["models"], coverage["sources"], coverage["tests"], coverage["edges"],
             coverage["readable"], coverage["unreadable"], parse_ok, parse_failed])

    def write_findings(self, run_id: str, findings) -> None:
        rows = [[run_id, f.check, f.subject, f.subject_name, f.file, f.summary, f.detail,
                 f.base, f.weight, f.descendants, f.marts, json.dumps(f.evidence, default=str)]
                for f in findings]
        if rows:
            self.con.executemany(
                "insert or replace into findings values (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def write_edge_facts(self, run_id: str, facts) -> None:
        rows = [[run_id, f.parent, f.child, f.parent_name, f.child_name,
                 len(f.available), len(f.carried), len(f.dropped),
                 json.dumps(f.joined_on), json.dumps(f.dropped[:40])] for f in facts]
        if rows:
            self.con.executemany(
                "insert or replace into edge_facts values (?,?,?,?,?,?,?,?,?,?)", rows)

    def write_unreadable(self, run_id: str, rows: list[tuple]) -> None:
        if rows:
            self.con.executemany(
                "insert or replace into unreadable values (?,?,?,?,?)",
                [[run_id, *r] for r in rows])

    def adjudicate(self, subject: str, question: str, family: str, answered: str,
                   verdict: str, correction: str = "", note: str = "",
                   who: str = "") -> None:
        if verdict not in ("agree", "disagree", "unclear"):
            raise ValueError("verdict must be agree, disagree or unclear")
        self.con.execute(
            "insert or replace into adjudications values (?,?,?,?,?,?,?,?,?)",
            [subject, question, family, answered, verdict, correction, note,
             who or "unknown", datetime.now(timezone.utc)])

    def adjudication_counts(self) -> dict:
        """How many verdicts exist PER FAMILY. Config reads this before allowing a gate."""
        self.con.execute(DDL)
        return dict(self.con.execute(
            "select family, count(*) from adjudications group by 1").fetchall())

    def accuracy(self, family: str | None = None) -> dict:
        """Agreement rate, and the denominator, because a rate without one says nothing."""
        q = "select verdict, count(*) from adjudications"
        args = []
        if family:
            q += " where family = ?"
            args.append(family)
        rows = dict(self.con.execute(q + " group by 1", args).fetchall())
        n = sum(rows.values())
        return {"n": n, "agree": rows.get("agree", 0), "disagree": rows.get("disagree", 0),
                "unclear": rows.get("unclear", 0),
                "agreement": (rows.get("agree", 0) / n) if n else None}

    def pending(self, limit: int = 25) -> list:
        """Judgments nobody has ruled on yet."""
        self.con.execute(DDL)
        return self.con.execute(
            """select d.decision_key, d.question, d.answer, d.confidence, d.prompt_version
               from model_decisions d
               left join adjudications a
                 on a.subject = d.decision_key and a.question = d.question
               where a.subject is null
               order by d.decided_at desc limit ?""", [limit]).fetchall()

    def previous_run(self, project_name: str, before: str) -> str | None:
        r = self.con.execute(
            """select run_id from runs where project = ? and run_id <> ?
               order by started_at desc limit 1""", [project_name, before]).fetchone()
        return r[0] if r else None

    def diff(self, run_a: str, run_b: str) -> dict:
        """What appeared and what went away between two runs."""
        q = """select check_name, subject_name, summary from findings where run_id = ?"""
        a = {tuple(r) for r in self.con.execute(q, [run_a]).fetchall()}
        b = {tuple(r) for r in self.con.execute(q, [run_b]).fetchall()}
        return {"new": sorted(b - a), "gone": sorted(a - b), "same": len(a & b)}
