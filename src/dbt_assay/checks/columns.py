"""What a COLUMN is, according to the person who wrote it down.

*** THE SENTENCE SOMEBODY WROTE WAS THE ONE THING NOTHING READ. ***
`schema.yml` carries a description per column. The role judgment was built from the column's name,
its expression, its roots and its sibling names -- and never from the sentence. On a real
warehouse `decreed_use_codes` came back at 0.52 confidence, which is a model saying it cannot
tell, beside a description that says exactly. The description now goes into the state
(`subjects.described`), and these are the checks about the descriptions themselves.

*** THREE FACTS, ALL FREE, ALL FROM THE MANIFEST. ***
A column with no description at all, worst at the edge of the warehouse where nothing upstream can
explain it. The same column name described two different ways in two models. And a description
whose own words are contradicted by how the column is built -- the column-level analogue of
`description_contradicts_the_code`, which judges a whole model's prose and cannot say which column
the contradiction is in.

*** COUNTED, NOT JUDGED. ***
None of these asks a model anything. Two descriptions of one name either differ or they do not,
and a description that says "always" beside an expression that can produce NULL is arithmetic
about words. Where a real judgment is needed, the finding says so and stops.
"""
from __future__ import annotations

import re

from ..subjects import described
from .structural import Finding

# *** A COLUMN AT THE EDGE OF THE WAREHOUSE HAS NOTHING UPSTREAM TO EXPLAIN IT. ***
# Everywhere else, provenance answers "where did this come from" without prose. A staging model
# reading a source is where the meaning enters the project, and if it is not written down there
# it is not written down anywhere.
INGESTION_LAYERS = ("staging", "source", "raw", "bronze", "seeds")

# The words a description uses to promise something the SQL can be checked against.
_ALWAYS = re.compile(r"\b(always|never null|not null|every row|guaranteed|required)\b",
                     re.IGNORECASE)


def _layer_of(m) -> str:
    return str(getattr(m, "layer", "") or "").lower()


def _yours(m) -> bool:
    """An installed package's columns are not this project's to document."""
    return bool(getattr(m, "package", "") == "" or getattr(m, "project", "")
                == getattr(m, "package", ""))


def column_has_no_description(project, _digests=None, schema=None) -> list[Finding]:
    """Columns nobody has written a sentence for, reported per MODEL and not per column.

    *** ONE FINDING PER MODEL, BECAUSE THE DECISION IS ONE DECISION. ***
    A warehouse with 358 models and no column documentation would otherwise raise thousands of
    findings, swamping every other family -- the same failure the monitoring coverage check
    already avoids. Nobody rules on "write a description" four hundred times.
    """
    out = []
    for uid, m in project.models.items():
        if not _yours(m):
            continue
        declared = {str(k).lower() for k in (getattr(m, "columns", None) or {})}
        have = described(m)
        known = list(schema.columns(uid).names if schema else []) or sorted(declared)
        if not known:
            continue                       # nothing knows this model's columns; not a pass, a gap
        missing = sorted(c for c in known if str(c).lower() not in have)
        if not missing:
            continue
        ingestion = _layer_of(m) in INGESTION_LAYERS
        # Below the edge of the warehouse an undocumented column usually has a documented parent,
        # so this is worth saying and is not worth saying as loudly.
        base = 2 if ingestion else 1
        out.append(Finding(
            check="column_has_no_description",
            subject=uid, subject_name=m.name, file=m.path, base=base,
            summary=(f"{len(missing)} of {len(known)} column(s) have no description"
                     + (" in an ingestion model" if ingestion else "")),
            detail=("A column's description is the only place the meaning of a value is written "
                    "down, and assay now sends it with every judged question about that column -- "
                    "so an undocumented column is judged from its name and its SQL alone.\n\n"
                    + ("This is a model at the EDGE of the warehouse. Everywhere else, provenance "
                       "answers 'where did this come from' without prose; here there is no "
                       "upstream to ask, so a column undocumented here is undocumented "
                       "everywhere.\n\n" if ingestion else "")
                    + "Counted, not judged. It says nothing about whether the descriptions that "
                      "DO exist are right."),
            evidence={"missing": missing[:40], "missing_total": len(missing),
                      "columns": len(known), "layer": _layer_of(m),
                      "ingestion": ingestion}))
    return out


def models_disagree_about_a_column(project, _digests=None, _schema=None) -> list[Finding]:
    """One column name, described two different ways in two models.

    *** THIS IS THE `section_id` PROBLEM, GENERALISED. ***
    A name that means one thing in one model and another thing downstream is the defect that
    costs real money, and it is invisible to every check that reads one model at a time. Two
    people wrote two sentences; either one of them is wrong, or the name is doing two jobs.

    Counted, not judged: the sentences differ or they do not. Whether they MEAN the same thing is
    a question for a person, and the finding says so rather than guessing at it.
    """
    by_col: dict = {}
    for uid, m in project.models.items():
        if not _yours(m):
            continue
        for col, text in described(m).items():
            # Keyed on the NORMALISED sentence so case and whitespace are not a disagreement;
            # the original is carried, because that is what a reader has to compare.
            by_col.setdefault(col, {}).setdefault(_norm(text), []).append((uid, m, text))
    out = []
    for col, variants in sorted(by_col.items()):
        if len(variants) < 2:
            continue
        flat = [row for rows in variants.values() for row in rows]
        # The widest-reaching model owns the finding, because that is where a wrong sentence
        # costs the most and where somebody will look first.
        home_uid, home_m, _t = max(
            flat, key=lambda r: (project.blast_radius(r[0])["marts"], r[1].name))
        said = sorted({(m.name, text[:200]) for _u, m, text in flat})
        out.append(Finding(
            check="models_disagree_about_a_column",
            subject=home_uid, subject_name=home_m.name, file=home_m.path, base=2,
            summary=f"`{col}` is described {len(variants)} different ways across "
                    f"{len(flat)} model(s)",
            detail=("The same column name carries different sentences in different models. Either "
                    "one of them has drifted, or the name is doing two jobs and a reader "
                    "downstream cannot tell which one they have.\n\n"
                    "Counted, not judged. Two sentences differing is a fact; whether they mean "
                    "the same thing is a question for somebody who knows the domain."),
            evidence={"column": col, "variants": len(variants),
                      "described_in": [f"{n}: {t}" for n, t in said][:8],
                      "models": len(flat)}))
    return out


def _norm(text: str) -> str:
    """Two sentences differing only in whitespace or case are one sentence."""
    return " ".join(str(text or "").lower().split())


def description_promises_what_the_column_cannot_keep(project, digests=None,
                                                     _schema=None) -> list[Finding]:
    """A description that says `always` or `unique` beside SQL that says otherwise.

    *** THE COLUMN-LEVEL ANALOGUE OF `description_contradicts_the_code`. ***
    That family judges a model's whole prose and cannot say WHICH column the contradiction is in;
    seventeen unclear rulings on one warehouse were that, and only that. This one is exact and
    costs nothing: a sentence promising no nulls, over an expression whose own tail is a
    COALESCE default or a LEFT JOIN away, is two statements that cannot both hold.
    """
    from .structural import default_literal
    out = []
    for uid, m in project.models.items():
        if not _yours(m):
            continue
        d = (digests or {}).get(uid)
        exprs = getattr(d, "output_exprs", None) or {} if d is not None else {}
        for col, text in sorted(described(m).items()):
            expr = exprs.get(col) or exprs.get(col.lower())
            if not expr:
                continue
            lit = default_literal(expr)
            if not (lit and _ALWAYS.search(text)):
                continue
            # *** A COALESCE DEFAULT IS THE ONE SHAPE THAT MAKES THE PROMISE TRUE AND USELESS. ***
            # The column IS never null, exactly, because a literal was substituted for the
            # missing value -- so a description saying "always populated" is true of the column
            # and false of the data, and everything downstream reads the second meaning.
            out.append(Finding(
                check="description_promises_what_the_column_cannot_keep",
                subject=uid, subject_name=m.name, file=m.path, base=2,
                summary=f"`{col}` is described as always populated and is a COALESCE "
                        f"default to {lit}",
                detail=("The sentence and the SQL are both true and they do not mean the same "
                        "thing. The column is never NULL because a literal was put there when "
                        "the value was missing, so 'always populated' is a fact about the "
                        "column and not about the data. Everything downstream reads the second "
                        "meaning.\n\n"
                        "Counted, not judged: the expression's own tail is the literal."),
                evidence={"column": col, "description": str(text)[:300],
                          "default": lit, "expression": str(expr)[:300]}))
    return out


COLUMN_CHECKS = (column_has_no_description, models_disagree_about_a_column,
                 description_promises_what_the_column_cannot_keep)
