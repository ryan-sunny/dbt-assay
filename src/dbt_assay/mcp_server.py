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

import json
from pathlib import Path

from . import live
from .store import Store


class Backend:
    """Reloads when the manifest moves, so an agent never reads a stale contract."""

    def __init__(self, target: str, store_path: str | None = None):
        self.target = target
        self.store_path = store_path
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
            return Store(self.store_path), ""
        except Exception as e:                                          # noqa: BLE001
            msg = str(e).lower()
            if "lock" in msg or "being used" in msg or "conflicting" in msg:
                return None, (f"the store at {self.store_path} is LOCKED by another process. "
                              f"DuckDB allows one writer: close the other `assay` session or CLI "
                              f"command and call this again. The store is fine and nothing was "
                              f"lost.")
            return None, f"the store at {self.store_path} could not be opened: {e}"

    def _manifest_mtime(self) -> float:
        p = Path(self.target) / "manifest.json"
        return p.stat().st_mtime if p.exists() else 0.0

    def state(self) -> live.LiveState:
        m = self._manifest_mtime()
        if self._state is None or m != self._stamp:
            store = Store(self.store_path) if self.store_path and Path(
                self.store_path).exists() else None
            self._state = live.read(self.target, store)
            self._stamp = m
            if store:
                store.close()
            if self.baseline is None:
                self.baseline = live.Snapshot.of(self._state.entries)
        return self._state

    # ---- the tools ----

    def contract(self, model: str) -> dict:
        c = live.contract_of(self.state(), model)
        return c or {"error": f"no model named {model}"}

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
        st = self.state()
        every = live.findings_for(st, model)
        if check:
            every = [f for f in every if f.check == check]
        fs = every[:limit]
        out = {"findings": [{"finding": f.id,
                             "check": f.check, "model": f.subject_name,
                             "file": f.file,
                             "summary": f.summary, "detail": f.detail,
                             "evidence": f.evidence or {},
                             "downstream": f.descendants, "marts": f.marts,
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
            "summary": diff_mod.summarise(ch) if ch else "Nothing means anything different.",
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
             question: str = "", correction: str = "", decided_by: str = "") -> dict:
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
        if verdict not in ("agree", "disagree", "unclear"):
            return {"error": "verdict must be agree, disagree or unclear"}
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
                          prompt_version=pv, model_version=mv)
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
        ruled: set = set()
        mine: dict = {}
        orphans: list = []
        if store is not None:
            try:
                ruled = store.ruled_subjects()
                known = set(self.state().project.models)
                for r in store.agent_rulings():
                    key = str(r["subject"])
                    if "::finding::" in key:
                        mine[key.split("::finding::")[1]] = r          # exact
                    else:
                        mine.setdefault(key.split("::")[0], r)         # model-level
                    if key.split("::")[0] not in known:
                        # *** A RULING THAT JOINS TO NOTHING IS REPORTED, NOT SWALLOWED. ***
                        # 99 of them existed before `rule` started refusing a subject it could
                        # not resolve. `assay review --repair` resolves the ones whose model name
                        # is unambiguous; nothing here guesses.
                        orphans.append(key)
            finally:
                store.close()
        rows = []
        for f in fs:
            if f.subject in ruled or f"{f.subject}::finding::{f.id}" in ruled:
                continue
            a = mine.get(f.id) or mine.get(str(f.subject).split("::")[0])
            rows.append({"finding": f.id,
                         "check": f.check, "model": f.subject_name, "file": f.file,
                         "summary": f.summary, "marts": f.marts,
                         "an_agent_already_said": (
                             {"verdict": a["verdict"], "because": a["note"],
                              "at": ("this exact finding" if mine.get(f.id) else
                                     "the model, so it covers every finding on it")}
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
            "waived": [{"model": f.subject_name, "check": f.check, "why": w} for f, w in waived],
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
                    "where question = 'align'").fetchall():
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
                from .contracts import current_versions
                rows = st.live_decisions(
                    "question = 'edge' and decision_key like ?", [uid + "::edge::%"],
                    current_versions(), columns="question, context, answer")
                out["judged"] = [{"hop": r[1], "verdict": r[2]} for r in rows]
                if st.retired_decisions:
                    out["verdicts_from_a_retired_version_of_the_question"] = st.retired_decisions
                    out["why_they_are_not_shown"] = (
                        "the question was rewritten since they were given, so they are answers "
                        "to a question that no longer exists. `assay traverse` re-asks; "
                        "`assay effectiveness` shows agreement per version.")
            finally:
                st.close()
        return out

    def rebase(self) -> dict:
        self.baseline = live.Snapshot.of(self.state().entries)
        return {"ok": True, "models": len(self.baseline.entries)}


TOOLS = [
    ("contract", ("What a model IS: grain, columns, roles, where each value comes from. "
                  "Fifteen lines instead of reading the SQL.")),
    ("lineage", "Follow a column back through the DAG to the hop that produced its value."),
    ("blast_radius", "Who consumes this model, and how many marts are downstream."),
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
    ("review_queue", ("What is waiting for a PERSON to rule on, agent-read items first, with the "
                      "reason already attached. Call it before `rule` to see whether a subject "
                      "has been read, and after, to see the queue you are building.")),
]


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


def serve(target: str, store_path: str | None = None) -> None:
    # *** THE SDK RENAMED ITS SERVER CLASS AT v2. ***
    # `FastMCP` became `MCPServer`. Importing only one spelling means this command dies on
    # whichever major the user happens to have, with a traceback instead of an explanation, so
    # both are tried and the failure says what to install.
    Server = server_class()
    be = Backend(target, store_path)
    app = Server("assay")

    @app.tool(description=TOOLS[0][1])
    def contract(model: str) -> str:
        return json.dumps(be.contract(model), default=str)

    @app.tool(description=TOOLS[1][1])
    def lineage(model: str, column: str) -> str:
        return json.dumps(be.lineage(model, column), default=str)

    @app.tool(description=TOOLS[2][1])
    def blast_radius(model: str) -> str:
        return json.dumps(be.blast_radius(model), default=str)

    @app.tool(description=TOOLS[3][1])
    def findings(model: str = "", limit: int = 20, check: str = "") -> str:
        return json.dumps(be.findings(model or None, limit, check), default=str)

    @app.tool(description=TOOLS[4][1])
    def changed_contracts() -> str:
        return json.dumps(be.changed_contracts(), default=str)

    @app.tool(description=TOOLS[5][1])
    def practices(model: str = "") -> str:
        return json.dumps(be.practices(model), default=str)

    @app.tool(description=TOOLS[7][1])
    def rule(verdict: str, why: str, finding: str = "", subject: str = "", question: str = "",
             correction: str = "", decided_by: str = "") -> str:
        return json.dumps(be.rule(verdict, why, finding, subject, question, correction,
                                  decided_by), default=str)

    @app.tool(description=TOOLS[8][1])
    def violations(model: str = "") -> str:
        return json.dumps(be.violations(model), default=str)

    @app.tool(description=TOOLS[9][1])
    def claims(model: str = "") -> str:
        return json.dumps(be.claims(model), default=str)

    @app.tool(description=TOOLS[10][1])
    def traversal(model: str) -> str:
        return json.dumps(be.traversal(model), default=str)

    @app.tool(description=TOOLS[11][1])
    def review_queue(limit: int = 20) -> str:
        return json.dumps(be.review_queue(limit), default=str)

    @app.tool(description=TOOLS[6][1])
    def rebase() -> str:
        return json.dumps(be.rebase(), default=str)

    app.run()
