"""assay as tools an agent can call, instead of reading your SQL.

*** THIS IS A CONTEXT ARGUMENT BEFORE IT IS A FEATURE. ***
To learn that a model is one row per section and joins on case number, an agent reads two hundred
lines of SQL and spends that much of its window. A contract is fifteen lines. Holding the meaning
of three hundred models costs about what reading four of them costs today.

*** AND IT IS A GUARDRAIL THE AGENT CAN CHECK ITSELF AGAINST. ***
`changed_contracts` answers "did my last edit change what anything MEANS", which is the question
nobody asks during a six-model refactor. Finding out mid-task beats finding out in review.

Requires the optional `mcp` extra. The rest of assay does not depend on it.
"""
from __future__ import annotations

import functools
import json
import traceback
from pathlib import Path

from . import live
from .store import Store, StoreLocked, _is_lock_error, lock_message

# Seconds a tool waits for another process to release the store before saying who holds it.
MCP_LOCK_WAIT = 5.0


class Backend:
    """Reloads when the manifest moves, so an agent never reads a stale contract."""

    def __init__(self, target: str, store_path: str | None = None, config_path: str = ".",
                 handbacks: str | None = None, verdicts_only: bool = False):
        self.target = target
        # Where `load_handback()` with no path looks, and whether it may only record verdicts.
        self.handbacks = handbacks
        self.verdicts_only = verdicts_only
        self.store_path = store_path
        # Where audit.yml lives, for the tools that read their words and their policy.
        self.config_path = config_path
        # When a lock was last seen, so one sweep holding the store does not cost every tool
        # call the full wait. And the lock message for THIS call, stamped on its result.
        self._lock_seen = 0.0
        self._store_locked = ""
        # Every store opened during one tool call, closed when the call ends (see `build_app`).
        self._opened: list = []
        self._state: live.LiveState | None = None
        self._stamp: float = 0.0
        self.baseline: live.Snapshot | None = None

    def _open_store(self):
        """Read-only use of the store, opened lazily: an agent that never asks for claims should
        not pay for a connection, and a missing store is an absence rather than an error."""
        return self._store_or_why()[0]

    def _store_or_why(self):
        """(store, why_not). *** A LOCKED STORE REPORTED ITSELF AS A MISSING ONE. ***

        Reported from the field: a session had the store open, so every write from the agent came
        back "no store to write to. Run any judged command once to create one" -- advice that
        cannot work, for a file that is right there. The agent then ran the judged command, which
        is the one thing guaranteed to fail for the same reason.

        DuckDB is single-writer. That is the actual fact, the fix is to close the other session,
        and an error that names the wrong cause sends a reader to the wrong place.
        """
        if not self.store_path:
            return None, "no --store was given, so there is nowhere to write."
        if not Path(self.store_path).exists():
            return None, (f"there is no store at {self.store_path}. Run any judged command once "
                          f"(`assay infer --judge`) to create one.")
        try:
            st = self._connect()
        except StoreLocked as e:
            # duckdb names the holding PID in its own error; the store reads it out and says who,
            # since then, and what they are running. "Waiting on PID 70034 since 19:42" is a
            # different experience from a cursor that does not move.
            return None, str(e)
        except Exception as e:                                          # noqa: BLE001
            # A lock error that did not arrive as `StoreLocked` -- a wording duckdb only uses in
            # some version, or a caller that opened the file some other way. The RULE for what
            # counts as a lock lives in one place; this is a second READER of it, not a second
            # spelling.
            if _is_lock_error(str(e)):
                return None, lock_message(self.store_path, str(e))
            return None, f"the store at {self.store_path} could not be opened: {e}"
        # *** AN EMPTY STORE AND A CLEAN WAREHOUSE PRODUCE THE SAME OUTPUT. ***
        # A store created at a path the container could not see reported `0 of 76 ruled`, with no
        # error and no warning. Every tool that reads the store carries this sentence when the
        # store is new, so an agent cannot mistake one for the other.
        self._new_store = st.new_store_warning()
        return st, ""

    def _connect(self) -> Store:
        """The store, waiting out a SHORT lock. Raises `StoreLocked` with the sentence to act on.

        *** AN AGENT BESIDE A PERSON RUNNING A SWEEP IS THE NORMAL CASE, NOT THE EDGE CASE. ***
        A CLI command holds the store for seconds; waiting that out costs nothing and needs no
        message at all. A 40-minute sweep will not be waited out, so once a lock has been seen the
        next half minute of calls do not wait again -- they say who holds it, immediately.
        `ASSAY_LOCK_TIMEOUT` still wins when it is set, the same knob every command reads.
        """
        import os
        import time
        env = os.environ.get("ASSAY_LOCK_TIMEOUT")
        try:
            wait = float(env) if env not in (None, "") else MCP_LOCK_WAIT
        except ValueError:
            wait = MCP_LOCK_WAIT
        if time.monotonic() - self._lock_seen < 30:
            wait = 0.0
        try:
            st = Store(self.store_path, timeout=wait)
            self._opened.append(st)
            return st
        except StoreLocked:
            self._lock_seen = time.monotonic()
            raise

    def _close_opened(self) -> None:
        """Close every store this call opened. Closing twice is harmless; holding one is not."""
        while self._opened:
            st = self._opened.pop()
            try:
                st.close()
            except Exception:                                    # noqa: BLE001,S110
                pass

    def _note_new_store(self, out: dict) -> dict:
        """Add `new_store` to a tool result when the store has nothing in it yet.

        On the result rather than in the prose, so an agent reading JSON cannot skim past it.
        """
        why = getattr(self, "_new_store", "")
        if why and isinstance(out, dict):
            out["new_store"] = why
        # *** A LOCKED STORE IS SAID ON THE RESULT, NOT LEFT TO LOOK LIKE AN EMPTY ONE. ***
        # The model's shape is read from the manifest and is still right; what the store adds
        # (rulings, measurements, judged answers) is absent for this call, and the reason is here.
        if self._store_locked and isinstance(out, dict) and "error" not in out:
            out["store_locked"] = self._store_locked
        return out

    def _manifest_mtime(self) -> float:
        p = Path(self.target) / "manifest.json"
        return p.stat().st_mtime if p.exists() else 0.0

    def state(self) -> live.LiveState:
        m = self._manifest_mtime()
        if self._state is None or m != self._stamp:
            # *** A LOCKED STORE PRESENTED AS TWELVE BROKEN TOOLS. ***
            # Reported from the field (25.20): this second store-open had none of the handling
            # `_store_or_why` has, so `StoreLocked` reached the SDK, which printed "Error executing
            # tool contract" for every tool that reaches `state()` -- and an agent reading twelve
            # of those concludes the server is broken and goes back to guessing. The lock is now
            # said, and the state is built from the manifest without the store and NOT cached, so
            # the next call after the lock clears reads the store again.
            store, locked = None, ""
            if self.store_path and Path(self.store_path).exists():
                try:
                    store = self._connect()
                except StoreLocked as e:
                    locked = str(e)
            if locked:
                self._store_locked = locked
            self._state = live.read(self.target, store)
            self._stamp = m if not locked else -1.0
            if store:
                # Refreshed here as well as in `_store_or_why`, because every tool reaches
                # `state()` and only some of them take a writable store.
                self._new_store = store.new_store_warning()
                store.close()
            if self.baseline is None:
                self.baseline = live.Snapshot.of(self._state.entries)
        return self._state

    # ---- the tools ----

    def run_cli(self, command: str, args: str = "", wait_seconds: float = 90) -> dict:
        """Any CLI command, as the CLI runs it. See `cli_tools`."""
        from . import cli_tools
        known = {c["name"]: c for c in cli_tools.commands()}
        if command not in known:
            return {"error": f"no command {command!r}. Commands: {sorted(known)}"}
        accepts = {f for fl in known[command]["flags"] for f in fl.split("/")}
        return cli_tools.run(command, args, self.target, self.store_path, wait_seconds,
                             accepts)

    def contract(self, model: str) -> dict:
        """The model's shape AND its health: what is open on it, what is waived, what is counted.

        *** AN ANATOMY CHART WITH NO CHART NOTES. ***
        The project's CLAUDE.md tells every agent to call this before editing a model, and it
        returned grain, columns and reads -- nothing about the three open findings, the waiver on
        `hop_multiplies_rows` that expires in March, or whether the grain was ever counted. "The
        grain is owner_key" tells an agent what a model is; "owner_key, measured unique over 3.09M
        rows today, one finding a person agreed with still open" changes what it writes.
        Everything here was already in the store; nothing is measured to answer this.
        """
        st = self.state()
        c = live.contract_of(st, model)
        if not c:
            return {"error": f"no model named {model}"}
        c["health"] = self._health(model, c)
        return c

    def _health(self, model: str, contract: dict) -> dict:
        from .config import Config
        from .judged import apply_policy
        from .probe import read as read_observed
        st = self.state()
        uid = next((u for u, m in st.project.models.items() if m.name == model), None)
        try:
            cfg = Config.load(self.config_path)
        except Exception as e:                                      # noqa: BLE001
            return {"error": f"audit.yml could not be read: {e}"}
        store = self._open_store()
        try:
            every = live.findings_for(st, model, store)
            kept, waived = apply_policy(every, cfg, store, st.project)
            ruled: dict = {}
            observed: dict = {}
            if store is not None:
                for subj, verdict, who, src, at in store.con.execute(
                        """select subject, verdict, decided_by, source, decided_at from (
                               select *, row_number() over (partition by subject, source
                                                            order by decided_at desc) rn
                               from adjudications where subject like ?)
                           where rn = 1""", [f"{uid}::finding::%"]).fetchall():
                    fid = str(subj).split("::finding::")[1]
                    # A person's ruling outranks an agent's; the agent's is shown as the agent's.
                    if src == "human" or fid not in ruled:
                        ruled[fid] = {"verdict": verdict, "by": who, "as": src,
                                      "at": str(at)[:10]}
                observed = read_observed(store)
        finally:
            if store is not None:
                store.close()
        open_ = [{"finding": f.id, "check": f.check, "summary": f.summary,
                  "action": a, "ruled_by": ruled.get(f.id)} for f, a, _w in kept]
        in_force = [{"finding": f.id, "check": f.check, "why": why} for f, why in waived]
        # *** THE GRAIN, AND WHETHER ANYBODY COUNTED IT. ***
        grain = contract.get("grain") or []
        rel = (st.schema.relation.get(uid) or "").replace('"', "").lower() if uid else ""
        seen = observed.get(rel) or {}
        measured = []
        for col in [*grain, ", ".join(grain)] if len(grain) > 1 else grain:
            o = seen.get(col)
            if o is not None:
                measured.append({"columns": col, "status": o.status, "rows": o.row_count,
                                 "observed_at": str(o.observed_at)[:10],
                                 "sampled": bool(o.sampled), "detail": o.detail})
        agreed_open = sum(1 for f in open_ if (f["ruled_by"] or {}).get("verdict") == "agree"
                          and (f["ruled_by"] or {}).get("as") == "human")
        # Fixed once and back: the finding that says so carries both commits. (G-B)
        regressed = [{"finding": f.id, "summary": f.summary,
                      "timeline": (f.evidence or {}).get("timeline")}
                     for f, _a, _w in kept if f.check == "fixed_finding_returned"]
        return {
            "open_findings": open_,
            "waived_or_accepted": in_force,
            "grain_measured": measured or None,
            "grain_note": (None if measured or not grain else
                           "the grain has not been counted in the data. `assay probe` counts it "
                           "through your own dbt."),
            "regressed": regressed,
            "summary": (f"{len(open_)} open finding(s), {agreed_open} a person agreed with"
                        + (f", {len(regressed)} fixed once and back" if regressed else "")
                        + f"; {len(in_force)} waived, accepted or dismissed"),
        }

    def premises(self, model: str = "", status: str = "", include_packages: bool = False) -> dict:
        """What the findings rest on: each premise, its evidence and status, and what rests on
        it -- the Guarantees tab's rows. Installed packages' premises are left out unless
        `include_packages`; `in_installed_packages` counts them, `counted` names the store and run."""
        st = self.state()
        store = self._open_store()
        try:
            return live.premises_report(st.project, st.digests, st.schema, st.entries, store,
                                        model=model, status=status,
                                        include_packages=include_packages)
        finally:
            if store is not None:
                store.close()

    def proofs(self, model: str = "") -> dict:
        """What is proven about each model, as long as what: the certificates `assay prove`
        wrote, each premise's status now, and whether the guarantee holds."""
        st = self.state()
        store = self._open_store()
        try:
            return live.proofs_report(st.project, st.digests, st.schema, st.entries, store,
                                      model=model)
        finally:
            if store is not None:
                store.close()

    def _proof_state(self):
        st = self.state()
        return st.project, st.digests, st.schema, st.entries

    def proof_goal(self, model: str, prop: str = "") -> dict:
        from . import proofwork
        store = self._open_store()
        try:
            return proofwork.goal(*self._proof_state(), store, model, prop)
        finally:
            if store is not None:
                store.close()

    def check_proof(self, model: str, prop: str, proof: str, helpers: str = "") -> dict:
        from . import proofwork
        store, _why = self._store_or_why()
        try:
            return proofwork.check(*self._proof_state(), store, model, prop, proof, helpers,
                                   by="agent")
        finally:
            if store is not None:
                store.close()

    def lineage(self, model: str, column: str) -> dict:
        from . import provenance
        st = self.state()
        uid = next((u for u, m in st.project.models.items() if m.name == model), None)
        if not uid:
            return {"error": f"no model named {model}"}
        hops = provenance.trace(column, uid, st.project, st.digests, st.schema)
        return {"model": model, "column": column,
                "hops": [{"model": a, "column": b, "what_happened": c, "why": d}
                         for a, b, c, d in hops],
                "note": ("the trail ends at a source; what produced the value is outside this "
                         "project" if hops and hops[-1][2] == "from_source" else "")}

    def blast_radius(self, model: str) -> dict:
        st = self.state()
        uid = next((u for u, m in st.project.models.items() if m.name == model), None)
        if not uid:
            return {"error": f"no model named {model}"}
        b = st.project.blast_radius(uid)
        return {"model": model, "descendants": b["descendants"], "marts": b["marts"],
                # The project's own declaration of what outside the warehouse this feeds. Empty
                # means no exposure reaches it, which is a fact about the project's yml, not a
                # proof that nothing reads the table.
                "exposures": b["exposures"],
                "consumers": [st.project.name_of(x) for x in st.project.models[uid].children]}

    def findings(self, model: str | None = None, limit: int = 20, check: str = "") -> dict:
        """Everything an agent needs to act, not a summary it has to re-derive.

        *** THIS RETURNED FIVE FIELDS WHILE `--json` RETURNED NINE. ***
        No file, so the agent could not open anything. No evidence, so three findings on one model
        came back with IDENTICAL summaries and no way to tell which column each was about. No
        blast radius, so it could not tell a leaf from a model twenty-five marts read. The data
        was already computed and thrown away at the one interface an agent uses.

        And the part neither surface had: WHAT MUST STAY TRUE. A fix is not judged by whether the
        finding disappears -- it is judged by whether the claims this project makes about the
        model are still true afterwards, and by whether a person's recorded verdict still holds.
        An agent that cannot see those will satisfy the check and break the meaning.
        """
        from .config import Config
        st = self.state()
        store, _why = self._store_or_why()
        cfg = Config.load(self.config_path)
        # The SAME open findings `check` reports: its stream, its self-audit, its policy. A
        # finding a person dismissed is not open, and is counted under `waived` instead.
        everything, waived, _acts = live.open_findings(
            st.project, st.digests, st.schema, st.entries, store, cfg, self.config_path)
        every = [f for f in everything if not model or f.subject_name == model]
        if check:
            every = [f for f in every if f.check == check]
        fs = every[:limit]
        from . import groups as groups_mod
        member = groups_mod.membership(groups_mod.build(st.project, everything))
        out = {"findings": [{"finding": f.id,
                             "check": f.check, "model": f.subject_name,
                             "file": f.file,
                             "summary": f.summary, "detail": f.detail,
                             "evidence": f.evidence or {},
                             "downstream": f.descendants, "marts": f.marts,
                             "exposures": f.exposures,
                             # The same construct in other models: fix it once, not N times.
                             **({"same_construct_in": member[f.id].as_dict()}
                                if f.id in member else {}),
                             "weight": round(f.weight, 2)} for f in fs]}
        # *** A SURFACE THAT SHOWS A SUBSET AND DOES NOT SAY SO IS THE SAME BUG AS A SCANNER
        # THAT MATCHES NOTHING AND REPORTS A PASS. ***
        # Reported from the field: `findings()` returned 20 of 162 across 2 of 7 families. Two
        # causes -- one was the judged stream missing entirely, now fixed in `live.all_findings`,
        # and the other is this: a default limit truncating by WEIGHT silently buries whole
        # families, and the buried ones were where a reading was worth most.
        counts: dict = {}
        for f in every:
            counts[f.check] = counts.get(f.check, 0) + 1
        out["showing"] = f"{len(fs)} of {len(every)}"
        if waived:
            out["not_shown_because_ruled_or_waived"] = len(
                [1 for f, _w in waived if not model or f.subject_name == model])
        out["every_check_in_this_project"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
        if len(fs) < len(every):
            out["what_you_are_not_seeing"] = (
                "ranked by blast radius, so the tail is not junk -- it is lower-reach. Pass "
                "`check=` to read one family end to end, or raise `limit`. "
                "`description_contradicts_the_code` and `hop_multiplies_rows` need prose or a "
                "join path read against the SQL, which is what you are for and a parser is not.")
        if model:
            out["must_stay_true"] = self._constraints(model)
        return out

    def _constraints(self, model: str) -> dict:
        """The claims and rulings a fix to this model must not break.

        A finding says what is wrong. This says what was already RIGHT, which is the half an agent
        needs in order not to trade one defect for another.
        """
        c = self.claims(model)
        claims = [x for x in c.get("claims", []) if x.get("code") == "supports"]
        st = self._open_store()
        verdicts: list = []
        if st is not None:
            try:
                uid = next((u for u, m in self.state().project.models.items()
                            if m.name == model), None)
                if uid:
                    rows = st.con.execute(
                        "select family, answered, verdict from adjudications "
                        "where source = 'human' and (subject = ? or subject like ?)",
                        [uid, uid + "::%"]).fetchall()
                    verdicts = [{"question": r[0], "answer": r[1], "a_person_said": r[2]}
                                for r in rows]
            finally:
                st.close()
        return {
            "claims_the_code_currently_supports": [x["claim"] for x in claims][:12],
            "verdicts_a_person_recorded": verdicts[:12],
            "note": ("A fix is not finished when the finding goes away. It is finished when these "
                     "are still true. If your change makes one of them false, change the claim "
                     "too and say so -- `assay claims --extract` will pick it up, and "
                     "`assay regress` will tell you which recorded verdicts your change moved."),
        }

    def changed_contracts(self) -> dict:
        """What the working tree changed since the baseline. The self-check.

        `state()` takes the baseline on its first load, so there is no "no baseline yet" branch to
        write; the first call correctly reports nothing changed, because nothing has.
        """
        st = self.state()
        from . import diff as diff_mod
        ch = live.changes_since(self.baseline, st)
        return {
            "changes": [{"model": c.model, "kind": c.kind, "column": c.column,
                         "before": c.before, "after": c.after, "detail": c.detail,
                         "consumers": len(c.consumers),
                         "aggregating_consumers": c.aggregating_consumers} for c in ch],
            "summary": diff_mod.summarize(ch) if ch else "Nothing means anything different.",
            "still_typing": st.unparsed,
        }

    def practices(self, model: str = "") -> dict:
        """Standard-practice status, and the uniqueness test a model is missing.

        The pure-code half only: what a model's grain is and whether anything asserts it. The
        evaluator-backed half needs a warehouse round trip and belongs in the CLI.
        """
        from . import practices as prac
        st = self.state()
        patches = prac.primary_key_patches(st.project, st.entries)
        if model:
            patches = [p for p in patches if p[0] == model]
        # *** A 5-TUPLE UNPACKED AS FOUR, AND NO TEST TOUCHED IT. ***
        # `primary_key_patches` grew `not_emitted` when the field found 9 of 15 proposed grains
        # naming a column the model does not emit. The CLI was updated; this tool was not, so the
        # MCP call raised ValueError while 375 tests passed -- the same shape as the circular
        # import that killed the binary. The guard below exercises the tool, not the source.
        out = []
        for n, cols, src, m, not_emitted in patches[:40]:
            row = {"model": n, "grain_a_test_should_cover": cols, "grain_source": src, "marts": m}
            if not_emitted:
                # The stronger finding: nothing downstream can assert this model's own uniqueness.
                row["but_the_model_does_not_emit"] = list(not_emitted)
                row["so"] = ("no test can assert this grain. The model groups or dedups on a "
                             "column it then drops, so its uniqueness is unassertable downstream")
            out.append(row)
        return {"missing_uniqueness_tests": out}

    def _resolve_subject(self, subject: str, question: str, store) -> tuple[str, str, str]:
        """(key, how_it_resolved, error). *** IT ACCEPTED A NAME THAT JOINED TO NOTHING. ***

        Reported from the field, and it is the ninth instance of this shape in this codebase --
        this time in the write path of the feature built to close the loop. `findings.subject` is
        `model.sunny_data.int_azcc_owners`. `rule()` was handed the bare `int_azcc_owners`,
        answered `recorded: true`, and wrote NINETY-NINE rows that join to zero findings. The
        visible symptom was `review_queue` returning twenty items with no agent readings attached,
        directly under its own note promising that findings an agent has read come first.

        One fact, two spellings, silent when they disagree. So there is now exactly one spelling
        and anything else is either resolved out loud or REFUSED. A write that cannot be joined
        back is not a write, and reporting it as one is worse than failing.
        """
        state = self.state()
        if subject in state.project.models:
            return subject, "a model unique_id", ""
        row = store.con.execute(
            "select 1 from model_decisions where decision_key = ? and question = ? limit 1",
            [subject, question]).fetchone()
        if row:
            return subject, "a decision key", ""
        if "::" in subject and subject.split("::")[0] in state.project.models:
            return subject, "a decision key on a known model", ""
        hits = [uid for uid, m in state.project.models.items() if m.name == subject]
        if len(hits) == 1:
            return hits[0], f"resolved from the bare name {subject!r}", ""
        if len(hits) > 1:
            return "", "", (f"{subject!r} is the name of {len(hits)} models. Pass the unique_id, "
                            f"or call review_queue() and pass the `finding` id it gives you.")
        return "", "", (f"nothing was recorded. {subject!r} is not a model unique_id, not a "
                        f"decision key, and not the name of any model in this project -- so a "
                        f"row written under it would join to no finding and no answer. Call "
                        f"review_queue() and pass back the `finding` id, which is exact.")

    def rule(self, verdict: str, why: str, finding: str = "", subject: str = "",
             question: str = "", correction: str = "", decided_by: str = "",
             until: str = "") -> dict:
        """*** RULINGS ARE THE ONLY THING IN THIS SYSTEM THAT DO NOT COMPOUND. ***

        More checks find more. Better states judge better. The warehouse accrues. None of that
        raises the number that says whether anything was UNDERSTOOD, because the only thing that
        can produce a ruling at scale could not, until now, write one down.

        So an agent can. And it is filed as `agent`, apart from what a person ruled, because the
        ruled-on figure is the one number here nobody can game -- an agent able to raise it would
        destroy the property that makes it worth printing. This gates nothing, satisfies no
        `min_adjudications`, and anchors no `regress`. It triages.

        A reason is required, exactly as it is for a waiver. A ruling with no reason is one nobody
        can check, which is the thing this whole tool exists to object to.

        *** `decided_by` NAMES A PERSON. IT DOES NOT MAKE THE RULING THEIRS. ***
        Asked for from the field, for the real case where somebody is sitting there saying "that
        one is wrong, it is a union". Their name is worth recording: a reviewer reading the queue
        later can tell "the agent thinks" from "Ryan said, and the agent typed it".

        What it deliberately does NOT do is file the ruling as human. The agent is the only thing
        in the loop that could write a hundred of these, so a field it fills in itself cannot be
        the field that decides whether a question may fail a build. The person re-rules it in
        `assay review -i`, where their own keypress is the evidence, and that takes one keystroke
        because the reason is already on screen.

        *** `finding` IS THE EXACT HANDLE AND `subject` IS THE COARSE ONE. ***
        A verdict on a model lands on every finding that model has: `az_section_summary` carries
        eight `test_cannot_fail` findings and one keypress answered all eight. It is also how a
        CORRECT finding gets ruled wrong -- `dim_business` was read as a union false positive,
        true of four of its six edges, while two join on (geography, building_key) against a grain
        of (geography, business_key, building_key) and fan out 1.48x. `silently_multiplied` was
        right about those two and the model-level ruling covered them anyway.

        `review_queue()` and `findings()` both return a `finding` id. Pass it back.
        """
        if verdict not in ("agree", "disagree", "unclear", "accept"):
            return {"error": "verdict must be agree, disagree, unclear or accept"}
        if until:
            from .store import _valid_date
            try:
                _valid_date(until)
            except ValueError as e:
                return {"error": str(e)}
        if not (why or "").strip():
            return {"error": "a reason is required. A ruling nobody can check is not evidence."}
        if not (finding or subject):
            return {"error": "pass `finding` (the id from review_queue or findings), or "
                             "`subject` and `question` for a judged answer."}
        st, store_why = self._store_or_why()
        if st is None:
            return {"error": f"nothing was recorded: {store_why}"}
        resolved_as = ""
        try:
            if finding:
                f = next((x for x in live.findings_for(self.state(), None) if x.id == finding),
                         None)
                if f is None:
                    return {"error": f"no finding with id {finding!r} in this project right now. "
                                     f"It may have been fixed, or the manifest may have moved. "
                                     f"Call review_queue() for the current ids."}
                subject = f"{f.subject}::finding::{f.id}"
                question = question or f.check
                resolved_as = f"finding {f.id} -- {f.check} on {f.subject_name}"
                # *** A VERDICT ON A JUDGED FINDING IS EVIDENCE ABOUT A PROBABILITY. ***
                # The finding is keyed on itself; the confidence lives on the decision that
                # produced it, keyed by the SUBJECT the question was asked about. Without this
                # the verdict never reaches the number it ruled on -- measured: of 105 agent
                # rulings on the field store, zero joined to a decision.
                #
                # `rests_on` is empty for a structural finding, and then so is this. A parser
                # decided it, no question was asked, and there is no probability to calibrate.
                dkey = f.subject if f.rests_on else ""
            else:
                subject, resolved_as, err = self._resolve_subject(subject, question, st)
                if err:
                    return {"error": err}
            answered, pv, mv = "", "", "" 
            row = st.con.execute(
                "select answer, prompt_version, model_version from model_decisions "
                "where decision_key = ? and question = ? order by decided_at desc limit 1",
                [subject, question]).fetchone()
            if row:
                answered, pv, mv = row[0], row[1] or "", row[2] or ""
            else:
                # *** A STRUCTURAL FINDING HAS NO QUESTION, SO IT HAS NO PROMPT VERSION. ***
                # It has assay's, which is the version of the CHECK being ruled on, and without it
                # 99 of 107 rulings on a real store carried no version at all -- the two families
                # 0.12.0 fixed were the two at 0% agreement and nothing could have said so.
                from . import __version__
                pv = f"assay.{__version__}"
            # `contracts.family_of` and not `cli._family_of`: cli imports this module, so
            # reaching back into it is a circular import that kills the binary while the test
            # suite -- which imports in a different order -- stays green.
            from .contracts import family_of
            who = (decided_by or "").strip()[:60]
            st.adjudicate(subject, question, family_of(question) or question.split("__")[0],
                          answered, verdict, correction=correction, note=why.strip(),
                          who=f"agent, relaying {who}" if who else "agent", source="agent",
                          prompt_version=pv, model_version=mv,
                          decision_key=locals().get("dkey", ""), until=until)
            human = len(st.ruled_subjects())
            mine = len(st.agent_rulings())
        finally:
            st.close()
        out = {
            "recorded": True, "subject": subject, "resolved_as": resolved_as, "verdict": verdict,
            "agent_rulings_now": mine, "models_a_person_has_ruled_on": human,
            "what_this_does": ("It puts this in front of whoever reviews next, ranked above what "
                               "nobody has read. `assay review -i` shows your reason beside the "
                               "finding."),
            "what_this_does_not_do": ("It does not gate a build, does not count toward the "
                                      "verdicts a question needs before it may fail one, and does "
                                      "not anchor `assay regress`. Those all require a person, on "
                                      "purpose."),
        }
        if (decided_by or "").strip():
            out["relayed_from"] = decided_by.strip()[:60]
            out["and_still_an_agent_ruling"] = (
                f"{out['relayed_from']} is recorded as who decided it, so a reviewer can tell "
                f"this from your own conclusion. It is still filed as `agent`: a person's ruling "
                f"is their keypress in `assay review -i`, which is one keystroke now that your "
                f"reason is on screen.")
        return out

    def load_handback(self, path: str = "", apply: bool = False, by: str = "",
                      verdicts_only: bool = False) -> dict:
        """Record the verdicts a PERSON wrote in the review form. The one tool that files `human`.

        *** THE HUMAN DID THE MOST VALUABLE WORK IN THE SYSTEM AND THE FILE SAT IN ~/Downloads. ***
        Reported from the field: "outputting that file is cool and all but we need to actually
        perform the steps to make that file do something." The CLI path existed --
        `assay review --load handback.json --apply` -- and MCP could not reach it, so the agent
        that handed somebody the form could not close the loop it had opened.

        *** THIS IS THE ONE PLACE AN AGENT MAY CAUSE A `human` ROW TO EXIST. ***
        Every verdict here came from a keypress in the form. The agent is a courier: it did not
        decide any of these and cannot add one, because `load` reads only what the file carries
        and a card nobody answered is not in it. That is why the tool takes a PATH and has no
        parameter for a verdict -- there is no shape of this call that invents an opinion.

        `apply` also writes the Words, Explanations and Waivers boxes into `audit.yml`. Off by
        default: the verdicts are a record of what somebody said, and editing their config is a
        different act that they should see a diff of first.
        """
        from pathlib import Path as _P

        from . import handback as hb
        # No path means the newest handback in the handback folder: `--handbacks` on `assay mcp`,
        # then `review.handbacks` in audit.yml, then ~/Downloads, where the form's download goes.
        # On a server the first two point at the folder `assay serve` saves into. (W1, S4)
        if not path or path == "latest":
            from .config import Config
            where = hb.folder(Config.load(self.config_path), self.handbacks)
            found = hb.newest(where)
            if found is None:
                return {"error": f"no handback*.json in {where}. Ask the person where it was "
                                 f"saved."}
            path = str(found)
        src = _P(path).expanduser()
        if not src.exists():
            return {"error": f"no file at {src}; ask for the path rather than guessing.",
                    "recorded": 0}
        store, why = self._store_or_why()
        if store is None:
            return {"error": why, "recorded": 0}
        try:
            payload = json.loads(src.read_text())
            out = {"file": str(src), **hb.record(store, payload, by)}
            edits = hb.refused_config(payload)
            if verdicts_only:
                # (S3) A server's audit.yml comes from git: refuse the edits, and name them.
                out["config_refused"] = edits
                out["config_note"] = ("verdicts only: audit.yml was not touched. Apply these "
                                      "from a checkout of the repository with `assay review "
                                      f"--load {src.name} --apply`, then commit audit.yml.")
            elif apply or edits:
                out["config_changes"] = len(edits)
                out["config_edits"] = edits[:12]
                out["config_note"] = ("Changes are reported, not written from here: run "
                                      "`assay review --load <path> --apply` so the person sees "
                                      "the diff against their own audit.yml before it changes.")
            return out
        finally:
            store.close()

    def review_queue(self, limit: int = 20) -> dict:
        """What is waiting for a PERSON, with the agent's reading already attached.

        *** A RULING THAT NOBODY EVER SEES AGAIN IS NOT TRIAGE. ***
        `rule` says it "puts this in front of whoever reviews next" and there was no way, from
        MCP, to see that queue -- so an agent could write a hundred rulings and never tell whether
        any of them had been read, or whether it was about to rule a second time on the same
        subject. Ranked by marts downstream, because that is the order a person should read in.
        """
        st = self.state()
        fs = live.findings_for(st, None)
        store, why = self._store_or_why()
        ruled_pairs: set = set()
        ruled: set = set()
        mine: dict = {}
        orphans: list = []
        if store is not None:
            try:
                ruled = store.ruled_subjects()
                # Read INSIDE the try: the `finally` below closes this connection, and a read
                # after it raises `Connection already closed`.
                ruled_pairs = store.ruled_pairs()
                known = set(self.state().project.models)
                for r in store.agent_rulings():
                    key = str(r["subject"])
                    if "::finding::" in key:
                        mine[key.split("::finding::")[1]] = r          # exact
                    else:
                        # *** A MODEL-LEVEL RULING ANSWERED ONE CHECK. *** Keyed by model alone, an
                        # agent's `bbox_as_radius` disagree was shown as the reading on three
                        # unrelated findings -- and disagree is a permanent dismissal, one careless
                        # keypress from landing on the wrong one.
                        mine.setdefault((key.split("::")[0], str(r.get("question") or "")), r)
                    if key.split("::")[0] not in known:
                        # *** A RULING THAT JOINS TO NOTHING IS REPORTED, NOT SWALLOWED. ***
                        # 99 of them existed before `rule` started refusing a subject it could
                        # not resolve. `assay review --repair` resolves the ones whose model name
                        # is unambiguous; nothing here guesses.
                        orphans.append(key)
            finally:
                store.close()
        # *** A VERDICT COVERS (SUBJECT, QUESTION), AND THIS SKIPPED ON THE SUBJECT. ***
        # Ruling one check on a model dropped every other check on it from the queue that exists
        # to show what is still waiting -- silently, by omission, which is the shape this project
        # reports in other people's warehouses. Found while building the review form, where the
        # identical line made four verdicts remove six cards.
        rows = []
        for f in fs:
            if (str(f.subject), str(f.check)) in ruled_pairs \
                    or f"{f.subject}::finding::{f.id}" in ruled:
                continue
            a = mine.get(f.id) or mine.get((str(f.subject).split("::")[0], str(f.check)))
            rows.append({"finding": f.id,
                         "check": f.check, "model": f.subject_name, "file": f.file,
                         "summary": f.summary, "marts": f.marts,
                         "an_agent_already_said": (
                             {"verdict": a["verdict"], "because": a["note"],
                              "at": ("this exact finding" if mine.get(f.id) else
                                     "this check on this model, so it covers every finding "
                                     "of this check here")}
                             if a else None)})
        rows.sort(key=lambda r: (r["an_agent_already_said"] is None, -r["marts"]))
        out = {
            "waiting_for_a_person": rows[:limit],
            "already_ruled_by_a_person": len(ruled),
            "note": ("Findings an agent has read are first: a person confirming a reading is one "
                     "keypress, and a finding nobody has looked at is a cold start. Nothing here "
                     "is resolved -- an agent ruling never clears an item from this queue."),
            "pass_the_finding_id_back": ("rule(finding='<id>', verdict=..., why=...). A verdict "
                                         "on a MODEL lands on every finding that model has, and "
                                         "one model here carries eight."),
        }
        if orphans:
            out["rulings_that_join_to_nothing"] = {
                "count": len(orphans), "examples": sorted(set(orphans))[:5],
                "why": ("these were written under a subject that is not a model unique_id, so "
                        "they attach to no finding. `rule` now refuses such a write. Run "
                        "`assay review --repair` to resolve the ones whose model name is "
                        "unambiguous; it guesses nothing."),
            }
        if store is None and why:
            out["and_no_rulings_could_be_read"] = why
        return out

    def violations(self, model: str = "", config_path: str = ".") -> dict:
        """What would actually fail, under this project's own policy.

        *** AN AGENT COULD SEE EVERY FINDING AND NOT WHICH ONES STOP A BUILD. ***
        `findings` lists what is wrong. Most of it is configured to annotate, some to queue, and a
        little to fail -- and only the last group is the difference between handing work back and
        handing back a red pipeline. Reading the whole list and guessing which is which is exactly
        the judgment an agent should not be making.

        This applies the SAME `apply_policy` the CLI and the Action apply, including the refusal
        to let a judged question fail a build before it has recorded human verdicts. A clean
        answer here means a green build, not an opinion that it should be.
        """
        from .config import Config
        from .judged import apply_policy
        st = self.state()
        fs = live.findings_for(st, model or None)
        cfg = Config.load(config_path)
        store = self._open_store()
        try:
            policed, waived = apply_policy(fs, cfg, store, st.project)
        finally:
            if store is not None:
                store.close()
        buckets: dict = {"fail": [], "queue": [], "annotate": []}
        for f, action, why in policed:
            buckets.setdefault(action or "annotate", []).append(
                {"check": f.check, "model": f.subject_name, "file": f.file,
                 "summary": f.summary, "marts": f.marts, "because": why})
        return {
            "would_fail_the_build": buckets["fail"],
            "queued_for_a_person": buckets["queue"][:20],
            "annotated_only": len(buckets["annotate"]),
            # *** ONE WAIVER, ONE ROW. *** A waiver covering two findings on one model was listed
            # twice, which reads as two decisions. Grouped, with how many findings it covers.
            "waived": _grouped_waivers(waived),
            "verdict": ("this would fail" if buckets["fail"] else "this would pass"),
            "note": ("Only `would_fail_the_build` stops CI. A judged question cannot appear there "
                     "until it has recorded human verdicts, so an empty list may mean nothing is "
                     "wrong OR that nothing has earned the right to gate yet -- `assay config` "
                     "says which."),
        }

    def claims(self, model: str = "") -> dict:
        """The claims on a model, each with what the code did to it.

        *** THE POINT IS TO BE READ BEFORE THE EDIT, NOT AFTER. ***
        An agent that reads the SQL learns what the code does. These are what a person SAID it
        does, which is the thing an edit is most likely to quietly break.
        """
        st = self._open_store()
        if st is None:
            return {"error": "no store yet. Run `assay claims --extract`."}
        try:
            rows = st.claims(subject=model or None, checkable_only=True, min_conf=0.7)
            if not rows:
                return {"claims": [], "note": "none extracted for this model yet"}
            by_id = {r["claim_id"]: r for r in rows}
            verdicts = {}
            for r in st.con.execute(
                    "select decision_key, answer from model_decisions "
                    "where question = 'claim'").fetchall():
                cid = str(r[0]).split("::claim::")[-1]
                if cid in by_id:
                    verdicts[cid] = r[1]
            return {"claims": [{"claim": r["text"], "where": r["source_ref"], "kind": r["kind"],
                                "code": verdicts.get(r["claim_id"], "not checked"),
                                **({"cites": r["citation"]} if r["citation"] else {})}
                               for r in rows]}
        finally:
            st.close()

    def traversal(self, model: str) -> dict:
        """Every hop into this model, and what the judgment made of it."""
        from . import relate
        ls = self.state()
        uid = next((u for u, m in ls.project.models.items() if m.name == model), None)
        if not uid:
            return {"error": f"no model named {model!r}"}
        facts, _ = relate.run_all(ls.project, ls.digests, ls.schema)
        hops = [{"from": ls.project.name_of(f.parent),
                 "joins_on": sorted(f.joined_on or []) or None,
                 "drops": len(f.dropped or []),
                 "carries": len(f.carried or [])}
                for f in facts if f.child == uid]
        out: dict = {"model": model, "hops": hops}
        st = self._open_store()
        if st is not None:
            try:
                # *** IT RETURNED TWELVE VERDICTS FOR FOUR HOPS AND CONTRADICTED ITSELF. ***
                # The same hop came back `silently_multiplied` and `deliberately_coarser`
                # because every one of those answers predated a version bump. An agent reading
                # this to decide about a hop was handed both and no way to tell which was live.
                rows = st.live_decisions(
                    "question = 'edge' and decision_key like ?", [uid + "::edge::%"],
                    columns="question, context, answer")
                out["judged"] = [{"hop": r[1], "verdict": r[2]} for r in rows]
                if st.superseded_decisions:
                    out["older_answers_to_the_same_hop_not_shown"] = st.superseded_decisions
                if st.stale_decisions:
                    # Dated, never hidden: the question was rewritten since these were given, so
                    # they are the only answers there are until `assay traverse` re-asks.
                    out["answers_given_against_a_question_that_has_since_changed"] = (
                        st.stale_decisions)
            finally:
                st.close()
        return out

    def rebase(self) -> dict:
        self.baseline = live.Snapshot.of(self.state().entries)
        return {"ok": True, "models": len(self.baseline.entries)}

    def _fixes(self):
        """(fixes, findings, store) for the current manifest."""
        from . import fixes as fixes_mod
        from . import groups as groups_mod
        from . import ledger as ledger_mod
        st = self.state()
        store, _why = self._store_or_why()
        fs = live.findings_for(st, None, store=store)
        fx = fixes_mod.build(st.project, fs, entries=st.entries, digests=st.digests,
                             schema=st.schema, store=store, led=ledger_mod.last(),
                             groups=groups_mod.build(st.project, fs),
                             root=st.project.project_root)
        return fx, fs, store

    def plan_items(self, limit: int = 25, kind: str = "") -> dict:
        """The fixes, ranked: what to change next and how much each resolves."""
        from . import fixes as fixes_mod
        fx, fs, store = self._fixes()
        st = fixes_mod.statuses(store)
        rows = [{**f.as_dict(), "status": st.get(f.id, {}).get("status", "proposed")}
                for f in fx if not kind or f.kind == kind]
        for r in rows:
            r.pop("findings", None)
        return {"open_findings": len(fs), "fixes": rows[:limit], "total": len(rows),
                "rule": ("A person approves a fix (the Fix cards in the review form, or `assay "
                         "fix <id> --approve`). You apply only an approved one, in a branch, "
                         "with apply_plan_item, then run dbt parse/compile and "
                         "verify_plan_item.")}

    def plan_item(self, fix_id: str) -> dict:
        """One fix in full: its diff, its recipe, the findings it resolves, where it stands."""
        from . import fixes as fixes_mod
        fx, _fs, store = self._fixes()
        f = next((x for x in fx if x.id == fix_id), None)
        if f is None:
            return {"error": f"no fix {fix_id} in the current plan (plan_items lists them)"}
        root = self.state().project.project_root
        return {**f.as_dict(), "diff": fixes_mod.diff(f, root),
                "status": fixes_mod.statuses(store).get(f.id, {}).get("status", "proposed"),
                "pending_files": fixes_mod.applied(f, root)}

    def apply_plan_item(self, fix_id: str) -> dict:
        """Write an APPROVED fix's files into the working tree (your branch). Refused otherwise."""
        from . import fixes as fixes_mod
        fx, _fs, store = self._fixes()
        f = next((x for x in fx if x.id == fix_id), None)
        if f is None:
            return {"error": f"no fix {fix_id} in the current plan"}
        status = fixes_mod.statuses(store).get(f.id, {}).get("status")
        if status not in ("approved", "applied"):
            return {"refused": (f"fix {fix_id} is {status or 'proposed'}, not approved. A person "
                                f"approves it on the Fix card in the review form or with "
                                f"`assay fix {fix_id} --approve`; then apply it.")}
        if f.refused:
            return {"refused": "parts of this fix could not be placed: " + "; ".join(f.refused)}
        root = Path(self.state().project.project_root)
        wrote = []
        for path, text in sorted(f.files.items()):
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            wrote.append(path)
        wstore, _why = self._store_or_why()
        if wstore is not None:
            fixes_mod.record(wstore, f.id, "applied", kind=f.kind, key=f.key, title=f.title,
                             by="agent")
        return {"wrote": wrote, "then": f.recipe + ["verify_plan_item(" + fix_id + ") after "
                                                    "dbt parse or compile"]}

    def verify_plan_item(self, fix_id: str) -> dict:
        """Is the fix in the working tree, and are the findings it resolves gone from the manifest
        as it is now? Run dbt parse (or compile, for SQL) first."""
        from . import fixes as fixes_mod
        fx, fs, store = self._fixes()
        f = next((x for x in fx if x.id == fix_id), None)
        if f is None:
            # once applied, a fix that worked has nothing left to resolve and leaves the plan
            return {"fix": fix_id, "verified": True,
                    "note": "no longer in the plan: nothing it resolves is still open"}
        root = self.state().project.project_root
        pending = fixes_mod.applied(f, root)
        live_ids = {x.id for x in fs}
        still = [x for x in f.findings if x in live_ids]
        ok = not pending and not still
        if ok and store is not None:
            fixes_mod.record(store, f.id, "verified", kind=f.kind, key=f.key, title=f.title,
                             by="agent")
        return {"fix": fix_id, "verified": ok, "files_not_yet_as_the_fix_writes": pending,
                "findings_still_open": len(still), "recipe": f.recipe}

    def plan(self, limit: int = 25) -> dict:
        """What to DO about the findings a PERSON agreed with.

        *** THE LOOP RAN OUT BETWEEN "THIS IS REAL" AND "IT IS FIXED". ***
        `findings` says what is wrong. `rule` records what you concluded. Nothing said what to
        change, so an agent holding forty agreed findings had forty sentences and no next step.

        The fix SHAPE is a lookup on the check name -- exact, free, and the same for every model
        carrying that check. The WORDS are not: what a sentence should say instead is a judgment
        about a real warehouse, and writing one yourself is the same failure as writing a
        vocabulary definition yourself.
        """
        from . import plan as plan_mod
        store, why = self._store_or_why()
        from . import groups as groups_mod
        _fs = live.findings_for(self.state(), None)
        rows = plan_mod.build(_fs, store, groups_mod.build(self.state().project, _fs),
                              project=self.state().project)
        out = {
            "to_fix": rows[:limit], "total": len(rows),
            "rule": ("`fix_shape` is what KIND of change this is, and it is exact. It does not "
                     "tell you the words -- for a prose finding, what the sentence should say "
                     "instead is theirs. Propose it and let them decide."),
            "then": ("after the edit: changed_contracts, violations, then `assay check`, which "
                     "reports how many of the agreed findings are now gone. That is the only "
                     "number measuring whether any of this worked."),
        }
        if not rows:
            n = len(store.ruled_findings("agree")) if store is not None else 0
            out["nothing_to_plan"] = (
                f"{n} finding(s) carry a human `agree`. A plan is built from those and only "
                f"those. An empty plan means nobody has agreed with anything yet, NOT that the "
                f"warehouse is clean. `assay review --emit` is where verdicts come from.")
        if why:
            out["store"] = why
        return out

    def spend(self) -> dict:
        """What the judged tier has cost on this project, from the calls that were made.

        *** AN AGENT DECIDING WHETHER TO ASK SHOULD BE ABLE TO SEE WHAT ASKING COSTS. ***
        Every total here is one row per CALL. `model_decisions` is one row per ANSWER carrying its
        call's token count, so summing that counts a batched call once per answer -- $4.25 on a
        store that spent $1.32.
        """
        store = self._open_store()
        if store is None:
            return {"note": "no store here, so nothing has been asked and nothing has been spent"}
        try:
            from . import cost as cost_mod
            led = cost_mod.ledger(store)
        except Exception as e:                                   # noqa: BLE001
            return {"note": f"no ledger in this store: {e}. Answers decided before assay recorded "
                            f"its calls cost something unknown, which is not zero."}
        if not led.get("calls"):
            return {"usd": 0.0, "calls": 0,
                    "note": "no calls recorded here. Either nothing has been asked, or every "
                            "answer came from the cache -- which is free and is the point."}
        return {"usd": round(led["usd"], 4), "calls": led["calls"],
                "input_tokens": led["input_tokens"],
                "by_caller": led["by_caller"][:8], "by_day": led["by_day"][:7],
                "calls_without_usage": led["calls_without_usage"],
                "note": "output tokens are counted and never priced: Jev does not bill them."}

    def stale(self, exact: bool = False) -> dict:
        """Judged answers that are about SQL which has since changed.

        *** SERVING A DATED ANSWER IS FINE. NOT KNOWING IT IS DATED IS NOT. ***
        The cheap tier compares the sha256 dbt already records for each model's source. `exact`
        rebuilds the state each answer was computed from and compares it, which catches a change
        to a PARENT that a file checksum by definition cannot. Neither makes an API call.
        """
        store = self._open_store()
        if store is None:
            return {"note": "no store here, so there are no judged answers to be stale"}
        from . import stale as stale_mod
        st = self.state()
        if exact:
            from . import states as states_mod
            from .config import Config
            cfg = Config.load(self.config_path or ".")
            ctx = states_mod.Ctx(project=st.project, digests=st.digests, schema=st.schema,
                                 store=store, vocab=getattr(cfg, "vocab", None) or {})
            out = stale_mod.exact(store, ctx)
            return {"judged": out["judged"], "would_be_computed_differently": len(out["moved"]),
                    "rebuild_identically": out["current"],
                    "cannot_be_compared": out["uncomparable"],
                    "why_not": out["why_uncomparable"][:6],
                    "by_blast_radius": out["by_model"][:10],
                    "note": "nothing here is hidden from any other tool: a dated answer is still "
                            "served, because hiding it leaves you with nothing."}
        out = stale_mod.survey(store, st.project)
        return {"judged": out["judged"], "about_sql_that_changed": len(out["moved"]),
                "current": out["current"], "cannot_be_checked": len(out["uncheckable"]),
                "why_not": out["why_uncheckable"][:6],
                "by_blast_radius": out["by_model"][:10],
                "note": "a moved checksum is necessary and not sufficient: a comment edit trips "
                        "it and a change to a PARENT does not. Call with exact=true for that."}

    def vocabulary(self) -> dict:
        """Their words, where each one is true, and every way the list is currently wrong.

        *** A TERM GOES INTO EVERY STATE, SO A TERM THAT IS FALSE HERE IS FALSE EVERYWHERE. ***
        Measured on a real warehouse: six terms asserting one state's water law reached all 358
        models, and 25% of every answer ever paid for there was about a model in another state.
        """
        from .config import Config
        from .lint import lint_vocab
        cfg = Config.load(self.config_path or ".")
        st = self.state()
        issues = lint_vocab(cfg.vocab, st.project)
        scoped = {t: b.get("applies_to") for t, b in (cfg.vocab or {}).items()
                  if isinstance(b, dict) and b.get("applies_to")}
        return {
            "terms": sorted(cfg.vocab or {}),
            "scoped": scoped,
            "sent_everywhere": sorted(set(cfg.vocab or {}) - set(scoped)),
            "issues": [{"term": i.question.split(".", 1)[-1], "level": i.level, "rule": i.rule,
                        "detail": i.detail} for i in issues],
            "note": ("`applies_to` takes the same selector as --select, and {select:, exclude:} "
                     "when the exception lives inside the rule. A term with no scope is sent to "
                     "every model, which is correct for a word about their DATA and wrong for one "
                     "that asserts somebody's law. Read guide('vocab') before writing one."),
        }

    def monitoring(self, volume_json: str = "") -> dict:
        """Is anything WATCHING this warehouse, and are the declared tests actually running?

        *** THE ONE QUESTION EVERY OTHER TOOL HERE ASSUMES SOMEBODY ELSE ANSWERED. ***
        Every other tool reports on the SQL. This reports on whether a change to what that SQL
        produces would be noticed by anybody -- the build cadence, each monitor's own freshness,
        how many declared tests have ever produced a result, and the models with a mart downstream
        and no row-count history at all.

        *** IT READS A FILE AND NEVER A WAREHOUSE. ***
        Taking the measurement needs their dbt connection, and assay never holds a credential, so
        `assay volume --json > volume.json` takes it and this reads it -- the same artifact the
        report and the review form take. Without the file this returns the command rather than a
        set of zeros, because a zero here reads as "nothing is wrong" and means "nobody looked".
        """
        import json as _json
        from pathlib import Path as _Path
        cmd = ("assay volume --json > volume.json   # needs their dbt connection; free, no "
               "judgment")
        if not volume_json:
            return {"measured": False, "run_this_first": cmd,
                    "note": ("Nothing here is a statement about their monitoring: it says the "
                             "measurement has not been taken. Then pass the path back to this "
                             "tool, and `assay page --monitoring volume.json` puts the same "
                             "numbers on the report.")}
        try:
            vol = _json.loads(_Path(volume_json).read_text())
        except (OSError, ValueError) as e:
            return {"measured": False, "error": f"could not read {volume_json}: {e}",
                    "run_this_first": cmd}
        cad = vol.get("cadence") or {}
        cov = vol.get("test_coverage") or {}
        readings = vol.get("readings") or []
        unwatched = vol.get("unwatched") or []
        return {
            "measured": True,
            "cadence": {"runs": cad.get("runs"), "explain": cad.get("explain"),
                        "derived_staleness_days": cad.get("derived_staleness_days"),
                        "floored": cad.get("floored"), "configured": cad.get("configured")},
            "monitors": readings,
            "monitors_stopped": [r["relation"] for r in readings if r.get("state") != "live"],
            "test_coverage": cov,
            "tests_never_run": (cov.get("declared") or 0) - (cov.get("ever_ran") or 0),
            "stale_failures": vol.get("stale_failures") or [],
            "unwatched": unwatched[:25],
            "unwatched_total": len(unwatched),
            "findings": vol.get("monitoring") or [],
            "note": ("assay never measures volume or freshness itself -- that would be a second "
                     "monitoring tool with a second opinion. Every row here asserts that a "
                     "monitor EXISTS, is CURRENT and COVERS what matters; the counting stays "
                     "Elementary's. A `stale_failure` is neither a live failure nor a pass: it "
                     "is an answer that has gone out of date, and it reads as a live failure in "
                     "any view that sorts by status."),
        }

    def suggestions(self, section: str = "", limit: int = 15) -> dict:
        """What this project should CONFIGURE, from what the checks found.

        *** AN AGENT COULD READ EVERY FINDING AND STILL NOT KNOW WHAT TO WRITE DOWN. ***
        `guide` teaches what a vocab term is for and `findings` says what is wrong, and nothing
        joined the two. This is that join, and it is the tool an agent should reach for when
        somebody asks "so what do I put in audit.yml".

        A `means:` comes back EMPTY, or quoting verbatim -- with the file cited -- a sentence this
        project already wrote about the column. Never one assay or an agent composed: filling it
        from model names is the exact failure the skill warns about, one step more convincing
        because it arrives inside a tool result.
        """
        from . import suggest as sug
        from .config import Config
        store, why = self._store_or_why()
        cfg = Config.load(Path(self.target).parent if Path(self.target).name == "target"
                          else self.target)
        # *** `LiveState` HAS NO `findings`, AND THIS TOOL WAS DEAD ON EVERY PROJECT. ***
        # Reported from the field (25.22): the one MCP tool that tells an agent what to put in
        # audit.yml raised AttributeError unconditionally. The CLI's `assay suggest` reads the
        # same single stream every other surface does; so does this now.
        st = self.state()
        _fs = live.open_findings(st.project, st.digests, st.schema, st.entries, store, cfg,
                                 self.config_path)[0]
        firing = {f.check for f in _fs}
        pairs = sug.live_pairs(_fs)
        run_id = None
        if store is not None:
            run_id = store.latest_run(self.state().project.project_name)
        items = sug.build(store, cfg, firing, run_id, pairs, self.state().project)
        if section:
            items = [i for i in items if i.section == section]
        out = {
            "total": len(items),
            "showing": min(limit, len(items)),
            "sections": sorted({i.section for i in items}),
            "suggestions": [i.as_dict() for i in items[:limit]],
            # *** ONE RULE, SAID THE WAY THE DRAFTS ACT. *** Reported from the field: this said
            # every `means:` was empty while the drafts above it quoted the project's own
            # sentences -- one of the two had to be wrong, and it was this sentence.
            "rule": ("Propose the candidate and the measurement. NEVER write the meaning. A "
                     "`means:` above is either empty -- leave it empty until a PERSON says what "
                     "the term means here -- or quotes, verbatim and cited, a sentence this "
                     "project already wrote; hand that over as it is, unedited. `implies:` is "
                     "always empty. A definition you write from a model name looks exactly like "
                     "one they decided on, and is sent with every judged question from then on."),
        }
        # *** AN EMPTY LIST BECAUSE THERE IS NO STORE IS NOT AN EMPTY LIST BECAUSE THE CONFIG IS
        # COMPLETE. *** Most of these signals are measurements taken during `check` and `probe`.
        if why:
            out["store"] = why
            out["caveat"] = ("Almost every rule here reads the store, so this list is missing "
                             "most of what it would otherwise say. It is not a clean bill.")
        return out

    def evidence(self, decision_key: str = "", question: str = "", subject: str = "",
                 limit: int = 5) -> dict:
        """The EXACT state a judged answer was produced from, as it was sent.

        *** AN ANSWER WITHOUT ITS INPUT CANNOT BE CHECKED, ONLY BELIEVED. ***
        Every judged answer is a function of a state that assay assembled and then threw away, so
        a disagreement was unresolvable: nobody could tell whether the judge was wrong or whether
        it had been handed the wrong facts. Those are opposite repairs -- one edits the question,
        one edits what gets sent -- and picking between them was guesswork.

        States are stored keyed by their hash, so a state reused across a thousand answers is
        stored once. This is what a ruling should be read against, and it is the difference
        between ruling on an answer and ruling on an answer's reasoning.
        """
        store, why = self._store_or_why()
        if store is None:
            return {"error": why}
        where, args = [], []
        if decision_key:
            where.append("d.decision_key = ?"), args.append(decision_key)
        if question:
            where.append("d.question = ?"), args.append(question)
        if subject:
            # A decision key is `<subject>::<question>` shaped; match either spelling rather than
            # requiring the caller to know which one this store used.
            where.append("(d.decision_key like ? or d.context like ?)")
            args += [f"%{subject}%", f"%{subject}%"]
        clause = (" where " + " and ".join(where)) if where else ""
        rows = store.con.execute(f"""
            select d.decision_key, d.question, d.answer, d.confidence, d.state_hash,
                   d.prompt_version, d.decided_at, s.state
            from model_decisions d left join states s on s.state_hash = d.state_hash
            {clause} order by d.decided_at desc, d.decision_key limit ?""",
            [*args, int(limit)]).fetchall()
        out = []
        for key, q, ans, conf, sh, pv, when, state in rows:
            item = {"decision_key": key, "question": q, "answer": ans, "state_hash": sh,
                    "prompt_version": pv, "decided_at": str(when) if when else ""}
            # *** A `noul` CARRIES NO SEPARATE CONFIDENCE BECAUSE THE ANSWER IS THE PROBABILITY.
            # *** Reporting a null one as "confidence: none" invites reading it as unmeasured.
            if conf is not None:
                item["confidence"] = conf
            try:
                item["state"] = json.loads(state) if state else None
            except (ValueError, TypeError):
                item["state"] = None
            if item["state"] is None:
                item["state_missing"] = (
                    "this answer predates state storage, or its state was never written. The "
                    "answer stands; what it was computed FROM cannot be shown, so do not read "
                    "its absence as an empty state.")
            out.append(item)
        return {"n": len(out), "decisions": out,
                "how_to_read": ("The state is what the judge actually saw. If the answer is "
                                "wrong AND the state is wrong, fix what gets sent. If the answer "
                                "is wrong and the state is right, fix the question. Those are "
                                "different files and guessing between them is why this exists.")}


TOOLS = [
    ("job_status", ("A command that outlasted its wait came back as a job id. This returns how "
                    "far it has got and, once it ends, its whole output or JSON.")),
    ("job_stop", "End a running job, and return what it had printed."),
    ("jobs", "Every job this server started, and whether each is still running."),
    ("contract", ("What a model IS: grain, columns, roles, where each value comes from -- and "
                  "its HEALTH: open findings with who ruled on them, what is waived or "
                  "accepted, and whether the grain was ever counted. Call it before an edit.")),
    ("premises", ("What the findings rest on: every key a declared grain or a held-back "
                  "finding assumes is unique, its evidence (the test's last result, a count, a "
                  "judgment), its status (broken / unchecked / assumed / unknown / holding) and "
                  "what rests on it. Pass `model` for one model, `status` to narrow. A broken "
                  "premise raises the finding it held back; `raised` lists them.")),
    ("proofs", ("What is PROVEN about a model, checked by Lean: each certificate (a join cannot "
                "multiply rows, a dedupe keeps the same rows in any order, the grain survives, an "
                "incremental run equals a full refresh), its premises with their status now, and "
                "the guarantee: holding, conditional (a premise unchecked), lost (a premise "
                "broke), stale (the file changed). `assay prove` writes them.")),
    ("proof_goal", ("A goal YOU can prove for one property of a model, as Lean: the theorem's "
                    "header, its premises as named hypotheses with their status, and every lemma "
                    "assay's library proves. Omit `prop` to list a model's properties, including "
                    "those assay's own templates could not prove.")),
    ("check_proof", ("Check YOUR proof of a goal from proof_goal with Lean. Send the proof body "
                     "only (a term, or `by` and tactics) and any helper lemmas; it is placed under "
                     "the goal's own header. Returns `proven`, or Lean's error and remaining goal. "
                     "sorry, axioms and set_option are refused; a proven one is kept as written "
                     "by an agent, and counts like assay's own: the kernel checked it.")),
    ("lineage", "Follow a column back through the DAG to the hop that produced its value."),
    ("blast_radius", ("Who consumes this model, how many marts are downstream, and which of the "
                      "project's exposures -- dashboards, apps, reports -- it reaches.")),
    ("findings", ("Contradictions assay currently sees, optionally for one model or one `check`. "
                  "It reports the TOTAL and a per-check breakdown beside what it returns, so a "
                  "limit never hides a whole family from you.")),
    ("changed_contracts", ("Did recent edits change what anything MEANS? The self-check to run "
                           "after editing and before moving on.")),
    ("practices", ("Models with no uniqueness test, and the grain a test should cover. "
                   "A patch, not a nag.")),
    ("rebase", "Take a fresh baseline for changed_contracts."),
    ("rule", ("Record what YOU concluded after reading a finding and its SQL. Pass the `finding` "
              "id from findings() or review_queue(): a verdict on a MODEL lands on every finding "
              "that model has, and one real model carries eight. Filed as an agent "
              "ruling: it triages what a person should look at first and it never gates a build, "
              "never counts toward a question's verdicts, and never anchors a regression check. "
              "Rule on what you have actually read, including when you conclude the finding is "
              "wrong -- that is the most useful answer you can give.")),
    ("violations", ("What in this project would FAIL a build under its own audit.yml, and what "
                    "is only queued or annotated. Call it before handing work back: it is the "
                    "same policy CI applies, so a clean answer here is a green build.")),
    ("claims", ("What this project ASSERTS about a model, and whether its own code supports each "
                "claim. Call this BEFORE editing: the claims are what the edit must keep true.")),
    ("traversal", ("How a model's parents reach it, and whether any hop multiplies rows without "
                   "declaring it. The defect class no single-model check can see.")),
    ("load_handback", ("Record the verdicts a PERSON wrote in the review form, from the "
                       "`handback.json` the form downloads. Call it the moment they say they "
                       "have filled the form in -- ask for the path rather than guessing at a "
                       "downloads directory. This is the ONLY tool that files `human` verdicts, "
                       "and it can only file what the file carries: you are the courier, not "
                       "the reviewer. A form that is downloaded and never loaded is the most "
                       "valuable work in this system sitting in a folder.")),
    ("review_queue", ("What is waiting for a PERSON to rule on, agent-read items first, with the "
                      "reason already attached. Call it before `rule` to see whether a subject "
                      "has been read, and after, to see the queue you are building.")),
    # *** EVERY OTHER TOOL REPORTS. THIS ONE TEACHES. ***
    # An agent could read every finding on a warehouse and could not help anybody write a
    # vocabulary term, frame a question, or waive a finding with a reason that holds up -- which
    # is most of what a project that has never run assay actually needs. The guidance is not
    # generic: every rule in it was measured, and the ones the linter enforces are read from the
    # linter rather than restated.
    ("guide", ("HOW TO SET ASSAY UP, when the project has not been configured or the person you "
               "are helping is new to it. Topics: `start` (the order to do things in, and what "
               "costs nothing), `vocab` (what their words mean HERE, sent with every question), "
               "`questions` (how to frame one this model can actually answer -- it cannot do "
               "arithmetic, absence is not disagreement, options must be able to lose), "
               "`waivers`, `policy` (what each check does on a build, and what may gate at all), "
               "`explanations`, `ruling`. Call with no topic for the index. READ THIS BEFORE "
               "writing anything into their audit.yml or assay_questions/.")),
    ("suggestions", ("WHAT TO PUT IN THEIR audit.yml, derived from what the checks actually "
                     "found: vocabulary candidates ranked by how often the warehouse joins on "
                     "them, columns named like a key that are nearly-but-not unique, waivers "
                     "whose reason is already written in a ruling, and per-check actions backed "
                     "by measured agreement. Call this when somebody asks how to configure "
                     "assay, or after `findings` when the list is long. It returns every "
                     "`means:` and `implies:` EMPTY, and you must leave them empty: a "
                     "definition you write from a model name looks exactly like one they chose "
                     "and then rides along with every judged question forever.")),
    ("plan_items", ("WHAT TO CHANGE NEXT: the findings grouped into the fixes that resolve "
                    "them, ranked by findings resolved per decision (customer-facing and "
                    "happening now break ties). Each has a kind, a title, how "
                    "many it resolves and its status. Call it before findings.")),
    ("plan_item", ("One fix in full: the diff it would make, the recipe that verifies it, and "
                   "whether a person approved it.")),
    ("apply_plan_item", ("Write an APPROVED fix's files into the working tree (your branch). "
                         "Refused unless a person approved it. Then dbt parse/compile and "
                         "verify_plan_item.")),
    ("verify_plan_item", ("After applying and parsing: are the fix's files in place, and are "
                          "the findings it resolves gone? Records it verified when both hold.")),
    ("plan", ("WHAT TO CHANGE, for the findings a person has agreed with. Call it after "
              "they have reviewed and before you edit. Each row carries `fix_shape` -- "
              "the KIND of change, looked up from the check name, so it is exact -- plus "
              "`their_reason`, the only part that knows anything about this warehouse. "
              "It does NOT carry the words: what a sentence should say instead is a "
              "judgment about their data, so propose it and let them decide. An empty "
              "plan means nobody has agreed with anything yet, not that the warehouse "
              "is clean.")),
    ("spend", ("What the judged tier has COST on this project, by caller and by day. Call it "
               "before proposing a judged run, and after one. Every figure is one row per CALL: "
               "a batch of eight questions about one state is one call and eight answers, so "
               "totalling the answers counts it eight times.")),
    ("stale", ("Judged answers that are about SQL which has since CHANGED, so you know whether "
               "an answer you are about to rely on is still about the code in front of you. "
               "`exact=true` rebuilds the state each answer came from and compares it, catching "
               "a change to a PARENT that a file checksum cannot. Makes no API calls either way. "
               "Nothing is hidden by this: a dated answer is still served, because hiding it "
               "would leave you with nothing.")),
    ("vocabulary", ("Their words, WHERE each one is true, and what is wrong with the list. A "
                    "vocab term is injected into every judged question's state, so one that is "
                    "false outside some corner of the project steers every answer wrong at once "
                    "-- measured at 25% of one real warehouse's answers. Call this before writing "
                    "or editing any term, and pair it with guide('vocab').")),
    ("monitoring", ("Is anything WATCHING this warehouse: the build cadence, whether each "
                    "monitor is still being written to, how many declared tests have ever "
                    "produced a result, tests whose last result was a FAILURE and which have not "
                    "run since, and the models with a mart downstream and no row-count history. "
                    "Pass the path to a file from `assay volume --json`; with no path it returns "
                    "the command to produce one, because a zero here reads as `nothing is wrong` "
                    "and means `nobody looked`. assay never measures volume itself and never "
                    "holds a credential.")),
    ("evidence", ("The exact STATE a judged answer was computed from, as it was sent. Call it "
                  "before disagreeing with an answer: if the answer is wrong and the state is "
                  "wrong, what gets sent needs fixing; if the answer is wrong and the state is "
                  "right, the question does. Those are different files, and without this you "
                  "are guessing which.")),
]

# *** A TOOL'S DESCRIPTION WAS FETCHED BY ITS POSITION IN THIS LIST. ***
# `TOOLS[6][1]` for `rebase`, `TOOLS[11][1]` for `review_queue`, and the numbers were already out
# of order because tools were added at the end and wired in wherever. Inserting one entry above
# silently re-points every later tool at a neighbour's description -- an agent then reads the
# wrong instructions for the right tool, which is the worst possible shape for this particular
# failure. Position is not identity. The name is.
_BY_NAME = {name: desc for name, desc in TOOLS}


def _grouped_waivers(waived) -> list:
    rows: dict = {}
    for f, why in waived:
        k = (f.subject_name, f.check, why)
        rows.setdefault(k, 0)
        rows[k] += 1
    return [{"model": m, "check": c, "why": w, **({"findings": n} if n > 1 else {})}
            for (m, c, w), n in sorted(rows.items())]


def _desc(name: str) -> str:
    if name not in _BY_NAME:
        raise KeyError(f"no description for tool `{name}`. Add it to TOOLS.")
    return _BY_NAME[name]


def server_class():
    """The MCP server class, or a RuntimeError naming the fix.

    *** SEPARATED FROM `serve` SO THE FAILURE LANDS ON A TERMINAL. ***
    Reported from the field: `uvx dbt-assay mcp` skips the optional extra, this guard raised the
    right message, and stdio already owned the channel -- so the client saw CONNECTION_CLOSED and
    the explanation went nowhere. An error nobody can read is the same as no error.

    The caller checks this BEFORE opening the transport.
    """
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
        return Server
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        return Server
    except ImportError as e:
        raise RuntimeError(
            "the MCP server needs the optional extra, and a bare `uvx dbt-assay` does not "
            "install it.\n"
            "  uvx --from 'dbt-assay[mcp]' assay mcp --target target\n"
            "  claude mcp add assay --scope project -- uvx --from 'dbt-assay[mcp]' assay mcp --target target\n"
            "Installed instead of uvx: `uv add 'dbt-assay[mcp]'` or `pip install 'dbt-assay[mcp]'`."
        ) from e


def serve(target: str, store_path: str | None = None, handbacks: str | None = None,
          verdicts_only: bool = False) -> None:
    build_app(target, store_path, handbacks, verdicts_only).run()


def build_app(target: str, store_path: str | None = None, handbacks: str | None = None,
              verdicts_only: bool = False):
    """The server with every tool registered, not yet running -- so a test can list them."""
    # *** THE SDK RENAMED ITS SERVER CLASS AT v2. ***
    # `FastMCP` became `MCPServer`. Importing only one spelling means this command dies on
    # whichever major the user happens to have, with a traceback instead of an explanation, so
    # both are tried and the failure says what to install.
    Server = server_class()
    be = Backend(target, store_path, handbacks=handbacks, verdicts_only=verdicts_only)

    def _out(obj) -> str:
        """Every tool result leaves through here, so the empty-store signal cannot be forgotten.

        *** A NEW STORE AND A CLEAN WAREHOUSE PRODUCE THE SAME NUMBERS. ***
        Stamping it at each return site would be a list that goes stale the first time somebody
        adds a tool, and the symptom is an agent reading a zero and believing it.
        """
        return json.dumps(be._note_new_store(obj) if isinstance(obj, dict) else obj, default=str)
    app = Server("assay")

    def tool(name: str | None = None, description: str | None = None):
        """Register a tool whose failure is a sentence, never a bare tool name.

        *** "Error executing tool contract" IS NOT SOMETHING AN AGENT CAN ACT ON. ***
        The SDK drops the exception's text when a tool raises. Caught here, once, so no tool added
        later can forget: a locked store comes back as who holds it and since when, and anything
        else as the exception and where it was raised -- a bug report rather than a mystery.
        """
        def register(fn):
            label = name or fn.__name__

            @functools.wraps(fn)
            def guarded(*a, **k):
                be._store_locked = ""
                try:
                    return fn(*a, **k)
                except StoreLocked as e:
                    return json.dumps({"error": str(e), "store_locked": True})
                except Exception as e:                           # noqa: BLE001
                    tb = traceback.extract_tb(e.__traceback__)
                    where = f"{Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else ""
                    return json.dumps({"error": f"`{label}` failed: {type(e).__name__}: {e}",
                                       "raised_at": where,
                                       "note": "a bug in assay, not in your project. The CLI "
                                               "form of this tool may still work."})
                finally:
                    # *** THE SERVER LOCKED ITSELF OUT OF ITS OWN STORE. ***
                    # Reported from the field: `plan`, `suggestions` and `evidence` opened the store
                    # and never closed it, so every CLI-backed tool after them -- and the person's
                    # own terminal -- was locked out by the server for as long as it ran. Closed
                    # here, once, for every tool, so a tool added later cannot leak one.
                    be._close_opened()
            return app.tool(name=name, description=description or _desc(label))(guarded)
        return register

    @tool()
    def contract(model: str) -> str:
        return _out(be.contract(model))

    @tool()
    def guide(topic: str = "") -> str:
        # Markdown, not JSON: it is prose for the agent to read and act on, and wrapping prose in
        # a JSON string only makes it harder to read for no gain.
        from .guide import guide as _guide
        return _guide(topic)

    @tool()
    def premises(model: str = "", status: str = "", include_packages: bool = False) -> str:
        return _out(be.premises(model, status, include_packages))

    @tool()
    def proofs(model: str = "") -> str:
        return _out(be.proofs(model))

    @tool()
    def proof_goal(model: str, prop: str = "") -> str:
        return _out(be.proof_goal(model, prop))

    @tool()
    def check_proof(model: str, prop: str, proof: str, helpers: str = "") -> str:
        return _out(be.check_proof(model, prop, proof, helpers))

    @tool()
    def lineage(model: str, column: str) -> str:
        return _out(be.lineage(model, column))

    @tool()
    def blast_radius(model: str) -> str:
        return _out(be.blast_radius(model))

    @tool()
    def findings(model: str = "", limit: int = 20, check: str = "") -> str:
        return _out(be.findings(model or None, limit, check))

    @tool()
    def changed_contracts() -> str:
        return _out(be.changed_contracts())

    @tool()
    def practices(model: str = "") -> str:
        return _out(be.practices(model))

    @tool()
    def rule(verdict: str, why: str, finding: str = "", subject: str = "", question: str = "",
             correction: str = "", decided_by: str = "", until: str = "") -> str:
        return _out(be.rule(verdict, why, finding, subject, question, correction, decided_by,
                            until))

    @tool()
    def violations(model: str = "") -> str:
        return _out(be.violations(model))

    @tool()
    def claims(model: str = "") -> str:
        return _out(be.claims(model))

    @tool()
    def traversal(model: str) -> str:
        return _out(be.traversal(model))

    @tool()
    def review_queue(limit: int = 20) -> str:
        return _out(be.review_queue(limit))

    @tool()
    def load_handback(path: str = "", apply: bool = False, by: str = "",
                      verdicts_only: bool = False) -> str:
        return _out(be.load_handback(path, apply, by, verdicts_only or be.verdicts_only))

    @tool()
    def rebase() -> str:
        return _out(be.rebase())

    @tool()
    def plan(limit: int = 25) -> str:
        return _out(be.plan(limit))

    @tool()
    def plan_items(limit: int = 25, kind: str = "") -> str:
        return _out(be.plan_items(limit, kind))

    @tool()
    def plan_item(fix_id: str) -> str:
        return _out(be.plan_item(fix_id))

    @tool()
    def apply_plan_item(fix_id: str) -> str:
        return _out(be.apply_plan_item(fix_id))

    @tool()
    def verify_plan_item(fix_id: str) -> str:
        return _out(be.verify_plan_item(fix_id))

    @tool()
    def suggestions(section: str = "", limit: int = 15) -> str:
        return _out(be.suggestions(section, limit))

    @tool()
    def spend() -> str:
        return _out(be.spend())

    @tool()
    def stale(exact: bool = False) -> str:
        return _out(be.stale(exact))

    @tool()
    def vocabulary() -> str:
        return _out(be.vocabulary())

    @tool()
    def monitoring(volume_json: str = "") -> str:
        return _out(be.monitoring(volume_json))

    @tool()
    def evidence(decision_key: str = "", question: str = "", subject: str = "",
                 limit: int = 5) -> str:
        return _out(be.evidence(decision_key, question, subject, limit))

    # *** AND EVERY COMMAND, SO NOTHING THE CLI DOES IS OUT OF A TOOL'S REACH. ***
    from . import cli_tools
    for cmd in cli_tools.commands():
        def _make(name: str):
            def run(args: str = "", wait_seconds: int = 90) -> str:
                return _out(be.run_cli(name, args, wait_seconds))
            return run
        tool(name=cli_tools.tool_name(cmd["name"]),
             description=cli_tools.description(cmd))(_make(cmd["name"]))

    @tool()
    def job_status(job: str) -> str:
        return _out(cli_tools.status(job))

    @tool()
    def job_stop(job: str) -> str:
        return _out(cli_tools.stop(job))

    @tool()
    def jobs() -> str:
        return _out(cli_tools.listing())

    return app
