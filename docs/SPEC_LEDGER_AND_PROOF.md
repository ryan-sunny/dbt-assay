# Spec: the premise ledger, four gaps, and Lean

Status: agreed in conversation 2026-09-24, not built. Build order at the end.

## The principle

assay never writes the fix. It says exactly what "correct" means, what is missing, and checks the
result. Three kinds of evidence, never blended:

- **Measured**: what the data does (probe, dbt test results, Elementary).
- **Judged**: what things mean (Jev), with a probability.
- **Proven**: what follows with certainty from the parsed structure, checked by Lean.

A proof is always conditional: "true as long as these premises hold". The premises are the ledger,
and the ledger is where measurement and proof meet.

## Grounding (checked in the code before writing this)

- Facts already carry `source` (declared, observed, derived, judged) and `resting_on`
  (`inventory.py`, `Fact`). Only the grain fills `resting_on` today.
- Checks already rely on premises without recording them: `hop_multiplies_rows` is dropped when the
  parent key is unique (declared or counted); `arbitrary_pick` when its last sort key is declared
  unique; `join_fans_out` compares the join to the declared key.
- `probe` stores per-column uniqueness and nulls with dates (`observed_keys`) and already raises
  `key_stopped_holding`.
- Test outcomes are readable: Elementary (`monitoring_bank.ran_test_ids`, test results) and
  `run_results.json` (`outcomes.read`).
- A declared grain is set from the manifest's tests with no look at whether the test ran
  (`inventory.py:337`).
- Column types come from `catalog.json`, then the manifest (`cost.py`).
- Per-run findings keep `finding_id` and `file_checksum`; agreed rulings are per finding;
  `store.finding_key` and `store.carried` match a finding across renames (N5).
- Nothing reads an incremental model's logic; only `config.unique_key` is read, as a key.
- Lean is not installed on this machine; users must not need it for anything but `assay prove`.

## 1. The premise ledger

### What a premise is

A statement about a relation that some fact, finding or proof depends on. v1 properties:

| property | example |
|---|---|
| `unique(cols)` | `parcel_id` unique in `stg_co_parcels_composite` |
| `not_null(col)` | `order_id` never null in `stg_payments` |
| `unique_per_batch(cols)` | an incremental model's `unique_key` is unique within one run's new rows |
| `max_lateness(col, days)` | no row arrives more than N days after its `event_time` |

`premise_id` = hash of (relation, sorted columns, property, parameter). Stable across runs.

### Evidence, and the status it yields

Every piece of evidence found for a premise is kept:

- **declared**: a dbt test, with its LAST ACTUAL RESULT from `run_results.json` or Elementary:
  `pass`, `fail`, `never_ran`, `skipped`. Also `config.unique_key` (declared, no test).
- **observed**: the latest `observed_keys` row and its date.
- **judged**: `column_is_part_of_the_key` and its confidence.
- **none**.

Status, strongest evidence wins, measured beating declared beating judged:

| status | when |
|---|---|
| `broken` | observed false, or its test's last result is `fail` |
| `holding` | observed true, or its test ran and passed |
| `unchecked` | declared only, and the test never ran or was skipped |
| `assumed` | only a judgment supports it |
| `unknown` | no evidence |

`broken` beats everything: one measurement or failed test that says false is enough.

### Dependents, and what a status change does

Every consumer registers the premises it used: the declared grain, each suppression in
`hop_multiplies_rows` / `arbitrary_pick` / `join_fans_out`, and (later) each proof.

- `holding`: the dependent stands as today.
- `unchecked` / `assumed`: it stands, but is no longer `firm`; it carries a `resting_on` entry
  naming the premise and why ("test `unique_stg_x_parcel_id` has never run"). Suppressed findings
  stay suppressed, marked "held back on an unchecked premise". Not raising them avoids a flood on
  projects with many never-run tests; the Monitoring side already counts those tests.
- `broken`: a suppressed finding is RAISED, with the reason in its evidence: "held back because
  `parcel_id` was unique; not unique since 2026-09-14 (probe: 118 duplicates)". A grain resting on
  it becomes `unresolved` with that reason.

### Storage (rebuildable, prunable: derived from manifest + store each run)

```
premises     (run_id, premise_id, relation, columns, property, param, status, since, evidence)
premise_uses (run_id, premise_id, dependent_kind, dependent_id)
```

`since` = the first run of the current unbroken status streak, read from earlier runs' rows.

### Surfaces

- Page: a **Premises** section in the navigator style (groups: broken, unchecked, assumed,
  holding), each premise showing evidence and dependents. A model's pane lists "correct as long
  as" with statuses. A finding raised by a broken premise names it.
- MCP: `premises(model)` returns what a model's facts and findings rest on, with status.
- `check`: prints premises that changed status since the previous run.

**Fixed means:** on the field store, a declared key whose test never ran shows `unchecked`; a probe
duplicate turns a suppressed `hop_multiplies_rows` into a raised finding naming the premise;
`premises(model)` returns the same list the page shows.

## 2. The four gaps

### G-A. A declared grain rests on a test that never runs (first slice of the ledger)

Join each declared key's test to its last result. The grain keeps its value; `firm` is false and
`resting_on` names the test and its status when that status is not `pass`. Evidence source in the
model pane reads "declared by `unique_x` (never ran)". Needs run results or Elementary; without
either, the status is `unchecked` and says why ("no test results were read").

**Fixed means:** a model whose only `unique` test never ran shows its grain as declared-but-
unchecked on the page, in `contract()` over MCP, and in the ledger.

### G-B. A fixed finding that comes back (new check `fixed_finding_returned`)

- A finding is **fixed** when `confirmed_and_fixed` counted it gone (code changed, not retired),
  in some earlier full run.
- It **returned** when a later full run has the same finding id, or a finding carrying it
  (`store.carried`).
- Raise `fixed_finding_returned` on the model: base 3 (high), default action `queue`, can be set
  to `fail`. Evidence: the original finding id, who agreed and when, the run and commit where it
  was gone, the run and commit where it came back.
- The loop gains a third number: **regressed**, beside fixed and still open. `check`, the page's
  loop section and MCP `contract` report it.
- A returned finding is never counted as fixed again until it goes again.

**Fixed means:** in a test store, agree, fix (checksum changes, finding gone), then reintroduce:
`check` prints 1 regressed and raises `fixed_finding_returned` naming both commits.

### G-C. A measure summed as a float (new check `float_sum_is_not_reproducible`)

Fires when all hold:
- the output column is built by `sum` or `avg` (parser `root` `agg:sum` / `agg:avg`) over an input
  whose type is `DOUBLE` / `FLOAT` / `REAL` (catalog, then manifest);
- its `column_role` is `measure` (judged; unclassified columns are counted, not flagged);
- the model is a mart or has a mart downstream.

Detail: floating-point addition is not associative, so the total depends on row order and moves
between builds on identical data. Recommendation: cast to `DECIMAL(p, s)` before aggregating.
Without a catalog the check reports the types as unknown, never as a pass.

**Fixed means:** a fixture mart with `sum(amount_double)` raises it; `sum(cast(amount as
decimal(18,2)))` does not; with no catalog the run says types were not read.

### G-D. Incremental models (Snowflake first)

Read from the manifest: `materialized = incremental`, `incremental_strategy` (Snowflake default
`merge`; also `delete+insert`, `append`, `microbatch`), `unique_key`, `on_schema_change`,
`event_time` / `lookback` / `batch_size` for microbatch, and the RAW code, because the
`is_incremental()` branch is not in the compiled full-refresh SQL. The `{% if is_incremental() %}`
block is extracted from the raw code, `{{ this }}` replaced by a placeholder, and parsed with
sqlglot.

| check | fires when | why it matters |
|---|---|---|
| `incremental_merge_without_key` | strategy `merge` or `delete+insert` and no `unique_key` | behaves as append: a rerun inserts duplicates |
| `incremental_key_not_unique` | the `unique_key` premise (`unique_per_batch`) is `broken` or `unknown` | Snowflake merge fails ("duplicate row detected") or updates arbitrarily |
| `incremental_filter_without_lookback` | the filter is `col > (select max(col) from this)` (or `>=`) with no subtraction | a row arriving late is skipped forever |
| `microbatch_without_lookback` | strategy `microbatch`, `lookback` unset or 0, and upstream has late arrivals | same, per batch |
| `incremental_schema_change_ignored` | `on_schema_change` unset or `ignore` and the model's output columns changed since the last run | new columns stay NULL in the existing table |

`incremental_filter_without_lookback` registers a `max_lateness` premise. Measuring it needs an
arrival column (`_loaded_at`, `_dlt_load_id`, `inserted_at`); a Jev question picks which column is
arrival time when the name does not say, and probe measures `max(arrival - event_time)`. With no
arrival column the premise is `unknown` and the finding says so.

**Fixed means:** each check fires on a fixture incremental model built for it and not on a
corrected copy; a Snowflake-style merge model with a correct key and a lookback raises nothing.

## 3. Lean

### What it adds that nothing else here can

Tests say "on today's data". Jev says "probably". A proof says "for every possible input that
satisfies these premises, this cannot happen". Its premises are ledger rows, so a proof's
guarantee is live: it stands while its premises hold and is reported lost the run one breaks.

### The trust boundary, stated on every surface

Lean checks claims about a small formal model of tables and about the STRUCTURE assay parsed from
the SQL. The step from SQL to that structure is sqlglot plus assay's parser, which is tested, not
proven. So the page says "proven from the parsed structure", never "the SQL is proven".

### Phase L1: the table model and the rules, in the repo

`lean/` is a Lake project, Lean 4, no Mathlib (keeps the toolchain to a few hundred MB).

- `Table.lean`: a row is a map from column to `Option Value` (`none` is NULL); a table is a
  `List Row` (bag semantics: duplicates allowed, order not meaningful); `Unique cols t`,
  `NotNull col t`.
- `Ops.lean`: filter, project, inner join, left join (a NULL key never matches, as in SQL), group
  by, and window `row_number` over (partition, order) keeping the first.
- `Rules.lean`, one theorem per rule assay enforces, each with no `sorry`:

| theorem | statement | assay rule it backs |
|---|---|---|
| `inner_join_no_fanout` | right side `Unique` on keys covered by the join → output length ≤ left length | `hop_multiplies_rows` suppression, `join_fans_out` |
| `left_join_preserves_rows` | same premise → output length = left length | the same, for left joins |
| `grain_through_join` | left unique on g, right unique on its join key → output unique on g | grain derivation |
| `filter_preserves_unique` | a filter keeps any uniqueness | grain derivation |
| `pick_is_order_independent` | if every projected column is a partition or order key, the result is the same for every permutation of the input | `arbitrary_pick` / N5 `picks_only_keys` |
| `pick_total_on_unique_key` | if the last order key is unique within each partition, the pick is order-independent | `arbitrary_pick` declared-unique exemption |

CI: a job installs elan, runs `lake build`, fails on any `sorry`, and `#print axioms` on each
theorem may list only Lean's standard axioms. A Python test holds `RULES_PROVEN`
(rule id → theorem name) and asserts each name exists in `lean/`. A finding from a rule in
`RULES_PROVEN` carries `proven_rule: <theorem>` in its evidence; the page shows it.

**Fixed means:** CI proves all six with no `sorry`; changing one rule's Python without its theorem
fails the mapping test.

### Phase L2: `assay prove`, per-model certificates

Optional: needs `lake` on PATH (`assay prove` prints the one-line elan install if not). Runs in the
Dagster container once elan is in its image.

1. From each model's digest, emit a small intermediate representation (relations, joins with kind
   and keys, filters as opaque predicates, projections, windows, group by) as a Lean term.
2. For each property that applies (row count through each join, grain, pick determinism), emit a
   theorem whose hypotheses are the model's premises, named by `premise_id`, proved by applying the
   L1 rule.
3. `lake build` checks it. Results go to a `proofs` table: (model, model checksum, theorem,
   premises, status `proven` / `failed` / `not_attempted`, Lean version, time).
4. A proof whose goal Lean cannot close reports the goal it was left with. When that goal is a
   missing premise, it becomes the recommendation ("needs `user_id` unique in `stg_users`").
5. Re-proved only when the model's checksum changes; otherwise only its premises are re-read from
   the ledger.
6. Incremental equivalence belongs here: "an incremental run equals a full refresh, given
   `unique_per_batch(unique_key)` and `max_lateness(event_time) <= lookback`", proved once as an
   L1 theorem and instantiated per incremental model.

Surfaces: the model pane gets "proven: rows cannot multiply through `stg_users`, as long as: …",
each premise with its ledger status. MCP `proofs(model)`.

**Fixed means:** on the field project `assay prove` produces certificates for the models whose
joins are covered, each lists its premises, and breaking one premise in a test store marks that
proof's guarantee lost without re-running Lean.

### Phase L3: the agent writes proofs, assay checks them

assay does not call an LLM to write proofs. It gives the agent that runs it everything needed and
checks what comes back, the same shape as the review loop.

- MCP `proof_goal(model, property)`: the goal as Lean text, the model's IR, its premises as named
  hypotheses, and the list of proven lemmas it may use.
- MCP `check_proof(model, property, lean_source)`: writes the source into a scratch copy of the
  Lake project, builds it, and returns `proven` or Lean's own error and remaining goal. A proof
  containing `sorry`, a new `axiom`, or anything `#print axioms` reports beyond the standard ones
  is refused.
- An accepted proof is stored with `written_by: agent`. Unlike a verdict, who wrote it does not
  matter: the kernel checked it. The page says "checked by Lean".

**Fixed means:** an agent can take a goal from `proof_goal`, submit a proof to `check_proof`, get
Lean's error back for a wrong one and `proven` for a right one; a proof using `sorry` is refused.

## Build order

One commit per item, tests with each, the page screenshotted where it changes.

1. Ledger core: `premises` / `premise_uses`, status from declared + observed + judged evidence.
2. G-A: test results joined onto declared keys (first ledger consumer).
3. Existing suppressions register their premises; `broken` raises them with the reason.
4. Ledger surfaces: page section, model pane, MCP `premises`, `check` status changes.
5. G-B: `fixed_finding_returned` and the regressed count.
6. G-C: `float_sum_is_not_reproducible`.
7. G-D: incremental checks, Snowflake strategies, `max_lateness` premise.
8. L1: `lean/` table model, ops, six theorems, CI job, `RULES_PROVEN` mapping, `proven_rule` on
   findings.
9. L2: IR emission, `assay prove`, `proofs` table, certificates on the page and MCP, incremental
   equivalence theorem.
10. L3: `proof_goal` and `check_proof` over MCP.

Items 1 to 7 need no Lean. Item 8 needs Lean only in CI. Items 9 and 10 need it wherever
`assay prove` runs.
