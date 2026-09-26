"""Findings become fixes: the few changes that resolve the most, each with its diff.

*** NOBODY RULES ON 1,852 FINDINGS. *** (Ryan, 2026-09-25: "I want to know what's important, and
how to fix it.") Most findings are not judgment calls: 208 undocumented models are one kind of
change, 88 models reading raw sources are a handful of staging models, 86 missing key tests are one
batch of tests assay already counted. So every finding is attributed to the change that would
resolve it, and the changes are ranked by how much they resolve per decision a person makes.

A fix carries its change as files: new files whole, edits to existing files as their full new
text. assay never writes them into the project; an agent applies them in a branch, and
`fixmeasure` says, on a patched copy, how many findings each one actually resolves.

Kinds, in the order a finding is attributed (each counts once, against the first that takes it).
The list itself is ranked by findings resolved per decision:
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
              "document", "declare_premise", "pin_the_date", "test_what_could_break",
              "stage_raw_source", "one_edit_in_a_macro", "review")

KIND_TITLE = {
    "make_it_pass": "Make a failing test pass",
    "run_the_tests": "Run the tests that never run",
    "schedule_freshness": "Schedule the freshness check",
    "add_proven_tests": "Add key tests assay counted",
    "document": "Document columns",
    "declare_premise": "Declare what many things rest on",
    "stage_raw_source": "Stage a raw source",
    "one_edit_in_a_macro": "One edit in a macro",
    "pin_the_date": "Pin the date a build represents",
    "test_what_could_break": "Test what could break silently",
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
    parts: list = field(default_factory=list)           # stage: (source uid, staging model)
    count: int = 0                                      # columns, tests or sources it adds
    queued: int = 0                                     # of `findings`, the ones queued
    pieces: list = field(default_factory=list)          # a batch: one line per part

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
                "queued": self.queued, "notes": len(self.findings) - self.queued,
                "pieces": self.pieces,
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
    """One fix per schema file: every model in it with columns to document, drafted into that
    file in one edit. (sunny-data: 207 models, 11 files, 117 in one of them. Per model, the fixes
    were 207 rows to read and 117 full new texts of one file that could not all be applied.)"""
    from . import schemapatch
    by_uid = {e.uid: e for e in entries or []}
    per_file: dict = {}
    for (check, uid), fs in sorted(by_check.items()):
        if check != "column_has_no_description" or uid not in project.models:
            continue
        m = project.models[uid]
        missing = list((fs[0].evidence or {}).get("missing") or [])
        if len(missing) < int((fs[0].evidence or {}).get("missing_total") or 0):
            from .checks.columns import missing_descriptions
            missing = missing_descriptions(project, uid, schema)[1]
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
        yml = _yml_of(project, uid)
        key = yml if yml and _read(root, yml) is not None else str(Path(m.path).parent)
        per_file.setdefault(key, []).append((m, edits, undrafted, fs))
    out = []
    for key, items in sorted(per_file.items()):
        n_cols = sum(len(e) for _m, e, _u, _f in items)
        names = [m.name for m, _e, _u, _f in items]
        fx = Fix("document", key,
                 f"Document {n_cols} column(s) of {names[0]}" if len(items) == 1 else
                 f"Document {n_cols} column(s) of {len(items)} models in {key}")
        fx.count = n_cols
        fx.models = names
        text = _read(root, key) if key.endswith((".yml", ".yaml")) else None
        undrafted_all = []
        for m, edits, undrafted, fs in items:
            if text is not None:
                new, refused = schemapatch.apply(text, m.name, edits)
                if refused:
                    fx.refused += refused
                    continue
                text = new
            else:
                path = str(Path(m.path).with_name(f"_{m.name}.yml"))
                fx.files[path] = schemapatch.new_entry(m.name, edits)
                fx.new_files.append(path)
            if not undrafted:
                fx.findings += [f.id for f in fs]
            undrafted_all += [f"{m.name}.{c}" for c in undrafted]
        if text is not None:
            fx.files[key] = text
        if not fx.files:
            continue
        fx.decisions = 1
        fx.how = ("Drafted from what assay already knows: a parent's description where the value "
                  "passes through, else the judged role, what a NULL means, and the expression. "
                  "Read each and correct it; it is a draft, and it becomes the project's once you "
                  "accept it.")
        if undrafted_all:
            fx.how += (f" {len(undrafted_all)} column(s) had nothing to draft from and still need "
                       f"a sentence: {', '.join(undrafted_all[:8])}.")
        fx.recipe = ["dbt parse (the yml is valid)",
                     "assay check --select " + " ".join(names[:40])]
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
        fx.parts = [(src_uid, target)]
        fx.count = 1
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
    return _merge_stages(out, project, root, by_check)


def _merge_stages(fxs: list[Fix], project, root: Path, by_check: dict) -> list[Fix]:
    """Stage fixes that edit the same model become one fix: two fixes each carrying their own
    full new text of one file cannot both be applied, and one decision covers what one edit to
    that file changes. (sunny-data: 80 sources, one mart edited by 11 of them, 33 fixes.)"""
    par = list(range(len(fxs)))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    owner: dict = {}
    for i, fx in enumerate(fxs):
        for path in fx.files:
            if path in fx.new_files:
                continue
            if path in owner:
                par[find(i)] = find(owner[path])
            else:
                owner[path] = i
    comps: dict = {}
    for i in range(len(fxs)):
        comps.setdefault(find(i), []).append(fxs[i])
    out = []
    for group in comps.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        srcs = [project.sources[u] for fx in group for u, _t in fx.parts]
        models = sorted({m for fx in group for m in fx.models})
        fx = Fix("stage_raw_source", "|".join(sorted(u for g in group for u, _t in g.parts)),
                 f"Stage {len(srcs)} raw sources for {len(models)} model(s) that read them raw")
        fx.moves_logic = True
        fx.parts = [p for g in group for p in g.parts]
        fx.count = len(fx.parts)
        fx.models = models
        fx.findings = [x for g in group for x in g.findings]
        fx.decisions = 1
        for g in group:
            for path in g.new_files:
                fx.files[path] = g.files[path]
                fx.new_files.append(path)
        # every reader edited once, with every source it reads raw repointed in the same text
        for path in sorted({p for g in group for p in g.files if p not in g.new_files}):
            text = _read(root, path) or ""
            for (u, target) in fx.parts:
                s = project.sources[u]
                text = _source_call(s.source_name, s.name).sub(f"{{{{ ref('{target}') }}}}", text)
            fx.files[path] = text
        # a model reading several of these sources raw is resolved once its text reads none
        ours = {u for u, _t in fx.parts}
        for uid, m in project.models.items():
            raw = [p for p in m.parents if p in project.sources]
            text = fx.files.get(m.path)
            if not raw or text is None or not set(raw) <= ours or any(
                    _source_call(project.sources[u].source_name,
                                 project.sources[u].name).search(text) for u in raw):
                continue
            for f in by_check.get(("reads_raw_source_outside_staging", uid), []):
                if f.id not in fx.findings:
                    fx.findings.append(f.id)
        fx.how = ("Each source gets a pass-through staging model (an existing one where it "
                  "already has one), and every model below that reads one of them raw points at "
                  "it instead: " + "; ".join(f"{project.sources[u].source_name}."
                                             f"{project.sources[u].name} -> `{t}`"
                                             for u, t in fx.parts[:20])
                  + ". It changes no rows: the readers see the same columns. They are one fix "
                    "because they edit the same models.")
        left = [h for g in group for h in g.how.split(" Left as it is: ")[1:]]
        if left:
            fx.how += " Left as it is: " + " ".join(left)
        fx.recipe = ["dbt build --select " + " ".join([t for _u, t in fx.parts] + models[:40]),
                     group[0].recipe[1]]
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


_CLOCK_WORDS = re.compile(r"(?i)\b(current_date|current_timestamp|now\(\)|getdate\(\)|"
                          r"sysdate\b|localtimestamp)(\(\))?")
AS_OF_MACRO = """{% macro as_of(kind='date') -%}
  {#- The date a build represents: the `as_of` var when a build, a test or a golden names one,
      today otherwise. Written by assay's pin_the_date fix. -#}
  {%- if var('as_of', none) is not none -%}
    cast('{{ var("as_of") }}' as {{ kind }})
  {%- elif kind == 'date' -%}
    current_date
  {%- else -%}
    current_timestamp
  {%- endif -%}
{%- endmacro %}
"""




def _clock_to_as_of(sql: str, macro: str = "") -> str:
    """Each clock call replaced: by the project's own pinning macro when it has one, else by
    `as_of()`."""
    def rep(mt):
        date = mt.group(1).lower() == "current_date"
        if macro:
            return "{{ " + macro + "() }}" if date else "cast({{ " + macro + "() }} as timestamp)"
        return "{{ as_of() }}" if date else "{{ as_of('timestamp') }}"
    return _CLOCK_WORDS.sub(rep, sql)


def _outside_jinja(text: str, fn) -> str:
    """Apply `fn` to the SQL that runs, never inside a Jinja tag (a call replaced inside `{{ }}`
    would nest one tag in another and stop the model compiling) or a comment (prose stays)."""
    from .checks.patterns import NOT_CODE
    parts = NOT_CODE.split(text)
    return "".join(p if NOT_CODE.fullmatch(p) else fn(p) for p in parts)


def _pin_the_date(project, findings, root: Path) -> Fix | None:
    """Every model reading the clock, in ONE change: an `as_of()` macro and each call replaced.
    Nothing moves until a build names a date; then every model agrees on which day it is."""
    fs = [f for f in findings if f.check == "output_depends_on_the_clock"]
    if not fs:
        return None
    from .checks.patterns import clock_macros
    fx = Fix("pin_the_date", "project", f"Pin the date in {len(fs)} model(s) that read the clock")
    # the project's own convention first: a macro that reads the clock AND a var already pins it
    own = sorted(n for n, pins in clock_macros(project).items() if pins)
    macro = own[0] if own else ""
    have_as_of = any(str(k).endswith(".as_of") for k in (project.raw.get("macros") or {}))
    if not macro and not have_as_of:
        fx.files["macros/as_of.sql"] = AS_OF_MACRO
        fx.new_files.append("macros/as_of.sql")
    left_out = []
    for f in fs:
        m = project.models.get(f.subject)
        text = _read(root, m.path) if m is not None else None
        if text is None or not _CLOCK_WORDS.search(text):
            left_out.append(f.subject_name)
            continue

        fx.files[m.path] = _outside_jinja(text, lambda t: _clock_to_as_of(t, macro))
        fx.models.append(m.name)
        fx.findings.append(f.id)
    if not fx.models:
        return None
    fx.title = f"Pin the date in {len(fx.models)} model(s) that read the clock"
    fx.decisions = 1
    fx.how = ((f"This project already pins its date with `{macro}()`, which reads a var and "
               f"falls back to today; these models read the clock directly instead, and each call "
               f"becomes `{{{{ {macro}() }}}}`. " if macro else
               "One macro, `as_of()`, returns the `as_of` var when a build names one and today "
               "otherwise, and every model that read the clock reads it instead. ")
              + "Nothing changes until a var is passed; then a rebuild of the same data gives the "
                "same rows, and a test or a golden can name its day."
              + (f" Left as it is (the clock is read through a macro that reads no var; make "
                 f"that macro read one): {', '.join(left_out)}." if left_out else ""))
    fx.recipe = ["dbt compile (the models compile with the macro)",
                 "dbt build --vars '{as_of: <a past date>}' twice: the rows are the same"]
    fx.moves_logic = False
    return fx


# the tests that catch what each judged failure mode names (questions/meaning.yml)
_RISK_TEST = {
    "joins_would_fan_out_or_drop": "unique, or relationships to the model it joins",
    "a_value_would_fall_through": "accepted_values on the input it classifies",
    "an_aggregate_would_be_wrong": "a range or a reconciliation test",
    "a_default_would_hide_missing_data": "a test on the share of rows at the default",
}


def _test_what_could_break(project, findings) -> list[Fix]:
    """One fix per schema file: the columns judged able to break silently in the models it
    declares, and the test each needs, because that file is where the tests are written."""
    per: dict = {}
    for f in findings:
        if f.check == "what_would_break_silently" and f.subject in project.models:
            m = project.models[f.subject]
            key = (_yml_of(project, f.subject) if getattr(project, "raw", None) else "") \
                or str(Path(m.path).parent)
            per.setdefault(key, {}).setdefault(f.subject, []).append(f)
    out = []
    for key, by_model in sorted(per.items()):
        lines, ids, names = [], [], []
        for uid, fs in sorted(by_model.items()):
            m = project.models[uid]
            names.append(m.name)
            for f in fs:
                col = str((f.evidence or {}).get("context") or "").split(".")[-1]
                test = _RISK_TEST.get(str((f.evidence or {}).get("answer") or ""), "a test")
                lines.append(f"{m.name}.{col}: {test}" if len(by_model) > 1 else f"{col}: {test}")
                ids.append(f.id)
        fx = Fix("test_what_could_break", key,
                 f"Test {len(ids)} column(s) of {names[0]} that could break silently"
                 if len(names) == 1 else
                 f"Test {len(ids)} column(s) in {len(names)} models of {key} that could break "
                 f"silently")
        fx.findings = ids
        fx.models = names
        fx.count = len(ids)
        fx.decisions = len(names)
        fx.how = ("Each column below was judged able to go wrong without any error, and the test "
                  "named is what would catch it: " + "; ".join(lines[:30])
                  + (f"; and {len(lines) - 30} more" if len(lines) > 30 else "")
                  + ". `assay plan --verify` counts the key and value tests through your dbt and "
                    "writes the ones that pass today.")
        fx.recipe = ["dbt test --select " + " ".join(names[:40]) + " (the new tests pass)"]
        out.append(fx)
    return out


# ------------------------------------------------------------------------ the whole plan


BATCH = {
    "document": lambda n, m: f"Document {n:,} column(s) in {m:,} model(s)",
    "test_what_could_break": lambda n, m: f"Test {n:,} column(s) in {m:,} model(s) that could "
                                          f"break silently",
    "stage_raw_source": lambda n, m: f"Stage {n:,} raw source(s) for the {m:,} model(s) that "
                                     f"read them raw",
}
BATCH_HOW = {
    "document": ("Drafted from what assay already knows: a parent's description where the value "
                 "passes through, else the judged role, what a NULL means, and the expression. "
                 "Read each and correct it; it is a draft, and it becomes the project's once you "
                 "accept it."),
    "test_what_could_break": ("Each column was judged able to go wrong without any error, and the "
                              "test named is what would catch it. `assay plan --verify` counts "
                              "the key and value tests through your dbt and writes the ones that "
                              "pass today."),
    "stage_raw_source": ("Each source gets a pass-through staging model (an existing one where it "
                         "has one), and every model that reads it raw points at it instead. It "
                         "changes no rows: the readers see the same columns."),
}


def project_source_label(uid: str) -> str:
    """`source.pkg.raw.permits` -> `raw.permits`."""
    parts = str(uid).split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else str(uid)


def _batch(fxs: list[Fix], kind: str) -> Fix:
    """*** ONE CARD PER EDIT, NOT PER MODEL. *** (Ryan, on the Fix tab: 207 document cards, 129
    test cards and 80 staging cards, one decision each.) A batch kind is one change: its parts
    touch different files, so one approval applies them all, and the parts are listed inside."""
    if len(fxs) == 1:
        return fxs[0]
    models = sorted({m for f in fxs for m in f.models})
    n = sum(f.count or len(f.findings) for f in fxs)
    fx = Fix(kind, "all", BATCH[kind](n, len(models)))
    fx.count, fx.models, fx.decisions = n, models, 1
    fx.findings = [i for f in fxs for i in f.findings]
    fx.parts = [p for f in fxs for p in f.parts]
    fx.moves_logic = any(f.moves_logic for f in fxs)
    fx.refused = [r for f in fxs for r in f.refused]
    for f in fxs:
        fx.files.update(f.files)
        fx.new_files += f.new_files
    fx.how = BATCH_HOW[kind]

    def piece(f: Fix) -> str:
        if kind == "stage_raw_source":
            srcs = [project_source_label(u) for u, _t in f.parts]
            return (f"{', '.join(srcs[:4])}{' and more' if len(srcs) > 4 else ''} -> "
                    f"{', '.join(f.models[:4])}{' and more' if len(f.models) > 4 else ''}")
        n = f.count or len(f.findings)
        where = f.key if ("/" in f.key or f.key.endswith((".yml", ".yaml"))) else f.models[0]
        return f"{where}: {n:,} column(s) in {len(f.models):,} model(s)"
    fx.pieces = [piece(f) for f in sorted(fxs, key=lambda f: -len(f.findings))]
    left = [f.how.split(" Left as it is: ", 1)[1] for f in fxs if " Left as it is: " in f.how]
    if left:
        fx.how += " Left as it is: " + " ".join(left)
    fx.recipe = {
        "document": ["dbt parse (every yml is valid)",
                     "assay check (the columns are documented)"],
        "test_what_could_break": ["dbt test (the new tests pass)"],
        "stage_raw_source": ["dbt build --select the staging models and their readers",
                             ("row equivalence: each repointed model's rows before and after "
                              "compare equal (count, key set, a hash over sorted rows)")],
    }[kind]
    return fx


def queued_ids(findings, acts: dict | None = None) -> set:
    """The findings the policy puts in front of a person (`queue` or `fail`). `acts` maps a
    finding id to its action, or to (action, why) as `live.open_findings` returns it."""
    from .judged import default_action
    out = set()
    for f in findings:
        fid = f.get("id") if isinstance(f, dict) else f.id
        a = (acts or {}).get(fid)
        if isinstance(a, (tuple, list)):
            a = a[0]
        if a is None:
            a = f.get("action") if isinstance(f, dict) else default_action(f)
        if a in ("queue", "fail"):
            out.add(fid)
    return out


def split(findings, fixes: list, acts: dict | None = None) -> dict:
    """{"fix", "settled", "decide", "notes"}: the open findings' ids, each in exactly one. A
    change in the plan clears it; or a count settled it and a proposal says what to do; or a
    person decides it; or it is a note, which the policy does not queue and which stays in
    Explore. (Ryan: "im not deciding on 1600 cards".) The four add up to the open findings."""
    from .subjects import settled_by_a_count
    fix = {i for fx in fixes if fx.kind != "review" for i in fx.findings}
    queued = queued_ids(findings, acts)
    out: dict = {"fix": set(), "settled": set(), "decide": set(), "notes": set()}
    for f in findings:
        fid = f.get("id") if isinstance(f, dict) else f.id
        k = ("fix" if fid in fix else "notes" if fid not in queued
             else "settled" if settled_by_a_count(f) else "decide")
        out[k].add(fid)
    return out



def build(project, findings, *, entries=None, digests=None, schema=None, store=None,
          led=None, groups=None, root: Path | None = None, acts: dict | None = None) -> list[Fix]:
    """The ranked changes. `acts` is {finding id: action} from the project's policy (`fail`,
    `queue`, `annotate`); without it, assay's default by severity."""
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
    # every test that never runs is one change to the job, whichever check noticed it
    never = [f for f in findings if f.check in ("test_declared_but_never_run",
                                                "test_skipped_rather_than_passed",
                                                "test_never_ran_is_a_gap_or_a_leftover")]
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
    pin = _pin_the_date(project, findings, root)
    if pin:
        fixes.append(pin)
    fixes += _test_what_could_break(project, findings)
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

    # everything a count settled and no change above takes: one proposal per check, naming the
    # models it is about, so the list is the kinds of change to read, not one row per model. A
    # judgment call is not a proposal: it is decided under Decide, once, and never here as well.
    from .subjects import settled_by_a_count
    queued = queued_ids(findings, acts)
    taken = {fid for fx in fixes for fid in fx.findings}
    # a note is never a proposal of its own: it stays inside a change that clears it, or in
    # Explore (Ryan: 2,202 notes are not 2,202 problems)
    taken |= {f.id for f in findings if not settled_by_a_count(f) or f.id not in queued}
    per_check: dict = {}
    for (check, _subj), fs in sorted(by_check.items()):
        rest = [f for f in fs if f.id not in taken]
        if rest:
            per_check.setdefault(check, []).extend(rest)
    from .titles import title as _title
    for check, rest in sorted(per_check.items()):
        shape, how = SHAPES.get(check, ("", ""))
        models = sorted({f.subject_name for f in rest if f.subject_name})
        fx = Fix("review", check, _title(check)
                 + (f": {len(models)} model(s)" if len(models) > 1 else
                    f": {models[0]}" if models else ""))
        fx.findings = [f.id for f in rest]
        fx.models = models
        fx.decisions = max(1, len(models))
        fx.how = ((f"{shape.capitalize()}. " if shape else "")
                  + (how or "No fix shape is recorded for this check; it needs reading."))
        fixes.append(fx)

    # each finding counts once, against the first fix in order
    seen: set = set()
    for fx in sorted(fixes, key=lambda x: KIND_ORDER.index(x.kind)):
        fx.findings = [x for x in fx.findings if x not in seen]
        seen |= set(fx.findings)
    # a change that clears nothing open is not on the list (Ryan: "structure 47" with nothing
    # attached); the batch kinds are one card each
    fixes = [fx for fx in fixes if fx.findings]
    for kind in BATCH:
        mine = [fx for fx in fixes if fx.kind == kind]
        if len(mine) > 1:
            fixes = [fx for fx in fixes if fx.kind != kind] + [_batch(mine, kind)]
    for fx in fixes:
        fx.queued = sum(1 for i in fx.findings if i in queued)

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
    """Most findings resolved per decision first, then most resolved, then customer-facing and
    live harm, then by layer. (Ryan, on the Fix tab: single failing tests at ~1 each sat on top
    while the fixes resolving hundreds were far down. The priority's reasons stay on each fix.)"""
    tiers = {"customer-facing": 0, "happening now": 1, "wide reach": 2, "the rest": 3}

    def key(fx: Fix):
        n = fx.measured if fx.measured is not None else len(fx.findings)
        return (-(n / max(1, fx.decisions)), -n, tiers.get(fx.tier, 3), fx.layer, fx.id)
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
