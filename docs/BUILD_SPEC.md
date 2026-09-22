# Build spec: the package shape, and four things it makes visible

Follows `VISIBILITY_SPEC.md`, which measured the gaps. This says what to build. Written
2026-09-21 against `sunny_data` — 358 models, 254 findings, 9,945 judged decisions, 602 tests.

Ordered by what compounds: the package is last, because three of the four features have to exist
before there is anything worth shipping in it.

---

# 0. The dbt package, and the line it must not cross

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

# 0b. A custom question can be asked and can never be a finding

**This is a correctness bug in a shipped headline feature, and it outranks everything below it.**

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

## Build

**Schema.** Three columns on `model_decisions`, added by the existing `_add_missing_columns` path:

| column | why |
|---|---|
| `output_tokens integer` | not stored at all today |
| `usd double` | computed at WRITE time from the rate then in force |
| `model_name varchar` | `model_version` is the question's, not the provider's |

`usd` is written, not derived on read. A rate change must not silently rewrite history, and this
project already has the parallel argument for `prompt_version`.

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

---

# Order, and why

0. **The generic finding producer.** A correctness bug in a shipped feature, not a new one: a
   custom question can be asked, paid for, stored and printed, and can never become a finding —
   while the linter demands the field that would fix it. Everything else here is an addition; this
   is something that is supposed to work already.
1. **Cost** — smallest, and answers a question with a real scar behind it.
2. **Stale** — free, and turns every future judged spend into a quoted one.
3. **Understanding** — uses only facts that exist, and tells you where 2 is worth spending.
4. **Elementary + `volume_contradicts_a_claim`** — biggest, and produces something neither tool has.
5. **The package** — last, because 1–4 are what it would surface. Shipping it earlier means
   shipping a hook that prints a findings count.

# What this deliberately does not propose

- **Shipping dbt models.** Every unreadable model in this warehouse came from a package. A tool
  that finds opacity must not install it.
- **Reimplementing volume monitoring.** Elementary is good at it and already installed.
- **A single health score.** Blending grain, provenance, claims and verdicts destroys the
  distinction that decides what to do next.
- **A hook that can fail a build.** Gating stays in `assay check`, run deliberately, against a
  config the user wrote.
- **Estimating cost where the provider returned no usage.** NULL and a count, never an average.
