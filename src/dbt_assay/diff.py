"""What changed about what your models MEAN, not about their text.

*** A GRAIN CHANGE IS INVISIBLE IN A SQL DIFF. ***
It looks like somebody edited a GROUP BY. Nothing in the patch says one row stopped being one row
per (section, case) and became one row per section, that fourteen models consume it, or that three
of them aggregate over it and are now inflated. A reviewer cannot compute that by hand, which is
why this is the output most likely to change what someone does.

*** IT DIFFS TWO INVENTORIES, NOT TWO FILES. ***
So the comparison is between meanings: a grain, a column's role, where a value comes from. A model
whose SQL was rewritten wholesale but whose contract is unchanged produces NOTHING here, which is
exactly right -- that refactor needs no semantic review.

*** CONSEQUENCE IS ARITHMETIC. ***
Who consumes the changed model, and which of those AGGREGATE over it, comes from the graph and the
parsed SQL. The judgment says what changed; code says what it costs.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Change:
    model: str
    kind: str                     # grain | column_added | column_removed | role | provenance
                                  # | model_added | model_removed
    column: str = ""
    before: object = None
    after: object = None
    detail: str = ""
    consumers: list = field(default_factory=list)
    aggregating_consumers: list = field(default_factory=list)
    marts: int = 0

    @property
    def severity(self) -> int:
        """A grain change with aggregating consumers is the one that silently corrupts numbers."""
        if self.kind in ("grain", "grain_in_sql"):
            return 3 if self.aggregating_consumers else 2
        if self.kind in ("column_removed", "model_removed"):
            return 2 if self.consumers else 1
        return 1


def _index(entries) -> dict:
    return {e.name: e for e in entries}


def _aggregating_children(project, digests, uid: str) -> list[str]:
    """Consumers that aggregate. A fan-out or a grain widening reaches a NUMBER through these."""
    out = []
    for child in project.models[uid].children:
        d = digests.get(child)
        if not d or not d.ok:
            continue
        if d.group_by_columns or any(n.startswith(("SUM", "COUNT", "AVG", "MIN", "MAX"))
                                     for n, _p in d.functions):
            out.append(project.models[child].name)
    return out


def compare(before, after, project=None, digests=None) -> list[Change]:
    """`before` and `after` are lists of ModelEntry. Order does not matter; names do."""
    b, a = _index(before), _index(after)
    changes: list[Change] = []

    for name in sorted(set(a) - set(b)):
        changes.append(Change(name, "model_added", detail="new model"))
    for name in sorted(set(b) - set(a)):
        e = b[name]
        changes.append(Change(name, "model_removed",
                              detail=f"was read by {e.descendants} models", marts=e.marts))

    for name in sorted(set(a) & set(b)):
        eb, ea = b[name], a[name]

        # Compare what the SQL says as well as the resolved contract. A declared key outranks a
        # derived grain in the inventory, so without this a GROUP BY change on a model that
        # declares a key is invisible -- and that is the change most worth seeing.
        if eb.derived_grain != ea.derived_grain and (eb.derived_grain or ea.derived_grain):
            c = Change(name, "grain_in_sql", before=eb.derived_grain, after=ea.derived_grain,
                       marts=ea.marts)
            if project is not None and ea.uid in project.models:
                c.consumers = [project.name_of(x) for x in project.models[ea.uid].children]
                if digests is not None:
                    c.aggregating_consumers = _aggregating_children(project, digests, ea.uid)
            db, da = eb.derived_grain, ea.derived_grain
            c.detail = (f"the SQL's grain moves from {', '.join(db or ['unsettled'])} to "
                        f"{', '.join(da or ['unsettled'])}")
            if ea.grain and ea.grain.source == "declared":
                dec = list(ea.grain.value)
                if da and sorted(da) != sorted(dec):
                    c.detail += (f", while a test still declares one row per {', '.join(dec)}. "
                                 f"One of the two is now wrong")
            changes.append(c)

        gb = list(eb.grain.value) if eb.grain else None
        ga = list(ea.grain.value) if ea.grain else None
        if gb != ga:
            c = Change(name, "grain", before=gb, after=ga, marts=ea.marts)
            if project is not None and ea.uid in project.models:
                c.consumers = [project.name_of(x) for x in project.models[ea.uid].children]
                if digests is not None:
                    c.aggregating_consumers = _aggregating_children(project, digests, ea.uid)
            # Stated plainly, because "grain: ['a'] -> ['a','b']" is not what a reviewer needs.
            if gb and ga:
                c.detail = (f"one row per {', '.join(gb)} becomes one row per {', '.join(ga)}")
            elif ga:
                c.detail = f"grain is now settled: one row per {', '.join(ga)}"
            else:
                c.detail = "grain was settled and is no longer"
            changes.append(c)

        cb = {x.name: x for x in eb.columns}
        ca = {x.name: x for x in ea.columns}
        for col in sorted(set(ca) - set(cb)):
            changes.append(Change(name, "column_added", column=col, marts=ea.marts))
        for col in sorted(set(cb) - set(ca)):
            consumers = []
            if project is not None and ea.uid in project.models:
                consumers = [project.name_of(x) for x in project.models[ea.uid].children]
            changes.append(Change(name, "column_removed", column=col, consumers=consumers,
                                  marts=ea.marts,
                                  detail="downstream models referencing it will break or go NULL"))
        for col in sorted(set(ca) & set(cb)):
            x, y = cb[col], ca[col]
            if x.provenance.value != y.provenance.value:
                changes.append(Change(name, "provenance", column=col,
                                      before=x.provenance.value, after=y.provenance.value,
                                      marts=ea.marts,
                                      detail=f"{y.provenance.note[:90]}"))
            rb = x.role.value if x.role else None
            ra = y.role.value if y.role else None
            if rb and ra and rb != ra:
                changes.append(Change(name, "role", column=col, before=rb, after=ra,
                                      marts=ea.marts))
    return sorted(changes, key=lambda c: (-c.severity, c.model, c.kind))


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def summarise(changes: list[Change]) -> str:
    """The paragraph a reviewer actually needs, assembled from facts."""
    if not changes:
        return "No model in this project changed meaning."
    lines = []
    for c in changes:
        if c.kind in ("grain", "grain_in_sql"):
            bit = f"**{c.model}**: {c.detail}."
            if c.consumers:
                n = len(c.consumers)
                bit += f" {n} {_plural(n, 'model consumes', 'models consume')} it"
                if c.aggregating_consumers:
                    k = len(c.aggregating_consumers)
                    bit += (f", and {k} of {_plural(k, 'them aggregates', 'them aggregate')} "
                            f"over it ({', '.join(c.aggregating_consumers[:3])})")
                bit += "."
            if c.marts:
                bit += f" {c.marts} {_plural(c.marts, 'mart', 'marts')} downstream."
            bit += " Nothing in the SQL diff says this."
            lines.append(bit)
        elif c.kind == "column_removed":
            n = len(c.consumers)
            lines.append(f"**{c.model}**: `{c.column}` is gone; "
                         f"{n} {_plural(n, 'model reads', 'models read')} this one.")
        elif c.kind == "provenance":
            lines.append(f"**{c.model}**: `{c.column}` changes from {c.before} to {c.after}.")
        elif c.kind == "role":
            lines.append(f"**{c.model}**: `{c.column}` now reads as {c.after}, was {c.before}.")
        elif c.kind == "model_added":
            lines.append(f"**{c.model}**: new.")
        elif c.kind == "model_removed":
            lines.append(f"**{c.model}**: removed; {c.detail}.")
        else:
            lines.append(f"**{c.model}**: `{c.column}` added.")
    return "\n".join(f"- {x}" for x in lines)
