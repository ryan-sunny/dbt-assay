"""Standard dbt practice, deferred to where it exists and adjudicated where it is noisy.

*** DO NOT REIMPLEMENT dbt-project-evaluator. ***
It is maintained, community-standard, and already right about twenty-four things. assay defers to
it, reads its `fct_*` tables through the same `dbt show` path everything else uses, and adds the
three things it does not have: CONSEQUENCE (evaluator has no notion of blast radius, so a fanout on
a leaf and a fanout feeding nine marts are the same row to it), ADJUDICATION for the checks with
real exceptions, and ONE STREAM with the gate discipline.

*** THE THREE-WAY SPLIT IS THE WHOLE DESIGN. ***
    enforce     exact and consequential, essentially no legitimate exception
    recommend   conventional; enforcing it is how a tool gets muted
    adjudicate  a real candidate with real exceptions, so ask

*** AND THREE PLACES A JUDGMENT IS STRICTLY BETTER, NOT MERELY ALONGSIDE. ***
`documentation_coverage` counts whether a description EXISTS; assay judges whether it is TRUE, and
a project can be fully documented and entirely wrong. `model_naming_conventions` is a regex against
a prefix. `missing_primary_key_tests` says "no PK test", where assay knows the inferred grain and
can say WHICH columns it should cover -- the difference between a nag and a patch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import QUESTIONS
from .jev import choice

PRACTICE_Q = QUESTIONS["practice_exception"]
PRACTICE_VERSION = PRACTICE_Q["prompt_version"]

ENFORCE = {
    "fct_staging_dependent_on_marts_or_intermediate":
        "a staging model reading downstream is a cycle in intent",
    "fct_marts_or_intermediate_dependent_on_source":
        "skipping the staging layer means no single place to fix the source's quirks",
    "fct_direct_join_to_source":
        "joining a source directly bypasses whatever staging cleans up",
    "fct_duplicate_sources":
        "the same table declared twice; two names for one thing drift apart",
    "fct_hard_coded_references":
        "a literal table name instead of ref(): it silently breaks lineage, and lineage is what "
        "every other check here depends on",
    "fct_exposures_dependent_on_private_models":
        "an exposure resting on something not meant to be depended on",
    "fct_chained_views_dependencies":
        "a long chain of views re-computes the whole chain on every read",
}

RECOMMEND = {
    "fct_model_naming_conventions": "naming",
    "fct_model_directories": "layout",
    "fct_source_directories": "layout",
    "fct_test_directories": "layout",
    "fct_documentation_coverage": "documentation",
    "fct_test_coverage": "testing",
    "fct_sources_without_freshness": "freshness",
    "fct_public_models_without_contract": "governance",
}

ADJUDICATE = {
    "fct_model_fanout": "many models read this one",
    "fct_source_fanout": "many models read this source directly",
    "fct_rejoining_of_upstream_concepts": "a model rejoins something already upstream of it",
    "fct_multiple_sources_joined": "several sources joined in one model",
    "fct_too_many_joins": "an unusual number of joins in one model",
    "fct_root_models": "a model that refs nothing",
    "fct_staging_dependent_on_staging": "a staging model reading another staging model",
    "fct_exposure_parents_materializations":
        "an exposure resting on a view rather than a table",
}

ALL = {**{k: "enforce" for k in ENFORCE},
       **{k: "recommend" for k in RECOMMEND},
       **{k: "adjudicate" for k in ADJUDICATE}}

# Evaluator's fct_ tables name the offending node under one of these, depending on the check.
_NAME_COLUMNS = ("resource_name", "child", "model", "parent", "source_name", "name",
                 "resource_id", "unique_id")


@dataclass
class Flag:
    check: str
    category: str
    model: str
    row: dict
    why: str = ""
    marts: int = 0
    descendants: int = 0
    contract: dict = field(default_factory=dict)


def model_of(row: dict) -> str | None:
    for c in _NAME_COLUMNS:
        for k, v in row.items():
            if k.lower() == c and isinstance(v, str) and v:
                return v.split(".")[-1]
    return None


def categories(config_overrides: dict | None = None) -> dict:
    """The shipped split, overridable per check in audit.yml."""
    out = dict(ALL)
    for check, cat in (config_overrides or {}).items():
        if cat not in ("enforce", "recommend", "adjudicate", "off"):
            raise ValueError(f"unknown practice category {cat!r} for {check}")
        out[check] = cat
    return out


def collect(project, entries, probe_mod, project_dir: str, profiles_dir: str | None,
            dbt_bin: str, cats: dict, schema_name: str | None = None,
            per_check: int = 40) -> tuple[list, list]:
    """(flags, unavailable). A check whose table is absent is reported, not counted clean."""
    by_name = {e.name: e for e in entries}
    db = None
    for n in (project.raw.get("nodes", {}) or {}).values():
        if n.get("resource_type") == "model":
            db = n.get("database")
            break
    flags, missing = [], []
    for check, cat in sorted(cats.items()):
        if cat == "off":
            continue
        rel = ".".join(p for p in (db, schema_name, check) if p)
        got = probe_mod.run_sql(f"select * from {rel}", project_dir, profiles_dir, dbt_bin,
                                limit=per_check)
        if not got:
            # *** A PARTIAL EVALUATOR BUILD READ AS A CLEAN PROJECT. ***
            # Reported from the field: five fct_ models of many were built, and the categories
            # whose tables did not exist were reported as nothing at all. An absent table and an
            # empty one are not the same fact, and only one of them is a pass. The caller is told
            # which; `collect` cannot tell them apart from an empty result alone.
            missing.append(check)
            continue
        for r in got:
            name = model_of(r)
            e = by_name.get(name) if name else None
            flags.append(Flag(
                check=check, category=cat, model=name or "?", row=r,
                why=ENFORCE.get(check) or ADJUDICATE.get(check) or RECOMMEND.get(check, ""),
                marts=e.marts if e else 0, descendants=e.descendants if e else 0,
                contract={"grain": e.grain.value if e and e.grain else None,
                          "reads": e.reads[:8] if e else []},
            ))
    return flags, missing


def build_state(flag: Flag, vocab: dict | None = None) -> dict:
    state = {
        "model": flag.model,
        "pattern": flag.why or flag.check,
        "what_the_check_found": {k: v for k, v in flag.row.items() if v is not None},
        "contract": {k: v for k, v in flag.contract.items() if v},
        "blast_radius": {"models_downstream": flag.descendants, "marts_downstream": flag.marts},
    }
    if vocab:
        state["vocabulary"] = vocab
    return {k: v for k, v in state.items() if v not in (None, [], {})}


def question_for(flag: Flag) -> dict:
    return {"exception": choice({"pattern": flag.why or flag.check,
                                 "model": flag.model,
                                 **PRACTICE_Q["instructions"]},
                                PRACTICE_Q["criteria"])}


def primary_key_patches(project, entries) -> list[tuple]:
    """*** WHERE A JUDGMENT BEATS THE STANDARD CHECK OUTRIGHT. ***

    Evaluator says "no primary key test". assay knows the inferred grain, so it says which columns
    the test should cover -- a patch rather than a nag. Pure code on top of the inventory.
    """
    tested = set()
    for t in project.tests:
        if t.kind in ("unique", "unique_combination_of_columns") and t.tests_model:
            tested.add(t.tests_model)
    out = []
    for e in entries:
        if e.uid in tested or not e.grain or e.unreadable:
            continue
        cols = e.grain.value if isinstance(e.grain.value, list) else [e.grain.value]
        # *** A TEST CANNOT ASSERT ON A COLUMN THE MODEL DOES NOT EMIT. ***
        # Reported from the field: 0 of 15 proposed grains held, and 9 named a column that is not
        # in the model's output. The grain is what the SQL groups or dedups by, and a model can
        # dedup on a key and then drop it -- which is the `my_prospects` case verification already
        # found, where `partition by name_key, city` is followed by `exclude (name_key)`.
        emitted = {c.name.lower() for c in (e.columns or [])}
        keep = [c for c in cols if str(c).lower() in emitted] if emitted else list(cols)
        dropped = [c for c in cols if c not in keep]
        if emitted and not keep:
            # Not a patch at all: nothing downstream can assert this model's own uniqueness.
            out.append((e.name, [], e.grain.source, e.marts, list(cols)))
            continue
        out.append((e.name, keep, e.grain.source, e.marts, dropped))
    return sorted(out, key=lambda x: -x[3])


# *** "CAN BE WRITTEN" IS NOT "WOULD PASS", AND THE DIFFERENCE WAS 0 OF 7. ***
# Reported from the field after the columns fix: every proposed grain was expressible in the
# output and none of them held. `water_division` was proposed as the grain of a 1,045-row model
# with SEVEN distinct values -- a reader following that recommendation writes a test that fails on
# its first run. A command that claims to hand over a patch rather than a nag cannot do that.
#
# The shape is already in this codebase: `rows.which_have_failures` batches a count across
# hundreds of relations in one statement. The same batching over count(*) against
# count(distinct <grain>) settles every proposal at once, wherever the model is built.
#
# AND A PROPOSAL THAT DOES NOT HOLD IS THE STRONGER FINDING. The model has no uniqueness test AND
# nobody knows what one row of it is, which is worse than a missing test and was invisible.
def verify_grains(patches: list, project, probe_mod, project_dir: str,
                  profiles_dir: str | None, dbt_bin: str, batch: int = 60) -> dict:
    """{model_name: (rows, distinct)} for every proposal that could be counted.

    A model absent from the result was not counted, and an absent count must never read as a pass:
    the caller reports `could not check` rather than `holds`.
    """
    by_name = {}
    for e in project.models.values():
        by_name[e.name] = getattr(e, "relation_name", None) or e.name
    todo = [(name, cols) for name, cols, _src, _m, _d in patches if cols and name in by_name]
    out: dict = {}

    def ask(chunk: list) -> bool:
        parts = []
        for name, cols in chunk:
            keys = ", ".join(f'"{c}"' for c in cols)
            parts.append(f"select '{name}' as m, count(*) as n, "
                         f"count(distinct ({keys})) as d from {by_name[name]}")
        got = probe_mod.run_sql(" union all ".join(parts), project_dir, profiles_dir, dbt_bin,
                                limit=len(chunk) + 1)
        if not got:
            return False
        for row in got:
            vals = list(row.values())
            m = row.get("m", vals[0] if vals else None)
            try:
                out[str(m)] = (int(row.get("n", vals[1])), int(row.get("d", vals[2])))
            except (TypeError, ValueError, IndexError):
                continue
        return True

    def walk(chunk: list) -> None:
        if not chunk or ask(chunk):
            return
        if len(chunk) == 1:
            return                        # uncounted, and deliberately absent from `out`
        mid = len(chunk) // 2
        walk(chunk[:mid])
        walk(chunk[mid:])

    for i in range(0, len(todo), batch):
        walk(todo[i:i + batch])
    return out
