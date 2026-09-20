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
        if not self.store_path or not Path(self.store_path).exists():
            return None
        try:
            return Store(self.store_path)
        except Exception:                                               # noqa: BLE001
            return None

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

    def findings(self, model: str | None = None, limit: int = 20) -> dict:
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
        fs = live.findings_for(st, model)[:limit]
        out = {"findings": [{"check": f.check, "model": f.subject_name,
                             "file": f.file,
                             "summary": f.summary, "detail": f.detail,
                             "evidence": f.evidence or {},
                             "downstream": f.descendants, "marts": f.marts,
                             "weight": round(f.weight, 2)} for f in fs]}
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
        return {"missing_uniqueness_tests": [
            {"model": n, "grain_a_test_should_cover": cols, "grain_source": src, "marts": m}
            for n, cols, src, m in patches[:40]]}

    def rule(self, subject: str, question: str, verdict: str, why: str,
             correction: str = "") -> dict:
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
        """
        if verdict not in ("agree", "disagree", "unclear"):
            return {"error": "verdict must be agree, disagree or unclear"}
        if not (why or "").strip():
            return {"error": "a reason is required. A ruling nobody can check is not evidence."}
        st = self._open_store()
        if st is None:
            return {"error": "no store to write to. Run any judged command once to create one."}
        try:
            answered = ""
            row = st.con.execute(
                "select answer from model_decisions where decision_key = ? and question = ? "
                "order by decided_at desc limit 1", [subject, question]).fetchone()
            if row:
                answered = row[0]
            # `contracts.family_of` and not `cli._family_of`: cli imports this module, so
            # reaching back into it is a circular import that kills the binary while the test
            # suite -- which imports in a different order -- stays green.
            from .contracts import family_of
            st.adjudicate(subject, question, family_of(question) or question.split("__")[0],
                          answered, verdict,
                          correction=correction, note=why.strip(), who="agent", source="agent")
            human = len(st.ruled_subjects())
            mine = len(st.agent_rulings())
        finally:
            st.close()
        return {
            "recorded": True, "subject": subject, "verdict": verdict,
            "agent_rulings_now": mine, "models_a_person_has_ruled_on": human,
            "what_this_does": ("It puts this in front of whoever reviews next, ranked above what "
                               "nobody has read. `assay review -i` shows your reason beside the "
                               "finding."),
            "what_this_does_not_do": ("It does not gate a build, does not count toward the "
                                      "verdicts a question needs before it may fail one, and does "
                                      "not anchor `assay regress`. Those all require a person, on "
                                      "purpose."),
        }

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
                rows = st.con.execute(
                    "select context, answer from model_decisions "
                    "where question = 'edge' and decision_key like ?",
                    [uid + "::edge::%"]).fetchall()
                out["judged"] = [{"hop": r[0], "verdict": r[1]} for r in rows]
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
    ("findings", "Contradictions assay currently sees, optionally for one model."),
    ("changed_contracts", ("Did recent edits change what anything MEANS? The self-check to run "
                           "after editing and before moving on.")),
    ("practices", ("Models with no uniqueness test, and the grain a test should cover. "
                   "A patch, not a nag.")),
    ("rebase", "Take a fresh baseline for changed_contracts."),
    ("rule", ("Record what YOU concluded after reading a finding and its SQL. Filed as an agent "
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
            "  claude mcp add assay -- uvx --from 'dbt-assay[mcp]' assay mcp --target target\n"
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
    def findings(model: str = "", limit: int = 20) -> str:
        return json.dumps(be.findings(model or None, limit), default=str)

    @app.tool(description=TOOLS[4][1])
    def changed_contracts() -> str:
        return json.dumps(be.changed_contracts(), default=str)

    @app.tool(description=TOOLS[5][1])
    def practices(model: str = "") -> str:
        return json.dumps(be.practices(model), default=str)

    @app.tool(description=TOOLS[7][1])
    def rule(subject: str, question: str, verdict: str, why: str, correction: str = "") -> str:
        return json.dumps(be.rule(subject, question, verdict, why, correction), default=str)

    @app.tool(description=TOOLS[8][1])
    def violations(model: str = "") -> str:
        return json.dumps(be.violations(model), default=str)

    @app.tool(description=TOOLS[9][1])
    def claims(model: str = "") -> str:
        return json.dumps(be.claims(model), default=str)

    @app.tool(description=TOOLS[10][1])
    def traversal(model: str) -> str:
        return json.dumps(be.traversal(model), default=str)

    @app.tool(description=TOOLS[6][1])
    def rebase() -> str:
        return json.dumps(be.rebase(), default=str)

    app.run()
