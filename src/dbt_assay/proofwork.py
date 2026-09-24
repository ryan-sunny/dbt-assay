"""An agent writes a proof, assay checks it. (L3)

*** assay DOES NOT CALL A MODEL TO WRITE PROOFS. *** It hands the agent running it everything a
proof needs -- the goal as Lean text, the model's structure, the premises as named hypotheses with
their statuses, and the lemmas it may use -- and checks what comes back, the same shape as the
review loop: the agent does the work, assay decides nothing it cannot check.

Who wrote a proof does not matter, which is the difference from a verdict: Lean's kernel checked
it. So an accepted proof is stored as `written_by: agent` beside assay's own, and the page says
"checked by Lean". What is checked, and refused:

* the STATEMENT is assay's. The agent sends a proof body (and optional helper lemmas); assay puts
  it under the goal's own header, so a proof of a weaker statement cannot be passed off.
* `sorry`, `admit`, `axiom`, `unsafe`, `implemented_by`, `extern`, `opaque` and any `set_option`
  are refused before Lean runs.
* after Lean accepts it, `#print axioms` on the theorem may list only Lean's three standard
  axioms. `native_decide` rests on another one and is refused there.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import ledger as L
from . import prove as P

FORBIDDEN = re.compile(r"\b(sorry|admit|axiom|unsafe|implemented_by|extern|opaque|set_option|"
                       r"native_decide|decreasing_by\s+sorry)\b")


def _obligation(project, digests, schema, entries, store, model: str, prop: str):
    led = L.build(project, schema, entries, store)
    L.register_grains(led, entries)
    obls = P.obligations(project, digests, schema, entries, led, store)
    got = [o for o in obls if o.name == model and o.prop == prop]
    return (got[0] if got else None), [o for o in obls if o.name == model], led


def header(o: P.Obligation) -> str:
    """The theorem's statement: everything before `:=`."""
    return o.lean.split(" :=", 1)[0] if " :=" in o.lean else ""


def library_lemmas() -> list[dict]:
    """Every theorem the shipped library proves, with its statement, for the agent to apply."""
    from .proofs import LEAN_DIR
    out = []
    for p in sorted((LEAN_DIR / "Assay").glob("*.lean")):
        text = p.read_text()
        for m in re.finditer(r"(?:/--(?P<doc>.*?)-/\s*)?^theorem\s+(?P<name>\S+)(?P<sig>.*?):=",
                             text, re.S | re.M):
            sig = " ".join(m.group("sig").split())
            doc = " ".join((m.group("doc") or "").split())[:300]
            out.append({"name": m.group("name"), "file": p.name, "statement": sig, "doc": doc})
    return out


def goal(project, digests, schema, entries, store, model: str, prop: str = "") -> dict:
    """The goal for one property of a model, or the list of properties when none is named."""
    o, all_, led = _obligation(project, digests, schema, entries, store, model, prop)
    if not all_:
        return {"error": f"no provable property of `{model}`: no join, dedupe, grain or merge "
                         f"a rule applies to"}
    if o is None:
        rows = {r["property"]: r for r in P.stored(store)} if store is not None else {}
        return {"model": model, "properties": [
            {"property": x.prop, "statement": x.statement, "has_goal": bool(x.lean),
             "status": (rows.get(x.prop) or {}).get("status", "not_checked"),
             "missing": x.missing} for x in all_],
            "note": "pass `property` for its goal"}
    if not o.lean:
        return {"model": model, "property": prop, "error": "no goal can be stated for this "
                "property yet: " + (o.missing or "no rule covers its structure")}
    return {
        "model": model, "property": prop, "statement": o.statement,
        "theorem": o.theorem,
        "goal": header(o) + " := by\n  sorry",
        "header": header(o),
        "premises": [{"hypothesis": P._hyp(p), "premise": p.statement(), "status": p.status,
                      "why": L.why(p)} for p in o.premises],
        "assay_tried": o.rule, "missing": o.missing,
        "lemmas": library_lemmas(),
        "how": ("Send the PROOF only, as `proof`: a term, or `by` and tactics. It is placed "
                "under exactly this header in a file that `import Assay` and `open Assay`. "
                "Helper lemmas go in `helpers`. sorry, axioms and set_option are refused, and "
                "#print axioms may list only propext, Quot.sound and Classical.choice."),
    }


def check(project, digests, schema, entries, store, model: str, prop: str, proof: str,
          helpers: str = "", by: str = "agent") -> dict:
    """Check an agent's proof of a goal with Lean. Stored when it holds."""
    from . import toolchain
    from .proofs import LEAN_VERSION, STANDARD_AXIOMS
    o, _all, _led = _obligation(project, digests, schema, entries, store, model, prop)
    if o is None or not o.lean:
        return {"status": "refused", "why": f"no goal for `{model}` `{prop}`: ask proof_goal"}
    bad = FORBIDDEN.search(f"{helpers}\n{proof}")
    if bad:
        return {"status": "refused", "why": f"`{bad.group(1)}` is not allowed in a proof assay "
                                            f"accepts"}
    lake = toolchain.lake_for_build()
    if lake is None:
        return {"status": "refused", "why": "no Lean toolchain: `assay prove --setup`"}
    toolchain.build_library(say=lambda *_a: None)
    body = proof.strip()
    src = ("import Assay\nopen Assay\n\n" + (helpers.strip() + "\n\n" if helpers.strip() else "")
           + f"{header(o)} :=\n  {body}\n\n#print axioms {o.theorem}\n")
    with tempfile.TemporaryDirectory(prefix="assay-proof-") as d:
        root = Path(d)
        lib = toolchain.library()
        (root / "lakefile.toml").write_text(
            'name = "assay_check"\n\n[[require]]\nname = "assay"\n'
            f'path = "{lib}"\n')
        f = root / "Check.lean"
        f.write_text(src)
        env = P._lean_env(lake)
        subprocess.run([lake, "build", "Assay"], cwd=root, capture_output=True, text=True,
                       env=env, timeout=1800, check=False)
        r = subprocess.run([lake, "env", "lean", str(f)], cwd=root, capture_output=True,
                           text=True, env=env, timeout=600, check=False)
    out = r.stdout + r.stderr
    errors = [m.group(0) for m in re.finditer(r"error: .*?(?=\n\S+\.lean:\d+:\d+:|\Z)", out,
                                              re.S)]
    if errors or r.returncode != 0:
        return {"status": "not_proven", "lean": "\n".join(errors)[:4000] or out[-2000:],
                "theorem": o.theorem}
    if "declaration uses 'sorry'" in out:
        return {"status": "refused", "why": "the proof uses sorry"}
    m = re.search(rf"'{re.escape(o.theorem)}' depends on axioms: \[([^\]]*)\]", out)
    axioms = [a.strip() for a in m.group(1).split(",")] if m else []
    extra = [a for a in axioms if a and a not in STANDARD_AXIOMS]
    if extra:
        return {"status": "refused", "why": f"rests on {extra}, beyond Lean's standard axioms",
                "axioms": axioms}
    o.status, o.detail = P.PROVEN, f"written by {by}, checked by Lean"
    if store is not None:
        store.con.execute(P.DDL)
        now = datetime.now(timezone.utc)
        prem = [{"id": p.id, "relation": p.relation, "name": p.name, "columns": list(p.columns),
                 "property": p.prop, "param": p.param} for p in o.premises]
        import json
        store.con.execute(
            "insert or replace into proofs values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [o.model, o.name, o.checksum, o.prop, o.statement, o.theorem, "agent proof",
             json.dumps(prem), P.PROVEN, o.detail, "", "agent", src, LEAN_VERSION, now])
    return {"status": "proven", "theorem": o.theorem, "axioms": axioms,
            "stored": store is not None, "written_by": "agent"}
