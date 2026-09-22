"""*** A STATE THAT CANNOT BE BUILT TWICE MAKES `state_hash` A ONE-WAY NUMBER. ***

Measured before this module existed: rebuilding all 871 model and edge subjects of a real
warehouse through `subjects.build` and hashing them reproduced **0** of the stored `state_hash`
values. Nothing had drifted. The states that were sent were assembled at eighteen call sites --
two inline, two merging the vocabulary in at the point of the call -- so no rebuild could ever
produce one of them again.

That is why `assay stale --exact` could not be written, and it is what these tests are for. The
proof is not that the code looks right: it is that every reproducible builder is RUN, stored
through `decide`, rebuilt from the identifiers it recorded, and hashed to the same value.
"""
from __future__ import annotations

import json

import pytest
from test_cost import FakeClient

from dbt_assay import states
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.jev import decide, state_hash
from dbt_assay.manifest import Project
from dbt_assay.parse import digest
from dbt_assay.store import Store


@pytest.fixture
def ctx(project_dir, tmp_path):
    project = Project.load(project_dir)
    digests = {uid: digest(m.compiled, m.name)
               for uid, m in project.models.items() if m.readable}
    schema = Schema.load(project, project_dir)
    derive_columns(project, digests, schema)
    store = Store(str(tmp_path / "s.duckdb"))
    yield states.Ctx(project=project, digests=digests, schema=schema, store=store,
                     vocab={"lead": "a business that might buy something"})
    store.close()


def _cases(ctx) -> dict:
    """One real (key, inputs) for every builder the fixture project can produce."""
    out = {}
    for u in sorted(ctx.proposed()):
        out["grain"] = (u, {"uid": u})
        break
    for u in sorted(ctx.project.models):
        facts = ctx.column_facts(u)
        if facts:
            out["columns"] = (u, {"uid": u, "columns": sorted(facts)[:3]})
            break
    edges = sorted(ctx.edge_facts())
    if edges:
        parent, child = edges[0]
        out["edge"] = (f"{child}::edge::{parent}", {"parent": parent, "child": child})
    sem = ctx.semantic_subjects()
    for u, s in sorted(sem.items()):
        if s.purpose:
            out["description"] = (f"{u}::desc", {"uid": u})
        if s.predicates:
            chunk = list(s.predicates)[:2]
            out["predicates"] = (f"{u}::pred::{states.digest_of(chunk)}",
                                 {"uid": u, "predicates": chunk})
    cands = ctx.claim_candidates()
    by_model: dict = {}
    for c in cands.values():
        by_model.setdefault(c.subject, []).append(c)
    for subject, cs in sorted(by_model.items()):
        if subject in ctx.project.models:
            ids = [c.claim_id for c in cs[:2]]
            out["claim_kind"] = (f"{subject}::sentence::{ids[0]}",
                                 {"uid": subject, "claim_ids": ids})
            break
    subs = ctx.subjects_of("model")
    if subs:
        k = min(subs)
        out["subject"] = (k, {"kind": "model", "key": k, "subject_state": "full"})
    pairs = ctx.align_pairs()
    if pairs:
        ks = sorted(pairs)[:2]
        out["align"] = (f"align::{states.digest_of(ks)}", {"pairs": ks})
    tsubs = ctx.test_subjects()
    if tsubs:
        ts = sorted(tsubs)[:2]
        out["severity"] = (f"sev::{states.digest_of(ts)}", {"tests": ts})
    out["bank"] = ("bank::column_role", {"family": "column_role"})

    # *** THE TWO THAT NEED A STORE WITH SOMETHING IN IT. ***
    # `claim_align` verifies a claim already recorded; `ruling_pair` is two disagreements somebody
    # wrote down. A fixture project alone produces neither, and an exemption saying "covered
    # elsewhere" is a claim about tests rather than a test -- so the rows are seeded here.
    _seed(ctx)
    for cid, subject in sorted(ctx.stored_claims().items()):
        out["claim_align"] = (f"{subject.subject}::claim::{cid}", {"claim_id": cid})
        break
    pairs = ctx.subjects_of("ruling_pair")
    if pairs:
        k = min(pairs)
        out["ruling_pair"] = (k, {"key": k})
    return out


def _seed(ctx) -> None:
    """One claim about a real column of a real model, and two differently-worded disagreements."""
    uid = next(u for u, m in sorted(ctx.project.models.items()) if m.readable)
    cols = sorted(ctx.column_facts(uid))
    text = f"Every row has a {cols[0]}." if cols else "One row per id."
    ctx.store.con.execute(
        "insert or replace into claims (claim_id, subject, subject_name, text, source_kind, "
        "source_ref, kind, kind_conf, citation, status, extracted_at) "
        "values ('c1', ?, ?, ?, 'description', 'schema.yml:1', 'claim_about_output', 0.9, '', "
        "'active', current_timestamp)",
        [uid, ctx.project.models[uid].name, text])
    for i, note in enumerate(("the grain is wrong here, it is one row per section",
                              "this says one row per right and it is one per section")):
        ctx.store.con.execute(
            "insert or replace into adjudications (subject, question, family, answered, verdict, "
            "correction, note, decided_by, source, prompt_version, model_version, decision_key, "
            "decided_at) values (?, 'role__x', 'column_role', 'a', 'disagree', '', ?, 'me', "
            "'human', 'v1', 'm1', '', current_timestamp)",
            [f"{uid}::finding::{i}", note])


def test_every_reproducible_builder_rebuilds_to_the_same_hash(ctx):
    """*** THE WHOLE ARGUMENT, RUN RATHER THAN ASSERTED. ***

    Build it, send it through `decide` so the store records the builder and its inputs, then
    rebuild from ONLY what the store recorded and hash it. Equal, or the state is not reproducible
    and `--exact` would be reporting drift that is not there.
    """
    cases = _cases(ctx)
    assert cases, "the fixture produced no states at all; this test is checking nothing"
    checked = []
    for i, (name, (key, inputs)) in enumerate(sorted(cases.items())):
        rec = states.make(name, ctx, key=key, inputs=inputs)
        assert rec is not None, f"{name} built nothing from {inputs}"
        decide(ctx.store, FakeClient(n_answers=1, usage={"input_tokens": 10}, call_id=f"c{i}"),
               rec, {"sentence__0": {"type": "noul", "instructions": {"question": "?"}}},
               prompt_version="v1", caller=f"assay.{name}")
        got = ctx.store.con.execute(
            "select state_hash, state_builder, state_inputs from model_decisions "
            "where decision_key = ?", [key]).fetchone()
        stored_hash, stored_builder, stored_inputs = got
        assert stored_builder == name
        rebuilt = states.rebuild(stored_builder, ctx, json.loads(stored_inputs))
        assert rebuilt is not None, f"{name} could not be rebuilt from {stored_inputs}"
        assert state_hash(rebuilt) == stored_hash, (
            f"{name} rebuilt to a DIFFERENT state. That is the defect this module exists to "
            f"remove: `assay stale --exact` would report drift on an unchanged project.")
        checked.append(name)
    assert len(checked) >= 10, f"only {checked} were exercised"


def test_every_reproducible_builder_is_covered_by_that_test(ctx):
    """*** A SCANNER MATCHING NOTHING PASSES WRONGLY. ***

    The test above proves the builders it happens to reach. A builder added later and never
    reached would be proven by nothing while the suite stayed green, which is the shape this
    codebase keeps finding. Every reproducible builder must appear, or be named here with a reason.
    """
    reproducible = {n for n, b in states.BUILDERS.items() if b.reproducible}
    missing = sorted(reproducible - set(_cases(ctx)))
    assert not missing, (
        f"these builders are never rebuilt by any test, so nothing proves they can be: {missing}")


def test_a_builder_that_cannot_be_reproduced_says_why(ctx):
    """Three states carry rows read out of the warehouse. They must be named, not silently
    absent -- an absent comparison is not a clean bill."""
    for name, b in states.BUILDERS.items():
        if b.reproducible:
            assert not states.why_not(name)
        else:
            assert len(states.why_not(name)) > 40, f"{name} gives no real reason"
            assert b.fn is None, f"{name} must not be callable; its caller supplies the state"


def test_a_reproducible_builder_refuses_a_handed_in_state(ctx):
    """The second path, refused at the door rather than reviewed for."""
    with pytest.raises(ValueError, match="builds its own state"):
        states.make("grain", ctx, key="model.p.stg_bad_notnull",
                    inputs={"uid": "model.p.stg_bad_notnull"}, state={"anything": 1})


def test_a_data_carrying_builder_requires_one(ctx):
    with pytest.raises(ValueError, match="cannot build its own state"):
        states.make("feed", ctx, key="model.p.x::feed", inputs={"uid": "model.p.x"})


def test_decide_refuses_a_bare_dict(ctx):
    """*** THERE IS NO SECOND WAY IN. ***"""
    with pytest.raises(TypeError, match="states.Recipe"):
        decide(ctx.store, FakeClient(), {"sql": "select 1"},
               {"sentence__0": {"type": "noul", "instructions": {"question": "?"}}},
               prompt_version="v1", caller="assay.test")


def test_a_chunk_id_is_stable_across_processes(ctx):
    """*** `hash()` IS SALTED PER PROCESS AND THREE DECISION KEYS WERE BUILT FROM IT. ***

    `align::{hash(tuple(...))}`, `{uid}::pred::{hash(...)}` and `sev::{hash(...)}`. The same ten
    pairs produced a different cache key on every run, so the lookup missed and the question was
    paid for again, forever.
    """
    import subprocess
    import sys
    code = ("from dbt_assay.states import digest_of; print(digest_of(['a','b','c']))")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           check=True).stdout.strip() for _ in range(3)}
    assert len(runs) == 1, f"the chunk id moved between processes: {runs}"
    assert runs.pop() == states.digest_of(["a", "b", "c"])


def test_no_decision_key_is_built_from_a_salted_hash():
    """The three that were. A guard, because it reads as ordinary code and costs money forever.

    Read through the AST rather than with a regex: this file and `states.py` both QUOTE the bad
    pattern in prose, and a guard that fires on its own documentation gets deleted.
    """
    import ast
    from pathlib import Path

    import dbt_assay
    bad = []
    for f in sorted(Path(dbt_assay.__file__).parent.rglob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "hash"):
                bad.append(f"{f.name}:{node.lineno}")
    assert not bad, (
        f"a cache key built from Python's per-process `hash()` misses on every run and re-buys "
        f"the answer: {bad}. Use `states.digest_of`.")


def test_exact_catches_a_change_to_a_PARENT_that_the_checksum_tier_cannot(ctx, project_dir):
    """*** THE WHOLE REASON THE EXACT TIER EXISTS. ***

    An answer about a HOP is filed under the child. Edit the parent and the child's file checksum
    does not move, so the cheap tier reports that answer as current -- correctly, by its own
    definition, and uselessly. The state the answer was computed from carries the parent's columns,
    so rebuilding it says what the checksum cannot.

    Necessary and not sufficient, demonstrated rather than asserted.
    """
    import json

    from dbt_assay import stale as stale_mod

    parent, child = min(ctx.edge_facts())
    key = f"{child}::edge::{parent}"
    rec = states.make("edge", ctx, key=key, inputs={"parent": parent, "child": child})
    assert rec is not None
    ctx.store.use_project(ctx.project)
    decide(ctx.store, FakeClient(n_answers=1, usage={"input_tokens": 10}, call_id="e1"),
           rec, {"sentence__0": {"type": "noul", "instructions": {"question": "?"}}},
           prompt_version="v1", caller="assay.traverse")

    # nothing has changed yet: both tiers agree it is current
    assert stale_mod.exact(ctx.store, ctx)["current"] == 1
    assert len(stale_mod.survey(ctx.store, ctx.project)["moved"]) == 0

    # the PARENT gains a column. The child's own file is untouched.
    pname = ctx.project.models[parent].name
    compiled = next(project_dir.rglob(f"compiled/**/{pname}.sql"))
    compiled.write_text(compiled.read_text().replace("select ", "select 1 as extra_col, ", 1))
    mf = json.loads((project_dir / "manifest.json").read_text())
    mf["nodes"][parent]["checksum"]["checksum"] = "a" * 64          # dbt would rehash the parent
    (project_dir / "manifest.json").write_text(json.dumps(mf))

    moved = Project.load(project_dir)
    digests = {uid: digest(m.compiled, m.name)
               for uid, m in moved.models.items() if m.readable}
    schema = Schema.load(moved, project_dir)
    derive_columns(moved, digests, schema)
    after = states.Ctx(project=moved, digests=digests, schema=schema, store=ctx.store,
                       vocab=ctx.vocab)

    cheap = stale_mod.survey(ctx.store, moved)
    assert len(cheap["moved"]) == 0, (
        "the child's own file did not change, so the checksum tier cannot see this -- that is "
        "the point, not a bug")
    exact = stale_mod.exact(ctx.store, after)
    assert len(exact["moved"]) == 1 and exact["current"] == 0, exact
    assert exact["moved"][0]["builder"] == "edge"
