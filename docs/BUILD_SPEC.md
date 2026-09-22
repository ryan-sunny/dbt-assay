# Build spec

Follows `VISIBILITY_SPEC.md`, which measured the gaps. This says what to build, with acceptance
criteria you can check against. Written 2026-09-21 against `sunny_data` — 358 models, 329
findings, 19,707 judged decisions, 617 tests.

## State

| # | item | status |
|---|---|---|
| 0b | generic finding producer | **SHIPPED 0.38.0** — 42 + 21 findings appeared |
| — | deferrals announce themselves | **SHIPPED 0.38.1** — now a constraint on item 4 |
| 1 | cost ledger | **BUILT** — `model_calls`, `assay cost`, $1.32 lifetime measured |
| 2 | stale against the code | **BUILT except `--exact`**, which the measurement disqualified |
| 3 | understanding rollup | to build |
| 4 | Elementary read + `volume_contradicts_a_claim` | to build |
| 5 | the dbt package (macros + hook, no models) | to build, last |

## A rule that now applies to all of it

From 0.38.1, learned by finding it broken: **every deferral announces itself.** assay was silent
on source freshness because dbt-project-evaluator covers it, that package's model was never built,
29 of 212 sources declared no freshness, and nobody was told. Deferring to another tool is right
and it is not free — name what you are resting on, and say what breaks if it did not run.

Item 4 inherits this directly. So does anything else that ever says "somebody else covers that".

---

# 0b. A custom question can be asked and can never be a finding — SHIPPED (0.38.0)

**Kept for the reasoning. 42 and 21 findings appeared on the live store, exactly what the
reporting session predicted. Total 252 to 329.**

## What was found

A custom family declared in `assay_questions/*.yml` is askable, answerable, storable and
printable — and invisible to `assay check`. On this warehouse, two custom questions produced 42
and 21 answers up to p=1.00, and **zero findings**, in this run or any run.

## Why, exactly

`judged.run_all` builds findings from a fixed tuple:

```python
CHECKS = (measure_inside_the_grain, unresolved_judgment, description_contradicts_the_code,
          code_contradicts_a_claim, hop_multiplies_rows)
```

Each is a hand-written function. A family added by YAML has no function, so it has no finding.

**And the tool demands the field that would fix it.** `finding_when:` names the answers that are
defects. It is:

| surface | reads it |
|---|---|
| `lint.py:219` | **yes** — it is an ERROR to omit it: *"asked, paid for, stored — and produces no finding"* |
| `assay ask` (`cli.py:2160`) | **yes** — it prints the hits |
| `judged.py` | **no** |
| every shipped question | **0 of 17 declare it**, because they have hand-written functions instead |

So the linter tells an author they must declare `finding_when`, they declare it, and `check`
ignores it. The error message is literally true in `check` no matter what they do.

This is the same defect the field notes already record one station earlier — *"a family with a new
name is loaded, linted, listed, and never asked"*, fixed in 0.7.0 by a generic **asker**. The
**finder** was never generalized.

## Build

A generic producer in `judged.py`: for every family declaring `finding_when`, read its stored
answers and emit a `Finding` where the answer is in that list. The logic already exists in
`cli.py:2160-2180`; it moves into `judged` and both callers use it, rather than `ask` keeping a
private copy.

The finding needs: `check` = family name, `subject` from the decision key, `summary` from the
family's own question text, `evidence` carrying the answer and its probability, and
`rests_on` = the family, so the gate discipline applies unchanged — a custom family cannot fail a
build until it has human verdicts, exactly like a shipped one.

**What it must not do.** Not invent a summary. A family that cannot produce a readable sentence
from its own YAML is a lint error at authoring time, not a finding with a generated headline at
check time.

**Tests.** A custom family with `finding_when` produces findings in `check`; one without produces
none and is reported as such; the gate floor applies to it identically; and the `ask` path and the
`check` path agree on the same store — the two-spellings guard, since they will otherwise drift.

**Size:** small. One function, one call site removed, four tests. It converts a feature that is
half-real into one that works.

# 1. Cost ledger

## The gap

`jev.max_spend_usd` caps a run; `Budget.spent_usd` accumulates in memory and dies with the
process. The store holds `input_tokens` per decision and **no dollars, no output tokens, no model
name**. The total is derivable and never shown:

```
45,806,589 input tokens / 4,946 calls  ->  $1.92
  assay.claims    5,794 answers   $1.51
  assay.verify    1,742 answers   $0.18
  assay.traverse  1,629 answers   $0.16
```

**Both of those figures are wrong, and the way they are wrong is the reason for the table below.**
`input_tokens` is a per-CALL measurement written onto every ANSWER row of that call, so a batch of
eight questions about one state counts eight times. Measured on the field store, day 20, where the
provider did return call ids:

```
gen-dec-1789955340-LJwigEfZTOqHHY2hU1oj  sentence__0..7   6,573 tok on each of 8 rows
                                                          -> 52,584 counted for 6,573 spent
```

Summed per call instead of per row:

```
                        tokens        usd
naive row sum       101,163,351      $4.25     <- what an earlier draft of this spec anchored on
honest per call      31,426,560      $1.32
```

Two independent checks that the second is right. On day 20, grouping by the provider's `call_id`
and grouping by `(decision_key, state_hash, prompt_version, model_version, input_tokens)` both
return exactly 4,946 calls and exactly 14,567,698 tokens. And the implementation, run against a
copy of the field store, produces 10,252 calls and **$1.3199** with no knowledge of either figure.

**$1.32 lifetime is the sanity anchor.** By day:

```
2026-09-21   5,306 calls   16,858,862 tok   $0.7081
2026-09-20   4,946 calls   14,567,698 tok   $0.6118
```

**And half the store could not say what a call was.** `call_id` was `resp.get("id") or ""`, and one
provider returned no id for a whole day: 9,762 of 19,707 decisions carry an empty one. assay mints
its own now, and history is reconstructed by the tuple above, which is checked against the
provider's own ids before it is trusted and refused if they ever disagree.

## Build

**Schema.** A table, not three columns, because the unit that has a price is the CALL and
`model_decisions` is one row per ANSWER. Three columns on the answer row reproduce the defect
above: written whole they sum to 8x, written as a share they are not a measurement of anything.

```
model_calls(call_id pk, id_source, caller, model_name, input_tokens, output_tokens,
            usd, usd_per_input_token, called_at)
```

`NEVER_PRUNED`: it is the record of what was spent. `usd` is written, not derived on read, and the
rate that produced it sits on the row — a rate change must not silently rewrite history, the same
argument this project already has for `prompt_version`.

`output_tokens` is recorded and **never priced**. Jev does not bill output, and multiplying it by
anything would be inventing a rate.

`model_decisions.input_tokens` stays where it is. It is a true fact about that row's call and it
always was; a test asserts nothing in the codebase sums it.

**Command.**

```bash
assay cost                    # by caller, by family, by day
assay cost --since 2026-09-01
assay cost --json
```

**What it must not do.** A call whose usage the provider did not return records `usd` as NULL,
and the report says *"N calls returned no usage and are not in this total"*. Never an average
presented as a measurement — the rule every other absence follows here.

**Tests.** A NULL-usage row is excluded and counted; the rate is read from the row, not recomputed;
two runs over one store produce one total.

**Size:** small — three columns, one command, one migration.

## Done when

- [x] `model_calls` holds one row per call; existing stores migrate without losing rows, and the
      calls behind 19,707 existing decisions are reconstructed and marked as reconstructed
- [x] **no second timestamp.** `decided_at` already exists, is populated on every row (19,707 of
      19,707 on the field store, 0 null) and already buckets by day. A `created_at` or
      `charged_at` beside it is two spellings of one fact, and this project has paid for that
      three times tonight alone. `--since` reads `decided_at`.
- [x] `usd` is written at decide time from the rate then in force, never derived on read
- [x] `assay cost` totals by caller, by family, by day; `--since`; `--json`
- [x] a call the provider returned no usage for records NULL and the report says how many
- [x] two runs over one store print the same total — integer tokens grouped by rate, one multiply each, added in rate order, because `sum()` over a DOUBLE moves with row order
- [x] the number matches a hand-computed **per-call** sum on a real store: $1.3199 over
      31,426,560 tokens in 10,252 calls. It must NOT match `sum(model_decisions.input_tokens) *
      rate`, which is $4.25 and is the bug this item exists to fix

---

# 2. Stale against the code

## The gap

`live_decisions` counts an answer stale by `prompt_version` — whether the QUESTION changed.
Nothing asks whether the CODE changed. A model edited after being judged serves its old answer
silently, and the only way to find out is to pay to re-ask.

## The fix is already in the manifest

```json
"checksum": {"name": "sha256", "checksum": "80dff5c259c242a7e912d4bb7be70f34..."}
```

**358 of 358 models carry one. assay reads none of them.**

## Build, two tiers, both free of API cost

**Cheap.** A `file_checksum varchar` column on `model_decisions`, written at decide time.
Comparing to the current manifest is a dict lookup. It answers *"this model's source moved"* —
necessary, not sufficient: a comment edit trips it, and a change to a PARENT does not.

**Exact.** `state_hash` is already stored. Rebuilding a subject's state and re-hashing costs CPU
and no calls — it is what `decide()` already does before choosing to call. It answers *"the thing
this answer was computed from is different now"*, including parent changes.

```bash
assay stale            # N judged answers are about SQL that has since changed
assay stale --exact    # rebuild the states; slower, no calls, catches parent drift
assay stale --cost     # ...and what re-asking them would cost, before you spend it
```

**Why this one first after cost.** It converts an unquoted spend into a quoted one. Today
re-running the judged tier is "some money"; after this it is a number you see first. Everything
judged becomes easier to authorize.

**What it must not do.** Not hide the stale answer. `live_decisions` already argues this and is
right: *"hiding it leaves the caller with nothing, which is strictly worse than serving it dated."*
Stale is reported, never suppressed.

**Size:** small-to-medium.

## Done when

- [x] `model_decisions.file_checksum` is written at decide time from the manifest's own sha256,
      off a map the command registers with `store.use_project(project)`. A test walks every
      `decide()` call site and fails on one that forgets, with two declared exceptions that
      carry their reasons (`disagreements` asks about verdict pairs, `judge_overlap` about
      assay's own question bank — neither is a dbt model)
- [x] `assay stale` lists judged answers whose model's checksum has moved, by family and by reach
- [ ] `assay stale --exact` — **NOT BUILT, and the specced design does not work.** Measured:
      rebuilding all 871 model and edge subjects of the field project through `subjects.build` and
      hashing them reproduces **0** of the stored `state_hash` values, because the call sites add
      to the state before sending it (`{**sub.state, "vocabulary": ...}`) and several families
      build their state elsewhere entirely. Shipped as specced, `--exact` would report 100% of
      judged answers as drifted on a warehouse where almost nothing changed — a wrong answer
      presented as measured, which is the defect this tool exists to find. Needs a decision:
      a transitive checksum over ancestors (exact about the code, no state rebuild), or making
      the state builders the single path every caller sends through
- [x] `assay stale --cost` quotes what re-asking would cost — the sum of those answers' OWN
      calls at the rate stored on each, not an average applied to a count
- [x] a stale answer is still SERVED, never hidden — this module returns counts and lists and
      removes nothing from anybody's read path
- [ ] on the field store, the count is non-zero — **cannot be met yet, and must not be faked.**
      All 18,079 current answers there were decided before the column existed, so every one
      reports `cannot be checked` (17,889 with no checksum, 190 under keys that name no model —
      `pair::` and `bank::`, exactly as predicted). Backfilling today's checksum onto them would
      make every one read `current`, which is the lie this whole tool is about. The count goes
      non-zero the first time the judged tier runs again and something is then edited.

---

# 3. Understanding rollup

## The gap

`project.coverage()` is a parse report — models, sources, readable, where the SQL came from. There
is no per-model *"how well is this understood"*, and every input already exists:

| component | source |
|---|---|
| grain known, and on what evidence | `ModelEntry.grain.source` — declared / judged / derived |
| provenance resolved | `provenance.kind`; `unknown` is the gap |
| claims verified | `claims` joined to `model_decisions` |
| ruled on by a person | `adjudications` where `source='human'` |
| anything counted | `observed_keys` — 24 of 358 relations today |
| how much rests on it | `descendants`, `marts` |
| judged answers still current | feature 2 |

## Build

```bash
assay understanding              # one row per model, components, ordered by reach
assay understanding --gaps       # only models where something is unknown
```

**What it must not do: no single blended score.** A model with a declared grain and no human
verdict is in a different situation from one with a judged grain and eight verdicts, and averaging
them to `0.62` destroys the distinction that decides what to do next. Components stay separate and
the ordering is by blast radius, because that is what decides where an afternoon goes.

The sentence it exists to produce is not a number:

> 12 models carry 19 marts between them, have no declared grain, and nobody has ever ruled on a
> finding in any of them.

**Size:** medium. A join over facts that exist, plus a surface, plus the discipline not to average.

## Done when

- [ ] `assay understanding` prints one row per model with each component separate: grain + its
      evidence, unresolved provenance count, claims verified, human verdicts, counted keys, marts
- [ ] there is NO blended score anywhere in the output
- [ ] ordering is by blast radius, and ties break deterministically
- [ ] `--gaps` shows only models with something unknown
- [ ] it produces the sentence it exists for: *N models carry M marts, have no declared grain, and
      nobody has ruled on any of them*

---

# 4. Anomalies and counts, via Elementary

## The gap

`observed_keys` covers **24 of 358** relations and counts *keys* — uniqueness and nulls. Nothing
tracks row counts over time. "This table halved last night" is invisible.

## Elementary already does it, here, now

```
main_elementary.data_monitoring_metrics        7,759 rows   row_count per table per bucket
main_elementary.alerts_anomaly_detection         476 rows   anomalies, already computed
main_elementary.dbt_source_freshness_results     105 rows   the freshness gap, already measured
```

assay reads none of them. **Read, do not rebuild** — the same treatment `practices` gives
dbt-project-evaluator's `fct_*` tables rather than reimplementing 23 checks.

## The three states, told apart

`practices.py` already carries this law, learned the hard way:

> *Five `fct_` models of many were built, and the categories whose tables did not exist were
> reported as nothing at all. An absent table and an empty one are not the same fact, and only one
> of them is a pass.*

| state | what assay says |
|---|---|
| package absent | *volume is not measured here, and nothing in this report covers it* |
| installed, never run | *Elementary is present and its models are not built* — a different fix |
| built, one bucket | *an anomaly needs two observations* — the rule `observed_keys` already has |

Never "no volume anomalies".

## And the one place a judged question earns its place

Elementary detects with no semantics: *"`water_parcels` row count fell 41%."*

assay has the semantics and no volume: the declared grain, the claims the project's own prose
makes, that the description says *"the FULL assessor roll"*, and that **19 marts** read it.

Joined:

> `water_parcels` fell 41% overnight. Its description claims "the FULL Maricopa assessor roll" —
> a claim `code_contradicts_a_claim` already flags, because line 27 drops rows missing an address
> or an owner. 19 marts read it.

The question is **not** "is this anomaly real" — counting settled that. It is
***"does this movement contradict what this project says about itself?"***, which needs the claim,
the grain and the drop in one state, and is the shape the claim families already handle. One new
family, `volume_contradicts_a_claim`.

Neither tool produces that sentence alone, and assay rebuilds nothing to get it.

## If the project has no Elementary

Push the way `suggest` does — name the candidate and the measurement, never the conclusion. assay
can compute what Elementary *would* buy without it being installed, because it already knows how
many sources carry no freshness declaration and how many marts rest on them:

> 6 sources feed 19 marts and nothing watches them for volume or silence. assay does not measure
> that and does not intend to; `elementary-data` does, and it is a dbt package.

Specific, checkable, and it stops the moment the tables appear.

## The architectural cost, stated

Elementary's tables live in the warehouse, so reading them needs the `dbt show` path `probe` uses —
a working connection. assay's structural tier works with **no credential at all** and that is part
of what it IS. Anything sourced from Elementary belongs in the **counted** tier beside `probe` and
`--verify`, never the free one, and the docs must say so rather than blurring the line that makes
the free tier trustworthy.

**Size:** medium for the read and the absence handling, small for the join, one new judged family.

## Done when

- [ ] a reader for `data_monitoring_metrics`, `alerts_anomaly_detection` and
      `dbt_source_freshness_results`, through the `dbt show` path `probe` uses
- [ ] the three absence states are distinguished and none of them reads as "fine"
- [ ] **the deferral announces itself**, per 0.38.1 — this is not optional
- [ ] anything sourced from Elementary is in the COUNTED tier in the docs, never the free one
- [ ] `volume_contradicts_a_claim` exists as one family, declaring `finding_when`, going through
      the generic producer shipped in 0.38.0 — not a hand-written function
- [ ] with no Elementary, assay still names what it would buy, computed from what it already knows
- [ ] it produces the sentence: *`stg_maricopa_parcels` fell 41%. It claims to be "the FULL
      Maricopa assessor roll" — a claim already flagged because line 27 drops rows missing an
      address or owner. 22 marts read it.*

---

# Order, and why

Cost first because it is smallest and answers a question with a real scar behind it. Stale second
because it is free and turns every future judged spend into a quoted one, which makes everything
after it easier to authorize. Understanding third because it uses only facts that already exist
and tells you where the judged tier is worth spending. Elementary fourth because it is the biggest
and produces something neither tool has alone. The package last, and only if it still looks worth
it by then.

**1 and 2 share a migration.** Both add columns to `model_decisions`. Do them in one pass or the
second rebuild is wasted work on a table holding paid data.

**3 wants 2.** "Are this model's judged answers still current" is a component of understanding,
and without item 2 it cannot be answered.

**4 wants 0.38.0**, which shipped: `volume_contradicts_a_claim` should be a declared family going
through the generic producer, not a sixth hand-written function. If writing it feels like it needs
a hand-written function, that is a signal the generic producer is missing something — fix that
rather than working around it.

# What this deliberately does not propose

- **Shipping dbt models.** Every unreadable model in this warehouse came from a package. A tool
  that finds opacity must not install it.
- **Reimplementing volume monitoring.** Elementary is good at it and already installed.
- **A single health score.** Blending grain, provenance, claims and verdicts destroys the
  distinction that decides what to do next.
- **A hook that can fail a build.** Gating stays in `assay check`, run deliberately, against a
  config the user wrote.
- **Estimating cost where the provider returned no usage.** NULL and a count, never an average.

---

# 5. The dbt package — approved, built last

Still last, because items 1–4 are what it surfaces: ship it earlier and the hook prints a findings
count.

**The one thing that must not slip.** The engine cannot run in dbt, so the hook can only ever show
the LAST EXPORTED state. Skip assay for three weeks and it prints three-week-old findings on every
build. A surface that cannot be live and does not say so is the defect this whole tool exists to
find, and 0.38.1 made announcing exactly that a rule. So every line the hook prints carries
`as of <assay_runs.started_at>`, and when that is older than the current invocation by more than a
day it says so in those words rather than leaving the reader to do the subtraction.

`action.yml` stays the live path and the two are not competing: the action runs the real checker
on a PR, the hook shows the last known state where you already are.

## What "become a dbt package like Elementary" can and cannot mean

Three separable things, and they have different answers.

**The engine cannot be a package.** dbt's `on-run-end` runs SQL macros. assay's checker is Python
walking sqlglot ASTs over compiled SQL. Not a preference — a wall.

**It must not ship models, and this was already decided on measurement.** From this warehouse,
re-verified:

```
models by package:      sunny_data 328,  elementary 30
UNREADABLE by package:  elementary 30
```

**Every unreadable model in this project belongs to an installed package. None of the project's own
are.** Elementary's models are macro-generated, so there is almost no SQL to read until dbt
compiles them, and they arrive carrying 541 columns of unknown provenance. A tool that finds
opacity must not install opacity. That is disqualifying, not ironic.

**The fit and the config shape are worth taking, and there is a clean way.** Elementary ships
**1,097 macros**, and assay parses none of them — macros are not nodes:

```
node resource_types assay reads:  model 358, test 1291, seed 35, operation 2
macros:                           1788 total, 1097 from elementary, 0 parsed
```

So: **a macro-and-hook-only package. Zero models. Zero added opacity.**

## What the package is

```
dbt-assay/                      # the dbt package, shipped from this repo
  dbt_project.yml               # name: assay, on-run-end hook, no model-paths
  macros/
    on_run_end.sql              # the hook
    assay_open_findings.sql     # reads the seeded tables
    assay_config.sql            # var() lookups with defaults
```

`packages.yml` in the consuming project:

```yaml
packages:
  - package: ryan-sunny/dbt_assay
    version: [">=0.38.0", "<1.0.0"]
```

`dbt deps`, and the hook fires on every `dbt build` with **no further setup** — which is the
property worth having and the reason Elementary's shape is right here.

## What the hook does, and what it must not

It runs after every build, in SQL, against relations that already exist because `assay export`
seeded them.

**It does:**
- read `assay_findings` and `assay_adjudications` for the current run;
- print the open **agreed** findings — the ones a person read and called real — with model and
  check. Nothing else: a hook that prints 254 findings on every build is a hook people disable in
  a week;
- print the loop number: *of the N findings a person agreed with, M are gone*;
- optionally materialise `assay_plan` as a relation, so BI and other models can read it.

**It must not:**
- fail the build. A package that can turn somebody's build red on install is a package nobody
  installs. Gating stays in `assay check`, which the user runs deliberately, in CI, with a config
  they wrote;
- run any check. The seeds are the output of a check that already happened;
- write anything if the seeds are absent — a project that has never run `assay export` gets
  silence, not an error.

## Config: `vars:` and `audit.yml` must not become two spellings

The hook needs a handful of settings and `audit.yml` is a Python-side file the SQL cannot read.
The rule: **`vars:` configures only the HOOK, and never anything `audit.yml` already decides.**

```yaml
vars:
  assay_on_run_end: true         # print at all
  assay_schema: analytics        # where the seeds landed
  assay_print_limit: 10          # how many agreed findings to show
```

Nothing about questions, waivers, gating or spend appears here. Those decide what a build FAILS
on and they live in one file. If a setting is ever wanted in both, it belongs in `audit.yml` and
the hook reads the seeded `assay_runs` row instead — the config that produced the data travels
with the data.

**Size:** small. No Python. A `dbt_project.yml`, three macros, and a fixture project to test
against.

---

## Done when

- [ ] a package published from this repo — `dbt_project.yml` with a name, `on-run-end`, and
      **no `model-paths`**
- [ ] `dbt deps` installs it and the hook fires on `dbt build` with no further config
- [ ] **zero models.** A test asserts the package ships no `.sql` under `models/`, because the
      whole argument for macro-only collapses the moment one appears
- [ ] the hook reads only relations `assay export` + `dbt seed` already created, and computes
      nothing
- [ ] it prints the open AGREED findings and the loop number, not the whole findings list
- [ ] **every line carries `as of <date>`**, and a state older than a day says so
- [ ] absent seeds produce silence, not an error — a project that has never run `assay export`
      must be able to install this
- [ ] it CANNOT fail a build, and a test proves it: gating stays in `assay check`
- [ ] `vars:` covers only `assay_on_run_end`, `assay_schema`, `assay_print_limit` — nothing that
      `audit.yml` decides, or they become two spellings
- [ ] it is tested against a real fixture dbt project, not asserted in prose
