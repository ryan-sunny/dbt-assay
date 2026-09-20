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
        fs = live.findings_for(self.state(), model)[:limit]
        return {"findings": [{"check": f.check, "model": f.subject_name, "summary": f.summary,
                              "detail": f.detail, "weight": round(f.weight, 2)} for f in fs]}

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
    ("rebase", "Take a fresh baseline for changed_contracts."),
]


def serve(target: str, store_path: str | None = None) -> None:
    # *** THE SDK RENAMED ITS SERVER CLASS AT v2. ***
    # `FastMCP` became `MCPServer`. Importing only one spelling means this command dies on
    # whichever major the user happens to have, with a traceback instead of an explanation, so
    # both are tried and the failure says what to install.
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:
            raise RuntimeError(
                "the MCP server needs the optional extra: `uv add 'dbt-assay[mcp]'` "
                "or `pip install mcp`") from e

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
    def rebase() -> str:
        return json.dumps(be.rebase(), default=str)

    app.run()
