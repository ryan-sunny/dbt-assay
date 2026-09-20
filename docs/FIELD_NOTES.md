# Field notes: one night on a 357-model warehouse

**Status: 1, 2, 3, 6 and 7 are fixed in 0.6.0; 4 and 5 were fixed in 0.5.1.** What each fix was is
noted inline. The notes are kept as written, because the report is the evidence.

Written from using `assay` 0.5.0 → 0.5.1 against `sunny_data` (357 models, 212 sources, 1,288 tests,
956 edges, DuckDB) on 2026-09-20. Everything below is something that happened, not something that
might. Ranked by what it cost or would have cost.

---

## 1. A custom family with a new name is never asked, and the docs teach that pattern

> **Fixed in 0.6.0.** `assay banks` prints an `asked by` column and names any family nothing
> asks, in red. The documented example now replaces a shipped family. A generic runner is
> **The generic runner landed in 0.7.0.** A family declares `subject:` and `assay ask` builds
> that state and asks it, whatever the family is called.

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

> **Fixed in 0.6.0.** `assay banks` prints an `about` column naming the state each family
> receives, and the overview carries a table of which family to replace for which subject.

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

> **Fixed in 0.6.0.** `Digest.pre_aggregated` records every relation collapsed inside a
> subquery or CTE, and `traverse` sends it. Measured on the hop named below: `cannot_tell`
> @0.24 became `same_thing` @0.41, and `silently_multiplied` stopped competing.

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

> **Fixed in 0.6.0.** A missing binary now reads the lockfile beside `dbt_project.yml` and
> suggests `uv run dbt`, `poetry run dbt` or `pipenv run dbt`, and a failed compile prints
> in red above the run with the count of models still unreadable.

`onboard --compile` ran, printed `'dbt' is not on PATH. Pass --dbt with the command you use.`, and
then completed the rest — so the compile silently did not happen while the run looked successful.
It needed `--dbt "uv run dbt"`. Detecting `uv.lock` / `poetry.lock` beside `dbt_project.yml` and
suggesting the wrapper would remove a whole failed run. At minimum, make a failed compile louder
than one line above a success summary.

## 7. `assay check --json` returns an object, the table implies a list

> **Fixed in 0.6.0.** The overview's command list says so inline.

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

---

## Second report, after 0.7.0

Three more, all fixed in 0.7.1.

- **`finding_when` matters more than its lint warning suggested.** Without it a family is asked,
  stored, and produces nothing. "That is valid" was true and undersold it: it is the dead-question
  problem wearing a new hat. It is an **error** now, acknowledgeable with a reason.
- **A question earned a `multi_hop` warning it did not have a version earlier**, kept deliberately
  because one hop could not distinguish the shapes. The lint was right and the trade was not
  expressible. `acknowledge: {multi_hop: "..."}` expresses it, a reason is required, and
  `assay banks` prints every acknowledgement rather than hiding it.
- **`subject: expression` yields thousands of subjects where `window` yields tens.**
  `assay ask` now prints the count and a cost estimate per family before asking anything, and
  refuses outright above `jev.max_spend_usd`.

- **The overlap you recorded rather than fixed**: `something_else` and `not_a_seniority_order` both
  fitting a window that ranks wildfire percentiles. You noted the lint has a rule that would catch
  it. **It does not** — word overlap scored that pair 0.25 against a 0.75 threshold, because they
  share a SITUATION and not a vocabulary. `assay banks --judge` now asks Jev whether any two
  options could both be right about one subject. It catches your pair, and it independently
  flagged `predicate_intent`, which had already been proven weak by hand.

---

## Third report, and what it changed

- **The state, not the wording** — the best observation made about this tool. A window subject
  carried the model, partition, order-by and position, and nothing saying what the ROWS ARE. Every
  subject now carries `what_one_row_of_this_model_is`: a declared key where one exists (239 of 356
  models on the test warehouse), else the model's own `group by`, else its columns. Measured on
  the wildfire case: `something_else` fell 0.79 → 0.67 and `not_a_seniority_order` rose 0.21 →
  0.32. **It helps and it does not settle that case.** The residue is real.
- **The judged lint flapping** — fixed and proven. It routes through the cache now, keyed on a hash
  of the question. Run one made ten calls; runs two and three made zero and produced byte-identical
  output. Only a reworded question is asked again, which is exactly when it should be.
- **The false negative on your own pair** — *not reproduced.* A reconstruction of v4 from your
  description flags at p=0.83–0.88 across five consecutive runs, and the v5 wording with the
  exclusion still flags at 0.74. The difference must be in wording not in the report. Paste the
  YAML and it can be run directly.

### And the thing worth underlining from that report

> 8/8 verdicts survived a criteria rewrite. That's the first time tonight I changed a question and
> could tell immediately that I hadn't broken what already worked — and it only cost eight
> `assay review` calls.

That is the whole argument for `min_adjudications`, made better than the docs make it. A verdict is
usually described as what earns a question the right to gate. It is also, and sooner, **a
regression test for the question itself** — the only way to change criteria and know what you broke.
Eight keypresses bought that.

---

## Fourth report: the state field cost two verified answers

`what_one_row_of_this_model_is` fixed the wildfire case and **cost 2 of 8** verified answers on a
family whose criteria reason about the **window's partition**: `water_reach_screen` declares
`(isf_key, water_right_id)`, the disqualifier mentions "a right id", and the model read a right id
in the MODEL's grain as satisfying a clause about the PARTITION, which is `isf_key` alone.
Rewording to compensate scored **5/8** — worse.

> "The field is doing exactly what it should and my criterion was relying on the model not knowing
> something."

That is the right diagnosis, so the fix is an opt-out rather than removing a field other families
need. **`subject_state: minimal`** in the bank.

### And the line that earned its own command

> The answer distribution barely moved — 79 `not_a_seniority_order` either way. A two-answer
> regression from a tool upgrade was invisible in the summary and only measurable because eight
> rulings were on record. Verdicts aren't just a regression test for the question; they're the only
> regression test you have for assay itself against a real bank.

`assay regress` replays every answer a person agreed with and reports what moved, exiting non-zero
when anything did. Verified both ways: 8/8 held on an unchanged bank at **zero calls** (unchanged
states are cached), and a forced move was caught and failed the command.

---

## Fifth report: the regression command could not replay the regression

- **`subject_state: minimal` restores 8/8**, and both moved answers come back at 0.51 and 0.59.
  The diagnosis is confirmed and the opt-out is the right shape.
- **`assay regress` skipped all eight verdicts and called it a pass.** `review` records the family
  as the id_prefix; `regress` looked it up by family name. It printed
  `0/0 confirmed answers still hold` with a non-zero skip count and exited 0.

  > "A green result computed over an empty set. Same family as everything else caught tonight — a
  > check that passes because it looked at nothing."

  Fixed on both sides of the boundary: the family resolves on either spelling, and **a pass with
  nothing replayed is now a failure** that names what was skipped. Verified by reproducing the
  exact shape — verdicts filed under the prefix now replay, and an unresolvable family exits 1.
- **The overlap false negative, reproduced with your numbers.** `no_overlap` 0.63 against an
  overlap mass of 0.35, not borderline. Your candidate was right: the sentence that routes to
  another option **by name**. A text check reads that as a disjointness guarantee; the answering
  model does not honour it. There is now a static rule — `option_routes_to_another` — which needs
  no model to be consistent about anything. It caught two shipped families, and both are real:
  `claim_alignment` (kept, with the measurement, acknowledged) and `column_role` (acknowledged as
  **not** separately measured, which is the honest record).
- **The margin is reported now**, not just the side of the line. A pass within 0.12 of the
  threshold prints as `near the line` and says to treat it as unsettled rather than clean.

---

# Third report: `practices --keys-only` proposes grains that cannot be tested

0.9.2, same 357-model warehouse. `assay practices` reports 21 models with no uniqueness test and
names **which columns a test should cover**, which is the thing dbt-project-evaluator does not do
and the reason to prefer it. Checked all fifteen it printed against the built tables:

```
9   name a column the model DOES NOT EMIT
4   fan out            fact_permit 5.81x, az_section_parcel_sales 2.23x,
                       int_water_call_exposure_basis 1.31x,
                       stg_water_resume_entry_facts 149.29x
2   empty table        vacuously unique
0   hold
```

Examples, model against its real columns:

```
int_address_crosswalk    proposed `parid`     emits building_key, geography, owner_name, …
int_water_call_exposure  proposed `wdid`      emits water_right_id, call_year, days_curtailed, …
water_isf_call_record    proposed `call_id`   emits calls_total, isf_rights, isf_calls, …
business_leads           proposed `business_key` among three; that one is not emitted
```

**The nine are free to fix and need no judgment.** Intersect the proposed grain with the model's
own output columns before printing it. A key column the model does not emit cannot be asserted by
dbt, so the recommendation is unactionable by construction — and a reader who trusts it writes a
test that fails to compile. This is `column_is_part_of_the_key`'s documented weakness
("can propose a grain that is not in the output") in its checkable form.

The remaining four are the judgment. `stg_water_resume_entry_facts → water_division` is the clearest:
seven distinct values over 1,076 rows, proposed as the grain of the model.

**What the command gets right, and why it is still the better tool here:** the ranking. It put
`int_address_crosswalk` and `int_assessor_parcels` at the top on 22 marts downstream, and those are
genuinely the models where an undeclared grain would hurt most. The blast radius is exact and free;
it is only the proposed key that needs the intersection.

## Closing `practice_exception` needs the evaluator built, and a partial build is silent

This project installs `dbt_project_evaluator` but only five `fct_` models are built
(`fct_documentation_coverage`, `fct_undocumented_models`, …). `assay practices` reported exactly one
standard-practice category — `recommend: documentation_coverage` — with nothing saying the other
checks were absent rather than clean. A run against a partially built evaluator looks like a project
with one practice issue.

---

## Sixth report: assay in the warehouse

> "Not a report you run, a relation you join to."

87 findings, 1,675 judgments with full distributions, 8 human verdicts, 573 per-edge facts, and an
`ops_assay_debt` model over them. The question it answers is one no findings list can:

```
 50 findings  41 models  worst 15 marts  0 ruled on   dead guard
 29 findings  25 models  worst 19 marts  1 ruled on   nondeterminism
  5 findings   5 models  worst 14 marts  0 ruled on   geometry
```

`marts` comes off the DAG, so that ranking is exact and free. **`ruled on` is the column to watch**
— 0 of 41 models carrying a dead guard has been looked at by a person. `min_adjudications` is
usually described as what earns a question the right to gate. Seen this way it is also a coverage
measure of the *reviewing*, which is the scarce thing.

Three fixes, all in 0.9.3:

- **`practices --keys-only` proposed 15 grains and 0 held**, 9 naming a column the model does not
  emit. It never intersected the grain with the output. Fixed, and a grain with **nothing** left
  after the intersection is now its own finding: a model that dedups on a column and then drops it
  cannot have its uniqueness asserted by anything downstream. Verified on `fact_sale`, which does
  `parcel_id as sale_id` — so the grain was tracking the pre-rename name. That is worth fixing on
  its own and has not been.
- **`dbt seed --select assay_*` matches nothing**, and it was the command's closing line — the one
  instruction a reader runs verbatim. It prints `path:seeds/assay` now, resolved relative to the
  dbt project, because `path:` is project-relative and an absolute path would have been a second
  instruction that does not work.
- **A partially built `dbt_project_evaluator` read as a clean project.** `collect` already returned
  the checks it could not reach and the caller threw them away as `_missing`. The same defect as a
  guard that scans nothing, in the one place that reports on other people's standards.

## 0.9.3 follow-up: the intersection is right, and "can be written" is not "would pass"

Re-ran `practices --keys-only` on the same warehouse. The fix works exactly as intended: the eight
whose grain is not in their own output are now their own finding, with the reason stated, and
`business_leads` annotates the partial case inline — *"geography, building_key (and business_key,
which it does not emit)"*. That reads better than anything I suggested.

Then checked the twelve it now says a test **can be written** for, against the built tables:

```
int_azcc_owners                owner_key                        0 rows   EMPTY
stg_pm_properties              building_key                     0 rows   EMPTY
int_water_call_exposure_basis  wdid                       172,695 rows   FANS OUT 1.31x
business_leads                 geography, building_key     73,608 rows   FANS OUT 1.56x
az_section_parcel_sales        section_id, county, …      944,604 rows   FANS OUT 2.23x
fact_permit                    permit_id                   45,959 rows   FANS OUT 5.81x
stg_water_resume_entry_facts   water_division               1,045 rows   FANS OUT 149.29x
```

**0 of 7 hold.** Somebody following this writes seven tests and five fail on the first run.
`water_division` proposed as the grain of a 1,045-row model is the clearest: seven distinct values.

**The batching already exists.** `which_have_failures` asks `count(*)` across hundreds of relations
in one statement. The same shape over `count(*)` versus `count(distinct <proposed grain>)` verifies
every proposal for free wherever the model is built, and turns the output into "here is a test that
would pass".

**And a proposed grain that does NOT hold is the stronger finding.** The model has no uniqueness
test and nobody knows what one row is — worse than a missing test, and currently invisible because
it is printed as a recommendation. That also makes the 149x case impossible to hand over as a
patch, which is the point of the command.

---

## Seventh report: "can be written" is not "would pass"

The columns fix made every proposal expressible in the output and **0 of 7 still held**.
`water_division` was proposed as the grain of a 1,045-row model with **seven distinct values** —
a reader following that writes a test that fails on its first run.

> "The command claims to hand over a patch rather than a nag, and a test that fails immediately is
> neither."

`practices` now COUNTS each proposal through your own dbt before recommending it, batched exactly
as `which_have_failures` is: one `union all` of `count(*)` against `count(distinct <grain>)`, not
one query per model. A proposal nobody could count is reported as **not counted**, never as
holding.

And the report's better point: **a proposal that does not hold is the stronger finding.** The model
has no uniqueness test *and* nobody knows what one row of it is. That is worse than a missing test
and it was invisible, printed as a recommendation. It has its own section now, ranked by how badly
the grain misses — the 149x case at the top.

### The `ruled on` column, taken further

> "It isn't just coverage of the reviewing; it's the only number in the whole system that can't be
> improved by the tool. Everything else responds to a release: findings move when checks improve,
> confidences move when states improve, marts moves when the DAG does. 0 of 41 moves only when a
> person reads SQL. And it's the number a good release makes look worse by finding more."

Checked against the store and it is exactly true — every other table responds to a release, and
`adjudications where source='human'` responds to nothing but a person. So it prints at the end of
every `assay check` now, with that property stated rather than hidden:

```
0 of 41 model(s) with a finding have been ruled on by a person.
This is the only number here a release cannot improve. Every other one moves when
assay gets better; this moves when you read SQL.
```

And the same reframe turned back on this project, where it is not flattering: **eight rulings, one
family of sixteen.** `assay regress` automates the catching. Nothing automates the ruling, and
nothing will.
