# Field notes: one night on a 357-model warehouse

Written from using `assay` 0.5.0 → 0.5.1 against `sunny_data` (357 models, 212 sources, 1,288 tests,
956 edges, DuckDB) on 2026-09-20. Everything below is something that happened, not something that
might. Ranked by what it cost or would have cost.

---

## 1. A custom family with a new name is never asked, and the docs teach that pattern

`QUESTIONS = load_all_banks()` loads your bank. Every **call site** then asks for a shipped family
by string literal — `judged.py`, `semantics.py`, `columns.py`, `claims.py`, `rows.py`. There is no
generic runner. So a family with a new name is loaded, linted, listed by `assay banks`, and never
asked by anything.

I wrote three water families, they linted at **0 errors**, `banks` showed them as `yours`, and they
did nothing. They looked exactly like coverage.

**`docs/OVERVIEW.md:334` teaches this.** Its worked example is `water_division_completeness` — a new
name. Anyone following the documentation writes a question that cannot run.

Three fixes, cheapest first:

- **`assay banks` should say whether each family has a caller.** A `called by` column: `traverse`,
  `columns`, `semantics`, or `— (nothing asks this)`. This is the same "a guard that matches nothing
  passes for the wrong reason" rule the rest of the tool is built on, applied to the bank itself.
- **The docs' example should replace a shipped name**, and say in one line why.
- **A generic runner** would make new names first-class: a family declares the subject it wants
  (`model`, `edge`, `column`, `test`, `row`) and assay builds that state and asks it. That is the
  real fix; the first two are worth doing regardless.

## 2. A replacement inherits its call site's state, and nothing says what that is

Replacing a shipped name works — proved below. But the replacement can only ask about what that
caller already hands it. `edge_preserves_the_grain` receives parent, child, join keys and grouping,
so it can judge a join and **cannot** judge a window function or an arithmetic expression.

Of the three questions I wanted, only one was expressible:

| question | needs | exists? |
|---|---|---|
| does this join a case number without its division | an edge | yes — replaced `edge_preserves_the_grain` |
| does this sum an ABSOLUTE amount with a CONDITIONAL one | an expression | no call site |
| does this order seniority by the adjudication date | a window | no call site |

Nothing in the docs or `banks` tells you which state a family receives, so choosing what to replace
is guesswork until you read `judged.py`. **A `state:` line per family in `banks` output** would fix
this for the price of a lookup table.

### It does work, and it found a real thing

Replaced `edge_preserves_the_grain` keeping its four options word for word, adding one:

```
wrong_scope_entirely — one row, the RIGHT COUNT, the WRONG INSTANCE, because the match used an
identifier only unique inside a scope the condition left out
```

No shipped option covers that, and no amount of `vocab` makes a model pick an option that is not on
the list. **That is the thing a replaced family does that vocab cannot.**

Over 543 edges it fired three times, all section joins, at 0.21 / 0.28 / 0.33 — one a literal
0.33/0.33 tie. Low, and correctly so. The strongest is right: `int_water_resume_sections` joins
sections **without the principal meridian**, and the model's own comment already says
*"no T/R/S string in the state resolves to both … the first state added west or south of Colorado
will not be so tidy."* A true positive with the author's own expiry condition attached.

## 3. `traverse` misses an inline `group by` that precedes the join

177 of 543 hops came back `silently_multiplied` — 33%. The top water hit was wrong for a checkable
reason:

```sql
) dl on dl.wdid = r.wdid        -- dl is ( select … from int_water_diligence … group by wdid )
```

The child collapses the parent to one row per key **in a subquery, before the join**. That is the
question's own `deliberately_coarser`. The state appears to carry the join keys and the model's
top-level grouping but not that the joined relation was already aggregated, so the question cannot
see the thing that makes it safe.

At 33% flagged, this family needs reading rather than trusting, and the fix is in the **state**, not
the criteria.

## 4. `assay adjudicate` took 5.4 hours — fixed in 0.5.1, noting the shape

`store_failures` is on for all 1,288 tests here. `collect` shelled a cold `dbt show --inline` per
test; 13 audit tables held a row between them. Killed at six minutes because it was blocking a
corpus build. 0.5.1's batched `union all` of counts turned it into minutes and **$0.0023**.

Worth generalising the lesson: **anything that shells `dbt` per node will not survive a project that
turns a config on globally.**

## 5. `assay onboard --compile` destroys `run_results.json`

`dbt compile` overwrites it, so after onboarding, `run_results.json` holds one entry and nothing
about which tests failed. Anything downstream reading test status from it sees a clean project.
Either warn before compiling, or restore the file afterwards.

## 6. `--dbt` defaults to `dbt`, which is not on PATH for a `uv` project

`onboard --compile` ran, printed `'dbt' is not on PATH. Pass --dbt with the command you use.`, and
then completed the rest — so the compile silently did not happen while the run looked successful.
It needed `--dbt "uv run dbt"`. Detecting `uv.lock` / `poetry.lock` beside `dbt_project.yml` and
suggesting the wrapper would remove a whole failed run. At minimum, make a failed compile louder
than one line above a success summary.

## 7. `assay check --json` returns an object, the table implies a list

`{coverage, parse_failures, unevaluable_tests, findings}`. Reasonable, but every example in the
README shows findings, so the first thing anyone writes is `for f in json.load(...)` and iterates
dict keys. One line in the docs.

---

## What worked, so it does not get changed by accident

- **`vocab` is the best idea in the tool.** Thirteen terms measured in this warehouse, and
  `traverse` immediately flagged `int_water_diligence -> water_rights on wdid` because the `wdid`
  entry told it a wdid identifies a structure and not a right. Knowledge written once reaching a
  question nobody wrote is exactly the promise, and it is kept.
- **`onboard` is the right first command.** Its "next" panel gave a priority order I actually
  followed, and running the description family inline for $0.0120 meant the first session had a
  real finding in it, not just an inventory.
- **Inline cost reporting on every judged command.** `24 calls, 54,839 tokens, $0.0023` removes the
  entire class of "should I risk running this".
- **Waivers requiring a reason.** Both of mine here were measured before being written, because the
  field made it obvious that "looks fine" would be the wrong thing to type.
- **`row_explanation` earned its keep.** It called wells `genuinely_wrong` at 0.50–0.70 and it was
  right for a physical reason: a 290-foot well with a water level of 26,018 feet. The
  `accepted_range` test that surfaced them catches 12 rows; the real invariant
  (`water_level_ft <= well_depth_ft`) holds on **708**. The check found a data defect *and* an
  inadequate test, from one sample of six rows.
- **`min_adjudications` refusing to let a question gate before it has verdicts.** This is the
  discipline most tools skip.
