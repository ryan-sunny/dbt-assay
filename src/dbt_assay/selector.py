"""A subset of dbt's selector syntax, applied to subjects.

*** A SELECTOR assay DOES NOT UNDERSTAND MUST RAISE, NEVER MATCH EVERYTHING. ***
`when: select: "tag:finance"` that silently matches every model turns a scoped question into a
project-wide one, and the config reads as though it were working. That is a guard that cannot see,
one layer up. Unsupported syntax is an error at load time, with the supported forms named.

Supported, because they cover almost all real use:
    name                 exactly that model
    path:models/water    anything under that path
    tag:finance          a tag in the model's config
    config.materialized:table
    +name / name+        ancestors / descendants (the graph operators)
    a b c                a union, space separated, as dbt reads it
"""
from __future__ import annotations

PREFIXES = ("path:", "tag:", "config.materialized:")


class SelectorError(ValueError):
    pass


def validate(expr: str) -> None:
    for tok in expr.split():
        bare = tok.strip("+")
        if ":" in bare and not bare.startswith(PREFIXES):
            raise SelectorError(
                f"assay does not understand the selector {tok!r}. Supported: a model name, "
                f"{', '.join(PREFIXES)}, and the + graph operators. A selector that is silently "
                f"ignored would scope nothing while looking as though it did.")


def _one(project, tok: str) -> set[str]:
    want_ancestors = tok.startswith("+")
    want_descendants = tok.endswith("+")
    bare = tok.strip("+")
    hit: set[str] = set()

    if bare.startswith("path:"):
        pref = bare[len("path:"):].rstrip("/")
        hit = {u for u, m in project.models.items() if m.path.startswith(pref)}
    elif bare.startswith("tag:"):
        want = bare[len("tag:"):]
        for u in project.models:
            tags = ((project.raw.get("nodes", {}).get(u) or {}).get("config") or {}).get("tags") or []
            if want in tags:
                hit.add(u)
    elif bare.startswith("config.materialized:"):
        want = bare.split(":", 1)[1]
        hit = {u for u, m in project.models.items() if m.materialized == want}
    else:
        hit = {u for u, m in project.models.items() if m.name == bare}

    out = set(hit)
    if want_descendants:
        for u in hit:
            out |= project.descendants(u)
    if want_ancestors:
        for u in hit:
            stack = list(project.models[u].parents)
            while stack:
                n = stack.pop()
                if n in out or n not in project.models:
                    continue
                out.add(n)
                stack.extend(project.models[n].parents)
    return out


def resolve(project, expr: str | None) -> set[str] | None:
    """None means 'everything', which is different from an empty set meaning 'nothing matched'."""
    if not expr:
        return None
    validate(expr)
    out: set[str] = set()
    for tok in expr.split():
        out |= _one(project, tok)
    return out
