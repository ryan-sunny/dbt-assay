"""Each model's parse, proven. (L4)

*** THE CLAIM: LEAN'S PARSER, RUN ON THE MODEL'S OWN TEXT, YIELDS EXACTLY SQLGLOT'S TREE. ***
Every certificate `assay prove` writes is about the structure sqlglot read. L2 measures whether
that reading is faithful (the round trip on generated rows); this proves it, one model at a time,
the way CompCert validates its parser's output rather than trusting the generator:

1. sqlglot parses the model, as always.
2. `sqlfrag` writes that tree in the fragment Lean defines (`lean/Sql/Syntax.lean`).
3. For a quick answer, `assay_sql parse` (Lean, compiled) reads the text and prints its tree;
   when the two differ, the report is where they part.
4. When they agree, a theorem `parseCodes <the text> = some <sqlglot's tree>` is written and the
   KERNEL checks it by evaluating Lean's parser on the text (`decide +kernel`). `#print axioms`
   must list only Lean's standard ones.

Then `parse_faithful(model)` is `holding` with evidence `proven`, and outranks the L2 measurement.
A model outside the fragment -- a UNION, a subquery in FROM, a construct the grammar does not
have -- is "parse unproven" and keeps the round trip. Nothing here makes a model read broken: a
mismatch is reported as the place the trees part, and the round trip still decides whether the
difference changes any row.
"""
from __future__ import annotations

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import ledger as L
from . import sqlfrag

VIA = "lean"


def exe_path() -> Path:
    from . import toolchain
    return toolchain.library() / ".lake" / "build" / "bin" / "assay_sql"


def lean_parse(sql: str) -> str:
    r = subprocess.run([str(exe_path()), "parse"], input=sql, capture_output=True, text=True,
                       timeout=120, check=False)
    return r.stdout.strip()


def _diverge(a: str, b: str, width: int = 70) -> str:
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return (f"sqlglot: …{a[max(0, i - width):i + width]}…\n"
            f"lean:    …{b[max(0, i - width):i + width]}…")


def theorem_file(name: str, sql: str, q: dict) -> str:
    return ("import Sql\nopen Sql\n\n"
            "set_option maxRecDepth 200000 in\n"
            f"/-- `{name}`: Lean's parse of the model's text is exactly the tree sqlglot read. -/\n"
            f"theorem parse_faithful : parseCodes {sqlfrag.lean_codes(sql)} = some\n"
            f"    {sqlfrag.l_query(q)} := by decide +kernel\n\n"
            "#print axioms parse_faithful\n")


def run(project, store, target_dir, *, select=None, force: bool = False, say=print,
        jobs: int = 0) -> dict:
    """Prove the parse of every model whose current file has no lean result yet."""
    from . import toolchain
    from .proofs import STANDARD_AXIOMS
    lake = toolchain.lake_for_build()
    if lake is None:
        raise RuntimeError("no Lean toolchain: `assay prove --setup`")
    toolchain.build_library(say=lambda *_a: None)
    store.con.execute(L.DDL_PARSE)
    done = {(m, cs) for m, cs in store.con.execute(
        "select model, model_checksum from parse_checks where via = ?", [VIA]).fetchall()}
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    root = (Path(target_dir) / "assay" / "lean").resolve()
    (root / "Parse").mkdir(parents=True, exist_ok=True)
    from .prove import write_project
    write_project(target_dir, [])                    # the lakefile requiring the library
    subprocess.run([lake, "build", "Sql"], cwd=root, capture_output=True, text=True,
                   env=_env(lake), timeout=1800, check=False)
    rows, attempts = [], []
    todo = [(u, m) for u, m in sorted(project.models.items())
            if m.readable and not getattr(m, "is_installed_package", False)
            and (select is None or u in select) and (force or (u, m.checksum or "") not in done)]
    for uid, m in todo:
        now = datetime.now(timezone.utc)
        try:
            q = sqlfrag.query(m.compiled, dialect)
        except sqlfrag.Outside as e:
            rows.append((uid, m.checksum or "", L.UNCHECKED, (f"parse unproven: outside the "
                         f"fragment ({e})"), 0, VIA, now))
            continue
        ours = sqlfrag.s_query(q)
        theirs = lean_parse(m.compiled)
        if theirs.startswith("OUTSIDE"):
            rows.append((uid, m.checksum or "", L.UNCHECKED, ("parse unproven: outside Lean's "
                         f"grammar of the fragment ({theirs.split(' ', 1)[-1]})"), 0, VIA, now))
            continue
        if ours != theirs:
            rows.append((uid, m.checksum or "", L.UNCHECKED, "parse unproven: sqlglot's tree "
                         "and Lean's reading of the text part here\n" + _diverge(ours, theirs),
                         0, VIA, now))
            continue
        f = root / "Parse" / f"{re.sub(r'[^A-Za-z0-9_]', '_', m.name)}.lean"
        f.write_text(theorem_file(m.name, m.compiled, q))
        attempts.append((uid, m, f))
    if attempts:
        say(f"proving the parse of {len(attempts)} model(s) with Lean's kernel")

    def one(item):
        uid, m, f = item
        r = subprocess.run([lake, "env", "lean", str(f)], cwd=root, capture_output=True,
                           text=True, env=_env(lake), timeout=1800, check=False)
        return uid, m, r.returncode, r.stdout + r.stderr

    import os
    with ThreadPoolExecutor(max_workers=jobs or max(1, (os.cpu_count() or 2) - 1)) as ex:
        for uid, m, rc, out in ex.map(one, attempts):
            now = datetime.now(timezone.utc)
            ax = re.search(r"'parse_faithful' depends on axioms: \[([^\]]*)\]", out)
            axioms = [a.strip() for a in ax.group(1).split(",")] if ax else []
            if rc != 0 or "error" in out or ax is None:
                err = next((ln for ln in out.splitlines() if "error" in ln), out[-300:])
                rows.append((uid, m.checksum or "", L.UNCHECKED,
                             f"parse unproven: Lean refused it: {err[:300]}", 0, VIA, now))
            elif any(a not in STANDARD_AXIOMS for a in axioms):
                rows.append((uid, m.checksum or "", L.UNCHECKED,
                             f"parse unproven: rests on {axioms}", 0, VIA, now))
            else:
                rows.append((uid, m.checksum or "", L.HOLDING,
                             ("proven: Lean's parser reads exactly sqlglot's tree from the "
                             "model's text, checked by the kernel"), 0, VIA, now))
    if rows:
        store.con.executemany("insert or replace into parse_checks values (?,?,?,?,?,?,?)", rows)
    from collections import Counter
    by = Counter("proven" if r[2] == L.HOLDING else "unproven" for r in rows)
    names = {u: m.name for u, m in project.models.items()}
    return {"checked": len(rows), "proven": by.get("proven", 0),
            "unproven": by.get("unproven", 0),
            "by_model": {names.get(r[0], r[0]): ("proven" if r[2] == L.HOLDING else "unproven")
                         for r in rows}}


def _env(lake: str) -> dict:
    import os
    env = dict(os.environ)
    env["PATH"] = str(Path(lake).parent) + os.pathsep + env.get("PATH", "")
    return env
