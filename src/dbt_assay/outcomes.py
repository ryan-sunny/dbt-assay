"""What the last build actually DID, as opposed to what the code says it would do.

*** ASSAY READS STRUCTURE AND THE FAILING POPULATION. IT NEVER READ OUTCOMES. ***
`manifest.json` for structure, `dbt_test__audit` for the rows a test failed on, `catalog.json` for
columns, `sources.json` for freshness. `run_results.json` appeared in exactly one place, to warn
that `dbt compile` overwrites it.

That file answers things structure cannot, and one of them is live on the field warehouse right
now: dbt applies a configured `limit` to the test query, so a failure count EQUAL to the limit is
the cap rather than the count. `assert_water_address_resolves` reported 500 against a real 6,251,
and every one of that project's 1,291 tests carries `+limit: 500`, so every failure count it has
ever printed may be an understatement and nothing says so.

*** OUTCOMES LIVE WHEREVER dbt RAN, WHICH IS NOT WHERE ASSAY RUNS. ***
On a real setup dbt runs on a box and assay runs on a laptop. So the file arrives by path --
`--run-results` -- rather than by assay being installed next to production to read a JSON file it
could read anywhere.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# dbt's own vocabulary. `pass`/`fail`/`warn` are test outcomes; `success`/`error` are node ones;
# `skipped` is both and is the one that matters most.
FAILED = ("fail", "error", "runtime error")
PASSED = ("pass", "success")


@dataclass
class Outcome:
    unique_id: str
    status: str
    failures: int | None = None
    message: str = ""


@dataclass
class Build:
    """One `run_results.json`, read."""
    generated_at: str = ""
    dbt_version: str = ""
    outcomes: dict = field(default_factory=dict)      # unique_id -> Outcome

    @property
    def n(self) -> int:
        return len(self.outcomes)


def read(path: str | Path) -> Build:
    """Load a run_results.json. Raises with the reason, because a silent empty Build would read
    exactly like a build in which nothing ran."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"no run_results.json at {p}. dbt writes it into its target directory on every "
            f"invocation, and `dbt compile` OVERWRITES it with a compile-only result -- so the "
            f"one you want is from the build you are asking about, not from a later compile.")
    data = json.loads(p.read_text())
    if "results" not in data:
        raise ValueError(f"{p} has no `results` key, so it is not a run_results.json.")
    meta = data.get("metadata") or {}
    b = Build(generated_at=str(meta.get("generated_at") or ""),
              dbt_version=str(meta.get("dbt_version") or ""))
    for r in data["results"]:
        uid = r.get("unique_id")
        if not uid:
            continue
        b.outcomes[uid] = Outcome(uid, str(r.get("status") or ""),
                                  r.get("failures"), str(r.get("message") or "")[:300])
    return b


def configured_limit(project, test_uid: str):
    """The `limit` dbt applied to this test's query, from the manifest. None when unset."""
    node = (project.raw.get("nodes", {}) or {}).get(test_uid) or {}
    lim = (node.get("config") or {}).get("limit")
    try:
        return int(lim) if lim is not None else None
    except (TypeError, ValueError):
        return None


def capped(project, build: Build) -> list[dict]:
    """Tests whose reported failure count IS the configured cap.

    *** "GOT 500 RESULTS" AGAINST A REAL 6,251 IS AN ORDER OF MAGNITUDE, REPORTED AS A FACT. ***
    dbt applies `limit` to the test query, so the number it prints is `min(real, limit)`. When
    those are equal the count carries no information about the real size except that it is at
    least that. Exact, free, and needs nothing but the two files.

    Equality is the whole test. A count BELOW the limit is a true count and is left alone.
    """
    out = []
    for uid, o in sorted(build.outcomes.items()):
        if o.failures is None:
            continue
        lim = configured_limit(project, uid)
        if lim is None or o.failures != lim:
            continue
        node = (project.raw.get("nodes", {}) or {}).get(uid) or {}
        out.append({"test": node.get("name", uid), "unique_id": uid,
                    "reported": o.failures, "limit": lim,
                    "model": node.get("attached_node", "") or "",
                    "file": node.get("original_file_path", "")})
    return out


def skipped(project, build: Build) -> list[dict]:
    """Nodes the build never ran.

    *** A SKIPPED TEST IS NOT A PASS, AND IT IS THE SENTENCE THIS WHOLE TIER OPENS WITH. ***
    Reported from the field: two model errors skipped 108 models in a 643-node build, and the
    alerting said "112 of 645 failed" with no distinction between failed and never ran. A test
    that did not execute asserted nothing, and a summary that counts it as neither failing nor
    outstanding has quietly shrunk the denominator.
    """
    out = []
    for uid, o in sorted(build.outcomes.items()):
        if o.status != "skipped":
            continue
        node = (project.raw.get("nodes", {}) or {}).get(uid) or {}
        out.append({"unique_id": uid, "name": node.get("name", uid),
                    "kind": node.get("resource_type", "")})
    return out


def coverage(project, build: Build) -> dict:
    """Of the models in this project, how many had an assertion actually EXECUTE in this build.

    Nothing else here computes it. `assay tests` says which models have a test declared; this says
    which had one run, and the gap between those two numbers is what a skipped build hides.
    """
    tests = {u: n for u, n in (project.raw.get("nodes", {}) or {}).items()
             if n.get("resource_type") == "test"}
    ran, models_with_a_test, models_asserted = 0, set(), set()
    for uid, n in tests.items():
        m = n.get("attached_node") or ""
        if m:
            models_with_a_test.add(m)
        o = build.outcomes.get(uid)
        if o is not None and o.status in PASSED + FAILED:
            ran += 1
            if m:
                models_asserted.add(m)
    total = len(project.models)
    return {"models": total,
            "models_with_a_test_declared": len(models_with_a_test),
            "models_an_assertion_ran_on": len(models_asserted),
            "tests_declared": len(tests), "tests_that_ran": ran,
            "tests_that_did_not_run": len(tests) - ran}
