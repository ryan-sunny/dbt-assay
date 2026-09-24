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
import os
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import duckdb

from .jev import DDL as JEV_DDL
from .checks.incremental import DDL_LATENESS
from .ledger import DDL as LEDGER_DDL

# `accept`: the finding is CORRECT and the person chose to leave it. Not `agree`, which leaves it
# outstanding forever, and not `disagree`, which is a lie that tells a working check it was wrong.
VERDICTS = ("agree", "disagree", "unclear", "accept")


def _valid_date(s: str) -> str:
    from datetime import date
    try:
        date.fromisoformat(str(s))
    except ValueError as e:
        raise ValueError(f"until must be a date, YYYY-MM-DD, not {s!r}") from e
    return str(s)


def _shipping_versions() -> set:
    """Every prompt_version any loaded bank currently ships. A SET, so nothing has to guess which
    family a question id belongs to -- the mapping that broke was never needed for this."""
    try:
        from .contracts import load_all_banks
        return {str(q["prompt_version"]).split("+")[0]
                for q in load_all_banks().values() if q.get("prompt_version")}
    except Exception:                                            # noqa: BLE001
        return set()


DDL = JEV_DDL + LEDGER_DDL + DDL_LATENESS + """
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
    parse_failed integer,
    scope        varchar
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
    -- *** THE HANDLE A RULING IS FILED UNDER, SO THE TWO TABLES CAN BE JOINED. ***
    -- An agent rules with `rule(finding=...)` and the verdict is stored under
    -- `<uid>::finding::<id>`. Without this column that id exists on one side of the store and
    -- nowhere on the other, so nothing -- not `export`, not a seed, not the page -- could put a
    -- ruling next to the finding it is about. The same orphaning as 0.17.0, one layer down.
    finding_id   varchar,
    primary key (run_id, check_name, subject, summary)
);
create table if not exists adjudications (
    -- *** THE LABELED SET, MANUFACTURED BY USE. ***
    -- Every human verdict recorded here is one row of evidence about a QUESTION, not just about a
    -- finding. It is the only thing that ever earns a question the right to fail a build, which is
    -- why config refuses to gate below min_adjudications. A tool without this loop ships flag-only
    -- forever and gets muted.
    subject      varchar,     -- model unique_id, or model::column
    question     varchar,     -- the question id, e.g. role__amount
    family       varchar,     -- the bank entry, e.g. column_role
    answered     varchar,     -- what assay said
    verdict      varchar,     -- agree | disagree | unclear | accept
    correction   varchar,     -- what it should have been, when known
    note         varchar,
    decided_by   varchar,
    -- *** WHERE THE VERDICT CAME FROM, AND IT IS NOT ALL THE SAME THING. ***
    -- `human` is somebody who looked. `label` is derived from an assertion already in the project
    -- -- a unique test, a declared key, a join condition -- which is a REAL human judgment, made
    -- earlier and about something slightly different. Measured: reading four role "disagreements"
    -- by hand showed THREE were the label being wrong, not the answer. So a label is evidence and
    -- never a gate, and only human verdicts count toward min_adjudications.
    source       varchar,
    -- *** A VERDICT IS ABOUT A VERSION OF A QUESTION, NOT ABOUT THE QUESTION FOREVER. ***
    -- The key used to be (subject, question), so re-ruling OVERWROTE. A question rewritten after
    -- people disagreed with it kept their verdicts and lost the fact that they were about the old
    -- wording, which is the one measurement that says whether the rewrite worked. `units` went
    -- 2/4 to 8/8 across a rewrite and the store could not have told you.
    --
    -- `model_version` is the second axis and it is free: Jev shipping a new model moves answers
    -- under questions nobody touched, and this is the only way that is ever visible.
    -- DEFAULT '' because a primary-key column is NOT NULL in duckdb, and a caller that does not
    -- know the version must still be able to record a verdict. An empty version reports as
    -- `(unversioned)` rather than failing the insert or inventing one.
    prompt_version varchar default '',
    model_version  varchar default '',
    -- *** THE DECISION THIS VERDICT IS ABOUT, WHEN THERE IS ONE. ***
    -- A verdict on a JUDGED finding is evidence about a probability, and until this column
    -- existed there was no path from the verdict back to the number it ruled on: `rule(finding=)`
    -- files under `<uid>::finding::<id>` and the confidence lives on a `model_decisions` row
    -- keyed by the SUBJECT the question was asked about. Measured on the field store: of 105
    -- agent rulings, zero reached a decision by any join.
    --
    -- Empty for a STRUCTURAL finding, and that is correct rather than missing. 97 of those 105
    -- were structural -- a parser decided them, no question was asked, there is no probability to
    -- calibrate. A calibration report must exclude them by construction, not treat them as a gap.
    decision_key varchar default '',
    decided_at   timestamp,
    until        varchar default '',   -- an `accept` lapses on this date
    primary key (subject, question, prompt_version)
);
-- *** WHAT THE MODEL WAS SHOWN, NOT ONLY WHAT IT SAID. ***
-- `model_decisions` kept a HASH of the state and threw the state away, so every reader could see
-- the answer, the distribution and a 120-character label, and none of them could see the evidence
-- that produced it. An agent ruling on a finding was being asked to judge an answer without the
-- question's own input.
--
-- Keyed by the hash, so identical states store ONCE: a real store holds 9,945 decisions over
-- 4,048 distinct states, and a cache hit re-uses a state by definition.
--
-- It is small, and the reason is the design rather than luck. A real claim state is ~320-540
-- characters, because "small state, better answer" is a measured rule here -- 0.96 with what the
-- claim needed against 0.47 with one extra correct sentence. Sized before building it: about
-- 5 MB of text for a 358-model warehouse, half a megabyte on disk after compression, against a
-- 13.9 MB store. An earlier estimate said 183 MB by reading `input_tokens`, which is the QUESTION
-- plus the state and is dominated by the question, resent in full on every call.
create table if not exists states (
    state_hash varchar primary key,
    state      varchar,
    first_seen timestamp
);
create table if not exists edge_facts (
    run_id varchar, parent varchar, child varchar, parent_name varchar, child_name varchar,
    available integer, carried integer, dropped integer, joined_on varchar, dropped_cols varchar,
    primary key (run_id, parent, child)
);
-- *** CLAIMS ARE DATA, NOT PROSE, AND THE ID MUST OUTLIVE A REWORDING. ***
-- A verdict attaches to claim_id. It is a hash of (subject, normalized text), so reflowing a
-- paragraph around a claim does not orphan the ruling that was made on it.
create table if not exists claims (
    claim_id      varchar primary key,
    subject       varchar,       -- model uid, or "<uid>.<column>"
    subject_name  varchar,
    text          varchar,
    source_kind   varchar,       -- description | sql_comment | meta | manual
    source_ref    varchar,       -- path, or path:line
    kind          varchar,       -- what the extractor judged it to be
    kind_conf     double,
    citation      varchar,
    status        varchar,       -- active | suppressed
    extracted_at  timestamp
);

create table if not exists unreadable (
    run_id varchar, subject varchar, subject_name varchar, file varchar, reason varchar,
    primary key (run_id, subject)
);
-- *** A NUMBER `calibrate` PRINTS AND NOTHING KEEPS CANNOT BE CHECKED LATER. ***
-- audit.yml's gating comment said "9 exact of 25, 9 flagged uncertain, 5 disagreeing" after the
-- store had moved to 8 of 24, 9 and 4, and `config_comment_contradicts_the_store` could not see
-- it: the outcome was only ever printed (25.7). One row per run of `assay calibrate`.
-- *** THE COMPILED SQL WAS WRITTEN ON EVERY BUILD AND THROWN AWAY ON THE NEXT. ***
-- Keyed on dbt's own checksum of the model file (sha256 of its text, whitespace stripped), so a git
-- blob from any commit hashes to the key its compiled body was stored under. `backtest` reads it.
create table if not exists compiled_sql (
    checksum varchar primary key, model varchar, sql varchar, first_seen timestamp
);
-- Every commit touching the project: what `backtest` replayed one-shot and threw away.
create table if not exists commits (
    sha varchar primary key, committed_at timestamptz, subject varchar, body varchar,
    files varchar, models varchar
);
create table if not exists calibrations (
    ran_at timestamp, assay_version varchar, n integer, exact integer, uncertain integer,
    kept_too_many integer, dropped_too_many integer, disagrees integer, code_exact integer
);
"""


# *** THE MIGRATION RUNS ON WHICHEVER STORE OPENS FIRST, AND THAT IS RARELY THE ONE REPORTING. ***
# `check` opens three. If the count lived only on the instance that did the work, the surface a
# person is watching would print nothing and 7,536 moved rows would read exactly like zero -- the
# failure this codebase keeps finding, where "it reported nothing" and "it is not wired up" are the
# same output. Append-only, per process, and it describes what THIS process did to the store.
QUESTION_IDS_MOVED: list[tuple[str, str, str, int]] = []
QUESTION_IDS_STUCK: list[tuple[str, str, str, int]] = []

# How many calls this process reconstructed from decision rows written before assay minted its own
# ids, and whether it refused to. Same reason as above: the store that does the work is rarely the
# one reporting, and a silent reconstruction is the thing `assay cost` must be able to disclose.
CALLS_RECONSTRUCTED: list[tuple[str, int]] = []


class StoreUnwritable(RuntimeError):
    """The store cannot be opened for writing. Says which path and why, rather than a traceback."""


class StoreLocked(StoreUnwritable):
    """Somebody else holds the write lock. A subclass, so every existing handler still catches it,
    and its own type, so a caller that wants to wait can tell this apart from a bad path."""


def _is_lock_error(message: str) -> bool:
    """duckdb spells the same condition several ways across versions, so match on all of them."""
    low = (message or "").lower()
    return any(s in low for s in ("lock", "being used", "conflicting"))


# *** DUCKDB IS SINGLE-WRITER, AND A SCHEDULED PIPELINE HOLDS THE LOCK FOR AS LONG AS IT LIKES. ***
# Reported from the field: `check --verify` hung for ELEVEN MINUTES against a cursor that did not
# move, blocked on a nightly job that had been running 37 minutes with two more queued behind it.
# Killing the assay run left a stale lock that needed `kill -9`.
#
# duckdb's own error names the holding PID. Reading it out and saying "PID 70034 has held this
# since 19:42" is a completely different experience from a hang, and it is the difference between
# "assay is broken" and "your nightly is still running".
_LOCK_PID = re.compile(r"PID\s+(\d+)", re.IGNORECASE)


def _lock_holder(message: str) -> dict:
    """Who holds the store's write lock, from duckdb's own error text plus `ps`.

    Everything here is best-effort and each piece is reported only if it was actually read. A
    guessed process name would be worse than none: it is the thing a person is about to kill.
    """
    m = _LOCK_PID.search(message or "")
    if not m:
        return {}
    pid = int(m.group(1))
    out: dict = {"pid": pid}
    try:
        import subprocess
        got = subprocess.run(["ps", "-o", "lstart=,command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5, check=False)
        line = (got.stdout or "").strip()
        if line:
            # `ps -o lstart=` is a fixed 24-character date, then the command.
            out["since"], out["command"] = line[:24].strip(), line[24:].strip()[:160]
    except Exception:                                            # noqa: BLE001,S110
        pass
    return out


def lock_message(path: str, err: str) -> str:
    """The sentence a person can act on, instead of a traceback or a hang."""
    who = _lock_holder(err)
    if not who:
        return (f"the store at {path} is LOCKED by another process. DuckDB allows one writer: "
                f"close the other `assay` session or the job that is writing, and try again. "
                f"The store is fine and nothing was lost.")
    since = f", holding it since {who['since']}" if who.get("since") else ""
    what = f"\n  {who['command']}" if who.get("command") else ""
    # *** "WAIT FOR IT, OR STOP IT" CANNOT WORK WHEN THE HOLDER IS THE ONE ASKING. ***
    # Reported from the field: the MCP server held the store after `plan`, and every CLI-backed
    # tool it then ran was locked out by its own parent. Said as what it is: a connection assay
    # opened and did not close, which is a bug in assay.
    import os
    if who["pid"] in (os.getpid(), os.getppid()):
        whose = "this process" if who["pid"] == os.getpid() else "the process that started it"
        return (f"the store at {path} is LOCKED by {whose} (PID {who['pid']}){since}.{what}\n"
                f"That is assay holding a connection it did not close -- a bug in assay, not "
                f"something to wait out. Restarting the MCP server releases it. Nothing was lost.")
    return (f"the store at {path} is LOCKED by PID {who['pid']}{since}.{what}\n"
            f"DuckDB allows one writer. Wait for it, or stop it. Nothing was lost, and nothing "
            f"was written by this run.")


class Store:
    def __init__(self, path: str | Path = "assay.duckdb", timeout: float | None = None):
        """`timeout` seconds to wait for the write lock. 0 means do not wait.

        Waiting is opt-in because the honest default for a person at a terminal is to be TOLD who
        holds the lock immediately, rather than to sit in front of a cursor that does not move --
        eleven minutes of it, in the field. A SCHEDULED caller would rather queue, and sets
        `ASSAY_LOCK_TIMEOUT` once for the whole box instead of threading a flag through every
        command that might open a store.
        """
        self.path = str(path)
        if timeout is None:
            try:
                timeout = float(os.environ.get("ASSAY_LOCK_TIMEOUT") or 0.0)
            except ValueError:
                timeout = 0.0
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        while True:
            try:
                self.con = duckdb.connect(self.path)
                break
            except Exception as e:
                text = str(e)
                if _is_lock_error(text):
                    if time.monotonic() < deadline:
                        time.sleep(0.5)
                        continue
                    raise StoreLocked(lock_message(self.path, text)) from e
                # *** A READ-ONLY WORKING DIRECTORY IS AN ORDINARY CI SETUP, NOT A BUG REPORT. ***
                # duckdb raises an IOException that surfaces as a full traceback through the
                # store's own internals, which reads like assay broke rather than like a path
                # being wrong.
                raise StoreUnwritable(
                    f"cannot open the assay store at {self.path!r}: {e}. "
                    f"Pass --store with a writable path, or run from a writable directory. "
                    f"The structural checks work without a store at all.") from e
        self.con.execute(DDL)
        self.renamed_question_ids: list[tuple[str, str, int]] = []
        self.unrenamable_question_ids: list[tuple[str, str, int]] = []
        # {model unique_id: sha256 of its source file}, filled by `use_project`. Empty means a
        # decision written now records NO checksum, which reports as "cannot be checked".
        self.checksums: dict = {}
        self._migrate()
        self.superseded_decisions = 0
        self.stale_decisions = 0

    # *** `create table if not exists` IS NOT A MIGRATION. ***
    # A store written by an older assay keeps its old shape forever, and the next insert fails with
    # a column-count error on somebody's machine rather than on mine. Columns added since are
    # applied on open; adding a column is cheap, safe and keeps every row that was already there.
    ADDED_COLUMNS: ClassVar[dict] = {
        # `until`: an `accept` stops suppressing on this date, the way a waiver's expiry does.
        "adjudications": [("source", "varchar"), ("decision_key", "varchar"),
                          ("until", "varchar")],
        # `exposures`: what outside the warehouse a finding reaches, as a JSON list of exposure
        # names, beside `descendants` and `marts`, which were the only reach there was.
        # `file_checksum`: dbt's checksum of the model file when the finding was seen, so a finding
        # that disappears can be told apart: the code changed, or assay did.
        "findings": [("finding_id", "varchar"), ("exposures", "varchar"),
                     ("file_checksum", "varchar")],
        "model_decisions": [("input_tokens", "integer"), ("context", "varchar"),
                            ("file_checksum", "varchar"), ("state_builder", "varchar"),
                            ("state_inputs", "varchar")],
        # *** THE ENGINE'S OWN TIME, ADDED AFTER THE TABLE SHIPPED. ***
        # A store written by 0.48.0 has `warehouse_calls` without it, and the reader selects it by
        # name -- so `assay cost` died with a binder error on every existing store until this
        # line existed. Which is what the banner above this dict says, and it still happened.
        "warehouse_calls": [("exec_ms", "integer")],
        # *** A PARTIAL RUN BECAME THE BASELINE. ***
        # `check --check <name>` wrote 21 findings as a run, and the next diff called the other
        # 515 resolved and the loop line said "63 are gone" on a project where none were. NULL is
        # a full run; anything else says what the run was narrowed to.
        # `git_sha`: the commit the project was at, `+dirty` when uncommitted -- the join from a
        # finding to the commit it was first seen at.
        # `unchecked`: the checks this run did not evaluate (JSON), so a diff never calls them gone.
        "runs": [("scope", "varchar"), ("git_sha", "varchar"), ("unchecked", "varchar")],
    }

    def _migrate(self) -> None:
        self._add_missing_columns()
        self._reshape_adjudications()
        self._add_missing_columns()
        self._rename_moved_question_ids()
        self._backfill_model_calls()
        self.verdict_versions_filled = self._backfill_verdict_versions()
        self.scoped_runs_marked = self._backfill_scoped_runs()
        self.unchecked_runs_marked = self._backfill_unchecked_runs()
        # *** THE ONE TABLE RECORDING A MEASUREMENT OF THE DATA WAS THE ONE THAT FORGOT. ***
        # `observed_keys` keyed on (relation, column) with `insert or replace`, so each probe
        # overwrote the last and assay could never say a key that held last week has stopped.
        from . import probe as _probe
        try:
            self.observations_kept = _probe.migrate(self)
        except Exception:                                        # noqa: BLE001
            # A store that cannot be migrated still opens; the probe will report it on use rather
            # than every command failing to start.
            self.observations_kept = 0
        # *** THE WAREHOUSE LEDGER BEGINS WHERE THE STORE DOES, MIGRATION OR NOT. ***
        # Every statement assay sends goes through `probe.run_sql` or `probe.run_via_dbt`, and
        # both write a `warehouse_calls` row while a ledger is attached. Attaching here means a
        # command records what it spent without having to remember to ask -- and attaching OUTSIDE
        # the try means a store that failed to migrate still records what it spends.
        _probe.attach(self)

    def _add_missing_columns(self) -> None:
        for table, columns in self.ADDED_COLUMNS.items():
            try:
                have = {r[0] for r in self.con.execute(
                    f"select column_name from information_schema.columns "
                    f"where table_name = '{table}'").fetchall()}
            except Exception:                                    # noqa: BLE001,S112
                continue
            if not have:
                continue
            for name, kind in columns:
                if name not in have:
                    self.con.execute(f"alter table {table} add column {name} {kind}")
                    if table == "adjudications" and name == "source":
                        # Rows written before the column existed came through the interactive
                        # path, which is a person. Leaving them NULL would silently drop every
                        # verdict somebody had already recorded.
                        self.con.execute(
                            "update adjudications set source = 'human' where source is null")



    # *** THE PROJECT THIS STORE IS BEING WRITTEN ABOUT, SO A DECISION CAN RECORD ITS CODE. ***
    # `decide()` gets a store and a client and never a project, and there are seventeen call sites.
    # Threading a checksum through all seventeen is the shape that has already failed here three
    # times -- a value that must reach every call site and reaches most of them. Registered once,
    # read from the store, and a command that forgets produces NULL, which `assay stale` counts
    # out loud as "cannot be checked" rather than folding into "current".
    def use_project(self, project) -> int:
        """Register the manifest's own file checksums. Returns how many models carry one.

        Cheap to call in a loop: the same project object registers once.
        """
        if getattr(self, "_checksum_project", None) is project:
            return len(self.checksums)
        self._checksum_project = project
        self.checksums = {uid: m.checksum for uid, m in getattr(project, "models", {}).items()
                          if getattr(m, "checksum", "")}
        return len(self.checksums)

    # *** HALF THIS STORE COULD NOT SAY WHAT A CALL WAS. ***
    # `call_id` was whatever the provider returned, and one provider returned nothing for a whole
    # day: 9,762 of 19,707 decisions on the field store carry an empty one. Those rows still
    # describe real calls -- a batch of eight answers about one state, or eight separate calls --
    # and nothing on the row said which, so every cost total over them was a guess.
    #
    # The reconstruction is not a guess either. Rows written by one `decide()` share
    # (decision_key, state_hash, prompt_version, model_version, input_tokens) and differ only by
    # question, because that is exactly what the writer does. On the half of the field store that
    # HAS provider ids, grouping by that tuple returns the same 4,946 calls and the same
    # 14,567,698 tokens as grouping by the id itself, with no disagreement. So it is checked
    # against the provider first and only runs when the check passes.
    #
    # Idempotent: after it runs there are no empty call ids, so the second open moves nothing.
    _PROXY = ("decision_key || '|' || state_hash || '|' || prompt_version || '|' || "
              "model_version || '|' || coalesce(cast(input_tokens as varchar), 'null')")

    def _backfill_model_calls(self) -> None:
        try:
            have = {r[0] for r in self.con.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'model_decisions'").fetchall()}
        except Exception:                                        # noqa: BLE001
            return
        if not have or "call_id" not in have:
            return
        todo = self.con.execute(
            "select count(*) from model_decisions d where not exists "
            "(select 1 from model_calls c where c.call_id = d.call_id)").fetchone()[0]
        if not todo:
            return

        # *** THE PROXY IS CHECKED AGAINST THE PROVIDER BEFORE IT IS TRUSTED. ***
        # If the two ever disagree, the rows with no id are left alone and counted, because a
        # wrong call boundary is a wrong total presented as measured -- worse than an absent one.
        by_id, by_proxy = self.con.execute(
            f"select count(distinct call_id), "
            f"count(distinct {self._PROXY}) from model_decisions "
            f"where call_id is not null and call_id <> ''").fetchone()
        blind = self.con.execute(
            "select count(*) from model_decisions where call_id is null or call_id = ''"
        ).fetchone()[0]
        if blind and by_id != by_proxy:
            CALLS_RECONSTRUCTED.append(("refused", blind))
            return

        if blind:
            # sha1 of the tuple, so the id is stable across opens and across machines: a second
            # run must not mint a second name for the same call.
            self.con.execute(
                f"update model_decisions set call_id = 'assay-bf-' || substr(sha256({self._PROXY}), 1, 24) "
                f"where call_id is null or call_id = ''")
            CALLS_RECONSTRUCTED.append(("reconstructed", blind))

        from .jev import USD_PER_INPUT_TOKEN
        self.con.execute(
            """insert or ignore into model_calls
               (call_id, id_source, caller, model_name, input_tokens, output_tokens, usd,
                usd_per_input_token, called_at)
               select d.call_id,
                      case when starts_with(d.call_id, 'assay-bf-') then 'reconstructed'
                           else 'provider' end,
                      any_value(d.caller), any_value(d.model_version),
                      -- 0 was the old writer's spelling for "the provider returned no usage",
                      -- because it did `or 0`. It is not a measurement of a free call and it
                      -- does not become one by being copied across.
                      nullif(any_value(d.input_tokens), 0),
                      -- never recorded before this table existed, and an absent count is NULL
                      null,
                      nullif(any_value(d.input_tokens), 0) * ?,
                      -- no historical rate was ever stored; these are priced at the rate shipping
                      -- now and `assay cost` says so rather than implying it was read off the row
                      ?,
                      min(d.decided_at)
               from model_decisions d
               where d.call_id is not null and d.call_id <> ''
                 and not exists (select 1 from model_calls c where c.call_id = d.call_id)
               group by d.call_id""", [USD_PER_INPUT_TOKEN, USD_PER_INPUT_TOKEN])

    # *** A QUESTION THAT MOVES TO ITS OWN PREFIX TAKES ITS STORED ANSWERS WITH IT. ***
    # `sentence_is_a_claim` filed under `claim__N` and `claim_alignment` filed under `align`, each
    # one a prefix a NEIGHBORING bank declares. Fixing the emitted ids without moving the rows
    # would orphan 7,536 answers on the field store: the writer would ask under the new id, find
    # no cached answer, and pay to re-ask a question whose TEXT never changed. So the ids move and
    # the answers move with them, which is a rename and not a re-ask -- state hashes are unchanged,
    # every row stays a cache hit, and no `prompt_version` moves because no question moved.
    #
    # (old id, new id, is_prefix, the family that actually writes it, the family it used to
    #  resolve to -- which is the wrong value sitting in `adjudications.family` on any store
    #  written before this.)
    MOVED_QUESTION_IDS: ClassVar[tuple] = (
        ("claim__", "sentence__", True,  "sentence_is_a_claim", "claim_alignment"),
        ("align",   "claim",      False, "claim_alignment",     "same_concept"),
    )

    # The two tables that key on a question id. `model_decisions` holds the answers;
    # `adjudications` holds the verdicts people gave on them, and they must move together or the
    # verdict stops joining to the answer it was about -- the 0.17.0 orphaning, one column over.
    _QUESTION_ID_TABLES: ClassVar[tuple] = (
        ("model_decisions", "decision_key", ("prompt_version", "model_version")),
        ("adjudications",   "subject",      ("prompt_version",)),
    )

    def _rename_moved_question_ids(self) -> None:
        """Move stored answers onto the question id their own bank declares.

        *** IT MOVES WHAT IT CAN AND SAYS WHAT IT CANNOT, AND IT NEVER DROPS A ROW. ***
        A rename can collide: a store holding both the old id and the new one under the same key
        cannot have both, because the id is in the primary key. An `update` there would raise and
        a `insert or replace` would silently destroy one of the two answers -- which is the shape
        this codebase keeps finding in other people's code. So collisions are counted, left where
        they are, and reported by name. Nothing here is a guess: a row under `claim__N` was written
        by `kind_questions` and by nothing else.

        Idempotent. After it runs the old ids do not exist, so the second run moves nothing and
        prints nothing.
        """
        for table, key, version_cols in self._QUESTION_ID_TABLES:
            try:
                have = {r[0] for r in self.con.execute(
                    "select column_name from information_schema.columns "
                    "where table_name = ?", [table]).fetchall()}
            except Exception:                                    # noqa: BLE001, S112
                continue
            if "question" not in have:
                continue
            for old, new, is_prefix, right_family, wrong_family in self.MOVED_QUESTION_IDS:
                self._move_one_question_id(table, key, version_cols, old, new, is_prefix,
                                           right_family, wrong_family, have)

    def _move_one_question_id(self, table, key, version_cols, old, new, is_prefix,
                              right_family, wrong_family, have) -> None:
        # `starts_with`, never `like`: `_` is a single-character wildcard in LIKE, so
        # `like 'claim__%'` also matches `claimXY...`. The ids being moved END in a double
        # underscore, which is precisely where that would bite.
        def new_id(col: str) -> str:
            """The id `col` becomes, as SQL."""
            return f"? || substr({col}, {len(old) + 1})" if is_prefix else "?"

        def matches(col: str) -> str:
            return f"starts_with({col}, ?)" if is_prefix else f"{col} = ?"

        cols = [key, *(c for c in version_cols if c in have)]
        same_key = " and ".join(f"b.{c} = t.{c}" for c in cols)
        # A row that ALREADY sits where one of these is going, under the same key. The id is in
        # the primary key, so both cannot exist: an `update` would raise and an `insert or
        # replace` would destroy one of the two answers silently.
        collides = (f"exists (select 1 from {table} b where {same_key} "
                    f"and b.question = {new_id('t.question')})")

        n = self.con.execute(
            f"select count(*) from {table} where {matches('question')}", [old]).fetchone()[0]
        if not n:
            return
        stuck = self.con.execute(
            f"select count(*) from {table} t where {matches('t.question')} and {collides}",
            [old, new]).fetchone()[0]
        if stuck:
            self.unrenamable_question_ids.append((old, new, stuck))
            QUESTION_IDS_STUCK.append((table, old, new, stuck))

        moved = n - stuck
        if moved <= 0:
            return
        self.con.execute(
            f"update {table} as t set question = {new_id('t.question')} "
            f"where {matches('t.question')} and not {collides}",
            [new, old, new])
        self.renamed_question_ids.append((old, new, moved))
        QUESTION_IDS_MOVED.append((table, old, new, moved))

        # *** THE FAMILY COLUMN CARRIED THE NAME THE OLD PREFIX RESOLVED TO. ***
        # That name is a REAL family, so `_unresolved_family`'s repair -- which only moves a
        # family nothing can join back to -- steps over it. Only the known-wrong value and an
        # empty one are corrected here; any other value is somebody's deliberate label.
        if table == "adjudications" and "family" in have:
            self.con.execute(
                f"update {table} set family = ? where {matches('question')} "
                f"and (family = ? or family is null or family = '')",
                [right_family, new, wrong_family])

    def _reshape_adjudications(self) -> None:
        """Put `prompt_version` in the key on a store written before it was there.

        *** duckdb CANNOT ALTER A PRIMARY KEY, SO THIS REBUILDS THE TABLE. ***
        The backfill uses the CLOCK, which is not a guess. `model_decisions` keeps every version
        of every answer with the time it was given, so the version a person was looking at is the
        latest one that produced that answer BEFORE they ruled. Where no such row exists -- the
        answer has changed since, or the ruling predates the decision -- the version stays empty
        and reports as `(unversioned)`.

        The first attempt required exactly one version ever to have given that answer, and on a
        real store it backfilled ZERO rows: the eight human verdicts there were all on a question
        that had been rewritten, which is precisely the case this table exists to measure. A
        correct rule that answers nothing is not better than a wrong one.
        """
        try:
            have = {r[0] for r in self.con.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'adjudications'").fetchall()}
        except Exception:                                        # noqa: BLE001
            return
        if not have or "prompt_version" in have:
            return
        self.con.execute("""
            create table _adj_reshaped (
                subject varchar, question varchar, family varchar, answered varchar,
                verdict varchar, correction varchar, note varchar, decided_by varchar,
                source varchar, prompt_version varchar default '',
                model_version varchar default '',
                decided_at timestamp, primary key (subject, question, prompt_version))""")
        self.con.execute("""
            insert into _adj_reshaped
            select a.subject, a.question, a.family, a.answered, a.verdict, a.correction, a.note,
                   a.decided_by, coalesce(a.source, 'human'),
                   coalesce(nullif((select d.prompt_version from model_decisions d
                                    where d.decision_key = a.subject and d.question = a.question
                                      and d.answer = a.answered and d.decided_at <= a.decided_at
                                    order by d.decided_at desc limit 1), ''),
                            -- *** A STRUCTURAL CHECK HAS A VERSION TOO, AND IT IS assay's OWN. ***
                            -- 99 of 107 rulings on a real store were on structural findings,
                            -- where no question was asked so there is no prompt to version. All
                            -- of them would have read `(unversioned)` and the before-and-after
                            -- could not have begun until new rulings came in. `runs` records
                            -- which assay was running and when, so the version being ruled ON is
                            -- the latest run at or before the ruling. A lookup, not a guess.
                            (select 'assay.' || r.assay_version from runs r
                             where r.started_at <= a.decided_at
                             order by r.started_at desc limit 1),
                            ''),
                   coalesce((select d.model_version from model_decisions d
                             where d.decision_key = a.subject and d.question = a.question
                               and d.answer = a.answered and d.decided_at <= a.decided_at
                             order by d.decided_at desc limit 1), ''),
                   a.decided_at
            from adjudications a""")
        self.con.execute("drop table adjudications")
        self.con.execute("alter table _adj_reshaped rename to adjudications")

    def _backfill_scoped_runs(self) -> int:
        """Mark the partial runs written before `scope` existed. Returns how many.

        *** ONLY THE SHAPE `--check` LEAVES, AND ONLY WHEN BOTH NEIGHBOURS DISAGREE WITH IT. ***
        A run whose findings are all one check, fewer than half of the full runs either side of it,
        where both of those carry more than one check. A project that genuinely has one kind of
        finding has full runs of one check too, so it is never marked. Recorded as `inferred` so
        the mark says it was reconstructed rather than observed.
        """
        try:
            rows = self.con.execute("""
                with per as (
                    select r.run_id, r.project, r.started_at,
                           count(f.run_id) as n, count(distinct f.check_name) as checks,
                           min(f.check_name) as only_check
                    from runs r left join findings f on f.run_id = r.run_id
                    where r.scope is null
                    group by all),
                ranked as (
                    select *, lag(n) over w as n_before, lag(checks) over w as c_before,
                              lead(n) over w as n_after, lead(checks) over w as c_after
                    from per window w as (partition by project order by started_at, run_id))
                select run_id, only_check from ranked
                where checks = 1 and n_before is not null and n_after is not null
                  and c_before > 1 and c_after > 1
                  and n * 2 < n_before and n * 2 < n_after""").fetchall()
        except Exception:                                        # noqa: BLE001
            return 0
        for run_id, only in rows:
            self.con.execute("update runs set scope = ? where run_id = ?",
                             [f"check:{only} (inferred)", run_id])
        return len(rows)

    def _backfill_unchecked_runs(self) -> int:
        """Mark the runs written before `unchecked` existed that did not run the monitoring checks.

        A full run holding NO monitoring finding, in a store where other runs hold some, did not
        look -- `--verify` is the only way they run. Marked so the next `--verify` run does not
        call them new. A store where no run ever held one is left alone: there is nothing to say.
        """
        from .elementary import MONITORING_CHECKS
        try:
            checks = list(MONITORING_CHECKS)
            some = self.con.execute(
                "select count(*) from findings where check_name in (select unnest(?))",
                [checks]).fetchone()[0]
            if not some:
                return 0
            rows = self.con.execute("""
                select r.run_id from runs r
                where r.unchecked is null and r.scope is null
                  and not exists (select 1 from findings f where f.run_id = r.run_id
                                  and f.check_name in (select unnest(?)))""", [checks]).fetchall()
        except Exception:                                        # noqa: BLE001
            return 0
        for (rid,) in rows:
            self.con.execute("update runs set unchecked = ? where run_id = ?",
                             [json.dumps(sorted(checks)), rid])
        return len(rows)

    def _backfill_verdict_versions(self) -> int:
        """Give an already-reshaped store's unversioned verdicts their version back.

        *** THE RESHAPE ABOVE RUNS ONCE, AND THE BUG WROTE ROWS AFTER IT. ***
        `_load_verdicts` wrote the per-finding rows through a second `adjudicate` call that passed
        no version, so every store that had already been reshaped kept collecting empty ones. On
        the production store that is 70 of 136 human verdicts -- and all 70 are `::finding::`
        subjects, which are precisely the rows that measure whether a fix removed the thing
        somebody agreed was real.

        The version is resolved the same way the reshape resolves it: the assay that was running
        when the verdict was made, from `runs`. A lookup, not a guess, and a verdict made before
        any recorded run stays empty rather than being given the nearest number.

        Idempotent, and it never overwrites a version that is already there.
        """
        try:
            have = {c[0] for c in self.con.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'adjudications'").fetchall()}
        except Exception:                                        # noqa: BLE001
            return 0
        if "prompt_version" not in have:
            return 0
        try:
            n = self.con.execute(
                "select count(*) from adjudications where coalesce(prompt_version, '') = ''"
            ).fetchone()[0]
            if not n:
                return 0
            # `prompt_version` is part of the primary key, so a row whose resolved version already
            # exists under the same (subject, question) would collide. Those are left alone and
            # counted, because losing a verdict to a migration is worse than one staying unversioned.
            self.con.execute("""
                update adjudications as a
                set prompt_version = coalesce(
                        (select 'assay.' || r.assay_version from runs r
                         where r.started_at <= a.decided_at
                         order by r.started_at desc limit 1), '')
                where coalesce(a.prompt_version, '') = ''
                  and exists (select 1 from runs r where r.started_at <= a.decided_at)
                  and not exists (
                      select 1 from adjudications b
                      where b.subject = a.subject and b.question = a.question
                        and b.prompt_version = coalesce(
                            (select 'assay.' || r.assay_version from runs r
                             where r.started_at <= a.decided_at
                             order by r.started_at desc limit 1), ''))""")
            after = self.con.execute(
                "select count(*) from adjudications where coalesce(prompt_version, '') = ''"
            ).fetchone()[0]
            return n - after
        except Exception:                                        # noqa: BLE001
            return 0

    def is_new(self) -> dict:
        """What this store does NOT yet hold, for a caller about to report on it.

        *** AN EMPTY STORE AND A CLEAN WAREHOUSE PRODUCE THE SAME OUTPUT. ***
        Reported from the field as the single most dangerous behaviour of the whole run: a store
        was staged at a path the container could not see, assay created an empty one there, and
        the run reported `0 of 76 model(s) ruled`. No error, no warning. That sentence is TRUE of
        a warehouse nobody has reviewed and TRUE of a store that was created four seconds ago,
        and only one of them means anything.

        So it is answerable, and every surface that reports a zero can say which zero it is.
        Counts, never a verdict: `{"runs": 0, "verdicts": 0, ...}` and a caller decides what to
        say about it.
        """
        out = {"path": str(self.path)}
        for key, table in (("runs", "runs"), ("verdicts", "adjudications"),
                           ("answers", "model_decisions"), ("observations", "observed_keys"),
                           ("claims", "claims")):
            try:
                out[key] = int(self.con.execute(f"select count(*) from {table}").fetchone()[0])
            except Exception:                                    # noqa: BLE001
                # A table this store is too old to have holds nothing, which is the same answer.
                out[key] = 0
        out["empty"] = not any(out[k] for k in ("runs", "verdicts", "answers", "observations",
                                                "claims"))
        return out

    def new_store_warning(self) -> str:
        """One sentence for a brand-new store, or `''`. Never printed for a store with history."""
        st = self.is_new()
        if not st["empty"]:
            return ""
        return (f"this store is NEW and holds nothing -- no runs, no verdicts, no answers. "
                f"Every count below is zero because nothing has been recorded at {st['path']}, "
                f"which is not the same as a warehouse with nothing wrong in it. If you expected "
                f"history here, check the path: a store created at one a container cannot see "
                f"reports exactly this.")

    def close(self) -> None:
        try:
            from . import probe as _probe
            _probe.detach(self)
        except Exception:                                        # noqa: BLE001,S110
            pass
        self.con.close()

    def write_run(self, run_id: str, project, coverage: dict, parse_ok: int, parse_failed: int,
                  target_dir: str, version: str, scope: str | None = None,
                  git_sha: str = "", unchecked: list | None = None) -> None:
        # Named, never positional, for the reason `write_findings` gives.
        self.con.execute(
            """insert or replace into runs
               (run_id, started_at, project, dbt_version, target_dir, assay_version, host,
                models, sources, tests, edges, readable, unreadable, parse_ok, parse_failed,
                scope, git_sha, unchecked)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [run_id, datetime.now(timezone.utc), project.project_name, project.dbt_version,
             target_dir, version, platform.node(),
             coverage["models"], coverage["sources"], coverage["tests"], coverage["edges"],
             coverage["readable"], coverage["unreadable"], parse_ok, parse_failed, scope,
             git_sha or None, json.dumps(sorted(unchecked)) if unchecked else None])

    def write_calibration(self, counts: dict, version: str) -> None:
        self.con.execute(
            """insert into calibrations (ran_at, assay_version, n, exact, uncertain, kept_too_many,
               dropped_too_many, disagrees, code_exact) values (?,?,?,?,?,?,?,?,?)""",
            [datetime.now(timezone.utc), version, counts["n"], counts["exact"],
             counts["uncertain"], counts["kept_too_many"], counts["dropped_too_many"],
             counts["disagrees"], counts["code_exact"]])

    def latest_calibration(self) -> dict | None:
        try:
            r = self.con.execute("select * from calibrations order by ran_at desc limit 1")
            row = r.fetchone()
        except Exception:                                        # noqa: BLE001
            return None
        return dict(zip([d[0] for d in r.description], row, strict=True)) if row else None

    def latest_run(self, project_name: str | None = None) -> str | None:
        """The most recent FULL run: what "the current findings" means everywhere.

        *** ONE DEFINITION, BECAUSE SIX PLACES SPELLED IT THEMSELVES. ***
        `suggest`, MCP, the page and the explorer each asked for `order by started_at desc limit
        1`, and each would have taken a scoped run for the current state of the project. A scoped
        run is history; it is never "now".
        """
        r = self.con.execute(
            "select run_id from runs where scope is null"
            + (" and project = ?" if project_name else "")
            + " order by started_at desc, run_id desc limit 1",
            [project_name] if project_name else []).fetchone()
        return r[0] if r else None

    def write_findings(self, run_id: str, findings, checksums: dict | None = None) -> None:
        checksums = checksums or {}
        rows = [[run_id, f.check, f.subject, f.subject_name, f.file, f.summary, f.detail,
                 f.base, f.weight, f.descendants, f.marts, json.dumps(f.evidence, default=str),
                 f.id, json.dumps(getattr(f, "exposures", None) or []),
                 checksums.get(f.subject) or None]
                for f in findings]
        if rows:
            # Named, never positional: a migration appends at the END and a positional insert
            # then writes the id into whichever column happens to sit there.
            self.con.executemany(
                """insert or replace into findings
                   (run_id, check_name, subject, subject_name, file, summary, detail,
                    base, weight, descendants, marts, evidence, finding_id, exposures,
                    file_checksum)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

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
                   who: str = "", source: str = "human",
                   prompt_version: str = "", model_version: str = "",
                   decision_key: str = "", until: str = "") -> None:
        if verdict not in VERDICTS:
            raise ValueError("verdict must be agree, disagree, unclear or accept")
        # *** `accept` IS A WAIVER WITH A NAME ON IT, SO IT NEEDS THE WAIVER'S REASON. ***
        # A waiver whose justification is "looks fine" is how a real finding gets silenced, which
        # is why `audit.yml` refuses one without a reason. The same rule, at the same strength.
        if verdict == "accept" and not (note or "").strip():
            raise ValueError("an `accept` needs a reason: what makes this correct finding the "
                             "right thing to leave as it is")
        if until:
            _valid_date(until)
        if source not in ("human", "label", "replay", "agent"):
            raise ValueError("source must be human, label, replay or agent")
        # *** NAME THE COLUMNS. ***
        # A positional insert assumes an order, and a migration appends new columns at the END, so
        # the two disagree the moment a store is upgraded -- writing "label" into a timestamp.
        self.con.execute(
            """insert or replace into adjudications
               (subject, question, family, answered, verdict, correction, note,
                decided_by, source, prompt_version, model_version, decision_key, decided_at,
                until)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [subject, question, family, answered, verdict, correction, note,
             who or "unknown", source, prompt_version or "", model_version or "",
             decision_key or "", datetime.now(timezone.utc), until or ""])

    def save_claims(self, rows: list) -> None:
        """Named columns, never positional. Positional inserts broke twice after a migration."""
        self.con.execute(DDL)
        self.con.executemany(
            """insert or replace into claims
               (claim_id, subject, subject_name, text, source_kind, source_ref,
                kind, kind_conf, citation, status, extracted_at)
               values (?,?,?,?,?,?,?,?,?,?, current_timestamp)""", rows)

    def claims(self, subject: str | None = None, checkable_only: bool = False,
               min_conf: float = 0.0) -> list[dict]:
        self.con.execute(DDL)
        q = ("select claim_id, subject, subject_name, text, source_kind, source_ref, "
             "kind, kind_conf, citation, status from claims where status = 'active'")
        args: list = []
        if subject:
            q += " and (subject = ? or subject_name = ?)"
            args += [subject, subject]
        if checkable_only:
            q += " and kind in ('claim_about_output', 'claim_about_a_rule')"
        if min_conf:
            q += " and coalesce(kind_conf, 0) >= ?"
            args.append(min_conf)
        cols = ("claim_id", "subject", "subject_name", "text", "source_kind", "source_ref",
                "kind", "kind_conf", "citation", "status")
        return [dict(zip(cols, r, strict=True))
                for r in self.con.execute(q + " order by subject_name, claim_id", args).fetchall()]

    def agent_rulings(self, subject: str | None = None) -> list[dict]:
        """What an agent has ruled, kept apart from what a person ruled.

        *** AN AGENT RULING IS EVIDENCE. IT IS NEVER AUTHORITY. ***
        It cannot gate a build, cannot satisfy `min_adjudications`, cannot anchor `regress`, and
        cannot move the coverage number -- every one of those filters on `source = 'human'`.
        That is deliberate and it is the point: the ruled-on figure is the only number in this
        system nobody can game, and an agent able to raise it would destroy exactly the property
        that makes it worth printing.

        What it CAN do is triage. Sixty-nine models with a finding and none looked at is a wall;
        six the agent believes are real is a place to start, and the person's ruling is still the
        one that counts.
        """
        self.con.execute(DDL)
        q = ("select subject, question, family, answered, verdict, note, decided_at "
             "from adjudications where source = 'agent'")
        args: list = []
        if subject:
            q += " and (subject = ? or subject like ?)"
            args += [subject, subject + "::%"]
        cols = ("subject", "question", "family", "answered", "verdict", "note", "decided_at")
        return [dict(zip(cols, r, strict=True))
                for r in self.con.execute(q + " order by decided_at desc", args).fetchall()]

    def ruled_findings(self, verdict: str) -> dict:
        """`{finding_id: (who, when, why)}` whose LATEST human ruling is `verdict`.

        Both halves of the loop read this. `disagree` is a dismissal: the finding is wrong and
        does not come back. `agree` is the opposite and removes nothing -- it is the record that
        somebody read this and said it was real, which is the only thing that makes "did fixing it
        work" a question anybody can ask. `accept` says it is real AND it stays: suppressed like a
        waiver, never counted as outstanding, and lapsing on its `until`.

        *** THE LATEST RULING, BECAUSE A PERSON CAN CHANGE THEIR MIND. ***
        This returned every ruling ever made, so a finding agreed and later accepted sat in
        "agreed and still here" forever, and one dismissed and later agreed with stayed dismissed.
        """
        self.con.execute(DDL)
        today = datetime.now(timezone.utc).date().isoformat()
        out = {}
        for subj, v, who, when, note, until in self.con.execute(
                """select subject, verdict, decided_by, decided_at, note, coalesce(until, '')
                   from (select *, row_number() over (partition by subject
                                                      order by decided_at desc) rn
                         from adjudications
                         where source = 'human' and subject like '%::finding::%')
                   where rn = 1 order by decided_at""").fetchall():
            if v != verdict:
                continue
            # An expired acceptance is not an acceptance: the finding comes back on its own.
            if v == "accept" and until and str(until) < today:
                continue
            out[str(subj).split("::finding::")[1]] = (who or "someone", when, note or "")
        return out

    def accepted(self) -> dict:
        """`{finding_id: (who, when, why, until)}` a PERSON called correct and chose to leave."""
        self.con.execute(DDL)
        live = self.ruled_findings("accept")
        out = {}
        for subj, until in self.con.execute(
                "select subject, coalesce(until, '') from adjudications where source = 'human' "
                "and verdict = 'accept' and subject like '%::finding::%' "
                "order by decided_at").fetchall():
            fid = str(subj).split("::finding::")[1]
            if fid in live:
                out[fid] = (*live[fid], until)
        return out

    def dismissed(self) -> dict:
        """`{finding_id: (who, when, why)}` a PERSON has read and called WRONG.

        *** RULING A FINDING FALSE DID NOT MAKE IT GO AWAY. ***
        Measured: record a human `disagree` on a finding, run `assay check` again, and the count
        is unchanged -- 115 before, 115 after. Only a hand-written waiver in `audit.yml` removed
        anything. So the loop did not compound: read 115 findings, rule every one of them wrong,
        and tomorrow you are handed the same 115.

        That is the whole premise of the review loop. A person disposes of a flag ONCE and it
        stays disposed, or the flag is a tax rather than a question.

        Keyed on the FINDING, not on (subject, question), and that is what makes it safe: the id
        hashes the check, the subject, the summary and the non-measured evidence, so it survives
        a rerun and CHANGES when the substance changes. Edit the model into a genuinely different
        defect and the dismissal does not follow it -- which is the guarantee a waiver needs an
        expiry date to approximate.
        """
        return self.ruled_findings("disagree")

    def ruled_pairs(self) -> set:
        """Every `(subject, question)` a PERSON has ruled on. What a verdict actually covers.

        *** `ruled_subjects` IS THE SUBJECT, AND TWO CALLERS USED IT TO MEAN THE PAIR. ***
        A verdict is recorded against a subject AND a question, and one answer clears every
        finding of that check on that model -- not every finding on that model. Skipping on the
        subject therefore hid checks nobody had answered, by omission, from the review queue and
        the review form, which are the two surfaces whose entire job is showing what is still
        unanswered. Measured: four verdicts removed six of 212 cards.

        Both callers wanted this. Neither had it, so both wrote the subject-level one and got a
        quiet over-skip -- so it lives here once rather than as a query in each.
        """
        self.con.execute(DDL)
        return {(str(a), str(b)) for a, b in self.con.execute(
            "select distinct subject, question from adjudications "
            "where source = 'human'").fetchall()}

    def ruled_subjects(self) -> set[str]:
        """Every subject a PERSON has ruled on, however they ruled.

        *** THE ONLY NUMBER IN THE SYSTEM A RELEASE CANNOT MOVE. ***
        Findings move when checks improve. Confidences move when states improve. Blast radius
        moves when the DAG does. This moves when somebody reads SQL, and nothing else touches it
        -- which makes it the only honest measure of whether a warehouse is being UNDERSTOOD
        rather than scanned. It is also the number a good release makes look worse, because
        finding more raises the denominator and a person raised none of it.
        """
        self.con.execute(DDL)
        return {r[0] for r in self.con.execute(
            "select distinct subject from adjudications where source = 'human'").fetchall()}

    def confirmed(self, family: str | None = None) -> list[dict]:
        """Every answer a person AGREED with, which is the set an upgrade must not move.

        *** THIS IS THE ONLY REGRESSION TEST assay HAS AGAINST A REAL BANK. ***
        Reported from the field: a change to the subject state moved two of eight verified answers
        on one family, and the answer DISTRIBUTION barely moved -- 79 of the same answer either
        side. The regression was invisible in the summary and measurable only because eight
        rulings were on record.
        """
        self.con.execute(DDL)
        # *** THE LATEST RULING PER PAIR, NOW THAT THE VERSION IS IN THE KEY. ***
        # A subject ruled `agree` at v1 and `disagree` at v2 keeps both rows. Taking them all
        # would anchor `regress` to a verdict the person has since withdrawn, which is worse than
        # no baseline: it would fail a build over an answer nobody stands behind any more.
        q = ("""select subject, question, family, answered, correction from (
                  select *, row_number() over (
                      partition by subject, question order by decided_at desc) as rn
                  from adjudications where source = 'human') t
              where rn = 1 and verdict in ('agree', 'accept')""")
        args: list = []
        if family:
            q += " and family = ?"
            args.append(family)
        cols = ("subject", "question", "family", "answered", "correction")
        return [dict(zip(cols, r, strict=True))
                for r in self.con.execute(q + " order by family, subject", args).fetchall()]

    def suppress_claim(self, claim_id: str) -> None:
        self.con.execute(DDL)
        self.con.execute("update claims set status = 'suppressed' where claim_id = ?", [claim_id])

    def adjudication_counts(self, source: str = "human") -> dict:
        """Verdicts per family. ONLY HUMAN ONES COUNT TOWARD A GATE.

        A label-derived verdict is evidence about a question and not permission for it to fail a
        build: the label can be the thing that is wrong, and on a real project three of four
        disagreements were exactly that.
        """
        self.con.execute(DDL)
        # *** DISTINCT PAIRS, NOT ROWS. ***
        # `prompt_version` joined the key so that a re-ruling is kept rather than overwritten.
        # Counting rows would then let ONE subject ruled twice look like two verdicts, and a gate
        # floor is meant to measure how many SUBJECTS somebody read, not how many times they
        # pressed a key. Ruling the same model again is not more evidence about the question.
        # *** AND NOT THE PER-FINDING ROWS, WHICH ARE THE SAME KEYPRESS. ***
        # One verdict on a card writes a model-level row and one `<uid>::finding::<id>` row per
        # finding the card covered, so a card over eight findings counted NINE toward the floor.
        # Every human write path writes the model-level row, so it is the one counted.
        q = ("select family, subject, question from adjudications "
             "where subject not like '%::finding::%'")
        args: list = []
        if source != "all":
            q += " and source = ?"
            args.append(source)
        out: dict = {}
        for fam, _subj, _q in {tuple(r) for r in self.con.execute(q, args).fetchall()}:
            out[fam] = out.get(fam, 0) + 1
        return out

    def effectiveness(self, source: str = "human") -> list[dict]:
        """Per family, per version: how often people agreed, and what is still open.

        *** THE ONE NUMBER THAT SAYS WHETHER A QUESTION GOT BETTER. ***
        Every question rewrite in this project so far was found by reading output, and its effect
        was written down by hand in a markdown file. `units_are_what_the_column_claims` went 2/4
        to 8/8 across a rewrite; `claim_alignment` took four rounds to halve its contradictions.
        Both numbers existed in verdicts somebody had already given and neither was in the store,
        because a re-ruling overwrote the row that would have carried it.

        An OPEN disagreement is one where nobody has since agreed at a DIFFERENT version. So the
        count falls only when a question changed and a person re-read it. A release cannot lower
        it, which is the same property the ruled-on figure has and the reason it is worth printing.
        """
        self.con.execute(DDL)
        where, args = "", []
        if source != "all":
            where, args = "where a.source = ?", [source]
        rows = self.con.execute(f"""
            select a.family,
                   coalesce(nullif(a.prompt_version, ''), '(unversioned)') as pv,
                   coalesce(nullif(a.model_version, ''), '(unrecorded)') as mv,
                   count(*) as n,
                   count(*) filter (where a.verdict = 'agree') as agree,
                   count(*) filter (where a.verdict = 'disagree') as disagree,
                   count(*) filter (where a.verdict = 'unclear') as unclear,
                   count(*) filter (where a.verdict = 'disagree' and not exists (
                       select 1 from adjudications b
                       where b.subject = a.subject and b.question = a.question
                         and b.verdict in ('agree', 'accept')
                         and b.prompt_version <> a.prompt_version
                         and b.decided_at > a.decided_at)) as still_open,
                   count(*) filter (where a.verdict = 'accept') as accept
            from adjudications a
            {where}
            group by 1, 2, 3
            order by 1, 2, 3""", args).fetchall()
        cols = ("family", "prompt_version", "model_version", "n", "agree", "disagree",
                "unclear", "open_disagreements", "accept")
        out = []
        for r in rows:
            d = dict(zip(cols, r, strict=True))
            # *** AN `accept` SAYS THE CHECK WAS RIGHT. ***
            # It is counted as correct, because it is: the finding was true and the person chose
            # to leave it. Recording it as `disagree` was the only other option and told a working
            # check it was wrong.
            decided = d["agree"] + d["accept"] + d["disagree"]
            # *** `unclear` IS NOT A DISAGREEMENT AND MUST NOT BE IN THE DENOMINATOR. ***
            # They say different things and they need different fixes. A family people DISAGREE
            # with has wrong criteria. A family they cannot rule on has a state problem, which is
            # exactly what seventeen unclears on one warehouse turned out to be. Averaging them
            # together hides which repair to make.
            d["agreement"] = ((d["agree"] + d["accept"]) / decided) if decided else None
            out.append(d)
        return out

    def accuracy_by_family(self, versions: dict, source: str = "human",
                           default: str = "") -> dict:
        """{family: (agreement, n)} counting ONLY verdicts given against the version now shipping.

        A verdict recorded against v1 of a question says nothing about whether people agree with
        v4 of it. `versions` maps family -> the prompt_version currently in the bank; a family
        with no verdicts at that version comes back absent, and an absent rate must never read as
        a failing one.

        `default` is the version for a family the bank does not name, which is a STRUCTURAL check:
        its version is assay's own, because it changed when the check changed. Without it such a
        family matches at every version it was ever ruled at, and verdicts about a check from two
        releases ago count as if they were about this one.
        """
        rows = self.con.execute(
            "select family, prompt_version, verdict, count(*) from adjudications "
            "where source = ? group by 1, 2, 3", [source]).fetchall()
        tally: dict = {}
        for fam, pv, verdict, n in rows:
            want = versions.get(fam) or (default if str(pv).startswith("assay.") else "")
            if want and pv != want:
                continue
            d = tally.setdefault(fam, {"agree": 0, "disagree": 0})
            if verdict in ("agree", "accept"):                   # accept: the check was right
                d["agree"] += n
            elif verdict == "disagree":
                d["disagree"] += n
        return {fam: (d["agree"] / (d["agree"] + d["disagree"]), d["agree"] + d["disagree"])
                for fam, d in tally.items() if (d["agree"] + d["disagree"])}

    def accuracy(self, family: str | None = None, source: str | None = None) -> dict:
        """Agreement rate, and the denominator, because a rate without one says nothing."""
        q = "select verdict, count(*) from adjudications"
        where, args = [], []
        if family:
            where.append("family = ?")
            args.append(family)
        if source:
            where.append("source = ?")
            args.append(source)
        if where:
            q += " where " + " and ".join(where)
        rows = dict(self.con.execute(q + " group by 1", args).fetchall())
        n = sum(rows.values())
        right = rows.get("agree", 0) + rows.get("accept", 0)
        return {"n": n, "agree": rows.get("agree", 0), "disagree": rows.get("disagree", 0),
                "unclear": rows.get("unclear", 0), "accept": rows.get("accept", 0),
                "agreement": (right / n) if n else None}

    def live_decisions(self, where: str, args: list,
                       columns: str = "question, answer, confidence, probabilities, context",
                       ) -> list[tuple]:
        """The CURRENT answer to each question: one row per (decision_key, question), the latest.

        *** THE FIRST VERSION HID 7,536 ANSWERS AND CALLED IT `246 resolved`. ***
        It resolved "the version shipping now" by splitting the question id on `__` and looking
        the prefix up as a bank's id_prefix. Three shipped questions file under an id that is not
        their own bank's prefix, so each lookup landed on a NEIGHBORING family and compared its
        version against someone else's. Two whole families -- `code_contradicts_a_claim` (213
        findings) and `description_contradicts_the_code` (18) -- went to exactly zero while
        nothing about those models had changed.

        So there is no family resolution here at all. The latest answer to a question wins, which
        needs no mapping and cannot land on the wrong bank. A version bump does not hide anything:
        re-running writes a newer row and that row wins, and until it is re-run the old answer is
        the only answer there is -- hiding it leaves the caller with nothing, which is strictly
        worse than serving it dated.

        `superseded_decisions` counts the older rows dropped, and `stale_decisions` counts served
        answers whose version no bank still ships. NOTHING is silent: being hidden without being
        counted is the failure this whole tool opens by describing.
        """
        self.con.execute(DDL)
        rows = self.con.execute(
            f"select {columns}, prompt_version, decision_key, question, decided_at "
            f"from model_decisions where {where} order by decided_at desc", args).fetchall()
        shipping = _shipping_versions()
        seen: set = set()
        out, superseded, stale = [], 0, 0
        for r in rows:
            pv, key, q = r[-4], r[-3], r[-2]
            if (key, q) in seen:
                superseded += 1
                continue
            seen.add((key, q))
            # The suffix a writer appends records the STATE SHAPE, not the question text, so the
            # base is what says whether this is still the question being asked.
            if shipping and str(pv).split("+")[0] not in shipping:
                stale += 1
            out.append(tuple(r[:-4]))
        self.superseded_decisions = superseded
        self.stale_decisions = stale
        return out

    def pending(self, limit: int = 25) -> list:
        """Judgments nobody has ruled on yet."""
        self.con.execute(DDL)
        return self.con.execute(
            """select d.decision_key, d.question, d.answer, d.confidence, d.prompt_version,
                      coalesce(d.context, ''), coalesce(d.model_version, '')
               from model_decisions d
               left join adjudications a
                 on a.subject = d.decision_key and a.question = d.question
               where a.subject is null
               order by d.decided_at desc limit ?""", [limit]).fetchall()

    def previous_run(self, project_name: str, before: str) -> str | None:
        r = self.con.execute(
            """select run_id from runs where project = ? and run_id <> ? and scope is null
               order by started_at desc, run_id desc limit 1""", [project_name, before]).fetchone()
        return r[0] if r else None

    def baseline_findings(self, project_name: str) -> tuple[dict | None, list[tuple]]:
        """The latest full `check` run for this project, and the findings it recorded.

        *** A SCOPED RUN NEVER WRITES A RUN, SO IT CAN NEVER BECOME ITS OWN BASELINE. ***
        If it could, a hook that blocked an edit once would read its own blocked run as the
        baseline on the next edit, and the finding it introduced would stop being new without
        anyone fixing it.
        """
        r = self.con.execute(
            """select run_id, started_at, assay_version from runs
               where project = ? and scope is null
               order by started_at desc, run_id desc limit 1""", [project_name]).fetchone()
        if not r:
            return None, []
        rows = self.con.execute(
            "select finding_id, check_name, subject from findings where run_id = ?",
            [r[0]]).fetchall()
        return {"run_id": r[0], "started_at": str(r[1]), "assay_version": r[2]}, rows

    def unchecked(self, *run_ids: str) -> set:
        """Checks any of these runs did not evaluate."""
        out: set = set()
        for rid in run_ids:
            try:
                row = self.con.execute("select unchecked from runs where run_id = ?",
                                       [rid]).fetchone()
            except Exception:                                    # noqa: BLE001
                row = None
            out |= set(json.loads(row[0])) if row and row[0] else set()
        return out

    def diff(self, run_a: str, run_b: str) -> dict:
        """What appeared and what went away between two runs.

        *** A CHECK ONE RUN DID NOT LOOK AT IS NEITHER RESOLVED NOR NEW. *** Reported from the
        field: a run without `--verify` called the five monitoring findings resolved, on the same
        screen that said monitoring was NOT checked -- and the next `--verify` run would have
        called them new. Only checks both runs evaluated are compared.
        """
        skip = self.unchecked(run_a, run_b)
        q = """select check_name, subject_name, summary, subject, evidence from findings
               where run_id = ?"""
        ra = [r for r in self.con.execute(q, [run_a]).fetchall() if r[0] not in skip]
        rb = [r for r in self.con.execute(q, [run_b]).fetchall() if r[0] not in skip]
        a = {tuple(r[:3]) for r in ra}
        b = {tuple(r[:3]) for r in rb}
        new, gone = b - a, a - b
        # *** A REWORDED FINDING IS THE SAME FINDING. *** (C1) 0.51.3 stopped cutting summaries
        # mid-word, and `check` reported "283 new, 283 resolved" on code nobody touched: the
        # comparison was on the summary's text. A finding that went and one that came with the
        # same check, the same model and the same evidence are one finding whose words changed,
        # paired one to one so two different findings are never folded together.
        key_of = {}
        for r in ra + rb:
            key_of[tuple(r[:3])] = finding_key(r[0], r[3], r[4])
        pool: dict = {}
        for t in sorted(gone):
            pool.setdefault(key_of[t], []).append(t)
        reworded = []
        for t in sorted(new):
            olds = pool.get(key_of[t])
            if olds:
                reworded.append((olds.pop(0), t))
        for old_t, new_t in reworded:
            gone.discard(old_t)
            new.discard(new_t)
        return {"new": sorted(new), "gone": sorted(gone), "same": len(a & b) + len(reworded),
                "reworded": len(reworded)}


# Evidence that DESCRIBES a finding rather than saying which one it is. (N5) Adding a sort key to
# an arbitrary pick is working on the finding, not replacing it with another.
DESCRIBES = {"arbitrary_pick": ("order_by",)}


def finding_key(check: str, subject: str, evidence) -> str:
    """What a finding IS, apart from how it is worded: its check, its subject, and the parts of
    its evidence that say what it points at (the measured numbers, and what only describes it,
    left out). Two runs' findings with one key are one finding. (C1, N5)"""
    from .checks.structural import _identity
    try:
        ev = json.loads(evidence) if isinstance(evidence, str) else (evidence or {})
    except (TypeError, ValueError):
        ev = {}
    ev = {k: v for k, v in (ev if isinstance(ev, dict) else {}).items()
          if k not in DESCRIBES.get(check, ())}
    return f"{check}|{subject}|{_identity(ev)}"


def carried(store, findings) -> dict:
    """{current finding id: earlier finding id} for a finding whose id changed while it stayed the
    same finding, so a ruling on the earlier id still reads as a ruling on it. (N5)

    Read-time only: nothing is written, and no verdict is ever copied onto a new id."""
    try:
        rows = store.con.execute(
            "select distinct finding_id, check_name, subject, evidence from findings "
            "where finding_id is not null").fetchall()
    except Exception:                                            # noqa: BLE001
        return {}
    by_key: dict = {}
    for fid, chk, subj, ev in rows:
        by_key.setdefault(finding_key(chk, subj, ev), set()).add(fid)
    out = {}
    for f in findings:
        for old in sorted(by_key.get(finding_key(f.check, f.subject, f.evidence), ())):
            if old != f.id:
                out.setdefault(f.id, []).append(old)
    return out


# --------------------------------------------------------------------------------- retention

# *** THE SCHEMA ALREADY ENCODES WHAT IS SAFE TO DELETE. ***
# A table carrying `run_id` is exactly one a parser regenerates for free: `assay check` rebuilds
# every row in seconds with no network and no spend. A table without one is exactly one that cost
# money or a keypress -- answers were paid for, claims were paid for, and a verdict is somebody's
# afternoon. So the split is not a judgement call, it is a column.
#
# Over-pruning costs one `assay check`. Under-pruning costs disk. Getting it wrong in the other
# direction costs the only thing in the store a release can never rebuild.
# *** A STATE IS NOT REBUILDABLE: IT IS WHAT WAS SENT TO A CALL SOMEBODY PAID FOR. ***
# It was in neither list, which is the one thing this split is supposed to make impossible -- and
# `SCHEMA.md` said "a new table belongs to one list or the other and a test fails until it does"
# while no such test existed. Kept, because a decision without the state it was computed from is
# an answer nobody can check. Orphans -- a state no decision points at -- are reported by
# `orphan_states()` and deleted by nothing, since a state is owned by its decision.
PRUNABLE = ("findings", "edge_facts", "unreadable", "premises", "premise_uses")
NEVER_PRUNED = ("model_calls", "model_decisions", "claims", "adjudications", "observed_keys",
                "runs", "calibrations", "compiled_sql", "commits",
                "states",
                # What assay spent on somebody's warehouse. A statement that has been paid for
                # cannot be regenerated by re-parsing a manifest, and on BigQuery it is a bill.
                "warehouse_calls",
                # A test's last result, read from Elementary or run_results.json. The build that
                # produced it is gone once dbt overwrites the file, so it cannot be re-read.
                "test_status",
                # How late rows arrive, counted through the project's own dbt: a warehouse query.
                "observed_lateness")


def prune(store, keep: int = 10) -> dict:
    """Drop all but the last `keep` runs from the tables a parser can regenerate.

    Returns {table: rows_removed}, plus `runs_kept` and `runs_dropped`.

    *** NOTHING WITHOUT A run_id IS EVER TOUCHED, AND THAT IS STRUCTURAL RATHER THAN CAREFUL. ***
    The loop only visits `PRUNABLE`. There is no flag, no `--all`, and no path through this
    function that reaches `model_decisions` -- because the way this goes wrong is somebody adding
    one later for a good reason.

    `runs` itself is kept whole. It is the clock every other table is dated by, nine rows on a
    real store, and dropping the row that names a run while keeping findings that point at it is
    how a `run_id` becomes unresolvable.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1; pruning to zero runs is not a retention "
                         "policy, it is a delete")
    rows = store.con.execute(
        "select run_id from runs order by started_at desc, run_id desc").fetchall()
    live = [r[0] for r in rows[:keep]]
    dead = [r[0] for r in rows[keep:]]
    out = {"runs_kept": len(live), "runs_dropped": len(dead)}
    if not dead:
        return out
    # A run present in a fact table and absent from `runs` has no clock, so it cannot be ranked
    # and must not be deleted on a guess. It is reported and left.
    for table in PRUNABLE:
        try:
            known = {r[0] for r in store.con.execute(
                f"select distinct run_id from {table}").fetchall()}
        except Exception:                                                # noqa: BLE001, S112
            # A table this store does not have yet. The others still prune.
            continue
        orphans = known - {r[0] for r in rows}
        if orphans:
            out.setdefault("orphan_runs", set()).update(orphans)
        n = store.con.execute(
            f"select count(*) from {table} where run_id in "
            f"({','.join('?' * len(dead))})", dead).fetchone()[0]
        if n:
            store.con.execute(
                f"delete from {table} where run_id in "
                f"({','.join('?' * len(dead))})", dead)
        out[table] = n
    if "orphan_runs" in out:
        out["orphan_runs"] = sorted(out["orphan_runs"])
    return out


def orphan_states(store) -> int:
    """States no decision points at any more. Reported, never deleted here.

    A state is owned by the decision that produced it, and decisions are never pruned -- so an
    orphan means something else removed a decision, which is worth saying out loud rather than
    tidying away.
    """
    try:
        return store.con.execute(
            "select count(*) from states s where not exists "
            "(select 1 from model_decisions d where d.state_hash = s.state_hash)").fetchone()[0]
    except Exception:                                                    # noqa: BLE001
        return 0
