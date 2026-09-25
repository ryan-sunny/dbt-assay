"""Areas rather than findings: the same rule written in several models, and the one that differs.

*** A CLUSTER IS A CALL SITE THAT DID NOT EXIST. *** (25.23a, 25.24)
A question can only ask about what its call site hands it, and every call site was one model, one
column, one edge. "These five models each defend against an empty string: one rule, or five
decisions?" cannot be put to one model. This module builds those call sites, and it builds them
for free: code groups by the shape of what was written, and a judgment is asked only what code
cannot settle.

*** NO CLUSTER IS EVER RULED ON AS A UNIT. ***
`assay-review` names the hazard: a verdict on a model lands on every finding the model has. A
verdict on a cluster would be that hazard multiplied, so there is no such verdict. A cluster answer
lands as one finding on EACH member, ruled one at a time, and the plan collapses them into one edit
the way it does any construct written in several places. Claim pairs follow `same_defect`: the
answer is one edge of a graph, the components are printed, and nothing gates on a pair.

*** CLUSTER ON WHAT WAS WRITTEN, NEVER ON CONFIDENCE. ***
`assay calibration` shows no two confidence bands separating on any family, so a cluster weighted
by a number that has not been shown to measure anything would import noise and look rigorous.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# A predicate that carries no decision anybody wrote: a null check, a bare flag, a column compared
# with another column or with 0/1. `NOT x IS NULL` is in 226 models on the field warehouse, and
# asking whether 226 null checks are "one rule" is paying to be told they are a habit.
_TRIVIAL = re.compile(
    r"^(not )?<col>( is (not )?null)?$"
    r"|^(not )?<col> (=|<>|!=|<|>|<=|>=) (<col>|0|1|true|false)$", re.IGNORECASE)

# A family of filters too generic to have an odd one out: one column compared with one value. On
# the field warehouse it put an empty-string guard and `<> 'Closed'` in one family, which are two
# intents, not one rule and its exception.
_GENERIC_COARSE = re.compile(r"^(not )?<col> (=|<>|!=|<|>|<=|>=) ('<v>'|0)$", re.IGNORECASE)

# *** BELOW THIS A CLUSTER ANSWER IS NOT A FINDING. *** The same floor `read` holds a suggested
# dismissal to, for the same reason: under 0.5 the model is saying it cannot tell, and read by hand
# on the field warehouse the two "undeclared" answers under it (0.49, 0.35) were both wrong.
FINDING_FLOOR = 0.5

# A cluster needs this many models before "one rule or a coincidence" is worth asking.
MIN_MODELS = 3
# How many members a state lists. The count is always given; the list is capped.
MAX_LISTED = 8


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def shapes(sql: str, dialect: str = "duckdb") -> tuple[str, str, str]:
    """(fine, coarse, first column) for one predicate, or ("", "", "") when it will not parse.

    `fine` masks every column, so `TRIM(address) <> ''` and `TRIM(c.county) <> ''` are one shape.
    `coarse` also masks every literal, so two hand lists of different codes are one coarse shape
    and differ in their fine one -- which is exactly how an odd one out is found.
    """
    try:
        node = sqlglot.parse_one(sql, read=dialect or None)
    except Exception:                                            # noqa: BLE001
        return "", "", ""
    cols = [c.name for c in node.find_all(exp.Column)]
    fine = node.copy()
    for c in list(fine.find_all(exp.Column)):
        c.replace(exp.column("__col__"))
    coarse = fine.copy()
    for lit in list(coarse.find_all(exp.Literal)):
        lit.replace(exp.Literal.string("<v>") if lit.is_string else exp.Literal.number(0))
    # A comment is not part of what a filter does: `/* arms-length proxy */` made one model of five
    # look different on the field warehouse.
    f = fine.sql(dialect=dialect or None, comments=False).replace("__col__", "<col>")
    c = coarse.sql(dialect=dialect or None, comments=False).replace("__col__", "<col>")
    return f, c, (cols[0] if cols else "")


@dataclass
class Member:
    uid: str
    model: str
    layer: str
    predicate: str          # as the compiled SQL writes it
    column: str             # the first column it reads
    fine: str = ""


@dataclass
class Cluster:
    key: str
    shape: str              # the fine shape, columns shown as <col>
    members: list = field(default_factory=list)
    macro: str = ""
    macro_at: str = ""

    @property
    def models(self) -> list[str]:
        return sorted({m.model for m in self.members})

    def as_dict(self) -> dict:
        return {"cluster": self.key, "shape": self.shape, "size": len(self.models),
                "models": self.models, "macro": self.macro, "macro_at": self.macro_at}


def members(project, digests) -> list[Member]:
    """Every filter predicate in a model of this project, with its shapes."""
    dialect = getattr(project, "dialect", None) or "duckdb"
    out = []
    for uid, m in sorted(project.models.items()):
        if m.is_installed_package:
            continue
        d = (digests or {}).get(uid)
        if d is None or not getattr(d, "ok", False):
            continue
        seen = set()
        for p in d.predicates_atomic or []:
            fine, _coarse, col = shapes(p, dialect)
            if not fine or _TRIVIAL.match(fine) or (uid, fine) in seen:
                continue
            seen.add((uid, fine))
            out.append(Member(uid, m.name, m.layer, " ".join(p.split())[:300], col, fine))
    return out


def predicate_clusters(project, digests, min_models: int = MIN_MODELS) -> list[Cluster]:
    """Filters written the same way in `min_models` or more models, largest first."""
    by: dict = {}
    for mem in members(project, digests):
        by.setdefault(mem.fine, []).append(mem)
    out = []
    for fine, ms in by.items():
        if len({m.uid for m in ms}) < min_models:
            continue
        c = Cluster(key=_key("pred", fine), shape=fine, members=ms)
        from .groups import macro_carrying
        name, path, lines = macro_carrying(project, [m.uid for m in ms], fine)
        if name:
            c.macro, c.macro_at = name, f"{path}:{','.join(map(str, lines))}"
        out.append(c)
    return sorted(out, key=lambda c: (-len(c.models), c.shape))


@dataclass
class OddOne:
    key: str
    member: Member
    shared: str             # the fine shape the majority writes
    sharing: list           # the models that write it
    difference: str         # what differs, computed, never judged
    written: list = field(default_factory=list)   # the others' filters, AS WRITTEN, columns shown
    added: list = field(default_factory=list)     # the tokens only this one writes


def odd_ones_out(project, digests) -> list[OddOne]:
    """Members of a family of filters that differ from what most of the family writes.

    Grouped by COARSE shape (literals masked), then the fine shape most of the group shares is the
    rule and every member writing something else is a candidate. Only where the majority is clear
    -- at least two models and more than half -- because "the odd one out" of a split is nobody.
    """
    dialect = getattr(project, "dialect", None) or "duckdb"
    by: dict = {}
    for mem in members(project, digests):
        _f, coarse, _c = shapes(mem.predicate, dialect)
        by.setdefault(coarse, []).append(mem)
    out = []
    for coarse, ms in by.items():
        models = {m.uid for m in ms}
        if len(models) < MIN_MODELS or _GENERIC_COARSE.match(coarse):
            continue
        counts: dict = {}
        for m in ms:
            counts.setdefault(m.fine, set()).add(m.uid)
        top, holders = max(counts.items(), key=lambda kv: (len(kv[1]), kv[0]))
        if len(holders) < 2 or len(holders) * 2 <= len(models):
            continue
        for m in ms:
            if m.fine == top or m.uid in holders:
                continue
            others = sorted((x for x in ms if x.fine == top), key=lambda x: x.model)
            out.append(OddOne(key=_key("odd", coarse, m.uid), member=m, shared=top,
                              sharing=sorted({x.model for x in others}),
                              difference=difference(top, m.fine),
                              written=[x.predicate for x in others[:3]],
                              added=_added(top, m.fine)))
    return sorted(out, key=lambda o: (o.member.model, o.key))


def _added(shared: str, this: str) -> list[str]:
    a, b = re.findall(r"'[^']*'|\S+", shared), re.findall(r"'[^']*'|\S+", this)
    out = []
    for op, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if op in ("replace", "insert"):
            out += [t.strip("'(),") for t in b[j1:j2]]
    return [t for t in out if len(t) >= 2]


def what_it_says(project, uid: str, tokens: list[str]) -> list[str]:
    """Lines of the model's own file and description that mention what only it writes.

    *** THE STATE HAD THE DIFFERENCE AND NOT THE SENTENCE THAT EXPLAINED IT. ***
    Read by hand on the field warehouse: `int_recent_permits` filters 540 days where five sales
    models filter 120, and its first line says "the most recent commercial build-out permit per
    building (last 540d)". The question was answered `undeclared_divergence` at 0.90 because that
    line was not in the state. Only a comment, the description or the code itself is evidence of
    intent -- so the lines that mention the differing value are given, and nothing else.
    """
    m = project.models.get(uid)
    if m is None or not tokens:
        return []
    texts = []
    root = getattr(project, "project_root", None)
    if root and m.path:
        try:
            texts += (root / m.path).read_text(errors="replace").splitlines()
        except OSError:
            pass
    texts += re.split(r"(?<=[.;])\s+", m.description or "")
    out = []
    for t in texts:
        t2 = " ".join(t.split())
        if t2 and any(tok.lower() in t2.lower() for tok in tokens) and t2 not in out:
            out.append(t2[:200])
        if len(out) >= 3:
            break
    return out


def difference(shared: str, this: str) -> str:
    """What `this` writes that `shared` does not, and the reverse, in the words of the SQL."""
    a, b = re.findall(r"'[^']*'|\S+", shared), re.findall(r"'[^']*'|\S+", this)
    add, drop = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if op in ("replace", "delete"):
            drop.append(" ".join(a[i1:i2]))
        if op in ("replace", "insert"):
            add.append(" ".join(b[j1:j2]))
    bits = []
    if add:
        bits.append("writes " + "; ".join(add)[:200])
    if drop:
        bits.append("where the others write " + "; ".join(drop)[:200])
    return ", ".join(bits) or "the same tokens in a different order"


def sources_behind(project, uid: str) -> set[str]:
    """Every source upstream of a model."""
    out, stack, seen = set(), [uid], set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in project.sources:
            out.add(n)
        elif n in project.models:
            stack.extend(project.models[n].parents)
    return out


def route_facts(project, c: Cluster) -> dict:
    """What a person would need to say WHERE the fix belongs, computed and never judged.

    Five staging models over five sources means the feeds ship the problem and the fix is
    ingestion; five models across three layers over one source means one model upstream should
    have cleaned it. The counts are the discriminator. The choice stays a person's (25.23c).
    """
    per, all_sources = [], set()
    for m in sorted(c.members, key=lambda x: x.model):
        srcs = sources_behind(project, m.uid)
        all_sources |= srcs
        parents = project.models[m.uid].parents if m.uid in project.models else []
        per.append({"model": m.model, "layer": m.layer or "unknown",
                    "reads_a_source_directly": any(p in project.sources for p in parents),
                    "sources_behind_it": len(srcs)})
    layers: dict = {}
    for p in per:
        layers[p["layer"]] = layers.get(p["layer"], 0) + 1
    return {"models": per[:MAX_LISTED], "how_many_models": len(per),
            "models_per_layer": layers,
            "distinct_sources_behind_all_of_them": len(all_sources),
            "a_shared_macro_already_writes_it": c.macro or "no"}


# ------------------------------------------------------------------------------ claims

_STOP = frozenset([
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "does", "for", "from", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this",
    "to", "was", "were", "which", "with", "when", "where", "every", "each", "one", "row", "rows",
    "model", "column", "value", "values", "only", "never", "always"])


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_][a-z0-9_]{3,}", text.lower()) if w not in _STOP}


def claim_pairs(store, limit: int = 150, threshold: float = 0.5,
                project=None) -> tuple[list, list]:
    """(pairs code cannot settle, groups code already settled) over claims in different models.

    The same normalized sentence in several models is one assertion, and code groups those for
    free -- the `same_defect` rule: identical is never sent. A pair that SHARES most of its words
    and is not identical is exactly the case worth deciding, and only those are asked.
    """
    if store is None:
        return [], []
    rows = [r for r in store.claims(checkable_only=True, min_conf=0.7)
            if not _installed(project, r["subject"])]
    same: dict = {}
    for r in rows:
        # Letters and digits only: `--` against `:` is the same sentence, and paying a judgment to
        # say so is the thing this rule exists to stop.
        same.setdefault(re.sub(r"[^a-z0-9]+", " ", r["text"].lower()).strip(), []).append(r)
    settled = [rs for rs in same.values() if len({r["subject"] for r in rs}) > 1]
    uniq = [rs[0] for rs in same.values()]
    words = [(r, _words(r["text"])) for r in uniq]
    pairs = []
    for i, (a, wa) in enumerate(words):
        if len(wa) < 3:
            continue
        for b, wb in words[i + 1:]:
            if b["subject"] == a["subject"] or len(wb) < 3:
                continue
            j = len(wa & wb) / len(wa | wb)
            if j >= threshold:
                pairs.append((j, a, b))
    pairs.sort(key=lambda x: (-x[0], x[1]["claim_id"], x[2]["claim_id"]))
    return pairs[:limit], settled


def _installed(project, uid: str) -> bool:
    """A package's own prose is not this project's claim: Elementary documents its alert views."""
    m = getattr(project, "models", {}).get(uid) if project is not None else None
    return bool(m is not None and m.is_installed_package)


def components(edges: list[tuple[str, str]]) -> list[set]:
    """Connected components of an undirected graph, largest first."""
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    return sorted(groups.values(), key=lambda g: (-len(g), sorted(g)))


# ------------------------------------------------------------------------------ findings

def _answers(store, prefix: str) -> dict:
    """{decision_key: (answer, confidence, probabilities)} for one cluster family's latest answers."""
    import json
    try:
        rows = store.live_decisions(
            "starts_with(decision_key, 'cluster::') and (question = ? or starts_with(question, ?))",
            [prefix, prefix + "__"],
            columns="decision_key, answer, confidence, probabilities")
    except Exception:                                            # noqa: BLE001
        return {}
    out = {}
    for key, ans, conf, probs in rows:
        try:
            p = json.loads(probs or "{}")
        except (TypeError, ValueError):
            p = {}
        out[key] = (ans, conf, p)
    return out


def findings(project, digests, store) -> list:
    """One finding on EACH member of a cluster read as one rule, and on each odd one out read as
    an undeclared divergence. Never one finding for a cluster: there is nothing to rule on there."""
    from .checks.structural import Finding
    from .contracts import QUESTIONS
    if store is None:
        return []
    out: list = []
    one = QUESTIONS.get("one_rule_or_a_coincidence") or {}
    odd = QUESTIONS.get("the_odd_one_out") or {}
    route_q = QUESTIONS.get("where_the_fix_belongs") or {}
    got = _answers(store, one.get("id_prefix", "orule")) if one else {}
    routes = _answers(store, route_q.get("id_prefix", "route")) if route_q else {}
    want = set(one.get("finding_when") or [])
    for c in predicate_clusters(project, digests) if got else []:
        if c.macro:
            continue                      # written once already, in the macro: nothing to fix
        a = got.get(f"cluster::pred::{c.key}")
        if not a or a[0] not in want or float(a[1] or 0) < FINDING_FLOOR:
            continue
        route = routes.get(f"cluster::route::{c.key}")
        where = (f" Where the fix belongs, as read: {route[0].replace('_', ' ')} "
                 f"(confidence {float(route[1] or 0):.2f})." if route else "")
        edit = f" One edit in {c.macro_at}." if c.macro_at else ""
        for m in c.members:
            out.append(Finding(
                check="one_rule_or_a_coincidence", rests_on="one_rule_or_a_coincidence",
                subject=m.uid, subject_name=m.model, file=project.models[m.uid].path,
                summary=f"`{c.shape[:90]}` is one rule written in {len(c.models)} models",
                detail=(f"This filter is written the same way in {len(c.models)} models "
                        f"({', '.join(c.models[:MAX_LISTED])}), and it was read as ONE rule "
                        f"repeated rather than independent decisions. Written once, it cannot "
                        f"drift between them.{edit}{where}"),
                base=1,
                evidence={"shape": c.shape, "models": c.models, "answer": a[0],
                          "probability": round(float((a[2] or {}).get(a[0], 0) or 0), 3)}))
    want_odd = set(odd.get("finding_when") or [])
    got_odd = _answers(store, odd.get("id_prefix", "odd")) if odd else {}
    for o in odd_ones_out(project, digests) if got_odd else []:
        a = got_odd.get(f"cluster::odd::{o.key}")
        if not a or a[0] not in want_odd or float(a[1] or 0) < FINDING_FLOOR:
            continue
        m = o.member
        out.append(Finding(
            check="the_odd_one_out", rests_on="the_odd_one_out",
            subject=m.uid, subject_name=m.model, file=project.models[m.uid].path,
            summary=f"{m.model} {o.difference[:110]}",
            detail=(f"{len(o.sharing)} models write `{o.shared[:160]}`; this one differs, and "
                    f"nothing in it says the difference is deliberate. If it is, a comment or the "
                    f"description saying so ends this; if not, it is the one the others drifted "
                    f"from, or the one that drifted."),
            base=2,
            evidence={"shared": o.shared, "this": m.fine, "sharing": o.sharing,
                      "answer": a[0],
                      "probability": round(float((a[2] or {}).get(a[0], 0) or 0), 3)}))
    return out


# ------------------------------------------------------------------------------ the report

FAMILIES = ("one_rule_or_a_coincidence", "where_the_fix_belongs", "the_odd_one_out",
            "claims_are_the_same_assertion")


def _ans(a) -> dict:
    ans, conf, _p = a
    return {"answer": ans, "confidence": round(float(conf), 2) if conf is not None else None}


def _truthy(a) -> bool:
    """A noul answer is stored as its probability of yes."""
    try:
        return float(a) >= 0.5
    except (TypeError, ValueError):
        return False


def report(project, digests, store, pcs=None, odds=None, pairs=None, settled=None) -> dict:
    """Every cluster, what code settled, and what a judgment read -- ONE assembly for the CLI and
    the page, so the two cannot describe the same clusters differently."""
    from .contracts import QUESTIONS
    pcs = predicate_clusters(project, digests) if pcs is None else pcs
    odds = odd_ones_out(project, digests) if odds is None else odds
    if pairs is None or settled is None:
        pairs, settled = claim_pairs(store, project=project)

    def got(fam: str) -> dict:
        q = QUESTIONS.get(fam) or {}
        return _answers(store, q.get("id_prefix", "")) if store is not None and q else {}
    one, route, odd, same = (got(f) for f in FAMILIES)
    edges = [tuple(k.split("::")[2:4]) for k, (a, _c, _p) in same.items() if _truthy(a)]
    by_id = {}
    for _j, a, b in pairs:
        by_id[a["claim_id"]], by_id[b["claim_id"]] = a, b
    claim_groups = [[by_id[i] for i in comp if i in by_id] for comp in components(edges)]
    claim_groups += settled
    return {
        "predicate_clusters": [
            {**c.as_dict(),
             **({"one_rule": _ans(one[f"cluster::pred::{c.key}"])}
                if f"cluster::pred::{c.key}" in one else {}),
             **({"fix_belongs": _ans(route[f"cluster::route::{c.key}"])}
                if f"cluster::route::{c.key}" in route else {})}
            for c in pcs],
        "odd_ones_out": [
            {"model": o.member.model, "this": o.member.predicate, "shared": o.shared,
             "shared_by": o.sharing, "difference": o.difference,
             **({"read_as": _ans(odd[f"cluster::odd::{o.key}"])}
                if f"cluster::odd::{o.key}" in odd else {})}
            for o in odds],
        # Sorted: a component is a set, and a set's order changed between two runs of the same
        # store, so the page's data did too.
        "same_claim": sorted(
            (sorted(({"model": r["subject_name"], "claim": r["text"]} for r in g),
                    key=lambda x: (str(x["model"]), str(x["claim"])))
             for g in claim_groups if len({r["subject"] for r in g}) > 1),
            key=lambda grp: [(str(x["model"]), str(x["claim"])) for x in grp]),
        "claim_pairs_code_could_not_settle": len(pairs),
    }
