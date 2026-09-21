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
  `assay banks` prints every acknowledgment rather than hiding it.
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
  model does not honor it. There is now a static rule — `option_routes_to_another` — which needs
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

## 0.9.4: the counting works, and misses every model in a custom schema

Verified on the same warehouse. The new section fires and matches a hand count exactly:

```
2 model(s) where nobody knows what one row is
  fact_permit      14 marts · permit_id gives 7,910 distinct over 45,959 rows
  business_leads    9 marts · geography, building_key gives 47,144 over 73,608
```

**But three proposals came back `(not counted)`, and the split is by schema:**

```
counted        fact_permit, business_leads, int_azcc_owners, stg_pm_properties   main
not counted    stg_water_resume_entry_facts, int_water_call_exposure_basis       main_water
               az_section_parcel_sales                                           main_water_az
```

dbt's `+schema:` config puts those in custom schemas and the count resolves against the default, so
every one of them is unverifiable. **The 149x case this release leads with is among them** — it is
`stg_water_resume_entry_facts`, in `main_water`. On any project that separates domains by schema —
which is most of them past a certain size — the section is blind to exactly the models it was built
for. The manifest carries `schema` per node.

Two smaller ones from the same run:

- `holds: 0 rows, 0 distinct` on two empty tables. An empty table satisfies any grain; it is the
  vacuous pass, printed in the column a reader scans for green. `empty -- nothing to count` says
  the true thing.
- `business_leads ... (2x)` is 73,608 over 47,144, which is **1.56x**. Rounding up overstates a
  number someone acts on.

And the failure message earned its keep: when this project's dbt could not parse at all — a schema
YAML I had broken myself — it said *"could not count any proposed grain ... Nothing below has been
verified"* and marked every row `(not counted)`. Not one was reported as holding. That is the sixth
instance of the absent-reads-as-pass defect this codebase has found, and the first one where the
code already got it right before anyone looked.

### The alias defect, traced

`fact_sale` does `parcel_id as sale_id` and its grain came back as `parcel_id`. The cause was not
the rename: the grain was **inherited from a parent and never translated into the child's own
column names**. So a `practices` proposal named a column the model does not emit, and every
consumer reading the inventory was told the wrong key.

Fixed in one place rather than per route — `_in_this_models_own_names` translates through an alias
or an output expression, and where it cannot translate it **keeps the column and marks it**, since
dropping it would quietly narrow a key.

**28 of 87 proposals on that warehouse name a column their model does not emit.** They were
silently wrong and are now marked. Checking one by hand found the next gap rather than a false
positive: `dim_building` states its own grain as `(geography, building_key)` in a comment and
groups by exactly that — **inside a CTE**. The top-level `group by` is empty, so the grain fell
through to inheritance. CTE-level grouping is not read yet, and that is the real reason those 28
exist.

### Still open

- The overlap check's judged false negative: mass **0.35** against a 0.60 threshold, with four
  shipped families within 0.11 of the line. The static `option_routes_to_another` rule catches the
  known cause; the judged check does not.
- CTE-level `group by` as a grain route, which is what the 28 above are really asking for.

## 0.10.1: `patch` would write two tests whose only evidence is an empty table

`assay regress` holds 8/8 at zero calls across the 0.9.x → 0.10.1 upgrade. `patch --dry-run`
refuses 18 of 21 with reasons that read exactly right — *"its grain is not in its own output"*,
*"NOT COUNTED -- and an uncounted grain is not a passing one"*, *"it does not hold: 45,959 rows,
7,910 distinct (6x). A test here fails on its first run"*.

**Of the three it would write, two are empty tables:**

```
int_azcc_owners      owner_key                         0 rows, 0 distinct
stg_pm_properties    building_key                      0 rows, 0 distinct
lead_volume          geography, city, zip, …      13,081 rows, 13,081 distinct
```

The command's promise is that anything it writes provably passes. An empty table passes any
uniqueness test, so two of the three proofs are vacuous — and **writing is worse than printing**,
because a committed test that passed on zero rows is indistinguishable in the repo from one
verified against real data. This project carries 1,288 tests and `test_cannot_fail` is 50 of its 87
findings; `patch` would add two more of that kind, from the command built to reduce them.

`0 rows` already reaches the decision — refusing it, or writing it with the count in the generated
comment, both close this. Refusing seems right: an empty table is not evidence of a grain, it is
absence of evidence, which is the distinction this codebase has now drawn six times elsewhere.

## `uvx dbt-assay mcp` cannot start — the documented line omits the extra

```
claude mcp add assay -- uvx dbt-assay mcp …            ✘ CONNECTION_CLOSED
claude mcp add assay -- uvx --from "dbt-assay[mcp]" …  ✔ Connected
```

A bare `uvx` installs the base package, and `serve()`'s import guard raises the right message — but
over stdio nobody sees it, so the client reports a closed connection with no cause. Two fixes, both
cheap: document `dbt-assay[mcp]`, and have `assay mcp` check the import before opening the
transport so the error lands on the terminal instead of down the pipe.

(Also: `uvx dbt-assay <cmd>` needs `--from`, since the package name and the console script differ.
`uvx dbt-assay skill` prints *"Use `uvx --from dbt-assay assay` instead"*, which is good — the MCP
line in the docs is the one place that guidance is missing.)

---

## Eighth report: two from running it, and one found by the fix

- **`patch` offered to write tests for EMPTY tables.** 0 rows and 0 distinct satisfies
  `distinct >= rows`, so two of the three it would have written pass because there is nothing in
  the table. Committed, such a file is indistinguishable from a verified one — **which is exactly
  the ambiguity `test_cannot_fail` exists to find, and that check is 50 of this project's 87
  findings.** assay would have generated the defect it is best at detecting. The count was already
  in hand; only the conclusion drawn from it was wrong.
- **The documented MCP line could not start.** `uvx dbt-assay mcp` skips the optional extra, the
  import guard raised the right message, and stdio already owned the channel — so the client saw
  `CONNECTION_CLOSED` and the explanation went nowhere. The check moved out of `serve` and runs
  before the transport opens, so the error lands on a terminal. The documented line is
  `uvx --from 'dbt-assay[mcp]' assay mcp`.
- **And the fix for that was broken by rich.** The message naming `dbt-assay[mcp]` printed
  `dbt-assay` — rich read `[mcp]` as a style tag and dropped it. An instruction destroyed by the
  defect it was written to fix, with a note about square brackets already sitting in
  `inventory.py`.

### assaying assay

`tests/test_assay_on_assay.py` applies assay's own rules to assay, because every one of them was a
real defect here first. On its first run it found the same `[mcp]` bug in a second place I had
missed — and three false positives, where `models[uid]` in an f-string is Python subscripting that
rich never sees. Narrowed, which is the same lesson as always: a guard that matches too much is the
mirror of one that matches nothing.

> "Every place tonight where the tool was right, code did the deciding and judgment did the
> noticing — including the two empty tables, where the count was already in hand and only the
> conclusion drawn from it was wrong."

That is the sharpest statement of the architecture anyone has made, and it is now the first thing
the overview says about the two tiers: **the structural tier is what makes the judged tier safe.**
`marts` being exact and free is why a judged finding can be ranked without trusting the judgment,
and why `patch` can refuse a proposal by counting rather than by asking.

# Fourth report: the agent is the executor, and MCP gives it no way to act

The framing in the docs is that assay cannot generate a question, decide its criteria are right, or
rule on its own findings — and that all three need a person. That is true of assay-the-CLI and it
undersells the architecture, because **the agent is the thing that does those three**, and MCP is
how it gets the context to do them well.

One night on this warehouse, everything below came from an agent reading assay's output:

```
15 vocab terms      each measured in the warehouse before being written
 3 question families one replacing a shipped family, two via `ask`
 8 verdicts          every non-default answer read against the SQL
 2 waivers           measured first, reason recorded
 1 real bug fixed    a fact table's grain that lived only in a comment
```

That is the loop working. It was not automated and it did not persist through the tool — it
happened because a human-facing CLI was available in the same session.

## The blocker: all ten MCP tools are read-only

`contract`, `lineage`, `blast_radius`, `findings`, `changed_contracts`, `practices`, `rebase`,
`violations`, `claims`, `traversal`. An agent can see every finding, read every contract, check its
own edits — and **record nothing**. The eight verdicts above went in through the CLI. An agent with
only MCP cannot record one.

Which means the binding constraint — ruling, the number no release can improve, 0 of 69 models
here — is the one thing the agent could scale and the one thing it cannot reach.

## The schema is already right for it

`adjudications` carries `decided_by` and `source`, and CLI rulings land as `source='human'`. So an
agent tier can accrue without contaminating the baseline: **`regress` keeps anchoring on
`source='human'`**, a thousand agent rulings and eight human ones stay distinguishable, and the
agent tier becomes the thing a person spot-checks rather than the thing anyone trusts blind. That
property is what makes agent writes safe, and it exists today.

## Four writes, in value order

- **`rule(subject, question, verdict, note)`** — by far the highest value. It is the only write that
  moves the number nothing else can, and the note field already exists to carry the reasoning.
- **`propose_vocab(term, means, implies, evidence)`** — with evidence required, the way a waiver
  requires a reason. Vocab reaches every question, so a wrong term steers every answer at once;
  requiring the measurement is what stops an agent writing plausible-sounding domain knowledge.
- **`ask(family, select, limit)`** — running a question family is CLI-only today, so an agent can
  write a question and never see it answered.
- **`waive(model, question, reason, until)`** — same reason-required discipline as the config.

Every one of those is a decision a person should be able to review afterwards, which is what
`decided_by` and `source` already make possible. The read tools make an agent well-informed. These
four make it useful.

---

## Ninth report: the read-only MCP was the thing in the way

> "Everything else compounds on its own: more checks find more, better states judge better, the
> warehouse accrues. Rulings don't compound, because the only thing that can produce them at scale
> can't write them down."

That is the argument, and it is right. `rule(subject, question, verdict, why)` is in the MCP
surface now.

**Filed as `agent`, and it cannot gate, cannot satisfy `min_adjudications`, cannot anchor
`regress`, and cannot move the ruled-on number.** All four already filtered on `source = 'human'`,
which is why this was safe to add at all — and it is the point rather than a limitation. The
ruled-on figure is the one number here nobody can game, and an agent able to raise it would
destroy exactly the property that makes it worth printing.

What it does is triage, and the loop only closes because the ruling reaches the person:
`assay review -i` opens with *"1 of these already have an agent's reading"* and prints the reason
beside the finding. `assay check` reports the two numbers separately.

A reason is required. A ruling nobody can check is not evidence, which is the thing this tool
exists to object to.

### And adding it broke the binary while the suite stayed green

`mcp_server` reaching back into `cli` for one helper is a circular import. `assay` died on startup
and **367 tests passed**, because pytest imports the modules separately and the entry point does
not. There is a guard now that imports the console script in a fresh interpreter, proved by
restoring the cycle and watching it fail.

### The ceiling, stated once

> "100% evaluated isn't a thing any of this delivers. What it delivers is that nothing you've
> looked at can silently change, and that the list of what you haven't looked at is itself a number
> in the warehouse."

That is the honest description of the whole tool, and it is better than anything in the docs. 69
models with a finding, 0 ruled on. Ugly, accurate, and visible.

## 0.11.0 in use: 23 agent rulings, and the one that found something

`rule()` works and the separation holds exactly: `adjudications` reads `agent 23, human 8`,
`regress` still replays 8/8 off `source='human'` and exits 0, and `check`'s footer says an agent
cannot raise the ruled-on figure. Nothing about the property needed testing twice.

**What the rulings contain is the part worth looking at.** Two examples from this pass:

*Six findings, one defect.* `stg_gilbert/mesa/tempe/peoria/maricopa/tucson_permits` are each a
single line calling the `permit_stg` macro, so they share one CASE at `macros/permit_stg.sql:28-32`
returning three values, tested against a shared YAML anchor written for Denver that lists four. The
test cannot fail, and on the AZ path one listed value — `Change of Use` — is unreachable. Filed with
the open question attached rather than as a claim: whether AZ change-of-use permits exist in the raw
feeds and fall through to NULL.

*A default hiding what the test was for.* `water_rights.dwr_analysis_status` is
`coalesce(ca.dwr_analysis_status, 'not looked up')`, so `not_null` can never fire — and counted,
**170,730 of 172,695 rows (99%) ARE that default**. The test passes on every row while saying
nothing about whether any analysis happened. That is not a dead guard, it is a live guard pointed at
the wrong column, and `test_cannot_fail` is what surfaced it.

## The lock error names the wrong cause

Nineteen rulings did not land because I had a read-only DuckDB connection open in the same process.
`rule()` was honest — it returned an error every time, and I lost them by not checking the return,
which is my mistake and the same shape as everything else in this file.

But the message is:

```
no store to write to. Run any judged command once to create one.
```

The store exists. It is locked, by this process. That message sends someone to create a file they
already have. `duckdb` raises something specific for a conflicting lock and the two cases want
different sentences — "not created yet" and "another connection holds it, close your reader" are
opposite fixes.

# Fifth report: every finding read, and what 99 rulings say about the checks

All 99 models carrying a finding now have an agent reading attached. `agree 70, disagree 12,
unclear 17`, `human 8` untouched, `regress` still 8/8 off `source='human'` and exiting 0. The
separation held through a hundred writes without needing to be thought about again.

**The verdicts are a measurement of the checks, not just of the warehouse.** Sorted by what they say:

## `hop_multiplies_rows` — 10 of 23 disagreed, and the cause is structural

A **union-member edge cannot multiply**. One parent row becomes exactly one child row; the child
having more rows than any single parent is a different thing. Nine models here union their parents
and several then aggregate on top — `dim_business` ends `group by geography, business_key,
building_key`, `int_water_section_match` uses `distinct on (entity_type, entity_id)`. Both are
`deliberately_coarser`, the opposite verdict, at 16 and 19 marts.

Two more were lookup joins onto keys that are unique but undeclared — `int_water_streamflow_summary`
2,387 rows / 2,387 distinct `abbrev`, `geo_places` 726/726, `city_aliases` 6/6, `freshness_windows`
25/25. Right about the project, wrong about the data, the same shape as the other join findings.

**Suggested discriminator, and it needs no judgment:** an edge whose parent is referenced inside a
`UNION` arm is never silent multiplication. That is readable from the AST.

The remaining 13 are `unclear` on purpose: the child contains a `GROUP BY`/`DISTINCT`/`QUALIFY`
somewhere, but proving it sits on *that* hop needs the path read, and I checked the file not the
path. Recorded as a question rather than a claim.

## `bbox_as_radius` — both disagreed, and the discriminator is mechanical

`stg_blm_plss_sections` builds `ST_MakeEnvelope(cx0, cy0, cx1, cy1)` from bounds **stored on the
same row** and intersects a land-grant polygon with it — the envelope is a grid cell and the box is
the intended shape. Its own comment says so. The check's detail argues "a box is not a circle",
which only applies when the envelope approximates a radius.

**An envelope whose corners are columns is a tessellation; one whose corners are a point ± a
constant is the proximity case this check is for.**

## `test_cannot_fail` — 40 agreed, and one of them found a live bug

Every one is correct as stated. Three patterns, all verified against source: a CASE whose branches
are all in the accepted list, a `unique` test on the only `GROUP BY` key, and `not_null` on a
`COALESCE` ending in a literal.

The third is not a dead guard — it is a **live guard pointed at the wrong column**:

```
water_rights.dwr_analysis_status      170,730 of 172,695 (99%) are the default 'not looked up'
az_section_summary.n_well_depth        95,650 of 114,305 (84%) are 0
az_section_summary.parcel_count        85,264 of 114,305 (75%) are 0
water_section_summary.rights_late…     45,842 of  64,433 (71%) are 0
water_parcels.irrigated_acres…      2,623,519 of 2,732,101 (96%) are 0
```

The `not_null` test passes on every row while saying nothing about whether any lookup happened. The
coalesce conflates "none" with "not measured". **`test_cannot_fail` is the check that surfaces this
class, and the finding as printed undersells it** — "this test cannot fail" is true and "this
default is 99% of your rows" is the sentence somebody acts on. The count is one query away.

Also worth naming: `water_outreach_agents.contact_role` is `case when max(w.email) is not null then
'brokerage_office' end` — one branch, no else. An `accepted_values` test on a **constant**, which
cannot fail by construction rather than by today's data. A distinct and stronger sub-case.

## `description_contradicts_the_code` — 4 agreed, 5 unclear, and it found the best defect

```
int_water_parcel_sections   says "by centroid"   code says "ST_PointOnSurface, NOT ST_Centroid,
                                                  AND THIS WAS A REAL BUG ... 369 of 40,000
                                                  parcels (0.92%) have a centroid outside their
                                                  own polygon"
int_az_parcel_sections      THE SAME WRONG SENTENCE, copied into Arizona with the model
int_discovered_contacts     says "one best contact per BUILDING"   code says "Grain = one row per
                                                  (building, occupant NAME) -- we keep ALL tenants"
stg_co_health               says "food establishment"   code routes five programs
```

**The description documents the bug that was fixed**, and the copy propagated it to a second region.
`int_discovered_contacts` is the dangerous one: 11 marts read a sentence promising a grain the model
deliberately does not have, so anything joining on `building_key` alone fans out.

The 5 `unclear` are where the generic detail line — *"A description is written once and the SQL
changes around it"* — does not say **which clause** contradicts. On `water_address_sections` the
model description and the SQL header are the same sentence verbatim, so the finding must rest on a
column description or the body, and there is no way to tell from the output. **Naming the contradicted
clause would convert most of those 5 into rulings.**

## What this says about the loop

70 agrees are the checks working. 12 disagrees are two fixable structural blind spots — unions and
tessellation envelopes. 17 unclears are almost all one thing: **the finding does not carry enough to
settle it**, and in every case the missing piece is something assay already has (which hop, which
clause, what the count is).

The ratio is the useful output. A check with a high disagree rate has a blind spot; a check with a
high unclear rate has a reporting gap. Neither is visible without someone reading, and the reading
is now in the warehouse next to the finding.

---

## Tenth report: every finding on a 357-model warehouse, ruled

99 findings, all read. `agree 70 / disagree 12 / unclear 17`, and `regress` still 8/8 off
`source='human'` after a hundred agent writes — the separation held without needing a second
thought, which was the whole bet.

`docs/REVIEW.md` is the ranked list. Items 1–3 are fixed:

**Two structural blind spots caused every single disagreement**, and both are readable from the
AST with no judgment and no call. That is the part worth sitting with: the judgment was allowed to
be wrong about something a parser settles exactly.

- **A union member cannot multiply** — ten of twelve. `dim_business` (16 marts) unions eleven
  staging feeds and was reported `silently_multiplied`. One parent row is one child row; the child
  having more rows than any single parent is the union. `Digest.union_members` now records it, the
  edge state carries it, and `hop_multiplies_rows` REFUSES the finding outright rather than
  believing a judgment about it. Verified: 17 union arms found on `dim_business`, 4 on
  `int_water_section_match`, 7 on `fact_sale`.
- **An envelope built from stored bounds is a tessellation, not a radius** — the other two. Bare
  columns are a grid cell that already exists in the data; arithmetic on a point is a box standing
  in for a circle, which is the only case "a box is not a circle" argues about. `bbox_as_radius`
  went 3 → 2 on the test warehouse and both survivors are `point_plus_offset`, in the two models
  that rank by degrees.

**All seventeen unclears were one problem** and in every case the missing piece was already
computed. `hop_multiplies_rows` now says where the collapse was found *or that it was not found on
this path*, which is as useful. `description_contradicts_the_code` names the prose it judged, since
it sends the schema.yml description and the in-file comments together and a reader could not tell
which one the contradiction was in.

**`test_cannot_fail` now reports the live bug as well as the dead one.** Every finding was correct
as stated, and a `not_null` on `COALESCE(x, <literal>)` is a live guard pointed at the wrong
column. `assay tests --count-defaults` counts the share that ARE the default, in one batched
statement. Run against the warehouse it reproduced all five reported numbers and found five more:

```
water_rights.dwr_analysis_status          170,730 of   172,695  (99%)  'not looked up'
water_parcels.irrigated_acres_on_parcel 2,623,519 of 2,732,101  (96%)  0
water_section_summary.wells_household_only   55,230 of    64,433  (86%)  0
az_section_summary.n_water_level             96,624 of   114,305  (85%)  0
```

Nine of ten are at least a third default. The sub-case has its own name now too:
`water_outreach_agents.contact_role` is a CASE with one branch and no ELSE, so its
`accepted_values` test cannot fail **by construction** rather than by today's data.

### Items 4 and 5 of the same report

**A custom schema made every model in it uncountable.** 192 of that project's 358 models live in
`main_water`, `main_water_az` or `main_elementary`, and a bare model name resolves to the default
schema — so the count did not fail loudly, it failed as an absence, which is the shape this
codebase keeps having to catch. `Schema.relation` already held the qualified name. Measured, same
warehouse, same command:

```
OLD (bare names): counted  6 of 12   (0 in a custom schema)
NEW (qualified):  counted 12 of 12   (6 in a custom schema)
```

The 149× case the release leads with was one of the six.

**A ratio that does not hold printed as one that does.** `business_leads` is 73,608 rows over
47,144 distinct and printed `2x`; `int_water_call_exposure_basis` is 172,695 over 132,175 and
printed **`1x`** — a grain that fails, rendered as a grain that holds. Rounding up overstates a
number somebody acts on and rounding down hides the finding, so a ratio above one now never prints
as one, however close: `1.56x`, `1.31x`, `5.81x`, `149x`.

**The MCP `practices` tool raised ValueError while 378 tests passed.** `primary_key_patches` grew
a fifth element for the not-emitted case, the CLI was updated and the tool was not. Same shape as
the circular import that killed the binary: a surface nothing calls is a surface nothing checks.
Its guard now calls the tool.

**A locked store reported itself as a missing one.** "no store to write to. Run any judged command
once to create one" — advice that cannot work, for a file that is right there, and the judged
command fails for the same reason. DuckDB is single-writer; the message says so and names the fix.

**`rule(..., decided_by='<name>')` and `review_queue()`.** A person sitting there saying "that one
is wrong, it is a union" is worth recording by name, so a reviewer can tell *the agent thinks*
from *they said and the agent typed it*. It is still filed as `agent`: the one field an agent fills
in itself cannot be the field that decides authority. And `rule` claimed to put things "in front of
whoever reviews next" with no way to see that queue, so an agent could write a hundred rulings and
never learn whether one had been read. Agent-read items rank first, because confirming a reading is
one keypress and a cold finding is not.

### The last three of the same report

**`practices` printed `holds: 0 rows, 0 distinct` for the table `patch` refuses as EMPTY.** Same
model, same run, opposite readings of the same two integers, and the word `holds` sat in the column
a reader scans for green in the command `onboard` points at first. `0 distinct < 0 rows` is false,
so an empty table fell through to the success branch of a condition that never considered it.

The refusal was already written and already right. It was written in one place, so the other
surface kept its own reading. `practices.grain_verdict` now holds it and both callers read it. On
the warehouse the writable count went 3 to 1, with `int_azcc_owners` (19 marts) and
`stg_pm_properties` (9 marts) moved into their own block.

**The wrapper hint looked beside `dbt_project.yml`, which is the wrong directory.**

```
dbt_project.yml   ./transform/dbt_project.yml
uv.lock           ./uv.lock            <- repo root, one level up
```

That is the normal layout for a repo that is not only dbt, so the hint that exists to prevent a
wasted run found nothing and printed nothing. It walks up to the repo root now and stops there: a
lockfile above the repo is somebody else's project, and suggesting its wrapper is worse than
suggesting nothing.

**The dbt-binary flag was spelled two ways and flipped between releases.** `--dbt` on five commands
and `--dbt-bin` on four, and `practices` took `--dbt` on 0.9.4 and `--dbt-bin` on 0.13.0, so a
script written against one release broke on the next. Every command accepts both now, `--dbt` is
the documented spelling, and the guard ENUMERATES the app rather than naming commands, because a
guard with a hardcoded list stops seeing the thing it was written for.

---

## `assay effectiveness`: the tool could not measure its own improvement

Every question rewrite in this project was found by a person reading output, and its effect was
typed into a markdown file by hand. The verdicts that prove it existed the whole time. The store
threw the older ones away, because `adjudications` was keyed on `(subject, question)` and a
re-ruling OVERWROTE rather than joining.

So `prompt_version` is in the key now. A verdict is about a VERSION of a question, not about the
question forever.

**A structural check has a version too, and it is assay's own.** 99 of the 107 rulings on the field
store are structural findings, where no question is asked and there is no prompt to version. All of
them would have read `(unversioned)` and no before-and-after could have started until new rulings
came in. The `runs` table records which assay was running and when, so the version being ruled on
is the latest run at or before the ruling. A lookup, not a guess.

**The first backfill rule was correct and answered nothing.** It stamped a version only where
exactly one version had ever produced that answer. On the real store that backfilled **zero** rows:
the eight human verdicts there were all on a question that had been rewritten, which is precisely
the case this table exists to measure. Using the clock instead, the version a person saw is the
latest one that produced that answer before they ruled. 107 of 107.

Run against the field store, the number points straight at the right place:

```
family                    version         ruled   agreed        unclear  open
arbitrary_pick            assay.0.11.0       24   24/24 (100%)        0     0
bbox_as_radius            assay.0.11.0        2     0/2    (0%)       0     2
description_contradicts   assay.0.11.0        8     4/4  (100%)       4     0
hop_multiplies_rows       assay.0.11.0       23    0/10    (0%)      13    10
join_fans_out             assay.0.11.0        4     4/4  (100%)       0     0
test_cannot_fail          assay.0.1.0         4     4/4  (100%)       0     0
test_cannot_fail          assay.0.11.0       34   34/34  (100%)       0     0
water.prio                water.prio.v4       8     8/8  (100%)       0     0
```

The two families 0.12.0 fixed are the two at 0%. Nothing in the tool could have said so before,
and a person reading 99 findings had to work it out.

**`unclear` is never in the agreement denominator.** Disagreement means the criteria are wrong.
Unclear means the subject state does not carry what the question asks about, which is what all
seventeen unclears on that warehouse turned out to be: every one fixed by putting something in the
state, none by rewording an option. Averaging them hides which repair to make.

**A disagreement is open until somebody agrees at a DIFFERENT version.** So the count falls only
when a question changed and a person re-read it. A release cannot lower it, which is the same
property the ruled-on figure has.

### And the other half of the gate

`min_adjudications` asks whether enough people looked. It never asked whether they AGREED, and a
count of wrong answers is still a count: a question twenty-five people read and disagreed with
twelve times satisfies that floor and has earned nothing. `gating.min_agreement` is the rate, and
it is measured only on verdicts given against the version shipping now. It ships at 0, which is
off, because a floor set before anything was measured is a guess wearing a number. Run
`assay effectiveness` and pick one from the rates you have.

An unmeasured rate arrives as `None` and cannot refuse anything. An absent measurement must never
read as a failing one.

---

## The third structural blind spot, found by the effectiveness number on its first run

`assay effectiveness` put `hop_multiplies_rows` at 0 agreed out of 10. Reading the reasons, eight
were the union case fixed in 0.12.0 and two were this, written by the agent that ruled them:

> FALSE POSITIVE, MEASURED. The flagged hop is a LEFT JOIN to a lookup that is unique on the join
> key: `int_water_streamflow_summary` 2,387 rows / 2,387 distinct abbrev; `geo_places` 726/726
> zip5; `city_aliases` 6/6; `freshness_windows` 25/25 lead_category. A join onto a unique key does
> not fan out. **These parents carry no declared uniqueness test, which is why assay cannot see
> it.**

That last sentence is the defect stated exactly. assay believed the PROJECT instead of the
WAREHOUSE, and the machinery to settle it was already in the codebase: `verify_grains` batches
`count(*)` against `count(distinct key)` through the project's own dbt. The same arithmetic pointed
at the parent of a flagged hop is `practices.verify_join_keys`.

Two halves, and only one costs anything:

- **Declared uniqueness is free and always applies.** `relate.declared_keys` already reads every
  `unique` and `unique_combination_of_columns` test. If the parent's declared key is covered by the
  join columns, the hop cannot fan out and the finding is refused.
- **Counted uniqueness needs the warehouse**, so it is `assay check --verify`. An uncounted key is
  not a unique one, which is the rule this codebase keeps relearning in the other direction.

On the field warehouse: **34 hop findings to 32**, retiring
`stg_cdss_surfacewater_stations -> water_stream_gauges` (one of the two that were ruled) and
`stg_wbd_huc8 -> water_eco_basin` (one nobody had read yet).

### Two things went wrong building it, both in the reporting

**It printed 9 hops retired against 2 findings removed.** The count was of parents marked unique,
and a parent can match a hop the union rule already refused. A number that reads like a result has
to be one, so the caller measures the finding delta instead.

**And the message broke `--json`.** It printed before the document and every parser downstream got
`Expecting value: line 1 column 1`. Exactly the class of rich eating `[mcp]` out of the instruction
telling somebody to install it. The count now rides INSIDE the document as `verified`, where a
machine can read it, and the guard parses the output rather than grepping the source.

---

## `assay disagreements`: twelve rejected findings, how many separate bugs?

Nine releases of this project came out of a person relaying "these ten findings are wrong, and
here is why" out of a terminal. The verdicts and the reasons were in the store the whole time and
nothing read them together.

**Three shapes were specced and only one had a corpus**, which measurement settled before a line
was written:

| shape | eligible cases on the real store |
|---|---|
| two rulings CONTRADICT each other | **zero**. No family had both an agree and a disagree |
| a reason does not MATCH its finding | no negative control available |
| two reasons are the SAME DEFECT | **12 of 12** disagreements carried a reason |

The first was the one recommended before measuring. It would have been a check that matches
nothing, which is the class this project has found eight times in other people's code.

**Code clusters first and for free.** Identical first sentences are the same defect and no
judgment is needed to say so. On the field store that alone took 12 disagreements to 4 groups,
and those pairs are never sent to be judged.

**Then the delta, which is the measurement of whether asking was worth anything:**

```
12 open disagreement(s) -> 3 distinct defect(s)
  8 ruling(s)  hop_multiplies_rows   a union member cannot multiply        (fixed 0.12.0)
  2 ruling(s)  hop_multiplies_rows   the join key is unique in the data    (fixed 0.15.0)
  2 ruling(s)  bbox_as_radius        the envelope is a grid cell           (fixed 0.12.0)

code alone found 4 group(s); judgment merged 1 more pair(s) that were worded differently.
17 questions, 13,930 input tokens, $0.00059.
```

The merge it made is the right one: two `bbox_as_radius` rulings, one saying *"the envelope is a
grid cell from stored bounds, not a radius approximation"* and the other *"this envelope is not a
distance proxy, it is a GRID CELL"*. No shared opening sentence, one defect.

And the merge it REFUSED is the better result. Sixteen of the seventeen pairs put a union reason
next to a unique-key-join reason, both about a join, both about `hop_multiplies_rows`, and it said
no to every one. They are two separate repairs and the tool shipped them as two separate releases.

**It never closes anything.** A disagreement closes when the check or the question changed and a
person re-read it, which is what `assay effectiveness` counts. A release that could resolve its own
disagreements would make every number downstream of them decoration.

**And a cluster of agent rulings says so.** All twelve here are an agent's. That is a hypothesis
about a check, not a verdict on it.

### The architecture change under it

`subjects.build(kind, project, digests, schema, ...)` assumed every subject is a dbt object, and a
ruling comes from the store. The choice was a seventh parameter five kinds ignore, or a bundle.
The bundle, `SubjectSource`, because the case that needs it is the one being written: an optional
argument added for a caller that already exists is a decision deferred rather than avoided. Two
call sites, both internal, and a custom family declares `subject:` in YAML and never touches it.

---

## Round three: the write path orphaned every ruling it took

`assay effectiveness` was built to close the loop. The tool that feeds it was throwing the input
away.

`findings.subject` is `model.sunny_data.int_azcc_owners`. `rule()` was handed the bare
`int_azcc_owners`, answered **`recorded: true`**, and wrote 99 rows that join to zero findings. The
visible symptom was `review_queue()` returning twenty items with no agent readings attached,
directly underneath its own note promising that findings an agent has read come first.

**Ninth instance of one fact, two spellings, silent when they disagree** — this time in the write
path of the feature built to close the loop. There is one spelling now, and anything else is either
resolved out loud (`resolved_as` comes back in the response) or refused. A write that cannot be
joined back is not a write, and reporting it as one is worse than failing.

`assay review --repair` re-points the ones already written. On the field store: **99 of 99**, and
`review_queue` went from 0 of 20 items carrying a reading to 8 of 8. It resolves and never guesses:
a name matching two models, or none, is reported and left exactly as it is, because a ruling moved
to the wrong model is worse than an orphaned one — it would look attached.

### And the ruling was coarser than the thing it ruled on

`rule(subject, question)` could only say "this model, this check". `az_section_summary` carries
eight `test_cannot_fail` findings and one keypress answered all eight.

It is also how a **correct** finding got ruled wrong. `dim_business` was read as a union false
positive, which is true of four of its six edges. The other two are `crime_leads` and
`buyer_leads` reading it on `(geography, building_key)` against a grain of
`(geography, business_key, building_key)`, measured at 69,966 rows over 47,178 pairs — a 1.48x
fan-out. `silently_multiplied` was right about those two and the model-level verdict covered them
anyway.

Both fixes are one change, as reported: `Finding.id` is a stable handle, `findings()` and
`review_queue()` hand it back, and `rule(finding=...)` takes it.

**The id nearly reintroduced the bug it fixes.** A first version hashed check, subject and summary,
and four `arbitrary_pick` findings on one model shared all three. Adding evidence fixed that and
broke something worse: a judged finding's evidence carries a PROBABILITY, which moves whenever the
model or the state moves, so every ruling would orphan itself on the next run. Measurements are
excluded from the handle — a float is a probability or a share, and reach is a property of the DAG
rather than of the defect. 94 findings, 90 distinct ids, and the four collapses are the same defect
reported once per window function.

---

## Round four: the agent could reach 20 of 162

`0.17.0`'s repair landed as intended on the field store:

```
99 subjects re-pointed
rulings joining to a finding    0  ->  249
review_queue() with a reading   0 of 20  ->  20 of 20
```

And the author corrected their own last report, which is the more interesting half. The summary
format is `parent -> child`, so the three findings under `dim_business` ARE its union arms and the
original disagree was right. The 1.48x fan-out belongs to `crime_leads` -- which had been marked
**unclear**, with the reason *"the model contains a collapse somewhere."* It does. The collapse is
in the parent, not on this hop. The hedge named exactly the thing that had not been checked and was
still wrong.

So of twelve disagrees one was fine, and of seventeen unclears one was a real finding ducked. That
is a better argument for `unclear` being its own bucket than anything the docs say about it.

### The gap, and it is the tenth instance of one shape

The latest run held **162 findings across 7 families**. MCP `findings()` returned **20 across 2**.
`hop_multiplies_rows` (58) and `description_contradicts_the_code` (18) were absent entirely,
reachable only by querying the store by hand.

`check` added the judged stream itself and `findings_for` never did. Two paths computing one fact,
so "what is wrong with this project" had two answers depending on which surface you asked -- and
the surface an agent uses had the smaller one. The two families it could not see are precisely the
ones worth an agent's time: a description contradicting its code needs prose read against SQL,
which is what an agent is for and a parser is not.

`live.all_findings` is the one path now and both callers use it. The guard RUNS both surfaces and
compares them, because a guard that read the source would not have caught the original.

### And the limit was hiding families silently

The second cause, underneath the first. A default limit of 20 truncating by blast radius buries
whole families, and nothing said so. `findings()` now returns `showing: "20 of 142"` and a
per-check breakdown beside the results, and takes `check=` to read one family end to end. A surface
that returns a subset without saying so is the same defect as a scanner that matches nothing and
reports a pass.

---

## The completeness tier: coverage of what the project itself declares

From `docs/RESPONSIBILITIES.md`, written after a night of using assay as the engineer on a
357-model warehouse. Five of the nine responsibilities were covered, one partial, two deliberate
exclusions, and completeness was called the gap.

**The table overstated it.** Three of the five things listed as in-scope already shipped:
`assay tests --count-defaults` (0.12.0), the empty-model refusal in `practices` (0.13.1), and
`assay scan`. The accurate statement is that assay computed completeness facts across three
commands and nothing collected them.

**Two checks were genuinely missing, and both are free.** Verified on the field warehouse:

```
source_reaches_nothing      5   declared, loaded every run, no model and no test refers to it
source_only_a_test_reads    2   you are paying to test data nothing consumes
```

Reported apart on purpose. Different populations, different facts.

**Freshness is tiered.** `source_freshness_undeclared` is SILENT when `dbt_project_evaluator` is
in the project, because it ships `fct_sources_without_freshness` and printing the same finding
twice is worse than not printing it: a reader cannot tell whether two tools agree or whether one
is echoing the other. `source_freshness_stale` reads `sources.json`, which nothing else here did,
and the file's absence is reported rather than read as every source being current.

### The row-loss check, and why "free from the DAG plus two counts" was wrong

`relate` tracks the COLUMNS dropped at a boundary and has no notion of rows. So `hop_multiplies_rows`
had no mirror image, and a hop turning 2,606 documents into 463 was invisible.

Most edges drop rows on purpose, so a raw ratio fires on half a DAG on day one. The refusals
shipped in the same commit: the child filters, the child aggregates, the child unions. That took
358 models down to 45 candidate hops.

**And the fourth refusal was already computed and the first version did not ask for it.** Seven of
the first eight findings were a parent the child had already COLLAPSED in a subquery.
`az_section_summary` turning 3,483,870 parcel-sections into 114,305 sections is `pre_aggregated`,
sitting on the entry, naming that exact parent. Same shape as the union fix: the count was allowed
to be wrong about something the parser settles exactly.

```
candidates   358 models -> 45 hops -> 35 after the pre-aggregation refusal
findings     8 -> 2
```

Both survivors are a join that is not matching: `stg_adwr_sections -> int_az_pending_sections`
keeps 0.5% on `section_id`, and `dim_owner -> mart_acquisition_targets` keeps 0.4% on `owner_key`.

**And the summary printed `keeps 0% of dim_owner: 13,694 rows`** -- the same rounding bug as
`1.56x` printing as `2x`, pointed the other way. That one overstated; this understates, and `0%`
sends somebody looking for an empty table that has thirteen thousand rows in it. A non-zero share
never prints as zero now.

### What this tier refuses to become

No funnels, no conversion rates, no anomaly on a trend. Every one needs somebody to say what the
funnel IS and what a normal week looks like. That is intent, and the refusal to guess at intent is
why the findings are worth reading. The line, from the review that prompted this:

> assay can say a column is 99% its default. It cannot say whether that is bad.

The first is a fact about code and rows. The second is a ruling, and that loop already exists.

---

## The wiring audit: what the new knowledge was not reaching

After three releases of new facts (finding ids, completeness checks, effectiveness, row loss), the
question was whether anything already built had been left behind. Six things had.

**The store could not join a ruling to its finding.** An agent rules with `rule(finding=...)` and
the verdict is filed under `<uid>::finding::<id>`. The `findings` table had no `finding_id` column,
so that id existed on one side of the store and nowhere on the other: nothing -- not `export`, not
a seed, not the page -- could put a ruling next to the finding it was about. The same orphaning as
0.17.0, one layer down.

**`check --json` had no finding id and the MCP tool did.** One document, two spellings. Anything
reading the CLI's JSON could see a finding and had no handle to rule on it.

**`known_checks()` read three modules and five real checks were built in two others.** So
`hop_drops_most_rows: {action: annotate}` in an `audit.yml` would have been reported as configuring
nothing. The existing floor catches a reader that finds NOTHING; it cannot catch one that finds
most things. The guard walks the package now and compares against every `Finding(check=...)` site.

**The shipped `audit.yml` did not name the new checks**, so a reader could not see they existed or
what to change. They defaulted to annotate by severity, which was right by accident.

**The skill file was MCP-only.** An agent with the procedure and no server had steps it could not
perform. Every tool has a command that answers the same question, and now that the CLI's `--json`
carries the same finding ids, ruling works either way. The skill also never mentioned
`completeness`, `effectiveness`, `disagreements` or `page`, so an agent following it ran none of
them.

**And it did not say what a completeness finding IS.** They are coverage of what the project
declares, not defects to go and fix. An agent told "this source is read by nothing" will helpfully
delete it. The skill now says: read it, rule on it, and do not act on your own initiative, because
whether a gap matters is a question about intent.

## The page

`assay page` writes one self-contained file: the ruled-on number first and largest, then agreement
per question version, then findings by defect class and reach, then coverage, then what moved.

It is deterministic. It carries the manifest's own `generated_at` and never a wall clock, so a
rerun that changes nothing writes a byte-identical file. That is the whole argument for a file over
a dashboard, and a page that churned on every run could not be committed at all.

On the field warehouse it opens with **0 of 149**. Nobody has ruled on a finding: all 99 rulings
are an agent's, and the eight human verdicts are on window subjects rather than findings. That is
the number doing its job on the first run.

### The page, second pass

**`--plain`.** The same page with a sober stylesheet: white, one accent, no background. Every class
name is shared, so there is one template and two palettes rather than two pages that drift. A
report somebody has to explain before a colleague reads it is a report that does not get forwarded.

**And it was thin.** Five sections of counts, with no findings on it. Four more, all from facts
assay already held:

- **What is one row of this**, split by who said so: 236 declared by a test, 23 worked out from the
  SQL, 0 judged, and **99 where nothing settles it**. Never summed, because a grain a person wrote
  down and one a judgment reached at 0.53 are not the same fact. Plus the 21 models with no
  uniqueness test at all.
- **The findings themselves**, eighteen by reach. A page that says `test_cannot_fail 50` and shows
  none of them is a page nobody acts on.
- **What this project claims about itself**: extracted, supported, contradicted.
- **Why a section is empty.** No claims prints `assay claims --extract` and *"nothing here is a
  pass -- it has not been asked"*. No verdicts prints that until somebody presses a key no question
  may fail a build. Row loss uncounted says so rather than showing a zero. The same discipline the
  checks have, applied to the page.

---

## Round five: two blind spots, both found by using it

`completeness` earned its keep on the first run: five sources declared, loaded every run, read by
nothing, holding 69,946 rows of AZ ADWR data. Ruled and recorded.

### A manifest check cannot see a Python reader, and the obvious action is to delete

`enriched_wells.well_documents` came back as read by nothing. True of the dbt graph and false of
the warehouse: `enrichment/well_scans.py` reads it. Its own description already said *"the document
index the scan reader works from"*, so a person had written it down and nothing could act on prose.

Two fixes, and the first matters more.

**Say what was actually checked.** The summary claimed "nothing reads it", which is a statement
about the warehouse when only the dbt graph was looked at. It now reads *"nothing in this dbt
project reads it"*, and the detail says outright: a reader outside dbt is invisible here, do not
delete on the strength of this finding.

**And let the project declare one.** `meta: {read_by: enrichment/well_scans.py}` on the source
suppresses it. That stays inside the rule this whole tier follows -- coverage of what the project
ITSELF declares -- and it turns a false positive into a fact recorded where a human will also read
it.

This is the case that justifies the skill note about not acting on initiative, and it arrived
within a day of that note being written.

### The narrowing can be on a different edge, and the counts already said so

`multifamily_leads` keeps 0.4% of `dim_owner` because it joins a roster of apartment buildings
only. The hop is not failing to match: the child is the SIZE of its other parent. Same family as
the union blind spot, and settled the same way -- by code, from numbers already in hand.
`verify_row_loss` now counts every parent of a candidate child rather than only the candidate
parents, and a hop whose child is roughly the size of a sibling parent is refused.

```
mart_acquisition_targets   13,694 rows   dim_owner 3,156,986   int_acquisition_targets 13,694
int_az_pending_sections       620 rows   stg_adwr_sections 114,305   stg_adwr_aaws_pending 162
```

Both explained, both refused.

### The sibling heuristic was wrong, and the report that landed it said why

*"This warehouse is a poor control for it: the models that would trip it legitimately -- the leads
marts -- all narrow in sibling CTEs, which is precisely what you just taught it to refuse."*

That is the refusal masking the defect it was built to find. The parser already held the two facts
that settle it without any coincidence of sizes:

- **A LEFT, RIGHT, FULL or CROSS join cannot lose the rows it drives on.** Only INNER drops. Half
  the candidates were LEFT joins, where row loss against the target is meaningless by construction.
- **Only the driving edge can be judged.** `from_relations` carries it, and the comment on that
  field already said the same thing pointed the other way: *"a model's driving table is NOT
  something it joins to, and a check that conflates the two reports a fan-out against the table
  the model is simply reading."*

`mart_acquisition_targets` drives on `int_acquisition_targets` (13,694 rows) and LEFT JOINs
`dim_owner` (3.1M). The child was never going to be 3.1M rows.

35 candidates became **9**, and what those nine measured is a better result than a finding:

```
100%  x8   every driving row survived its join
3048% x1   a fan-out, which is hop_multiplies_rows's job
```

Eight of nine at *exactly* 100% is what a correct warehouse looks like on this dimension.
Scattered ratios are what a wrong candidate set would have produced.

### And it still has not caught a real defect, which is worth saying plainly

It is verified against a **planted control** only: a driving INNER edge that really does lose its
rows, which fires. Nobody has yet watched it catch a real join that was failing to match, and this
warehouse cannot supply one. Recorded in `VERIFICATION.md` rather than counted as a clean result.

---

## Round six: a third orphaning, and the shipped questions were fitted to one warehouse

### `assay config` read `human: 0` while eight verdicts existed

They were filed under `family='water.prio'`, which is a question's id PREFIX and not any bank's
name. `config` keys on the family name, so the gate floor said "20 more" when it was 12.
`effectiveness` found them fine, because it groups by whatever is stored.

**Third instance of one fact and two spellings, in the same table as the first two.** `_FAMILY` is
a snapshot built at import, because fourteen modules bind their question at module level and a bank
found later would add a family while silently failing to override one. The snapshot has to stay.
What did not have to stay was trusting it alone: the live banks are consulted before falling back,
and the fallback is now LOUD -- a verdict filed under a name no bank claims says so on the spot and
names the fix.

`assay review --repair` re-files them. On the field store: `water.prio ->
seniority_ordered_by_the_wrong_date`, and the eight verdicts count toward a gate floor again.

The repair also cried wolf about every completeness finding, because their subjects are
`source.<pkg>.<src>.<table>` and it only knew about models. A repair that reports correct rows as
broken teaches a reader to ignore it.

### The shipped questions sent this warehouse's vocabulary

Asked directly: does any of this translate to a warehouse that is not this one? It did not, in the
place that matters most.

`criteria.examples` are **sent to the model**. The shipped unit question carried
`examples: ["decreed_af", "amount_acre_feet", "storage_af"]`, the claim question `"one row per
water division and case number"`, and six other families carried `permit_id`,
`appropriation_date`, `only commercial parcels`, `a diversion structure on a right that is not a
diversion`. On a retail warehouse that is water-rights vocabulary arriving as part of the question.

Comments in the YAML are fine and stay: they are for whoever maintains the bank and are never sent.
Eight families were rewritten with examples spread across several domains, which is better than
neutral ones because they show the PATTERN rather than one industry's nouns.

The guard reads the sent half only -- `instructions` and `criteria`, never comments -- and a second
one refuses any project identifier in a shipped string. It caught `--model.column, e.g.
water_rights.decreed_af` in a help message.

### And the version bump was a lie before it was a fix

A blanket regex bumped every question in the touched files, including four whose sent text never
changed. `effectiveness` compares agreement per version, so a false bump splits one question's
verdicts across two versions that are the same question and makes the before-and-after meaningless.
Only the families whose criteria actually moved carry a new version. Caught by reading the diff,
which is the only thing that could have caught it.

---

## Round seven: the one finding that could not have come from this warehouse

Every other thing in eight rounds came from running assay here. The bank audit came from asking
what it looks like on somebody ELSE'S warehouse -- noticing that the shipped
`edge_preserves_the_grain` example matched this project's exact bug class, `case_number` unique
only within a division, and not being able to tell whether that was coincidence, a generality
problem, or evidence the check was built from a real case.

It was the middle one, and one project can never tell you which.

## A fork made to preserve one property silently forfeits another

A cost of the pattern rather than a bug, and the report named it exactly. A project forked
`edge_preserves_the_grain` and copied four options WORD FOR WORD on purpose, so the hand
verification recorded against that family would still apply. That same copying is what stops every
later improvement reaching it, and `assay banks` said only `yours, replacing`.

assay holds both halves, so it can say it once:

```
note  edge_preserves_the_grain  override_copies_the_shipped_text
  9 of 11 blocks are byte-identical to the shipped `edge_preserves_the_grain`. Those are
  copies rather than changes, and a later improvement to them will not reach this fork.
  Add `forked_from: edge.v2` and assay will tell you when the shipped one moves.
```

Three signals, all from what was already loaded:

- **How many blocks are copies.** Byte-identical, field by field, over the sent text only.
- **`forked_from`**, the declared half and the same pattern as `meta.read_by`: say which shipped
  version you took it from and assay tells you when that moves.
- **An override that keeps the shipped `prompt_version` while changing the text** is an ERROR, not
  a note. Two different questions under one version means `effectiveness` cannot tell their
  verdicts apart and the agreement rate mixes answers to two questions.

It is a **note**, not a warning, and `--strict` stays green. A deliberate fork is the normal case;
the note exists so it does not go stale unnoticed. Counting it as a warning would make a build red
for doing exactly what the docs suggest.

---

## Round eight: answers to a question that no longer exists

`model_decisions` is keyed on `(decision_key, question, prompt_version, model_version)` so every
version of every answer is kept. That is what makes `effectiveness` possible. Reading it without
filtering hands back all of them at once, and two surfaces did.

**`traversal` returned twelve verdicts for four hops** and called the same hop both
`silently_multiplied` and `deliberately_coarser`, because all of them predated a version bump. An
agent reading it to decide about a hop was handed both and no way to tell which was live.

**And `inventory._judgments` had the same cause with two different symptoms.** `edge` and `align`
answers were keyed `edge__0, edge__1, ...`, so every version ACCUMULATED. Every other question was
keyed by its id, so the later row OVERWROTE and the survivor was whichever row duckdb happened to
return last. Two defects, one cause, and the second is `arbitrary_pick` -- the class this tool
checks other people's code for -- inside its own inventory.

Measured on the field store, which holds three versions of 543 `edge` answers:

```
OLD  (every version, every row)   83 hops judged silently_multiplied
NEW  (the version shipping now)   13

     edge.v1          57     <- the SHIPPED question, before the project forked it
     edge.water.v1    13
     edge.water.v2    13
```

The fork cut it from 57 to 13 and the tool was still counting the pre-fork 57. A question was
rewritten precisely to stop a false positive, and the answers it was rewritten to retire went on
producing findings.

`store.live_decisions` is the one reader now: one row per `(decision_key, question)`, from the
version shipping now, and `retired_decisions` counts what it left out. A question whose prefix no
loaded bank claims is passed through rather than hidden, because an unloaded custom bank must not
silently empty the inventory. `traversal` reports the count and says why they are not shown.

### Why running assay on assay did not catch this

Asked directly, and it is worth writing down. `arbitrary_pick` is a **SQL** check: it parses dbt
models with sqlglot and looks for a `row_number() over (partition by ... order by ...)` whose
tie-break is not total. assay's own code is Python. The entire structural tier is blind to it.

`test_assay_on_assay.py` runs assay's METHOD on assay -- claims extracted from its own docstrings,
plus mechanical guards -- and that is a real loop which has caught real defects. What it cannot do
is see a dict assignment in a loop and recognize it as the same defect as a non-total window
ordering. Recognizing those two as one class is exactly the semantic step, and the tier that would
do it is the one that does not exist yet: tree-sitter where sqlglot is, a call graph where the DAG
is.

## Two smaller things from the same report

**The MCP install line wrote a machine-local entry.** `claude mcp add assay -- ...` goes to
`~/.claude.json` keyed to one absolute directory: invisible to a session started one directory up,
and absent entirely from a fresh clone. `--scope project` writes `.mcp.json` in the repo, so the
server travels with it. Four places said the wrong thing, including `onboard`'s own closing line.

**And a debt model built on a hand-written class list had 63% of its rows land in `other`** after
one release added a check. The shipped example groups by `check_name` for that reason and now says
so: a hand-written class list is a second copy of the check list, and the second copy is what
drifts. If you want named classes, `else check_name` makes a new check name itself instead of
disappearing.

---

## Round nine: the fix from round eight hid two whole families

0.24.0's version filter took a real project from 349 findings to 103 and reported it as
**0 new, 246 resolved**. Fifteen of those were real. **231 were hidden.**

```
check                              0.23.0   0.24.0
hop_multiplies_rows                    22        7   the actual fix
code_contradicts_a_claim              213        0
description_contradicts_the_code       18        0
everything else                        96       96   unchanged
```

Two families to exactly zero, and nothing about those models had changed.

### The cause: a mapping that was never the right one

`live_decisions` resolved "the version shipping now" as `question_id.split("__")[0]` -> the bank
claiming that prefix -> its `prompt_version`. Three shipped questions file under an id that is not
their own bank's prefix, so each lookup landed on a NEIGHBORING family:

```
stored question   written by            stored version   prefix resolves to   shipping
claim__0..7       sentence_is_a_claim   sentence.v1      claim_alignment      claim.v2    retired
align             claim_alignment       claim.v2         same_concept         align.v1    retired
desc              description_...       desc.v1+comments+scoped               desc.v1     retired
edge              edge_preserves...     edge.water.v2    edge_preserves...    (match)     kept
```

The `edge` row is the only one whose id and bank agree, which is why the one family the fix was
built for worked and the rest did not.

The `desc` case is separate and worse: the writer stores
`DESC_Q["prompt_version"] + "+comments+scoped"` and the reader compared against the bare string, so
re-asking writes the same suffixed value and that family could never come back by re-running.

### `check_question_ids` asserted the wrong invariant, for eight releases

It asserts that SOME bank claims the prefix, and one always does. It never asserted that the prefix
claims the RIGHT bank. `contracts.id_prefix_conflicts` asserts the real one and `assay banks`
reports all three as errors. It also catches the deeper problem: `claim_alignment` and
`same_concept` both file under `align`, so NO mapping from a question id to a family can be correct
for them.

### So the reader stopped resolving families at all

The latest answer to a question wins. That needs no mapping and cannot land on the wrong bank. A
version bump hides nothing: re-running writes a newer row and that row wins, and until it is
re-run the old answer is the only answer there is -- hiding it leaves the caller with nothing,
which is strictly worse than serving it dated. `stale_decisions` dates them instead, comparing the
base version against the set of ALL shipping versions, which needs no per-question resolution.

Restored on the field store: 213 and 18 back, `hop_multiplies_rows` still correctly 7.

### And nothing said a word

`retired_decisions` was read in exactly one place, inside an MCP tool. `check` never touched it, so
7,656 dropped decisions printed as "246 resolved" -- the failure `VERIFICATION.md` opens by
describing, where *it reported nothing* and *it is not configured* read identically from outside.
`check` prints both counts now:

```
stored judgments: 1,496 older answer(s) superseded by a newer one;
                  5,842 answer(s) given against a question that has since changed, still used.
Nothing is hidden -- re-ask with the command that owns the family and the newer answer wins.
```

A number that moves for a reason that is not the reader's code has to say so where the reader is
watching it move.

---

## Round ten: four open items, and the one the fourth uncovered

Not a field report. Four things carried on the open list long enough to be worth closing together,
and closing the fourth turned up a fifth that nobody had noticed.

### The question ids moved, and the answers moved with them

0.24.1 left `id_prefix_conflicts()` returning three live conflicts behind an xfail, on the
reasoning that fixing them would orphan ~7,500 stored decisions and cost a re-ask. **It does not,
and the number says why:**

```
5,794  claim__<i>   all written by sentence_is_a_claim   ->  sentence__<i>
1,742  align        all written by claim_alignment       ->  claim
    0  align__<i>   same_concept has never been asked here
```

Nothing sits under `align__<i>` to collide with and nothing sits under a bare `claim`, so both
renames are one-to-one in both directions. More to the point, **a finding id is hashed over
`check|subject|summary|evidence` and not over a question id**, so no finding-keyed ruling is
touched. The only rows keyed on a question id are `model_decisions.question` and
`adjudications.question`, and both are renameable in place.

So it is a rename, not a re-ask. The question TEXT does not change, which means no
`prompt_version` moves and every stored answer stays a cache hit. `Store.MOVED_QUESTION_IDS`
carries them on open, idempotently, and prints what it moved.

**It moves what it can and says what it cannot.** The question id is in the primary key, so a
store holding both the old id and the new one under one key cannot hold both: `update` raises
there and `insert or replace` destroys one of the two answers silently, which is the exact shape
this tool checks other people's code for. Collisions are counted, left where they are, and named.

**And `starts_with`, never `like`.** `_` is a single-character wildcard in LIKE, so
`like 'claim__%'` also matches `claimXY`. The ids being moved end in a double underscore, which is
precisely where that bites.

### What the conflict was actually costing, which was more than an xfail

Round nine took *version* resolution off `family_of`. Family *attribution* still ran through it:

```
claim__0     -> claim_alignment      written by sentence_is_a_claim
align        -> same_concept         written by claim_alignment
```

Two of three shipped questions filed their verdicts under a neighboring family's name. That
reaches `mcp_server.rule()`, which writes that name into `adjudications.family`; `store`'s
per-family human counts, which are what `min_adjudications` gates on; and `review -i`, which
prints it. **A human verdict on a `claim_alignment` question counted toward `same_concept`'s gate
floor.** Nothing had yet ruled on a claim question on the field warehouse, so no verdict was
actually corrupted — it would have bitten on the first one.

### The claim refusal ran at the call site and nowhere else

0.23.0 taught `verify` not to ask the two claim shapes SQL cannot settle, which cut contradictions
**389 to 240** and removed both hand-read false positives. It changed no `prompt_version`, because
the question did not change — only which subjects are worth sending.

Which means every answer given before it shipped is still the live answer, is **not stale**, and
went on producing findings from a read path that never consulted the refusal. One fact, two
places, silent when they disagree.

`inventory` applies the same refusal now, and prints what it refused. It reads the claim text from
the claims table by way of the decision key, **not** from the stored context, which is truncated at
120 characters — a claim whose literal value sits past that would read as having none, which is the
same absence-reads-as-a-pass defect one layer down. A decision key that resolves to no stored claim
returns *askable*, not *refused*: a guard that cannot see its subject must not report a verdict on
it.

The third split from round seven, deduplication, shipped too. `claim_id` already collapsed
byte-identical text, so what survived in the field were pairs differing by a backtick or a trailing
full stop — `int_water_diversion_history` at 0.95 and again at 0.93, one sentence a person had put
in both a header and a schema description. The key strips punctuation **and nothing else**:
dropping stop words or stemming would collapse two claims that genuinely differ, and the louder a
normalizer is the more quietly it loses one of them. Both sentences are printed for every merge.

### `hop_drops_most_rows` has an end-to-end control

It was verified against a planted candidate dict, which skips the two things most likely to be
wrong: whether candidate selection reaches the hop at all, and whether the counts get back to it.
There is now a dbt project on disk, parsed by the real loader, counted against real rows in a real
DuckDB.

```
int_parcel_owner     100 of 1,000 driving rows survive an INNER join    FIRES
int_parcel_zone    1,000 of 1,000 survive                               SILENT
int_parcel_where      10 of 1,000, and the model declares a WHERE       REFUSED
```

The third stops the threshold taking the credit: it loses more than the first and produces
nothing, because the loss is declared in the SQL. Selection narrows four hops to two before a row
is counted.

### And the fifth thing: a better summary orphaned five human rulings

`ops_assay_debt`'s `model_has_been_ruled_on` was `a.subject like '%' || f.model || '%'`, which
reads true on nearly everything and also matches every other model whose name CONTAINS this one —
`water_rights` catching `water_rights_history`. It is two columns now: the exact
`subject || '::finding::' || finding_id` join, and the weaker "is this ground anybody has looked
at" with the weakness moved into its name.

The exact one reads **0 of 349**, and five per-finding rulings exist. They join to nothing in any
of the five stored runs, and the subject matches exactly:

```
ruled    source.sunny_data.raw_az_water.adwr_townships::finding::53cf3662d357
current  source.sunny_data.raw_az_water.adwr_townships::finding::f0b89021a48b
```

`finding_id` is stable across runs — 349 of 349 findings keep exactly one id across the three runs
that contain them — so this is not drift. **It is 0.21.0 rewording the summary.** Round five asked
for `source_reaches_nothing` to stop claiming "nothing reads it" when only the dbt graph had been
looked at, and it now reads "nothing in this dbt project reads it". That was the right fix. The id
is hashed over the summary, so making the sentence more honest minted new ids and orphaned every
ruling made on the old one.

The docstring on `Finding.id` states the property this breaks, in as many words: *"Hashed over
check, subject and summary rather than the run: a ruling has to survive the next run or it is not
a ruling."* It survives the next run. It does not survive the next **release**, and a check whose
wording is being actively improved is exactly the one people will have ruled on.

Not fixed here, because every fix trades something: dropping the summary from the hash merges the
eight findings one model can carry under one check, and a `supersedes` map is a second copy of the
check list, which is the thing that drifts.

---

## Round eleven: the page became the whole warehouse, and driving it found two defects

The page answered one question well and showed almost nothing. The store holds 9,945 answers,
5,794 claims, 3,438 edge facts and 195 verdicts; the page showed a scorecard and eighteen
findings. Everything else was reachable only by running four commands and holding the answers in
your head.

### Two jobs that were always conflated

The record is small, committed, diffed across commits, and handed to a colleague. Its whole
argument is that it **accrues**, which needs it to stay small. The explorer is for the person who
owns the warehouse, and has no determinism requirement of its own because nobody diffs an
explorer. Separating them is what made the size question answerable at all: `--plain` writes the
record at about 15 KB, and the default writes the explorer with the record inside it as a tab.

### One file, and it is not a style preference

Browsers block `fetch` and XHR on `file://`. So a directory of HTML plus JSON that loads a model
when you click it **cannot be opened by double-clicking it**. That option does not exist locally:
an artifact you open from disk carries its data inside it, and the moment lazy loading is wanted a
server is required. There is no middle rung.

Measured at roughly 24 KB per model, so this warehouse is 8.2 MB, which opens instantly, and a
2,000-model one would not. Everything renders from one object called `DATA`, embedded in a script
tag here and one `fetch` on a server, so the file already builds most of `assay serve` and the
decision can be made later for a fraction of the work rather than a rewrite.

### `order by count(*) desc limit 1` is an arbitrary pick, and it shipped here first

The first version read edge facts from the fullest run, copying the shipped example model, whose
comment says a tie "would mean two runs found exactly the same thing". On this store **six runs
hold exactly 573 edge facts each**. The tie is the normal case, duckdb returned a different winner
between two invocations, and two runs of `assay page` against an unchanged store wrote different
files:

```
run A   int_lead_contact <- stg_business_entities   carried 7   dropped 25
run B   int_lead_contact <- stg_business_entities   carried 8   dropped 24
```

`arbitrary_pick` and `first_match_pick` are checks this tool runs against other people's SQL. This
was the same defect, in the code that renders their results, and it broke the one property the
file exists to have. The run is now chosen by `started_at` with `run_id` breaking the tie, because
a comparison that can tie is not an order. Byte-identical across three runs afterwards.

**The same bug is in the shipped `ops_assay_debt` example**, whose `latest` CTE picks the fullest
run the same way. On this store the findings table has three runs tied at 349.

### `json.loads(s) or {}` turns an empty list into an empty dict

`edge_facts.joined_on` is a list. A hop with no join key stores `"[]"`, which parses to `[]`,
which is falsy, which the `or` replaced with `{}`. Every such hop then carried a dict where the
reader expected a list, `(h.joined_on || []).join` threw on the first one, and **the entire chain
tab rendered as an empty page** -- 573 hops, showing nothing, with no error visible to anyone
looking at it.

Nothing about the shape of that code looked wrong, and no test that asserted on populated rows
would have caught it. It was found by driving the page in a real DOM.

### Which is the part worth keeping

The page was verified by loading it in jsdom and clicking every tab, not by checking that the
generator produced plausible HTML. That found the two defects above, and both are invisible to
any check that reads the source or the output markup:

```
models      rows=  385  text= 12483  OK
chain       rows=    0  text=     0  ** EMPTY **     <- joined_on was a dict
claims      rows= 2000  text=443375  OK
findings    rows=  244  text= 12005  OK
answers     rows= 2000  text=399686  OK
questions   rows=   19  text=  2674  OK
config      rows=   36  text=  3563  OK
understood  rows=    0  text=   201  OK
```

A tab that renders nothing and a warehouse that has nothing to show produce the same page. That is
the failure `VERIFICATION.md` opens by describing, one surface over, and the only thing that
separates them is opening it.

### Smaller

`history.replaceState` throws a SecurityError on `file://` in some browsers, and `file://` is the
entire delivery mode. It sat after the panels were swapped and before the view was built, so a
throw would have left a blank tab. It is in a try/catch: the deep link is a convenience, the tab
is not.

The record travels inside the JSON blob and renders into an iframe rather than being dropped into
a div. It is a whole document with its own stylesheet and so is the page around it. The `</`
sequence is escaped in the blob for the same class of reason: a dbt model with `</script>` in a
comment is not exotic, and it would truncate the page describing it.

---

## Round twelve: a long list is data, not navigation

Field reaction to 0.25.0, and it separated two things that had been running together: the page
held the right content and gave you no way to move around it.

> *"very high vol of shit too like claims and answers and that's fine and to be expected but just
> a list to scroll of all this is overwhelming and useless... the chain has better ways to display
> that information, same with the checks."*

The diagnosis: Models, Findings and Questions had an index and a detail. Chain, Claims and Answers
opened on a flat list of everything. **5,794 claims in one scroll is not more information than 358
models in one scroll, it is less** -- the first screen tells you nothing about the shape of what is
there and gives you nowhere obvious to click.

So: no tab opens on a flat list. Claims group by model and lead with the 113 contradicted, answers
group by the 19 question families with mean confidence and how many fall under the 0.60 gate,
findings get a check strip that filters the list in place. And every model name in every table is
a link to that model, which is the cheapest change here and the one that turns eight islands into
somewhere you can move around.

### You never draw 573 hops

That is the whole graph. Measured before drawing anything:

```
parents per model    median 0   p95 7    max 36   (water_section_fingerprint)
children per model   median 1   p95 4    max 20   (stg_adwr_sections)
boxes in a drawing   median 3   p95 12   max 37
```

A drawing is always one model's neighborhood, so 95% of them are twelve boxes or fewer and need
no graph algorithm: three bands, straight lines, driving parents first because the driving edge is
the spine.

**The edge label goes on the parent box, never on the line.** With eight parents converging on one
focus, labels on the lines overlap into mush; on the boxes they cannot. Each parent box carries
its own join kind, keys, dropped count and rows kept.

Past nine in a band it degrades to a list with the same facts, because 36 boxes with 36 converging
lines is the hairball the drawing exists to avoid. That is 14 models of 358 on the parent side and
6 on the child side, and you can see it coming.

### The page is not the thing worth committing

```
             minified     jsonl     pretty     lines in a diff
models          1.81M     1.81M      2.80M     358  vs  110,111
decisions       3.20M     3.20M      3.87M   8,449  vs  118,287
TOTAL           8.12M     8.12M     10.35M
```

JSON Lines costs **nothing** over minified and diffs as one line per entity. Pretty-printing costs
2 MB and produces a 110,000-line models file that git will happily diff and no human will read.

So every run writes `assay-data/`: one `.jsonl` per table, `meta.json` and `config.json` whole
because a person reads those and wants a field-level diff, and `record.html`, which is the 15 KB
report. Commit the directory, gitignore the page. Demonstrated by adding one human ruling and
regenerating:

```
adjudications.jsonl   1 line added
record.html           changed, because the ruled-on number moved
everything else       identical
```

`assay page out.html --from assay-data/` re-renders with no dbt target, no store and no manifest,
which is most of why the artifact exists: a committed artifact readable only from the machine that
produced it is not a record. Verified byte-identical to a render straight from the warehouse.

It is written every run rather than behind a flag. A page and an artifact that can disagree is the
one-fact-two-spellings defect, in the two files that are supposed to be the same thing.

### The half you write by hand was the half dumped as JSON

> *"ideally you're seeing the user configured shit in a nicely formatted way too like vocab and
> questions and other config."*

Fair, and worse than it sounds. The vocabulary, the question text and the per-check policy are
hand-written and hand-maintained -- they are exactly what a person opens this page to read -- and
they were the only parts rendered as `JSON.stringify(..., null, 2)` inside a `<pre>`. Fifteen
vocabulary terms written once made `traverse` flag `wdid` joins without anyone writing a water
question. That is the promise of the whole config, and it was being skimmed past as a blob.

Each question now shows its prompt as prose and every option as a block: the name, what it means,
what it is explicitly not for, and its examples as chips. That is the text `effectiveness` measures
agreement against, so it is the text a person needs when a version number moves. The config tab
renders the vocabulary, the per-check policy, the waivers with their required reasons, and the
runs, as tables. Zero `<pre>` blobs on either tab, asserted.

### And the theme went

> *"the understood tab being the only one to follow the theme at all is funny... if it's not done
> properly across the entire page and it's a bit cluttered to allow such distractions then let's
> not."*

There were two record stylesheets, a loud one and a sober one, and the loud one was the default.
Once the record moved inside the explorer as a tab it was the only styled surface on a dense page
of plain tables, which reads as an accident rather than as emphasis. One style now, the sober one,
and `PAGE_CSS` is deleted rather than left unused. Color stays where it carries meaning: a pill
for provenance, red for a contradiction, amber for a confidence under the gate.

### Two defects, both found by driving it again

`Node.append()` returns undefined, so `g.append(svg('title'))..textContent = ...` was setting a
property on nothing and every hover title on the lineage threw. And the `--from` path originally
rendered an Understood tab that was silently empty, because the record was added inside
`explorer_html` and so was never in the data that got written. Both are the same shape as round
eleven's: invisible to anything that reads the source or the generated markup, obvious the moment
the page is opened and clicked.

---

## Round thirteen: four things a screenshot showed that no test could

0.26.0 opened in a browser and photographed. Every item below is invisible to the suite, to the
DOM driver, and to reading the source, because all four are about what the page LOOKS like once
real data is in it.

### "209 hop(s) worth a look" was my own bad rule, hidden behind a disclosure triangle

Two defects in one control. It was a collapsed `<details>`, which is the same shape as every
absence-reads-as-nothing failure in this file: a closed summary and a check that found nothing
are the same picture.

And 209 of 573 is not a signal, it is the table. **163 of those 209 came from one rule: "a driving
edge joining on nothing".** A driving edge IS the `FROM` clause. Of course it joins on nothing. It
was flagging the normal case.

Measured on the 573 real hops before rewriting it:

```
dropped columns per hop   median 5   p75 12   p90 24   max 233
a join carrying no resolvable key                       46
judged silently_multiplied                              13
driving edge with no join key            163   <- the normal case
union arms                               195   <- also normal
```

Every threshold now comes off that distribution: **65 of 573**, each with a reason rather than a
boolean. And it is in the page instead of behind a triangle -- a `notable` column in the model
list you can sort on, a toggle that narrows the list to the 47 models that have one, the notable
edges drawn dashed and amber in the lineage, and each model's own named under its drawing.

### The boxes overflowed, and then the drawing overflowed

`int_az_parcel_sections` painted straight through its own border and then through the right edge
of the canvas. The box was 148 wide and the title truncated at 22 monospace characters, which at
12px is about 158px. Truncating by character count guesses at font metrics and the guess was
wrong.

Two layers now, and both are needed. An **ellipsis** says "there is more here", which a hard cut
does not -- a name sliced mid-character reads as a rendering fault rather than as an abbreviation.
A **clip path** is the backstop that makes the box the boundary whatever font renders it.

The drawing was also small: 148x54 boxes on a 316px canvas inside a panel twice that tall. Now
210x66 on a 400px canvas with a minimum width, so a two-box lineage fills its panel instead of
huddling in the corner, and a wide one scrolls rather than being cut.

### Eleven chips carrying a name, a count and "0 read" each

Two rows of furniture above the table they filtered, spending more space than the thing itself.
It is a `select` in the filter bar that already exists, so it adds no row at all: *"every check ·
244"*, then one line per check. Same function, one control, no new row.

### And the same thing again on Claims, one tab over

`the 113 contradicted, across every model ->` was a button floating above the table it switched,
which is the chip wall with a different shape: a row of the page spent on something the filter bar
already had room for. It is a select in that bar now, with two options and no row of its own.

The half worth writing down is what it does when you come back. The control owns no state the view
can disagree with: returning to the groups resets it, because a select reading "contradicted" over
a table of every model is one fact with two spellings, which is the defect this whole tool is
about, in its own furniture.

### A switch that vanishes when you use it

The select fixed the floating button and introduced a smaller version of the same problem: it sat
in the group bar only, so choosing *the 113 contradicted* left you in a view with a breadcrumb, a
back button, and no way to choose again.

> *"I'd greatly prefer that dropdown to remain so it's flipping between the two, rather than
> different UI."*

Right, and the rule it implies is worth keeping: **a control that switches between two views
belongs in both of them.** It is in the bar in every state now. The only thing that adds a way
back is DRILLING into one group, because that is the one move the switch cannot undo.

And the notable filter on the chain was a button whose label flipped between two sentences, which
makes you read it to find out which state you are in. A checkbox shows you. Same argument as
everything else here: the control must not be able to disagree with the view.

### The overview was the last tab

A person opening this file has not picked a model yet, and landing on 358 rows asks them to
choose before they have been told anything. The record is the only surface here with an argument
to make rather than a table to show, so it is the way in. First tab, and the default when there is
no hash.

### What this round is actually about

Rounds eleven and twelve were caught by driving the page in a DOM, which finds a tab that throws
and a tab that renders nothing. It cannot find a control that is technically correct and visually
a wall, a box whose text paints outside it, or a threshold that is a bad idea rather than a bug.
**65 of 573 and 209 of 573 are both "working".** The only thing that separated them was somebody
looking at the page and saying the number was absurd.

---

## Round fourteen: "came from unknown" was mostly the classifier, not the SQL

Three questions from the field, all about the Models tab, and two of them turned out to be
defects rather than design.

### The unreadable 30 are one package, and the tempting filter is the wrong one

```
models by package        sunny_data 328   elementary 30
unreadable by package    elementary 30
readable by package      sunny_data 328
```

Exact, from the manifest's `package_name`, not a name heuristic. Which matters because the obvious
filter is "hide what did not parse", and the report that asked for it named the reason not to:
*"filtering the absent ones could be bad if you didn't compile first."*

Right. A package's model failing to parse is not your problem; **one of yours failing to parse is
the "you did not compile" signal**, and a filter on `unreadable` hides exactly that. So the split
is by OWNER and never by whether it parsed. The explorer opens on your 328 with a checkbox for the
other 30, and a model of your own can never be filtered away by it.

### `unknown` was 1,010 columns and only 541 of them were honest

> *"lots of came from unknown and shit, ideally if that value came from another model or view or
> table then it's aware of that? I assume sqlglot has that lineage? don't we have that anyway?"*

We did. The classifier was not consulting it, in four separate places.

```
541  Elementary models with no SQL to read      correctly unknown, and now SAYS so
236  `select *` over several relations          the star lost the attribution
233  a root the classifier had no case for      the parser had already answered
131  descendants of a star that never expanded  a column literally named `*`
```

**A root is an answer, so a column carrying one is never unknown.** 233 columns read *"assay could
not resolve where this came from"* while holding a perfectly good root: `filter` 91, `null` 34,
`ignorenulls` 20, `paren` 19, then a tail of `gt`, `not`, `dpipe`, `div`, `like`, `subquery`. The
parser had resolved the expression and the classifier had no case for the shape, so it shrugged
about something it was holding in its hand. `filter` is an aggregate's own clause. `ignorenulls` is
a window. `null` is a union arm padding a column it has no value for, which is now
`null_placeholder` rather than lumped in with a constant somebody chose. The rest are `computed`,
and **the catch-all names what it caught** -- `computed (paren)` -- so a new node type gets its own
case instead of disappearing into the pile.

**A star gives you the names and loses where each came from.** `water_reach_screen` ends in
`select *` over six relations: 73 column names, one root, `{'*': 'star'}`, and 71 of 73 unknown.
The parents' column lists are already assembled for sqlglot, so the parent offering the name is a
lookup. It answers only when **exactly one** parent offers it -- two parents publishing `isf_key`
is genuinely ambiguous, and picking the first is `first_match_pick`, which is a check this tool
runs against other people's SQL. The ambiguity is reported as itself, naming the candidates.

**And a column list containing `*` is not a column list.** When qualify could not expand, the
fallback was the raw output columns, which still hold the literal `*`. Downstream that is a column
NAMED `*` while every real column of the model is simply absent. Three models, and 131 unknown
columns among their descendants. The catalog knows the real names, so it is used, and the count is
reported rather than buried.

```
                      before    after
unknown                1,010      732     of which 541 are "no SQL to read", and say so
carried                1,197    1,317
columns known          5,656    5,793     137 that did not exist before
water_reach_screen    71 of 73  41 of 73  the rest genuinely ambiguous, and they name why
```

The unknown count did not fall as far as the fixes did, because 137 columns that were invisible
now exist. Invisible is worse than unknown.

### An empty column on every row is a question nobody asked

Every one of 5,656 columns had no role. Not a bug: `assay columns` has never run on that
warehouse. But the page printed "not settled" 5,656 times, which says the same thing as a check
that found nothing -- the failure this whole tool is built to name, in its own UI. The column is
dropped when nothing has been asked, and the reason is said once with the command that fills it.

For the record, since the same report asked how the three are settled: **grain** is a precedence,
declared (a uniqueness test a person wrote) over derived (a `GROUP BY` or dedup the parser found)
over judged over none, never added together -- 236/17/6/99 here. **Roles** come from the
`column_role` question plus free labels off the project's own tests. **Provenance** is pure code
and asks nothing.

---

## Round fifteen: reading the graph walked you out of the graph

Two more from opening it, and the first has a cause worth naming.

### A click that types into the search box

Going to a model was implemented by **typing its name into the filter**. Which worked, and left
the list showing one row and the box full of text you had to clear by hand before you could see
anything else. It fired on every node click, so reading the lineage walked you out of the lineage.

> *"clicking a node adds it to the search thing and just FUCKS the ui... making it impossible to
> even see the graph view again without removing the search and clicking back... ideally if you're
> clicking a node it's just a popup right there with the relevant info."*

Two rules, and they are separate.

**A node click opens a card where the node is.** It says what the model is -- description, grain,
reads, read by, reach, findings, claims -- plus what this particular hop carries, so you can
decide from where you are standing. A node that is a SOURCE has no model entry, and the card says
that rather than rendering empty. Clicking the canvas dismisses it.

**Navigation is a second, deliberate click, and it still never touches what you typed.** The
detail pane is authoritative and the list is an index into it, so going somewhere shows the model
and then *highlights* its row if that row happens to be on screen. If it is filtered out or past
the cap, nothing happens, which is correct.

Driving a control by writing into another control is the same defect as everything else in this
file: one fact with two spellings, where the second one is a text box somebody else owns.

### A group that will not say what it is a group of

Claims groups by model, and the group table was a name and two counts, so you clicked to find out
whether you cared. It carries the model's description now, and the filter box searches it.

```
model                  what it is                                      claims  contradicted
buyer_leads_enriched   buyer_leads + enrichment joined on building_key      44             4
```

### And the card was cut off by the box it lived in

> *"it gets cut off bruh it needs to fit in the window"*

It was absolutely positioned inside `.linwrap`, which needs `overflow-x: auto` so a wide graph can
scroll. A node near the left edge therefore had half its card clipped away by the very container
that made the graph readable.

`position: fixed` on the body escapes every ancestor's overflow, so the only thing left that can
cut it is the viewport, and that is clamped: centered under the node, pushed inside on either edge,
flipped above when there is no room below, pinned to the top and scrolling inside itself when
there is room for neither.

The clamp is pure arithmetic, so it is tested as arithmetic in the five positions that matter
rather than by hoping a browser agrees:

```
node at the far left    left=   12  top= 400   fits
node in the middle      left=  420  top= 400   fits
node at the far right   left=  828  top= 400   fits
no room below           left=  420  top= 392   fits
no room either way      left=  420  top=  12   fits
```

**A card on the body does not outlive what it points at**, which is the cost of escaping the
panel: nothing removes it when that panel changes. Scrolling moves the node out from under it and
switching tabs replaces everything it described, so both dismiss it, as do Escape and a click
anywhere outside -- the last being the only way out on a touch device, where there is no canvas
to click. A click INSIDE the card must not dismiss it, or its own buttons could never be reached.

### And we speak American English here

A UI button read `centre the graph here`. Sweeping the rest found 155 British spellings across 40
files: `colour`, `neighbourhood`, `normalise`, `judgement`, `labelled`, `metres`, and a handful of
identifiers (`normalise_reason`, `summarise`) that needed their call sites moved with them.

**The question banks were the part worth thinking about**, because their text is what gets SENT,
and this codebase's rule is that a question's text and its `prompt_version` move together.

- `metres` in `feeds.yml` is an **option name**. The model returns that literal string and the
  code keys its unit bounds on it, so a spelling change there is material in both directions.
  `feed.units.v3` became `v4`. Zero stored answers on the field warehouse, so it cost nothing,
  and it would have cost a re-ask on a warehouse that had used it.
- The rest were **prose in a note, an example, or a YAML comment**. Those changed without a
  version bump, and the reason is worth recording rather than assuming: *a version boundary that
  means nothing is a false signal in `effectiveness`*. The version exists so a rewrite that moved
  answers can be detected. An orthography change cannot move an answer, and bumping for one would
  permanently split that family's agreement history at a point where nothing happened.

The first fix and the last one are the same defect from opposite ends: a test asserting `meters`
against a bank saying `metres` failed immediately, which is the sweep catching its own
one-fact-two-spellings the moment it created one.

---

## Round sixteen: twelve tools that report, and none that teach

> *"ensure mcp and skills are still up to date... so someone relatively unfamiliar with assay can
> be led by Claude to onboard and go through everything like defining vocab and questions and
> waivers and per check policies and explanations... we can even provide some insight into how to
> frame questions and vocab within the mcp and skills so Claude leading it isn't being a dumb
> fuck."*

Checked, and the gap was real. Twelve MCP tools, every one of them read-and-rule. The skill
mentioned `audit.yml` once and `practices` once. So an agent could read every finding on a
358-model warehouse and could not help anybody write a vocabulary term, frame a question, waive a
finding with a reason that holds up, or decide what a check should do on a red build.

**That is most of what a project which has never run assay actually needs**, and it was the one
thing nothing surfaced.

`assay guide <topic>` and `guide(topic)` over MCP, across seven topics: `start`, `vocab`,
`questions`, `waivers`, `policy`, `explanations`, `ruling`.

### The guidance is measurement, not advice

The failure mode for a guide is confident generic advice, which reads exactly like earned advice
and is worth less than nothing. So every rule in it is one that was paid for:

- *It cannot do arithmetic and will not say so.* `land_acres` holding 218,235 read **consistent at
  0.82**; that is square feet, wrong by 43,560x. If your question needs two numbers compared, it
  is two questions.
- *Absence is not disagreement, and you must say so in the criteria.* A claim about `d_class_cn`
  read `contradicts` at **0.97** because the evidence listed thirty other columns and not that one.
- *Choose the evidence BY the subject.* 0.96 with the state the claim needed, **0.47** with one
  extra correct sentence added.
- *An option must not route to another by name.* Measured: one subject under both options at 0.55
  and 0.63, while the text check scored them disjoint at 0.63.
- *A waiver's reason is a measurement.* Both waivers on the field warehouse were measured before
  being waived, and the measurement IS the reason.
- *Policy is keyed by the CHECK, never the family.* assay's own shipped example made that mistake,
  and a config that parses but matches nothing looks exactly like one that works.

### And it reads the rules from the code that enforces them

A guide that quietly disagrees with the linter it describes is **worse than no guide**: somebody
follows it, the check fires anyway, and now they distrust both. So the WHAT is derived and only
the WHY is written down. The lint rule codes come out of `lint.py` by reading the codes it
raises; the `audit.yml` sections come out of `DEFAULT_YML`; the family count comes out of the
loaded banks. A test asserts each direction: **a rule the linter raises and the guide does not
teach fails, and so does a key the guide names that `audit.yml` does not read.** That is the
`else check_name` lesson applied to prose.

### What the skill now tells an agent not to do

> **Do not invent configuration on their behalf.** Vocabulary, waiver reasons and adjudication
> options are domain knowledge you do not have. Ask, draft from what they say, and show them the
> measurement that would justify a waiver rather than asserting one.

An agent that writes a plausible `vocab` block from the model names alone has produced something
that looks like knowledge and is not, and it will be sent with every judged question from then on.

### The wiring guards earned their keep again

Adding one tool failed three tests immediately: every CLI command must be named in the docs, every
MCP tool must be in the skill, and the skill must work WITHOUT the server, which means every tool
needs a command that answers the same question. None of those is something anyone would have
remembered.
