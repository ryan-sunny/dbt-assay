"""Findings become fixes: the few changes that resolve the most, each with its diff.

*** NOBODY RULES ON 1,852 FINDINGS. *** (Ryan, 2026-09-25: "I want to know what's important, and
how to fix it.") Most findings are not judgment calls: 208 undocumented models are one kind of
change, 88 models reading raw sources are a handful of staging models, 86 missing key tests are one
batch of tests assay already counted. So every finding is attributed to the change that would
resolve it, and the changes are ranked by how much they resolve per decision a person makes.

A fix carries its change as files: new files whole, edits to existing files as their full new
text. assay never writes them into the project; an agent applies them in a branch, and
`fixmeasure` says, on a patched copy, how many findings each one actually resolves.

Kinds, in the order they are done (free first, then by layer):
  make_it_pass        a failing dbt test                         no diff; the test names the rows
  run_the_tests       tests declared and never run               no diff; the job's selector
  schedule_freshness  freshness declared and never checked        no diff; the job
  add_proven_tests    no key test, and the key is counted unique  singular tests, one batch
  document            columns and models with no description      drafts in the model's own yml
  declare_premise     a premise many things rest on, unasserted   a test in the yml
  stage_raw_source    models reading a raw source directly        a pass-through staging model
                                                                  and the readers repointed
  one_edit_in_a_macro one construct written by a project macro    proposal: the macro, at a line
  review              everything else, by check and model         proposal: the fix shape
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

KIND_ORDER = ("make_it_pass", "run_the_tests", "schedule_freshness", "add_proven_tests",
              "document", "declare_premise", "stage_raw_source", "one_edit_in_a_macro", "review")

KIND_TITLE = {
    "make_it_pass": "Make a failing test pass",
    "run_the_tests": "Run the tests that never run",
    "schedule_freshness": "Schedule the freshness check",
    "add_proven_tests": "Add key tests assay counted",
    "document": "Document columns",
    "declare_premise": "Declare what many things rest on",
    "stage_raw_source": "Stage a raw source",
    "one_edit_in_a_macro": "One edit in a macro",
    "review": "Review",
}

LAYER_ORDER = ("staging", "base", "intermediate", "int", "dim", "fct", "fact", "marts", "mart")


@dataclass
class Fix:
    kind: str
    key: str
    title: str
    findings: list = field(default_factory=list)        # finding ids it resolves (estimated)
    checks: dict = field(default_factory=dict)          # check -> count
    models: list = field(default_factory=list)          # model names it touches
    decisions: int = 1                                  # what a person has to decide
    files: dict = field(default_factory=dict)           # path -> full new text
    new_files: list = field(default_factory=list)       # which of `files` are new
    recipe: list = field(default_factory=list)          # how it is verified
    how: str = ""
    why: list = field(default_factory=list)             # the priority's reasons
    tier: str = ""
    layer: int = 99
    moves_logic: bool = False                           # row equivalence applies
    measured: int | None = None                         # resolved on a patched copy
    measured_note: str = ""
    refused: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.kind}|{self.key}".encode()).hexdigest()[:10]

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "kind_title": KIND_TITLE[self.kind],
                "kind_rank": KIND_ORDER.index(self.kind),
                "key": self.key, "title": self.title, "findings": self.findings,
                "resolves": len(self.findings), "measured": self.measured,
                "measured_note": self.measured_note, "checks": self.checks,
                "models": self.models[:40], "decisions": self.decisions,
                "files": sorted(self.files), "new_files": self.new_files,
                "recipe": self.recipe, "how": self.how, "why": self.why, "tier": self.tier,
                "moves_logic": self.moves_logic, "refused": self.refused,
                "effect": self.effect()}

    def effect(self) -> str:
        """What the change does, for a fix that no open finding is attached to (the layering
        rules come from dbt-project-evaluator; without it a staging fix still stands)."""
        n = len(self.models)
        if self.kind == "stage_raw_source":
            return f"{n} model(s) stop reading it raw"
        if self.kind == "add_proven_tests":
            return f"{n} key test(s)"
        if self.kind == "declare_premise":
            return "a premise becomes a test"
        if self.kind == "document":
            return "columns documented"
        return ""


def _layer(m) -> int:
    lay = str(getattr(m, "layer", "") or "").lower()
    name = str(getattr(m, "name", "") or "").lower()
    for i, k in enumerate(LAYER_ORDER):
        if lay.startswith(k) or name.startswith(k + "_"):
            return i
    return len(LAYER_ORDER)


def _is_staging(m) -> bool:
    lay = str(getattr(m, "layer", "") or "").lower()
    return lay in ("staging", "base") or m.name.startswith(("stg_", "base_"))


def _read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text()
    except (OSError, UnicodeDecodeError):
        return None


# ------------------------------------------------------------------- the kinds with a diff


def _proven_keys(project, entries, store) -> dict:
    """{model uid: [columns]} for models with no key test whose grain was COUNTED unique and
    never null in the data (a latest, unsampled observation), or proven by Lean."""
    from . import practices, probe
    obs = probe.read(store) if store is not None else {}
    out = {}
    for name, cols, _src, _marts, not_emitted in practices.primary_key_patches(project, entries):
        if not cols or not_emitted:
            continue
        uid = next((u for u, m in project.models.items() if m.name == name), None)
        if uid is None:
            continue
        key = ",".join(str(c) for c in cols)
        rel = next((v for r, v in obs.items() if r == name.lower() or r.endswith("." + name.lower())),
                   {})
        o = rel.get(key)
        if o is None or o.sampled or o.status != "unique" or not o.row_count:
            continue
        out[uid] = (list(cols), o.row_count, o.distinct_ct)
    return out


def _add_proven_tests(project, entries, store, by_check) -> Fix | None:
    from . import __version__
    from .patch import render
    keys = _proven_keys(project, entries, store)
    if not keys:
        return None
    fx = Fix("add_proven_tests", "batch", f"Add {len(keys)} key test(s) assay counted unique")
    tests_dir = "tests/assay"
    for uid, (cols, n, d) in sorted(keys.items(), key=lambda kv: project.models[kv[0]].name):
        name = project.models[uid].name
        fx.files[f"{tests_dir}/unique_{name}.sql"] = render(name, cols, n, d, __version__)
        fx.new_files.append(f"{tests_dir}/unique_{name}.sql")
        fx.models.append(name)
        for f in by_check.get(("no_primary_key_test", uid), []):
            fx.findings.append(f.id)
    fx.decisions = 1
    fx.how = ("One singular test per model, each asserting the key assay counted unique and "
              "never null on the latest full count. Singular tests collide with no schema entry "
              "and can be deleted by deleting the file.")
    fx.recipe = ["dbt build --select " + " ".join(sorted(fx.models)[:50]) + " (the tests pass)"]
    return fx


def _draft(project, entries_by_uid, digests, schema, uid: str, col: str) -> str:
    """A description from what assay already knows, never a guess: the parent's description where
    the value passes through, else the judged role and NULL meaning and the expression."""
    from .provenance import classify
    m = project.models[uid]
    try:
        prov = {k.lower(): v for k, v in classify(uid, project, digests, schema).items()} \
            if schema is not None else {}
    except Exception:                                            # noqa: BLE001
        prov = {}
    p = prov.get(col.lower())
    if p is not None and p.kind in ("carried", "from_source") and p.origin:
        up = _described_upstream(project, p.origin, col)
        if up:
            return up
    e = entries_by_uid.get(uid)
    ce = next((c for c in (getattr(e, "columns", None) or []) if c.name.lower() == col.lower()),
              None)
    bits = []
    if ce is not None and ce.role is not None and ce.role.value:
        bits.append(str(ce.role.value).replace("_", " ").capitalize() + ".")
    if ce is not None and ce.null_meaning is not None and ce.null_meaning.value:
        bits.append(f"NULL means {str(ce.null_meaning.value).replace('_', ' ')}.")
    if not bits:
        # lineage alone is not a meaning: a sentence that only says where a value came from reads
        # as documentation and tells the next person nothing, so there is no draft
        return ""
    d = digests.get(uid)
    expr = (d.output_exprs or {}).get(col) if d is not None and d.ok else None
    if p is not None and p.kind in ("computed", "aggregated", "defaulted", "ranked") and expr:
        bits.append(f"Computed as `{expr[:120]}`.")
    elif p is not None and p.origin:
        bits.append(f"From `{p.origin.split('.')[-1]}`.")
    del m
    return " ".join(bits)


def _described_upstream(project, origin: str, col: str) -> str:
    o = origin.lower().split(".")[-1]
    for m in project.models.values():
        if m.name.lower() == o:
            for k, v in (m.columns or {}).items():
                if str(k).lower() == col.lower():
                    desc = (v or {}).get("description") if isinstance(v, dict) else None
                    if desc:
                        return str(desc).strip()
    for s in project.sources.values():
        if s.name.lower() == o:
            for k, v in (s.columns or {}).items():
                if str(k).lower() == col.lower():
                    desc = (v or {}).get("description") if isinstance(v, dict) else None
                    if desc:
                        return str(desc).strip()
    return ""


def _yml_of(project, uid: str) -> str:
    node = (project.raw.get("nodes", {}) or {}).get(uid) or {}
    pp = str(node.get("patch_path") or "")
    return pp.split("://", 1)[1] if "://" in pp else pp


def _document(project, entries, digests, schema, by_check, root: Path) -> list[Fix]:
    from . import schemapatch
    by_uid = {e.uid: e for e in entries or []}
    out = []
    for (check, uid), fs in sorted(by_check.items()):
        if check != "column_has_no_description" or uid not in project.models:
            continue
        m = project.models[uid]
        missing = list((fs[0].evidence or {}).get("missing") or [])
        if not missing:
            continue
        edits, undrafted = [], []
        for c in missing:
            text = _draft(project, by_uid, digests, schema, uid, c)
            if text:
                edits.append(schemapatch.Edit(column=c, description=text))
            else:
                undrafted.append(c)
        if not edits:
            continue
        fx = Fix("document", uid, f"Document {len(edits)} column(s) of {m.name}")
        fx.models = [m.name]
        fx.findings = [f.id for f in fs] if not undrafted else []
        yml = _yml_of(project, uid)
        text = _read(root, yml) if yml else None
        if text is not None:
            new, refused = schemapatch.apply(text, m.name, edits)
            if refused:
                fx.refused += refused
            else:
                fx.files[yml] = new
        else:
            path = str(Path(m.path).with_name(f"_{m.name}.yml"))
            fx.files[path] = schemapatch.new_entry(m.name, edits)
            fx.new_files.append(path)
        fx.how = ("Drafted from what assay already knows: a parent's description where the value "
                  "passes through, else the judged role, what a NULL means, and the expression. "
                  "Read each and correct it; it is a draft, and it becomes the project's once you "
                  "accept it.")
        if undrafted:
            fx.how += (f" {len(undrafted)} column(s) had nothing to draft from and still need a "
                       f"sentence: {', '.join(undrafted[:8])}.")
        fx.recipe = ["dbt parse (the yml is valid)", f"assay check --select {m.name}"]
        out.append(fx)
    return out


def _source_call(source_name: str, table: str) -> re.Pattern:
    q = "['\"]"
    return re.compile(r"\{\{\s*source\(\s*" + q + re.escape(source_name) + q + r"\s*,\s*" + q
                      + re.escape(table) + q + r"\s*\)\s*\}\}")


def _passes_through(d) -> bool:
    """Every output column is a source column, unchanged, and nothing filters, joins or groups:
    readers repointed to it see exactly what they read before."""
    if d is None or not d.ok or not d.output_exprs:
        return False
    if d.joins or d.group_by or d.distinct or d.predicates:
        return False
    return all(str(e).split(".")[-1].strip('"').lower() == str(c).lower()
               for c, e in d.output_exprs.items())


def _stage(project, by_check, root: Path, digests: dict | None = None) -> list[Fix]:
    """One fix per raw source read directly outside staging: an existing pass-through staging
    model or a new one, and every such reader repointed to it."""
    readers: dict = {}
    for uid, m in project.models.items():
        if getattr(m, "is_installed_package", False) or _is_staging(m):
            continue
        for p in m.parents:
            if p in project.sources:
                readers.setdefault(p, []).append(uid)
    out = []
    for src_uid, uids in sorted(readers.items()):
        s = project.sources[src_uid]
        stg = f"stg_{s.source_name}__{s.name}"
        existing = next((u for u, m in project.models.items()
                         if m.parents == [src_uid] and _is_staging(m)
                         and _passes_through(digests.get(u))), None)
        fx = Fix("stage_raw_source", src_uid, f"Stage {s.source_name}.{s.name} for "
                                              f"{len(uids)} model(s) that read it raw")
        fx.moves_logic = True
        target = project.models[existing].name if existing else stg
        if not existing:
            path = f"models/staging/{s.source_name}/{stg}.sql"
            fx.files[path] = (f"-- generated by assay: a pass-through staging model, so readers "
                              f"stop reading the raw source.\nselect * from "
                              f"{{{{ source('{s.source_name}', '{s.name}') }}}}\n")
            fx.new_files.append(path)
        pat = _source_call(s.source_name, s.name)
        left_out = []
        for uid in uids:
            m = project.models[uid]
            text = _read(root, m.path)
            if text is None or not pat.search(text):
                # built by Jinja (a loop, a macro): no text substitution can repoint it safely
                left_out.append(m.name)
                continue
            fx.files[m.path] = pat.sub(f"{{{{ ref('{target}') }}}}", text)
            fx.models.append(m.name)
            for f in by_check.get(("reads_raw_source_outside_staging", uid), []):
                # resolved only when this was the model's only raw source
                raw = [p for p in m.parents if p in project.sources]
                if raw == [src_uid]:
                    fx.findings.append(f.id)
        for f in by_check.get(("source_read_directly_by_many_models", src_uid), []):
            fx.findings.append(f.id)
        fx.decisions = 1
        fx.how = ((f"Readers point at `{target}`, the existing pass-through staging model. "
                   if existing else
                   f"A new pass-through model `{stg}` reads the source once, and the readers "
                   f"point at it. ")
                  + "It changes no rows: the readers see the same columns. Cleaning (renames, "
                    "types, filters) can then be added in one place."
                  + (f" Left as it is: {', '.join(left_out)}, which call the source through "
                     f"Jinja (a loop or a macro), so the call cannot be repointed by text."
                     if left_out else ""))
        fx.recipe = ["dbt build --select " + " ".join([target] + fx.models[:40]),
                     ("row equivalence: each repointed model's rows before and after compare "
                      "equal (count, key set, a hash over sorted rows)")]
        if fx.models:
            out.append(fx)
    return out


def _declare_premises(project, led, by_check, root: Path, top: int = 25) -> list[Fix]:
    """The premises most things rest on, where nothing asserts them yet: a unique / not_null test
    in the model's yml turns an observation into something dbt checks every build."""
    from . import schemapatch
    if led is None:
        return []
    uses: dict = {}
    for u in led.uses:
        uses[u.premise_id] = uses.get(u.premise_id, 0) + 1
    out = []
    ranked = sorted(led.premises.values(), key=lambda p: (-uses.get(p.id, 0), p.id))
    for p in ranked[:top * 3]:
        if uses.get(p.id, 0) < 3 or p.status != "holding" or p.prop not in ("unique", "not_null"):
            continue
        if any(ev.kind in ("declared", "config") for ev in p.evidence):
            continue                       # already asserted
        if p.relation not in project.models or len(p.columns) != 1:
            continue
        m = project.models[p.relation]
        col = p.columns[0]
        fx = Fix("declare_premise", p.id,
                 f"Declare {p.statement().replace('`', '')}: {uses[p.id]} things rest on it")
        fx.models = [m.name]
        yml = _yml_of(project, p.relation)
        text = _read(root, yml) if yml else None
        tests = ["unique", "not_null"] if p.prop == "unique" else ["not_null"]
        edit = schemapatch.Edit(column=col, tests=tests)
        if text is not None:
            new, refused = schemapatch.apply(text, m.name, [edit])
            if refused:
                fx.refused += refused
                continue
            fx.files[yml] = new
        else:
            path = str(Path(m.path).with_name(f"_{m.name}.yml"))
            fx.files[path] = schemapatch.new_entry(m.name, [edit])
            fx.new_files.append(path)
        fx.decisions = 1
        fx.how = (f"It holds today by observation, and {uses[p.id]} findings, grains and proofs "
                  f"rest on it. As a test, dbt checks it every build, and a proof resting on it "
                  f"stops being conditional.")
        fx.recipe = [f"dbt build --select {m.name} (the test passes)"]
        out.append(fx)
        if len(out) >= top:
            break
    return out


# ------------------------------------------------------------------------ the whole plan


def build(project, findings, *, entries=None, digests=None, schema=None, store=None,
          led=None, groups=None, root: Path | None = None) -> list[Fix]:
    from . import priority
    from .plan import SHAPES
    root = Path(root or getattr(project, "project_root", ".") or ".")
    by_check: dict = {}
    for f in findings:
        by_check.setdefault((f.check, f.subject), []).append(f)
    fixes: list[Fix] = []

    for f in findings:
        if f.check == "test_is_failing":
            fx = Fix("make_it_pass", f.id, f.summary.replace("`", ""))
            fx.findings, fx.models = [f.id], [f.subject_name]
            fx.how = SHAPES["test_is_failing"][1]
            fx.recipe = [f"dbt test --select {f.evidence.get('test', '')}"]
            fixes.append(fx)
    never = [f for f in findings if f.check in ("test_declared_but_never_run",
                                                "test_skipped_rather_than_passed")]
    if never:
        fx = Fix("run_the_tests", "build", f"Run {len(never)} test finding(s) the build never "
                                           f"runs")
        fx.findings = [f.id for f in never]
        fx.models = sorted({f.subject_name for f in never})
        fx.decisions = 1
        fx.how = SHAPES.get("test_declared_but_never_run", ("", ""))[1]
        fx.recipe = ["the scheduled job's `dbt build` / `dbt test` selector includes them"]
        fixes.append(fx)
    fresh = [f for f in findings if f.check == "source_freshness_not_run"]
    if fresh:
        fx = Fix("schedule_freshness", "build", "Schedule `dbt source freshness`")
        fx.findings = [f.id for f in fresh]
        fx.decisions = 1
        fx.how = SHAPES["source_freshness_not_run"][1]
        fx.recipe = ["`dbt source freshness` runs in the scheduled job"]
        fixes.append(fx)
    pt = _add_proven_tests(project, entries or [], store, by_check)
    if pt:
        fixes.append(pt)
    fixes += _document(project, entries, digests or {}, schema, by_check, root)
    fixes += _declare_premises(project, led, by_check, root)
    fixes += _stage(project, by_check, root, digests or {})
    for g in groups or []:
        info = g.as_dict()
        if not info.get("macro_at"):
            continue
        fx = Fix("one_edit_in_a_macro", g.key,
                 f"One edit in {info['macro_at']} ({info['size']} models, {g.check})")
        fx.findings = list(info["findings"])
        fx.models = list(info["models"])
        fx.how = (f"The same construct is written in {info['size']} models by the "
                  f"`{info['macro']}` macro. Fix it there, once. "
                  + SHAPES.get(g.check, ("", ""))[1])
        fx.recipe = [f"assay check (the {len(fx.findings)} finding(s) are gone)"]
        fixes.append(fx)

    # everything not yet in a fix: one proposal per (check, model)
    taken = {fid for fx in fixes for fid in fx.findings}
    for (check, subj), fs in sorted(by_check.items()):
        rest = [f for f in fs if f.id not in taken]
        if not rest:
            continue
        shape, how = SHAPES.get(check, ("", ""))
        from .titles import title as _title
        who = rest[0].subject_name or subj
        fx = Fix("review", f"{check}|{subj}",
                 (f"{who}: " if who else "") + _title(check)
                 + (f", {shape}" if shape else ""))
        fx.findings = [f.id for f in rest]
        fx.models = [rest[0].subject_name] if rest[0].subject_name else []
        fx.decisions = len(rest)
        fx.how = how or "No fix shape is recorded for this check; it needs reading."
        fixes.append(fx)

    # each finding counts once, against the first fix in order
    seen: set = set()
    for fx in sorted(fixes, key=lambda x: KIND_ORDER.index(x.kind)):
        fx.findings = [x for x in fx.findings if x not in seen]
        seen |= set(fx.findings)
    fixes = [fx for fx in fixes if fx.findings or fx.files]

    ctx = priority.Context.of(project, led)
    by_id = {f.id: f for f in findings}
    for fx in fixes:
        members = [by_id[x] for x in fx.findings if x in by_id]
        pr = priority.of_many(members, ctx)
        fx.why, fx.tier = pr["why"], pr["tier"]
        fx.checks = {}
        for f in members:
            fx.checks[f.check] = fx.checks.get(f.check, 0) + 1
        ms = [project.models[u] for u in project.models
              if project.models[u].name in set(fx.models)]
        fx.layer = min((_layer(m) for m in ms), default=len(LAYER_ORDER))
    return rank(fixes)


def rank(fixes: list[Fix]) -> list[Fix]:
    """Free first (no decision, or one), then customer-facing and live harm, then by layer, then
    findings resolved per decision."""
    tiers = {"customer-facing": 0, "happening now": 1, "wide reach": 2, "the rest": 3}

    def key(fx: Fix):
        n = fx.measured if fx.measured is not None else len(fx.findings)
        early = fx.kind in ("make_it_pass", "run_the_tests", "schedule_freshness")
        return (0 if early else 1, tiers.get(fx.tier, 3), fx.layer,
                -(n / max(1, fx.decisions)), -n, fx.id)
    return sorted(fixes, key=key)


# ---------------------------------------------------------------- what people decided about them

DDL = """
create table if not exists fix_decisions (
    fix_id      varchar,
    kind        varchar,
    fix_key     varchar,
    title       varchar,
    status      varchar,      -- approved | deferred | rejected | applied | verified
    note        varchar,
    decided_by  varchar,
    decided_at  timestamp,
    detail      varchar       -- what verify found, as JSON
);
"""
STATUSES = ("approved", "deferred", "rejected", "applied", "verified")


def record(store, fix_id: str, status: str, *, kind: str = "", key: str = "", title: str = "",
           note: str = "", by: str = "", detail: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"a fix is {', '.join(STATUSES)}, not {status!r}")
    store.con.execute(DDL)
    store.con.execute("insert into fix_decisions values (?, ?, ?, ?, ?, ?, ?, now(), ?)",
                      [fix_id, kind, key, title, status, note, by, detail])


def statuses(store) -> dict:
    """{fix_id: {status, note, by, at}}: the latest per fix."""
    if store is None:
        return {}
    try:
        store.con.execute(DDL)
        rows = store.con.execute("""
            select fix_id, status, note, decided_by, decided_at from (
                select *, row_number() over (partition by fix_id order by decided_at desc) rn
                from fix_decisions) where rn = 1""").fetchall()
    except Exception:                                            # noqa: BLE001
        return {}
    return {r[0]: {"status": r[1], "note": r[2] or "", "by": r[3] or "",
                   "at": str(r[4])[:16]} for r in rows}


def diff(fx: Fix, root: Path) -> str:
    """The fix as a unified diff against the project as it is on disk."""
    import difflib
    out = []
    for path in sorted(fx.files):
        old = _read(root, path)
        a = (old or "").splitlines(True)
        b = fx.files[path].splitlines(True)
        out += difflib.unified_diff(a, b, f"a/{path}" if old is not None else "/dev/null",
                                    f"b/{path}")
    return "".join(out)


def applied(fx: Fix, root: Path) -> list[str]:
    """The files of the fix whose content on disk is not yet what the fix writes."""
    return [p for p, text in sorted(fx.files.items()) if _read(root, p) != text]


# ------------------------------------------------------ graduation: judged roles become tests

def _args_style(project) -> bool:
    """dbt 1.10 moved a generic test's arguments under `arguments:`; older dbt reads them flat."""
    v = str(getattr(project, "dbt_version", "") or "")
    try:
        major, minor = (int(x) for x in v.split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= (1, 10)


def _test_yaml(project, name: str, args: dict) -> str:
    import json as _j

    def fmt(v):
        if isinstance(v, list):
            return "[" + ", ".join(fmt(x) for x in v) + "]"
        return _j.dumps(v) if isinstance(v, str) else str(v)
    inner = ", ".join(f"{k}: {fmt(v)}" for k, v in args.items())
    return (f"{{{name}: {{arguments: {{{inner}}}}}}}" if _args_style(project)
            else f"{{{name}: {{{inner}}}}}")


def role_test_candidates(project, entries) -> list[dict]:
    """What the judged column roles say a test should assert, before anything is counted:
    a foreign key the relationships test to the model whose key it is, a status flag or a
    dimension the accepted_values test of its values. Only judged roles a person has not
    contradicted, on this project's own models."""
    keyed: dict = {}
    for e in entries or []:
        g = e.grain.value if e.grain is not None else None
        if isinstance(g, list) and len(g) == 1:
            keyed.setdefault(str(g[0]).lower(), []).append(e)
    out = []
    for e in entries or []:
        m = project.models.get(e.uid)
        if m is None or getattr(m, "is_installed_package", False):
            continue
        tested = {(t.column or "").lower() for t in project.tests if t.tests_model == e.uid}
        for c in e.columns or []:
            role = str(c.role.value) if c.role is not None and c.role.value else ""
            col = c.name.lower()
            if role == "foreign_key" and col in keyed:
                parents = [p for p in keyed[col] if p.uid != e.uid and p.uid in m.parents]
                if len(parents) == 1:
                    out.append({"kind": "relationships", "model": e.uid, "column": c.name,
                                "to": parents[0].name, "field": c.name})
            elif role in ("status_flag", "dimension") and col not in tested:
                out.append({"kind": "accepted_values", "model": e.uid, "column": c.name})
    return out


def prove_role_tests(cands: list[dict], project, schema, probe_mod, project_dir: str,
                     profiles_dir, dbt_bin: str, max_values: int = 20) -> list[dict]:
    """Count each candidate through the project's dbt; keep only the ones that would pass."""
    if not cands:
        return []
    rel_of = dict(getattr(schema, "relation", None) or {})

    def rel(uid_or_name: str) -> str:
        uid = uid_or_name if uid_or_name in project.models else next(
            (u for u, m in project.models.items() if m.name == uid_or_name), uid_or_name)
        return (rel_of.get(uid) or project.models[uid].name).replace('"', "")
    stmts, kept = [], []
    for c in cands:
        child = rel(c["model"])
        if c["kind"] == "relationships":
            parent = rel(c["to"])
            sql = (f"select count(*) as orphans from {child} c left join {parent} p "
                   f"on c.{c['column']} = p.{c['field']} "
                   f"where c.{c['column']} is not null and p.{c['field']} is null")
            stmts.append(probe_mod.Statement(sql, caller="assay.graduate", kind="count", limit=1,
                                             relation=child, columns=[c["column"]]))
        else:
            sql = (f"select cast({c['column']} as varchar) as v, count(*) as n from {child} "
                   f"where {c['column']} is not null group by 1 order by 1 "
                   f"limit {max_values + 1}")
            stmts.append(probe_mod.Statement(sql, caller="assay.graduate", kind="profile",
                                             limit=max_values + 1, relation=child,
                                             columns=[c["column"]]))
        kept.append(c)
    got = probe_mod.run_many(stmts, project_dir, profiles_dir, dbt_bin,
                             getattr(project, "dialect", "duckdb") or "duckdb")
    out = []
    for c, r in zip(kept, got):
        if r.failed or not r.rows:
            continue
        if c["kind"] == "relationships":
            if int(r.rows[0].get("orphans") or 0) == 0:
                out.append({**c, "proof": "every non-null value has a parent row"})
        else:
            vals = [str(x.get("v")) for x in r.rows if x.get("v") is not None]
            if 1 < len(vals) <= max_values and all(len(v) <= 40 for v in vals):
                out.append({**c, "values": vals,
                            "proof": f"{len(vals)} distinct value(s) today"})
    return out


def graduate(project, proven: list[dict], root: Path) -> list[Fix]:
    """One fix per model: the tests its judged roles justify, proven to pass, in its own yml."""
    from . import schemapatch
    by: dict = {}
    for p in proven:
        by.setdefault(p["model"], []).append(p)
    out = []
    for uid, ps in sorted(by.items()):
        m = project.models[uid]
        edits = []
        for p in ps:
            if p["kind"] == "relationships":
                t = _test_yaml(project, "relationships",
                               {"to": f"ref('{p['to']}')", "field": p["field"]})
            else:
                t = _test_yaml(project, "accepted_values", {"values": p["values"]})
            edits.append(schemapatch.Edit(column=p["column"], tests=[t]))
        fx = Fix("add_proven_tests", f"roles|{uid}",
                 f"Test what {m.name}'s columns were judged to be ({len(edits)} test(s))")
        fx.models = [m.name]
        yml = _yml_of(project, uid)
        text = _read(root, yml) if yml else None
        if text is not None:
            new, refused = schemapatch.apply(text, m.name, edits)
            if refused:
                fx.refused += refused
                continue
            fx.files[yml] = new
        else:
            path = str(Path(m.path).with_name(f"_{m.name}.yml"))
            fx.files[path] = schemapatch.new_entry(m.name, edits)
            fx.new_files.append(path)
        fx.how = ("The judged role of each column, confirmed as a test dbt runs on every build: a "
                  "foreign key's relationships test to the model whose key it is, a status or "
                  "category's accepted values. Each was counted to pass today: "
                  + "; ".join(f"{p['column']}: {p['proof']}" for p in ps)
                  + ". Once it is a test, the next run reads the answer from the project and does "
                    "not ask.")
        fx.recipe = [f"dbt test --select {m.name} (the new tests pass)"]
        out.append(fx)
    return out
