"""Evidence that something is wrong NOW, as findings: a guarantee that stopped holding, and a dbt
test whose last result failed.

*** THE STRONGEST EVIDENCE assay HAD REACHED NO DECISION. *** (sunny-data, assay-loops.md) A
proof that was lost lived on the Guarantees tab, and a failing test on the Explanations tab. The
fix list, the review order, the gate and the digest all read findings, so neither reached any of
them. As findings they flow into every loop by the one path the rest takes.
"""
from __future__ import annotations

from .checks.structural import Finding

# what each guarantee state means for a person, and how heavy it is
_GUARANTEE = {
    "lost": (3, "stopped holding: a premise the project declared broke in the data"),
    "contradicted": (3, ("proven, and a run of the model on inputs meeting its premises "
                         "contradicted it")),
    "refuted": (2, "does not hold: the key it needs is duplicated in the data"),
}


def guarantee_findings(project, store, led) -> list[Finding]:
    """One finding per stored certificate that is lost, contradicted, or does not hold.

    `regrouped` (a fan-out grouped back by the model's own grain) is left out on purpose: the
    output is fine, and that is what the proof layer already concluded (L9)."""
    if store is None or led is None:
        return []
    from . import prove as prove_mod
    rows = prove_mod.stored(store)
    if not rows:
        return []
    try:
        rows = prove_mod.with_guarantees(rows, led, project, store)
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for r in rows:
        g = r.get("guarantee")
        if g not in _GUARANTEE:
            continue
        m = project.models.get(r["model"])
        if m is None or getattr(m, "is_installed_package", False):
            continue
        base, what = _GUARANTEE[g]
        broke = next((p for p in r.get("premises") or [] if p.get("status") == "broken"), None)
        kw = {
            "subject": r["model"], "subject_name": r["model_name"],
            "file": getattr(m, "path", "") or "",
            "summary": f"{r['statement']}: {what}", "detail": (r.get("lost_because") or what),
            "base": base,
            "evidence": {"property": r["property"], "statement": r["statement"],
                         "guarantee": g, "rule": r.get("rule") or "",
                         **({"premise": broke.get("statement", ""),
                             "premise_why": broke.get("why", "")} if broke else {}),
                         **({"run_check": r["run_check"]["detail"]}
                            if g == "contradicted" else {})}}
        # two literal names, so the checks registry (which reads `check="..."`) sees both
        if g == "refuted":
            out.append(Finding(check="guarantee_does_not_hold", **kw))
        else:
            out.append(Finding(check="guarantee_lost", **kw))
    return _reach(project, out)


def failing_test_findings(project, store) -> list[Finding]:
    """One finding per dbt test on this project's own models whose LAST result failed or
    errored (a warn is a warn: its own severity says it is allowed to)."""
    if store is None:
        return []
    from . import ledger
    last = ledger.test_status(store)
    if not last:
        return []
    out = []
    for t in project.tests:
        st = last.get(t.unique_id)
        if not st or st[0] not in ("fail", "error"):
            continue
        m = project.models.get(t.tests_model or "")
        if m is None or getattr(m, "is_installed_package", False):
            continue
        out.append(Finding(
            check="test_is_failing", subject=t.tests_model, subject_name=m.name,
            file=getattr(m, "path", "") or "",
            summary=f"`{t.name}` {'fails' if st[0] == 'fail' else 'errors'} on its last run",
            detail=("The project's own test says this model is wrong today. Make it pass, or "
                    "say why the rows it flags are acceptable; the Explanations in the review "
                    "form show the failing rows."),
            base=3,
            evidence={"test": t.name, "test_id": t.unique_id, "status": st[0],
                      "ran_at": st[1][:10]}))
    return _reach(project, out)


def _reach(project, out: list) -> list:
    for f in out:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return out
