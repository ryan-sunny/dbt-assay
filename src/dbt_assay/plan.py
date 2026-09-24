"""What to DO about the findings a person agreed with.

*** THE LOOP HAD A GAP BETWEEN "THIS IS REAL" AND "IT IS FIXED". ***
`check` finds it, `review` settles whether it is real, and then nothing. The person who agreed
with forty findings is holding forty sentences about what is wrong and no statement of what to
change, which is the same gap `suggest` closed on the config side: a list of problems and an essay
about the tool, with the connection left to the reader.

*** THE FIX SHAPE FALLS OUT OF THE CHECK NAME, EXACTLY, FOR FREE. ***
It is a lookup, not a judgment. `arbitrary_pick` is always "add a tie-break column";
`test_cannot_fail` is always "the test asserts nothing, remove or repair it". Nothing about the
particular model changes the SHAPE of its repair, which is why this costs no calls and cannot be
wrong in the way a judged answer can.

What it deliberately does not say is the WORDS. For `code_contradicts_a_claim` the shape is "edit
the prose at the claim's file:line" and what the sentence should say instead is a real judgment
about a real warehouse -- the same two-tier split as everything else here. Structure decides the
shape; a person or a judged call writes the words.

*** AND IT IS A JSONL, BECAUSE THE CONSUMER IS AN AGENT. ***
A markdown report is for a person who is going to read it once. This is read by whatever is about
to make the edit, which wants one object per thing to do.
"""
from __future__ import annotations

import json
from pathlib import Path

# *** ONE ROW PER CHECK, AND A CHECK MISSING FROM IT IS NAMED RATHER THAN SKIPPED. ***
# A plan that silently omits the findings it has no shape for is a plan that reads as complete.
SHAPES: dict[str, tuple[str, str]] = {
    # *** THE MONITORING CHECKS FIX THE MONITOR, NEVER THE DATA. ***
    # assay asserts that a monitor exists, is current and covers what matters. Every fix here is
    # a change to how the project is WATCHED; none of them touches a model.
    # *** A STALE COMMENT IS FIXED IN THE COMMENT. ***
    "config_comment_contradicts_the_store": (
        "correct the number, or date it",
        ("A comment in audit.yml states a count the store no longer holds. Write the number the "
         "store holds now, or say when the old one was true -- \"38 of 38 agreed at 0.37\" is "
         "history and is not checked. Nothing in the warehouse changes.")),
    "monitor_declared_but_never_run": (
        "build the monitor",
        ("The monitor is configured and has never produced a result: installed is not built. Run "
         "the package's models, and check the job that should be running them -- both tools go "
         "quiet the same way, and the silence reads as a clean bill.")),
    "monitor_ran_then_stopped": (
        "find out why it stopped",
        ("The table has rows and nothing has written to it since. This is not a data problem and "
         "not something to waive: something that used to run does not. The threshold that decided "
         "`stopped` is derived from how often this project actually runs dbt; "
         "`monitoring.source_freshness.max_staleness_days` overrides it if the cadence changed "
         "on purpose.")),
    "volume_is_not_being_watched": (
        "extend the monitor's coverage",
        ("Models that feed marts have no row-count history. assay does not measure volume and "
         "does not intend to -- add the tables to `elementary-data`'s monitoring, or decide "
         "deliberately that they do not need it and raise `monitoring.min_marts`.")),
    "test_declared_but_never_run": (
        "find out why the test never fires",
        ("A test that never ran and a test that passed are indistinguishable in a summary. Either "
         "the selector never reaches it, or the model it hangs off is never built. Run it once "
         "and see.")),
    "test_skipped_rather_than_passed": (
        "fix what the test hangs off",
        ("dbt skips a test whose model failed upstream, so a green run can contain a test that "
         "has not evaluated your data in months. Fix the upstream failure; the skip is a symptom "
         "and waiving it hides the cause.")),
    "code_contradicts_a_claim": (
        "edit the prose",
        (        "The sentence and the code disagree. Open the claim where it was written -- "
        "`assay claims --write claims.yml` gives the file and line -- and change whichever one is "
        "wrong. Usually it is the sentence, because code moves and prose does not.")),
    "description_contradicts_the_code": (
        "edit the description",
        (        "The schema.yml description says something the SQL does not do. Fix the description, or "
        "the SQL if the description was the intent.")),
    "grain_unresolved": (
        "declare the grain",
        (        "Nothing in the project says what one row of this model is. Add a "
        "`unique_combination_of_columns` test, or `meta: {grain: [...]}`, naming the real key.")),
    "identifier_outside_grain": (
        "declare the grain",
        (        "A column that identifies a row is not part of the declared key. Either the key is "
        "incomplete or the column does not identify what its name suggests.")),
    "arbitrary_pick": (
        "add a tie-break column",
        (        "`row_number() ... = 1` keeps one row per partition and none of the ORDER BY keys is "
        "unique, so ties are broken by whatever the engine returned and the winner can change "
        "between builds on identical data. Add a column that is unique per row as the LAST sort "
        "key.")),
    "first_match_pick": (
        "add a tie-break column",
        (        "The same class in a different spelling: one value taken from a multi-valued field with "
        "nothing deciding which.")),
    "test_cannot_fail": (
        "remove or repair the test",
        (        "The test passes on every row by construction and asserts nothing. Either delete it -- a "
        "test that cannot fail is worse than no test, because it reads as coverage -- or point it "
        "at the column the guard was meant to protect.")),
    "test_outruns_its_source": (
        "move or drop the assertion",
        (        "The column can be NULL by construction, so the test asserts something the data never "
        "promised. Assert it where the value is produced, or stop asserting it here.")),
    "seed_reaches_nothing": (
        "delete the seed, or wire it up",
        (        "Nothing reads it. Either it is dead and should go, or something was meant to `ref` it "
        "and does not.")),
    "one_rule_or_a_coincidence": (
        "write the rule once",
        ("The same filter is written in several models and was read as one rule. Put it in one "
         "macro every model calls, or in the model upstream they share -- `assay clusters` shows "
         "where the fix was read to belong, and the counts that read rests on.")),
    "the_odd_one_out": (
        "bring it back in line, or say why it differs",
        ("Most of its family writes the filter one way and this model writes it another, with "
         "nothing saying why. Either it drifted and should match, or it is deliberate and a "
         "comment on the filter should say so.")),
    "exposure_undeclared": (
        "declare the exposure, or retire the model",
        ("Nothing in the project reads it. If a dashboard, app or report does, add an `exposures:` "
         "entry with this model under `depends_on` -- the name, owner and URL are yours to write. "
         "If nothing does, it is dead and should go.")),
    "source_reaches_nothing": (
        "delete the source, or wire it up",
        (        "Declared and unread. The same choice as a dead seed.")),
    "source_only_a_test_reads": (
        "wire it up, or drop the test",
        (        "The only thing reading this source is a test on it, which tests that a thing nobody "
        "uses is well-formed.")),
    "hop_multiplies_rows": (
        "declare the fan-out, or collapse it",
        (        "A join multiplies rows and nothing says so. Either the multiplication is intended and "
        "the child's grain should say it, or the hop needs a group by.")),
    "float_sum_is_not_reproducible": (
        "cast to decimal before aggregating",
        (        "The measure is added up as a floating-point number, so its total depends on row "
        "order and moves between builds on the same data. `sum(cast(x as decimal(18, 2)))` "
        "makes the addition exact; the evidence carries the expression.")),
    "fixed_finding_returned": (
        "find what undid the fix",
        (        "A finding a person agreed with was fixed and is back. Compare the model at the "
        "commit where it was gone with the commit where it came back (both are in the "
        "timeline): the change between them reintroduced it.")),
    "join_fans_out": (
        "join on the whole key",
        (        "The join uses part of the key the parent declares unique, so one parent row matches "
        "several. Join on all of it, or aggregate first.")),
    "variant_column": (
        "pin the column type",
        (        "dlt split one column into two by inferred type (`__v_double`), so half the values are "
        "in a column nothing reads. Pin the type at the source.")),
    "duckdb_full_match": (
        "use the operator you meant",
        (        "`~` is a FULL-string match in DuckDB, not a partial one. Use `like`/`similar to` if a "
        "partial match was intended.")),
    "ranks_by_degrees": (
        "measure in a projected CRS",
        (        "Ordering by latitude/longitude treats degrees as distance. A degree of longitude shrinks "
        "with latitude, so the ranking is wrong by a factor that varies across the data.")),
    "bbox_as_radius": (
        "use a real distance",
        (        "A bounding box is a square and a radius is a circle. If proximity is the intent, "
        "measure it.")),
    "window_after_where": (
        "move the window, or the filter",
        (        "The window function can only see rows a WHERE already removed, so its ranking is over a "
        "subset. Use QUALIFY, or rank before filtering.")),
    "key_started_holding": (
        "declare the key",
        (        "A column now holds unique where it did not before. Nothing is wrong today; this is the "
        "moment to declare it, before something starts depending on an accident.")),
    "key_column_stopped_mattering": (
        "find out what changed",
        (        "A key that held last week does not now. This is the failure that corrupts a warehouse "
        "and no check describing the present can see it.")),
    "narrow_read": (
        "read the columns that carry the meaning",
        (        "The model reads far fewer columns of its parent than it could, and the ones it skips are "
        "where the meaning is.")),
    "grain_contradicts_test": (
        "reconcile the key with the test",
        ("The declared grain and a uniqueness test on this model disagree about what one row is. "
         "One of them is wrong and both are written down, so a reader has no way to tell which.")),
    "measure_inside_grain": (
        "take the measure out of the key",
        ("A column that measures something is part of the declared key, so two rows differing "
         "only in an amount are two entities. Usually the key is too wide.")),
    "hop_drops_most_rows": (
        "say why the rows go",
        ("A counted hop keeps a small fraction of its parent. Either the filter is intended and "
         "belongs in the model's prose, or an enrichment join is missing most of its matches.")),
    "key_stopped_holding": (
        "find out what changed",
        ("A declared key no longer holds. Nothing downstream that assumed one row per key is "
         "safe until this is understood.")),
    "key_column_started_mattering": (
        "declare the key",
        ("A column began holding unique. Declare it now, while it is a choice rather than an "
         "accident something already depends on.")),
    "source_freshness_stale": (
        "find out why the feed stopped",
        ("The source has not moved within its declared freshness window. Everything derived from "
         "it is being served as current.")),
    "source_freshness_undeclared": (
        "declare freshness",
        ("Nothing says how current this source should be, so nothing can notice it going quiet. "
         "A source going silent is invisible to every check that describes the present.")),
    "column_has_no_description": (
        "write the sentence",
        ("A column's description is where the meaning of its values is written down, and assay "
         "sends it with every judged question about that column. Without one, the column is "
         "judged from its name and its SQL alone.")),
    "models_disagree_about_a_column": (
        "settle what the name means",
        ("One column name, two different sentences in two models. Either one has drifted, or the "
         "name is doing two jobs and a reader downstream cannot tell which one they have.")),
    "description_promises_what_the_column_cannot_keep": (
        "fix the sentence or drop the default",
        ("The description says the column is always populated and the SQL fills a literal in "
         "when the value is missing. Both are true and they do not mean the same thing.")),
    "seniority_ordered_by_the_wrong_date": (
        "order by the right date",
        (        "Seniority is ordered by a date that does not establish it.")),
}


def build(findings, store, groups: list | None = None) -> list[dict]:
    """One row per THING TO DO: a finding a person agreed with that is still here, or several of
    them that are one construct written in several models.

    Only the agreed ones, because a plan built from everything is the findings list again. Only
    the ones still present, because a fix nobody needs is worse than no plan.

    *** NINE ROWS, NINE FIX SHAPES, ONE EDIT. *** (25.21) Agreed findings in one `groups.Group`
    become one row carrying every call site, and when a project macro carries the construct the
    fix is that macro at its line. The rulings stay on the findings; only the plan is collapsed.
    """
    agreed = store.ruled_findings("agree") if store is not None else {}
    if agreed and store is not None:
        for fid in store.ruled_findings("disagree"):
            agreed.pop(fid, None)
    if not agreed:
        return []
    out = []
    for f in findings:
        if f.id not in agreed:
            continue
        who, when, note = agreed[f.id]
        shape, how = SHAPES.get(f.check, ("", ""))
        row = {
            "finding": f.id, "check": f.check, "model": f.subject_name, "file": f.file,
            "summary": f.summary, "marts": f.marts, "descendants": f.descendants,
            "exposures": list(getattr(f, "exposures", None) or []),
            "agreed_by": who, "agreed_at": str(when)[:10] if when else "",
            "their_reason": note,
            "fix_shape": shape, "how": how,
            "evidence": f.evidence,
        }
        if not shape:
            # *** NAMED, NOT DROPPED. ***
            # A check with no shape here is a gap in this table, and a plan that quietly omits it
            # reads as a plan that covered everything.
            row["fix_shape"] = "unknown"
            row["how"] = (f"`{f.check}` has no fix shape in assay's table, so this one needs "
                          f"reading. That is a gap in the tool, not a judgment about the model.")
        out.append(row)
    out = _collapse(out, groups or [])
    # What reaches a product first, then the widest blast radius, ties on the id so two runs agree.
    return sorted(out, key=lambda r: (-len(r["exposures"]), -r["marts"], -r["descendants"],
                                      r["finding"]))


def _collapse(rows: list[dict], groups: list) -> list[dict]:
    """Agreed rows that share a group become one row with its call sites."""
    from .groups import membership
    member = membership(groups)
    out, merged = [], {}
    for r in rows:
        g = member.get(r["finding"])
        if g is None:
            out.append(r)
            continue
        info = g.as_dict()
        r["group"] = {"group": info["group"], "size": info["size"], "models": info["models"],
                      "macro": info["macro"], "macro_at": info["macro_at"]}
        if g.key not in merged:
            merged[g.key] = r
            r["call_sites"] = [{"model": r["model"], "file": r["file"], "finding": r["finding"]}]
            if info["macro_at"]:
                r["fix_shape"] = f"one edit in {info['macro_at']}"
                r["how"] = (f"The same construct is written in {info['size']} models by the "
                            f"`{info['macro']}` macro. Fix it there, once, and every call site "
                            f"follows. " + r["how"])
            else:
                r["how"] = (f"The same construct is written inline in {info['size']} models "
                            f"({', '.join(info['models'][:6])}). Fix it in each, or move it into "
                            f"one macro so it cannot drift. " + r["how"])
            out.append(r)
            continue
        head = merged[g.key]
        head["call_sites"].append({"model": r["model"], "file": r["file"],
                                   "finding": r["finding"]})
        head["marts"] = max(head["marts"], r["marts"])
        head["descendants"] = max(head["descendants"], r["descendants"])
        head["exposures"] = sorted(set(head["exposures"]) | set(r["exposures"]))
    return out


def write(rows: list[dict], path: str | Path) -> Path:
    """One JSON object per line, sorted keys, trailing newline. Diffs one line per thing to do."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), default=str) + "\n" for r in rows))
    return p
