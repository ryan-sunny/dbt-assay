"""The Lean side of assay: which rules are proven, and checking that they still are.

*** A RULE IN PYTHON AND ITS THEOREM IN LEAN ARE ONE FACT WRITTEN TWICE. ***
`RULES_PROVEN` names, for each check whose reasoning a theorem backs, the theorem in
`lean/Assay/Rules.lean`. A test holds both sides: a theorem renamed or deleted, or a rule mapped to
a theorem that does not exist, fails the build. And `check_library` builds the Lean library and
refuses it when any theorem uses `sorry` or an axiom beyond Lean's own three.

What "proven" means here, and what it does not: each theorem is about assay's FORMAL model of
tables (bags of rows, SQL NULLs, joins, picks) and holds for every input that satisfies its
premises. That the model matches what a real engine does is a separate claim, measured by the
conformance suite (L4), and whether a model's SQL was parsed faithfully is its own premise (L2).
The page says "proven from the parsed structure" for exactly that reason.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

LEAN_DIR = Path(__file__).parent / "lean"
LEAN_VERSION = (LEAN_DIR / "lean-toolchain").read_text().strip().split(":")[-1] \
    if (LEAN_DIR / "lean-toolchain").exists() else ""
# The only axioms a proof here may rest on: Lean's own.
STANDARD_AXIOMS = frozenset({"propext", "Quot.sound", "Classical.choice"})

# check -> the theorems its reasoning rests on, and what each one settles for it.
RULES_PROVEN: dict[str, list[tuple[str, str]]] = {
    "hop_multiplies_rows": [
        ("inner_join_no_fanout", "a join onto a key unique on the columns it joins cannot add rows"),
        ("left_join_preserves_rows", "a left join onto such a key keeps exactly the left rows"),
    ],
    "join_fans_out": [
        ("inner_join_no_fanout", ("only a join that covers the declared key is guaranteed not to "
                                 "fan out; this one does not cover it")),
    ],
    "arbitrary_pick": [
        ("pick_total_on_unique_key", ("a dedupe whose order no two rows of a partition tie on "
                                     "keeps the same rows in any input order; this one's may tie")),
        ("pick_is_order_independent", ("a dedupe that keeps only partition and order keys is "
                                      "order-independent, which is why such a pick is exempt")),
    ],
}
# Rules a theorem backs that are not checks: how assay carries a grain.
DERIVATIONS_PROVEN: dict[str, list[tuple[str, str]]] = {
    "grain_through_join": [
        ("grain_through_join", "a grain unique and not null survives a join onto a unique key")],
    "grain_through_filter": [
        ("filter_preserves_unique", "a filter keeps any uniqueness")],
}


def theorem_for(check: str) -> str:
    """The first theorem backing this check, or ''."""
    got = RULES_PROVEN.get(check) or []
    return got[0][0] if got else ""


def stamp(findings) -> list:
    """Every finding of a check a theorem backs carries `proven_rule`, in the ONE stream every
    surface reads. Outside the finding's id (`structural._MEASURED`), so proving a rule later
    does not orphan a ruling."""
    for f in findings:
        t = theorem_for(getattr(f, "check", ""))
        if t and isinstance(getattr(f, "evidence", None), dict):
            f.evidence.setdefault("proven_rule", t)
    return findings


def theorems_in_source() -> set:
    """Every `theorem` declared in the shipped Lean library."""
    out = set()
    for p in sorted((LEAN_DIR / "Assay").glob("*.lean")):
        out |= set(re.findall(r"^theorem\s+([A-Za-z_][A-Za-z0-9_.']*)", p.read_text(), re.MULTILINE))
    return out


def lake_bin() -> str | None:
    """How to run Lake: $ASSAY_LAKE, assay's own cached toolchain, then an elan install."""
    env = os.environ.get("ASSAY_LAKE")
    if env:
        return env
    from . import toolchain
    own = toolchain.lake_path()
    if own is not None and own.exists():
        return str(own)
    found = shutil.which("lake")
    if found:
        return found
    elan = Path.home() / ".elan" / "bin" / "lake"
    return str(elan) if elan.exists() else None


def _env(lake: str) -> dict:
    """PATH with the toolchain's own bin first, so `lake` finds its `lean`."""
    env = dict(os.environ)
    env["PATH"] = str(Path(lake).parent) + os.pathsep + env.get("PATH", "")
    return env


def check_library(project_dir: Path | None = None, timeout: int = 1200) -> dict:
    """Build the Lean library and audit it: {ok, built, sorry, axioms: {thm: [..]}, bad, why}.

    `bad` names every theorem that rests on an axiom outside `STANDARD_AXIOMS`, and `sorry` every
    file that says it. Either one makes `ok` false: a proof with a hole is not a proof."""
    root = Path(project_dir or LEAN_DIR)
    lake = lake_bin()
    if lake is None:
        return {"ok": False, "built": False, "why": "no Lean toolchain: `assay prove --setup` "
                                                    "installs the pinned one"}
    sorry = [str(p.relative_to(root)) for p in sorted(root.rglob("*.lean"))
             if ".lake" not in p.parts and re.search(r"\bsorry\b|\badmit\b", _code(p.read_text()))]
    b = subprocess.run([lake, "build"], cwd=root, capture_output=True, text=True,
                       timeout=timeout, env=_env(lake), check=False)
    if b.returncode != 0:
        return {"ok": False, "built": False, "sorry": sorry,
                "why": (b.stdout + b.stderr)[-3000:]}
    names = sorted(theorems_in_source()) if project_dir is None else sorted(
        set(re.findall(r"^theorem\s+([A-Za-z_][A-Za-z0-9_.']*)",
                       "\n".join(p.read_text() for p in root.rglob("*.lean")
                                 if ".lake" not in p.parts), re.MULTILINE)))
    axioms = print_axioms(root, [f"Assay.{n}" for n in names], lake)
    bad = {t: [a for a in ax if a not in STANDARD_AXIOMS] for t, ax in axioms.items()}
    bad = {t: v for t, v in bad.items() if v}
    return {"ok": not sorry and not bad and len(axioms) == len(names), "built": True,
            "sorry": sorry, "axioms": axioms, "bad": bad, "theorems": names,
            "lean": LEAN_VERSION}


def _code(text: str) -> str:
    """Lean source with comments removed, so a docstring saying 'no sorry' is not a sorry."""
    text = re.sub(r"/-.*?-/", " ", text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", text)


def print_axioms(root: Path, names: list, lake: str | None = None) -> dict:
    """{theorem: [axioms]} from Lean's own `#print axioms`."""
    lake = lake or lake_bin()
    if not names or lake is None:
        return {}
    src = "import Assay\n" + "".join(f"#print axioms {n}\n" for n in names)
    scratch = root / ".lake" / "assay_axioms.lean"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(src)
    r = subprocess.run([lake, "env", "lean", str(scratch)], cwd=root, capture_output=True,
                       text=True, timeout=600, env=_env(lake), check=False)
    out = {}
    for m in re.finditer(r"'([^']+)' (?:depends on axioms: \[([^\]]*)\]|does not depend on any "
                         r"axioms)", r.stdout):
        out[m.group(1).removeprefix("Assay.")] = [a.strip() for a in (m.group(2) or "").split(",")
                                                  if a.strip()]
    return out
