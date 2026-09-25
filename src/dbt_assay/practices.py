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
        if not str(check).startswith("fct_"):
            continue                     # `evaluator_schema`, `evaluator: off`: settings, not rules
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
    wanted = [(check, cat, ".".join(p for p in (db, schema_name, check) if p))
              for check, cat in sorted(cats.items()) if cat != "off"]
    # One read per evaluator table was one dbt startup per table. They are independent, so they go
    # through the batching door, and each still answers -- or fails -- on its own.
    answers = _read_all(probe_mod, [rel for _c, _k, rel in wanted], project_dir, profiles_dir,
                        dbt_bin, per_check, getattr(project, "dialect", "duckdb"),
                        caller="assay.practices.collect")
    for (check, cat, rel), got in zip(wanted, answers):
        if got.failed:
            # *** A PARTIAL EVALUATOR BUILD READ AS A CLEAN PROJECT. ***
            # Reported from the field: five fct_ models of many were built, and the categories
            # whose tables did not exist were reported as nothing at all. An absent table and an
            # empty one are not the same fact, and only one of them is a pass.
            #
            # This used to be `if not got`, which could not tell them apart -- so a table that
            # existed and was clean and a table that was not there produced the same answer. The
            # statement now says which, and only the failure is reported as unavailable.
            missing.append(check)
            continue
        for r in got.rows:
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


def _read_all(probe_mod, relations: list[str], project_dir: str, profiles_dir: str | None,
              dbt_bin: str, limit: int, dialect: str, caller: str) -> list:
    """`select *` from each relation, batched when the probe module can batch."""
    many = getattr(probe_mod, "run_many", None)
    if many is None:
        return [probe_mod.run_sql(f"select * from {rel}", project_dir, profiles_dir, dbt_bin,
                                  limit=limit, caller=caller, kind="metadata", relation=rel)
                for rel in relations]
    return many([probe_mod.Statement(f"select * from {rel}", caller=caller, kind="metadata",
                                     limit=limit, relation=rel) for rel in relations],
                project_dir, profiles_dir, dbt_bin, dialect)


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


# Engines whose information_schema lists columns per (schema, table) and answers it cheaply.
# BigQuery's is per dataset (`project.dataset.INFORMATION_SCHEMA.COLUMNS`), asked per dataset.
_LISTABLE = ("duckdb", "postgres", "snowflake", "redshift", "mysql", "bigquery")


def present_columns(probe_mod, rels, project_dir: str, profiles_dir: str | None, dbt_bin: str,
                    dialect: str = "duckdb") -> dict | None:
    """{relation as given: its columns (lowercase), or None when the table is not built}, from
    ONE statement; None overall when the engine cannot be asked, and then nothing is filtered.

    *** ONE MISSING COLUMN COST A dbt CALL PER HALVING. *** (sunny-data box) The counted checks
    send a union of counts; a member naming a table that is not built, or a column the built
    table does not have yet, fails the whole statement, which is then split in halves to find it,
    each half another dbt start (~9s on the box). `practices` made 13 calls where one would do.
    Listed first, a member that cannot run is left out and stays uncounted, which is what the
    halving concluded about it anyway, one call later each time.
    """
    if dialect not in _LISTABLE:
        return None
    parts = {}
    for r in set(rels):
        bits = [x.strip('"`').lower() for x in str(r).split(".")]
        if len(bits) < 2:
            return None                 # a bare name: which schema is not known here
        parts[r] = (bits[-2], bits[-1])
    if not parts:
        return {}
    names = ", ".join("'" + t.replace("'", "''") + "'" for t in sorted({t for _s, t in
                                                                           parts.values()}))
    cols = "select lower(table_schema) as s, lower(table_name) as t, lower(column_name) as c "
    where = f"where lower(table_name) in ({names})"
    if dialect == "bigquery":
        # one listing per dataset, in one statement
        sets = {}
        for r in rels:
            bits = [x.strip("`") for x in str(r).replace("`", "").split(".")]
            if len(bits) >= 3:
                sets[(bits[-3], bits[-2])] = True
            elif len(bits) == 2:
                sets[("", bits[-2])] = True
        sql = " union all ".join(
            f"{cols}from `{(p + '.') if p else ''}{ds}`.INFORMATION_SCHEMA.COLUMNS {where}"
            for p, ds in sorted(sets))
    else:
        sql = f"{cols}from information_schema.columns {where}"
    got = probe_mod.run_sql(sql, project_dir, profiles_dir, dbt_bin, limit=500_000,
                            caller="assay.practices.present_columns", kind="metadata")
    if got.failed:
        return None
    if any(not {"s", "t", "c"} <= set(row) for row in got.rows):
        return None                     # not a listing: filter nothing rather than everything
    have: dict = {}
    for row in got.rows:
        have.setdefault((str(row["s"]), str(row["t"])), set()).add(str(row["c"]))
    return {r: have.get(st) for r, st in parts.items()}


def _runnable(present: dict | None, rel: str, cols=()) -> bool:
    """Whether a count over `rel` and `cols` can run, as far as the listing knows."""
    if present is None:
        return True
    have = present.get(rel)
    return have is not None and all(str(c).lower() in have for c in cols)


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
        m = (getattr(project, "models", None) or {}).get(e.uid)
        if m is not None and getattr(m, "is_installed_package", False):
            continue                    # a package's model is not this project's to test
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
def share(kept: int, total: int) -> str:
    """A share written so a non-zero one never prints as `0%`.

    *** `keeps 0% of dim_owner: 13,694 rows` -- THE SAME ROUNDING BUG, POINTED THE OTHER WAY. ***
    `1.56x` printing as `2x` overstated; this understates, and reading `0%` as "it keeps nothing"
    sends somebody looking for an empty table that has thirteen thousand rows in it.
    """
    if total <= 0:
        return "?"
    r = kept / total
    if r == 0:
        return "0%"
    for places in (0, 1, 2, 3):
        s = f"{r:.{places}%}"
        if float(s.rstrip("%")) > 0:
            return s
    return "<0.001%"


def fanout(n: int, d: int) -> str:
    """`n / d`, written so it cannot read as a smaller problem -- or a bigger one -- than it is.

    *** 1.56x PRINTED AS `2x`. *** Reported from the field on `business_leads`: 73,608 rows over
    47,144 distinct. Rounding overstates a number somebody acts on, and the opposite rounding is
    worse -- 1.004 reading as `1x` says the grain holds when it does not. So a ratio above one
    never prints as one, however close it is.
    """
    if d <= 0:
        return "?"
    r = n / d
    if r >= 10:
        return f"{r:,.0f}x"
    for places in (2, 3, 4):
        s = f"{r:.{places}f}".rstrip("0").rstrip(".")
        if r <= 1 or float(s) > 1:
            return f"{s}x"
    return f"{r:.4f}x"


def verify_join_keys(entries, project, probe_mod, project_dir: str, profiles_dir: str | None,
                     dbt_bin: str, schema=None, batch: int = 200, store=None) -> int:
    """Count whether each flagged hop's join key is unique IN THE DATA, and refuse the finding.

    *** dbt KNOWS WHICH KEYS ARE DECLARED UNIQUE. IT DOES NOT KNOW WHICH KEYS ARE. ***
    Two of twelve disagreements on a hand-ruled warehouse were a LEFT JOIN onto a lookup that is
    unique on the join key and carries no uniqueness test. The ruling named the blind spot
    exactly: assay believed the project instead of the warehouse. `int_water_streamflow_summary`
    is 2,387 rows over 2,387 distinct `abbrev`, and a join onto that cannot multiply anything.

    The machinery already existed. `verify_grains` batches `count(*)` against
    `count(distinct key)` in one statement, and this is the same arithmetic pointed at the parent
    of a flagged hop instead of at a proposed grain. Returns how many PARENTS it newly
    established as unique, which is NOT the number of findings it retires: a parent can match a
    hop the union rule already refused, and on a real warehouse that was 9 against 2. The caller
    measures the finding delta rather than printing a number that reads like one.

    A parent it could NOT count stays flagged. An uncounted key is not a unique one, which is the
    rule this codebase keeps relearning in the other direction.
    """
    rel_of = dict(getattr(schema, "relation", None) or {})
    by_name = {}
    for uid, m in project.models.items():
        by_name[m.name] = (rel_of.get(uid) or m.name).replace('"', "")
    todo: dict = {}
    for e in entries:
        for ctx, _p in e.fanout_hops:
            for pname, cols in (e.join_keys or {}).items():
                if pname in e.unique_key_parents or pname not in by_name or not cols:
                    continue
                if f" {pname} " in f" {ctx} ":
                    todo[(pname, tuple(cols))] = by_name[pname]
    if not todo:
        return 0
    items = sorted(todo.items())
    unique: set = set()
    counted: dict = {}                    # (pname, cols) -> (rows, distinct)
    idx = {pname: (pname, cols) for (pname, cols) in todo}

    def ask(chunk) -> bool:
        parts = []
        for (pname, cols), rel in chunk:
            keys = ", ".join(f'"{c}"' for c in cols)
            parts.append(f"select '{pname}' as m, count(*) as n, "
                         f"count(distinct ({keys})) as d from {rel}")
        got = probe_mod.run_sql(" union all ".join(parts), project_dir, profiles_dir, dbt_bin,
                                limit=len(chunk) + 1,
                                caller="assay.practices.verify_join_keys", kind="count")
        if got.failed:
            return False
        for row in got.rows:
            vals = list(row.values())
            try:
                m, n, d = str(row.get("m", vals[0])), int(row.get("n", vals[1])), \
                    int(row.get("d", vals[2]))
            except (TypeError, ValueError, IndexError):
                continue
            if m in idx:
                counted[idx[m]] = (n, d)
            if n and d >= n:
                unique.add(m)
        return True

    def walk(chunk) -> None:
        if not chunk or ask(chunk):
            return
        if len(chunk) == 1:
            return                        # uncounted, and an uncounted key is not a unique one
        mid = len(chunk) // 2
        walk(chunk[:mid])
        walk(chunk[mid:])

    present = present_columns(probe_mod, [rel for _k, rel in items], project_dir, profiles_dir,
                              dbt_bin, getattr(project, "dialect", "duckdb"))
    items = [(k, rel) for k, rel in items if _runnable(present, rel, k[1])]
    for i in range(0, len(items), batch):
        walk(items[i:i + batch])

    # *** KEPT, SO THE LEDGER CAN READ IT AND A LATER COUNT CAN CONTRADICT IT. *** A key counted
    # unique here holds back `hop_multiplies_rows`; the premise is that count, dated.
    if store is not None and counted:
        obs = [probe_mod.Observation(
                   relation=todo[k].lower(), column=", ".join(k[1]), row_count=n, non_null=n,
                   distinct_ct=d, status="unique" if n and d >= n else "has_duplicates",
                   detail=f"{n:,} rows, {d:,} distinct, counted by check --verify")
               for k, (n, d) in sorted(counted.items())]
        try:
            probe_mod.write(store, obs, via="verify-join-keys")
        except Exception:                                        # noqa: BLE001, S110
            pass                          # a store that cannot take it loses the record, not the run
    marked = 0
    for e in entries:
        for pname in list(e.join_keys or {}):
            if pname in unique and pname not in e.unique_key_parents:
                e.unique_key_parents.add(pname)
                marked += 1
    return marked


def grain_verdict(counted) -> tuple[str, str]:
    """What a counted grain MEANS, in one place, because two places disagreed about zero.

    *** `practices` SAID `holds: 0 rows, 0 distinct` FOR THE TABLE `patch` REFUSES AS EMPTY. ***
    Same model, same run, opposite framings, and the word `holds` sat in the column a reader scans
    for green -- in the command `onboard` points at first. `0 distinct < 0 rows` is false, so an
    empty table fell through to the success branch of a condition that never considered it.

    The refusal was already written and already correct. It was written in `patch` only, so the
    older surface kept its own reading of the same two integers. One fact, two spellings, silent
    when they disagree: the class this codebase has now found nine times. Both callers read this.
    """
    if counted is None:
        return "uncounted", "NOT COUNTED -- and an uncounted grain is not a passing one"
    n, d = counted
    if n == 0:
        return "empty", ("the table is EMPTY, so any uniqueness test on it passes for the wrong "
                         "reason. Build the model and run this again")
    if d < n:
        return "fails", (f"it does not hold: {n:,} rows, {d:,} distinct ({fanout(n, d)}). "
                         f"A test here fails on its first run")
    return "holds", f"holds: {n:,} rows, {d:,} distinct"


def verify_grains(patches: list, project, probe_mod, project_dir: str,
                  profiles_dir: str | None, dbt_bin: str, batch: int = 200, schema=None) -> dict:
    """{model_name: (rows, distinct)} for every proposal that could be counted.

    A model absent from the result was not counted, and an absent count must never read as a pass:
    the caller reports `could not check` rather than `holds`.

    *** A CUSTOM SCHEMA MADE EVERY MODEL IN IT UNCOUNTABLE. ***
    Reported from the field: everything in `main` counted and everything in `main_water` and
    `main_water_az` came back `(not counted)` -- including the 149x case the release leads with.
    A bare model name resolves to the default schema, so the count did not fail loudly, it failed
    as an absence, which is the shape this codebase keeps having to catch. dbt's `+schema:` is
    ordinary past a certain project size and the manifest carries the qualified relation per node,
    which `Schema.relation` already holds. Pass it and the name is only the fallback.
    """
    rel_of = dict(getattr(schema, "relation", None) or {})
    by_name = {}
    for uid, e in project.models.items():
        by_name[e.name] = (rel_of.get(uid) or getattr(e, "relation_name", None)
                           or e.name).replace('"', "")
    todo = [(name, cols) for name, cols, _src, _m, _d in patches if cols and name in by_name]
    out: dict = {}

    def ask(chunk: list) -> bool:
        parts = []
        for name, cols in chunk:
            keys = ", ".join(f'"{c}"' for c in cols)
            parts.append(f"select '{name}' as m, count(*) as n, "
                         f"count(distinct ({keys})) as d from {by_name[name]}")
        got = probe_mod.run_sql(" union all ".join(parts), project_dir, profiles_dir, dbt_bin,
                                limit=len(chunk) + 1,
                                caller="assay.practices.verify_grains", kind="count")
        if got.failed:
            return False
        for row in got.rows:
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

    present = present_columns(probe_mod, [by_name[n] for n, _c in todo], project_dir,
                              profiles_dir, dbt_bin, getattr(project, "dialect", "duckdb"))
    todo = [(n, c) for n, c in todo if _runnable(present, by_name[n], c)]
    for i in range(0, len(todo), batch):
        walk(todo[i:i + batch])
    return out


# *** THE MIRROR IMAGE OF `hop_multiplies_rows`, AND assay HAD NOTHING FOR IT. ***
# `relate` tracks the COLUMNS dropped at a boundary and has no notion of rows at all. So a hop that
# turns 2,606 documents into 463 was invisible, and that is exactly where an enrichment gap lives:
# 18% of well scans read, 18% of decree cases, 6% of resume filings, and nothing in the project
# reports any of it.
#
# *** BUT IT IS NOT "FREE FROM THE DAG PLUS TWO COUNTS". ***
# Most edges drop rows ON PURPOSE. A staging model filtered to one county, a mart filtered to
# active records, an aggregate. Shipped as a raw ratio this fires on half a DAG on day one, which
# is how a check becomes one nobody reads -- the same failure `hop_multiplies_rows` took three
# releases to climb out of. So the refusals ship in the same commit, not after:
#
#   the child FILTERS           -> it declared that it drops rows
#   the child AGGREGATES        -> fewer rows is the entire point
#   the child UNIONS            -> its count is the sum of arms, not a function of one parent
#   it PRE-AGGREGATED this parent -> it collapsed the parent in a subquery before joining it
#
# *** THAT LAST ONE WAS ALREADY COMPUTED AND THE FIRST VERSION DID NOT ASK FOR IT. ***
# Seven of eight findings on the first real run were it. `az_section_summary` turning 3,483,870
# parcel-sections into 114,305 sections is `pre_aggregated`, sitting right there on the entry,
# naming that exact parent. Same shape as the union fix: the judgment -- here, the count -- was
# allowed to be wrong about something the parser settles exactly. 8 findings became 1.
#
# What survives is a child that reads a parent, joins it, declares no filter, no group by and no
# collapse, and still emits a fraction of the rows. That is an inner join dropping silently.
def row_loss_candidates(entries) -> list[tuple]:
    """(entry, parent_name) for hops where losing rows would NOT be declared behavior."""
    out = []
    for e in entries:
        if e.filters_rows or e.aggregates or e.unreadable:
            continue
        for pname in sorted(e.join_keys or {}):
            if pname in (e.pre_aggregated_parents or {}):
                continue
            # *** A LEFT, RIGHT, FULL OR CROSS JOIN CANNOT LOSE THE ROWS IT DRIVES ON. ***
            # Only an INNER join drops. Half the candidates on a real warehouse were LEFT joins,
            # where row loss against the target is meaningless BY CONSTRUCTION.
            kind = (e.join_kind or {}).get(pname, "")
            if kind and kind != "INNER":
                continue
            # *** AND ONLY THE DRIVING EDGE CAN BE JUDGED AT ALL. ***
            # The parser's own comment on `from_relations` says it: "a model's driving table is
            # NOT something it joins to, and a check that conflates the two reports a fan-out
            # against the table the model is simply reading." The same is true pointed the other
            # way. `mart_acquisition_targets` drives on `int_acquisition_targets` (13,694 rows)
            # and LEFT JOINs `dim_owner` (3.1M); the child was never going to be 3.1M rows and
            # nothing is wrong.
            #
            # This replaces a sibling-size heuristic that refused whenever the child happened to
            # be the size of SOME parent -- which masked the real defect, because the models that
            # would trip this legitimately narrow in a sibling CTE and look exactly like that.
            if e.driving_parents and pname not in e.driving_parents:
                continue
            out.append((e, pname))
    return out


def verify_row_loss(entries, project, probe_mod, project_dir: str, profiles_dir: str | None,
                    dbt_bin: str, schema=None, batch: int = 200) -> int:
    """Count parent and child rows for every candidate hop. Returns how many it counted.

    A hop it could not count stays absent from `row_loss`, and an absent count is never a pass:
    `hop_drops_most_rows` reports only what it actually measured.
    """
    cands = row_loss_candidates(entries)
    if not cands:
        return 0
    rel_of = dict(getattr(schema, "relation", None) or {})
    by_name = {m.name: (rel_of.get(uid) or m.name).replace('"', "")
               for uid, m in project.models.items()}
    # *** EVERY PARENT OF A CANDIDATE CHILD, NOT ONLY THE CANDIDATE PARENTS. ***
    # A hop can only be judged against its siblings, so the ones that are not candidates still
    # have to be counted. They are what explains the loss.
    kids = list({id(e): e for e, _p in cands}.values())     # ModelEntry is not hashable
    wanted = {e.name for e in kids} | {p for e in kids for p in (e.join_keys or {})}
    todo = sorted(n for n in wanted if n in by_name)
    counts: dict = {}

    def ask(chunk: list) -> bool:
        parts = [f"select '{n}' as m, count(*) as n from {by_name[n]}" for n in chunk]
        got = probe_mod.run_sql(" union all ".join(parts), project_dir, profiles_dir, dbt_bin,
                                limit=len(chunk) + 1,
                                caller="assay.practices.verify_row_loss", kind="count")
        if got.failed:
            return False
        for row in got.rows:
            vals = list(row.values())
            try:
                counts[str(row.get("m", vals[0]))] = int(row.get("n", vals[1]))
            except (TypeError, ValueError, IndexError):
                continue
        return True

    def walk(chunk: list) -> None:
        if not chunk or ask(chunk):
            return
        if len(chunk) == 1:
            return
        mid = len(chunk) // 2
        walk(chunk[:mid])
        walk(chunk[mid:])

    present = present_columns(probe_mod, [by_name[n] for n in todo], project_dir, profiles_dir,
                              dbt_bin, getattr(project, "dialect", "duckdb"))
    todo = [n for n in todo if _runnable(present, by_name[n])]
    for i in range(0, len(todo), batch):
        walk(todo[i:i + batch])

    for e in kids:
        for pname in (e.join_keys or {}):
            if pname in counts:
                e.parent_rows[pname] = counts[pname]
    n = 0
    for e, pname in cands:
        if e.name in counts and pname in counts:
            e.row_loss[pname] = (counts[pname], counts[e.name])
            n += 1
    return n


def hop_drops_most_rows(project, entries, threshold: float = 0.8) -> list:
    """A hop that loses more of the parent than `threshold`, where nothing declared it would."""
    from .checks.structural import Finding
    out = []
    for e in entries:
        for pname, (pn, cn) in sorted((e.row_loss or {}).items()):
            if pn <= 0 or cn >= pn * (1 - threshold):
                continue

            kept = cn / pn
            out.append(Finding(
                check="hop_drops_most_rows", subject=e.uid, subject_name=e.name, file=e.path,
                summary=(f"this hop keeps {share(cn, pn)} of `{pname}`: "
                         f"{cn:,} rows from {pn:,}"),
                detail=("The child declares no filter and no group by, so nothing in the SQL "
                        "says these rows were meant to be dropped -- which makes this a join "
                        "that is not matching, and every count downstream is of a subset "
                        "nobody chose. If the drop IS intended, say so with a filter or a "
                        "description and this stops firing."),
                base=2,
                evidence={"parent": pname, "parent_rows": pn, "child_rows": cn,
                          "kept": round(kept, 4),
                          "joined_on": list((e.join_keys or {}).get(pname) or [])}))
    return out


# ------------------------------------------------------------------- minimality, counted not judged

def verify_minimality(candidates: dict, probe_mod, project_dir: str, profiles_dir: str | None,
                      dbt_bin: str, batch: int = 50, dialect: str = "duckdb") -> dict:
    """Which columns of each candidate key actually ADD identifying power.

    *** THIS WAS A JUDGED QUESTION AND IT IS ARITHMETIC. ***
    `column_is_part_of_the_key` asks a model whether a column is part of the MINIMAL set or is
    carried along because the others determine it. Drop the column, recount the distinct
    combination, and if the number does not move it was carried. Two counts. Exact.

    The tool's own rule says so in as many words -- "if code can answer it, Jev is never asked" --
    and this one slipped through: asked 109 times on a real warehouse, its answer load-bearing for
    six models, listed in VERIFICATION.md as weak, and its confidence measured worst exactly where
    it was most sure.

    `candidates` is {relation: [columns]}. Returns {relation: {column: "adds" | "carried"}}.
    A relation that could not be counted is ABSENT, never guessed: an uncounted key is not a
    minimal one.
    """
    work = [(rel, cols) for rel, cols in sorted(candidates.items()) if len(cols or []) > 1]
    out: dict = {}
    if not work:
        return out

    def ask(chunk: list) -> bool:
        parts, labels = [], []
        for rel, cols in chunk:
            full = ", ".join(f'"{c}"' for c in cols)
            # The full set, then the set without each column in turn. One scan per relation per
            # statement, which is the same shape `verify_grains` already pays for.
            parts.append(f"select '{rel}' as r, '' as c, count(distinct ({full})) as d from {rel}")
            labels.append((rel, ""))
            for col in cols:
                rest = ", ".join(f'"{x}"' for x in cols if x != col)
                parts.append(f"select '{rel}' as r, '{col}' as c, "
                             f"count(distinct ({rest})) as d from {rel}")
                labels.append((rel, col))
        got = probe_mod.run_sql(" union all ".join(parts), project_dir, profiles_dir, dbt_bin,
                                limit=len(parts) + 1,
                                caller="assay.practices.verify_minimality", kind="count")
        if got.failed:
            return False
        seen: dict = {}
        for row in got.rows:
            vals = list(row.values())
            try:
                r = str(row.get("r", vals[0]))
                c = str(row.get("c", vals[1]) or "")
                seen[(r, c)] = int(row.get("d", vals[2]))
            except (TypeError, ValueError, IndexError):
                continue
        for rel, cols in chunk:
            full = seen.get((rel, ""))
            if full is None:
                continue
            for col in cols:
                without = seen.get((rel, col))
                if without is None:
                    continue
                # *** EQUALITY IS THE WHOLE TEST. ***
                # Removing the column left the distinct count unchanged, so the remaining columns
                # already determined it. Anything else means it carries identifying power.
                out.setdefault(rel, {})[col] = "carried" if without == full else "adds"
        return True

    def walk(chunk: list) -> None:
        # Halve on failure, exactly as the other counted checks do: one relation that cannot be
        # read must not take the rest of the batch down with it.
        if not chunk or ask(chunk):
            return
        if len(chunk) == 1:
            return
        mid = len(chunk) // 2
        walk(chunk[:mid])
        walk(chunk[mid:])

    present = present_columns(probe_mod, [rel for rel, _c in work], project_dir, profiles_dir,
                              dbt_bin, dialect)
    work = [(rel, cols) for rel, cols in work if _runnable(present, rel, cols)]
    for i in range(0, len(work), batch):
        walk(work[i:i + batch])
    return out
