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
- Lean is not installed on this machine, and it is not a Python package: assay manages its own
  (see "Lean is part of assay" below).

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

Designed in section 4: the Guarantees tab, the model pane's "correct as long as", the "why it
is back" block on a raised finding, MCP `premises(model)`, and `check` printing status changes.

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

### The trust boundary, and how it is closed

Through L3, Lean checks claims about a formal model of tables and about the STRUCTURE assay parsed
from the SQL; the page says "proven from the parsed structure". L2 adds a per-model round-trip that
measures whether the parse is faithful, and L4 proves it. After L4 the only link that is not proven
is "Lean's definition of SQL behaves like the engine", which no tool can prove (the engine is not a
mathematical object) and which the conformance suite measures instead. Every link is then either
proven or measured, and each measured one is a ledger premise.

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

### Lean is part of assay (all four phases are in scope)

Decided 2026-09-24: L1 to L4 are all built, as a core part of dbt-assay, not an optional extra.
Lean is not a Python package and its toolchain is far over PyPI's wheel size limit, so assay
manages its own:

- `pip install dbt-assay`, then `assay prove` works with no other install step.
- On first use assay downloads the Lean toolchain version it is pinned to from Lean's official
  releases, verifies the checksum, and keeps it in `~/.cache/assay/lean/` (never on PATH, never
  touching an existing elan install).
- The layer-1 and layer-2 sources ship inside the package and are compiled into that cache once
  per assay version.
- `assay prove --setup` does the download and compile ahead of time, for a Docker image or CI, so
  a scheduled run never downloads. `--offline` refuses to download and says what is missing.
- Each assay release pins exactly one Lean version, so the same model proves the same way on every
  machine. A Lean upgrade is an assay release, with the conformance suite rerun.

### Phase L2: `assay prove`, per-model certificates

Uses the toolchain assay manages. In the Dagster container, `assay prove --setup` runs in the image
build.

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
6. **The parse is checked against the SQL, on the warehouse.** assay prints its parsed structure
   back to SQL with a printer it controls, generates adversarial rows for every input (duplicate
   keys, NULL keys, ties in sort order, empty inputs), replaces each `ref()` with an inline
   `VALUES` list of them, and runs the original and the printed SQL through `dbt show` on the
   project's own connection. Read-only, no tables created; each statement is priced in the
   warehouse ledger like any other. DuckDB is the fallback when there is no connection. The result
   is the premise `parse_faithful(model)`: `holding` when the two agree, `broken` with the rows
   that differ, `unchecked` when the model uses something the printer cannot express. A proof
   about a model whose parse premise is not `holding` says so.
7. Incremental equivalence belongs here: "an incremental run equals a full refresh, given
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

### Phase L4: the parse is proven, not only measured

No verified SQL parser or SQL semantics exists in Lean (checked 2026-09-24). The closest work is
in Coq: Benzaken and Contejean's mechanised semantics for select/distinct/where/group by/having
with NULLs, aggregates and correlated subqueries (CPP 2019), their certified analyser and the
DBCert verified compiler; Cosette and HoTTSQL for query equivalence; and CompCert's parser, which
is generated by Menhir with a certificate a small Coq checker validates. None is usable from Lean.

So assay does not write a verified parser. It proves EACH PARSE, the way CompCert validates its
parser's output rather than trusting the generator:

1. sqlglot parses the model, as now, for every dialect.
2. The parse tree, restricted to the fragment Lean knows, is handed to Lean as a term.
3. Lean prints it with a printer proven correct against the fragment's grammar, tokenises the
   original SQL with a proven tokeniser, and checks the two token streams are equal, ignoring
   whitespace, comments, keyword case and redundant parentheses.
4. Equal: the tree is exactly what the text says, proven for this model, and
   `parse_faithful(model)` is `proven` rather than measured. Not equal: sqlglot misread the model,
   and the difference is the report. Outside the fragment: "parse unproven", falling back to the
   L2 round-trip.

**The meaning is written in Lean**, as definitions of what each construct in the fragment does to
a table (bag semantics, SQL NULLs and three-valued logic, joins, grouping, windows). Benzaken and
Contejean's Coq semantics is the reference for getting it right. It is the L1 table model grown to
cover the syntax.

**Engine conformance**, the one link that is measured, not proven: for each construct (a LEFT
JOIN on NULL keys, `row_number` ties, `count(x)` against `count(*)`, integer division, NULL in
`IN`, and so on), the Lean evaluator and the engine run the same inputs and must agree. Run
against Snowflake and DuckDB, once per assay release and again when the engine version changes.
Each construct's result is a ledger premise, `engine_conforms(construct, engine, version)`; a
proof that uses a construct whose conformance is not `holding` says so.

**Fixed means:** for a model inside the fragment, `assay prove` reports the parse proven; a
deliberately corrupted parse tree is refused with the token where it diverges; a construct whose
conformance run disagrees with Snowflake marks every proof using it.

### How the meaning is codified, and what is generated

Three layers; only the first is written by hand.

1. **The meaning of SQL** (`lean/Sql/Semantics.lean`, shipped in the package). A data type mirroring
   the supported part of sqlglot's tree; values with NULL (`Option`); three-valued truth (`WHERE`
   keeps only `true`); `evalExpr` and `evalQuery`, one clause per construct. Written once, ported
   from the Benzaken and Contejean Coq semantics. It cannot be generated: there is no
   machine-readable definition of an engine to generate it from. It is kept honest by being
   EXECUTABLE: the same definitions run, so random differential tests (Plausible on the Lean side;
   thousands of small tables with duplicates, NULLs and ties) and the conformance suite compare
   `evalQuery` with Snowflake and DuckDB on identical inputs, and any disagreement fails.
2. **The rules** (`lean/Sql/Rules.lean`, shipped). The L1 theorems, proven once against layer 1.
3. **Per model, generated every run** from the parse and templates: the model as a `Query` term
   (the part L4 proves matches the SQL text), one theorem per applicable property (no fan-out,
   grain, pick determinism, incremental equals full refresh) with its ledger premises as named
   hypotheses (`p_<premise_id>`), and a proof that applies a layer-2 lemma. A goal automation
   cannot close is reported as the missing premise, or offered to the agent under L3.

### Where things live

- Layers 1 and 2 ship inside the package. Lean compiles them once into a per-machine cache
  (`~/.cache/assay/lean/`), reused by every project. A user project never contains them.
- Layer 3 files are build output, regenerated by every `assay prove`, written to
  `target/assay/lean/` beside dbt's own build output (already gitignored in dbt projects). Never
  next to the models, never committed.
- Results live in the store: a `proofs` table (model, checksum, property, premises, status, Lean
  version). Page, MCP and the ledger read it.
- Agent-written proofs (L3) cannot be regenerated for free, so their source is kept whole in the
  store, tied to the model checksum. `assay proofs --export <dir>` writes them out for anyone who
  wants them in git.
- The warehouse is never written to by default. `assay export` gains `proofs` and `premises` as
  tables, so a dashboard or a Dagster asset can read which marts are proven and which premises are
  broken with plain SQL.

## 4. The UI, for every item above

Every item ships with its UI in the same commit, built to the page's existing rules (from
sunny-data's `docs/audit/assay-feedback.md` and the 0.51.x rounds), and screenshotted at 1500px,
1100px and 420px before it is called done. The rules:

- **Navigator:** a high-volume list is the three-column layout (groups on the left with counts,
  rows in the middle, paged at 200, the picked row on the right), built with `drill()`. Never a
  stacked report to scroll.
- **One detail layout:** `pane()`, in the order kind, title, where, the one-line what, the reading,
  what to do.
- **No grey sentences:** an explanation is a tip on the tab, heading, column header or label;
  only facts about this warehouse are text, in ink.
- **One badge:** `badge(label, class, confidence)`, one line, spaced from the text before it, the
  confidence as a number beside it. Status colour only on status: `bad` (rust) for broken and
  failing, ink for holding and proven, ember for assumed and judged, faint for unchecked and
  unknown. Never a colour without its word.
- **One-row tab strip,** grouped under its labels, a menu below 940px.
- Long names break at `_` with `wbr()`; nothing scrolls sideways; wording is plain, with no
  "X, not Y" cadence.

### The page (assay.html)

**Tab strip: one new tab, `Guarantees`, in the "what is wrong" group** after Monitoring. It holds
premises and proofs together, because a proof is only as good as its premises and splitting them
would send a reader between two tabs to answer one question. The strip currently ends at 1287px
on a 1366px screen (79px spare) and a tab with a count is about 110px, so exactly one tab is
allowed: the 1360px step moves to 1440px and the counts-off step from 1180px to 1280px, and the
existing one-row browser test (960 to 1920px) is the gate. The tab's number is the premises that
are not holding (broken + unchecked + assumed + unknown); its tip says so.

**Guarantees tab** (navigator):
- A fixed strip on top, like Monitoring's at a glance: premises by status as one stacked bar
  (broken, unchecked, assumed, holding, unknown) with counts, and once L2 exists, proven properties
  out of attempted. A fact line in ink when anything is broken: "3 premises broke since the
  previous run".
- Groups on the left: `broken`, `unchecked`, `assumed`, `unknown`, `holding`, and (L2) `proven`,
  `not proven`, `parse unproven`. Each group's sub-line says what it costs, e.g. "12 findings held
  back on these".
- Rows: the premise in words ("`parcel_id` unique in `stg_co_parcels_composite`"), its status
  badge, `since`, and how many things rest on it.
- Pane: kind "premise · unique"; title the statement; where: the relation, linked to Models;
  the reading:
  - **evidence**, one row per source with its own badge and date: the dbt test with its last
    result ("`unique_stg_x_parcel_id` · never ran"), the probe count ("118 duplicates in
    412,901 rows · 2026-09-14"), the judgment ("column_is_part_of_the_key · 0.91");
  - **what rests on it**: grains, findings held back, proofs, each linked to its tab;
  - **what to do**, only when not holding: the single concrete step ("run `unique_stg_x_parcel_id`
    in the next build", "`assay probe --select stg_x`", "the key is broken: 118 duplicates; either
    dedupe upstream or change the join to include `county`").
- For a proof row (L2): kind "proof · no fan-out"; title the model; the reading: the property in
  words, its premises each with a status badge, the rule it applied (`left_join_preserves_rows`,
  tip: "proven once in assay's Lean library"), the parse premise, and the Lean version. A proof
  whose premises are not all holding reads "guarantee lost: `p_7f3a` broke on 2026-09-14".

**Models pane:**
- The grain row keeps its value; its badge shows the evidence behind it ("declared · never ran",
  "declared · passing", "counted", "judged 0.91"). (G-A)
- A new section **correct as long as**: that model's premises with status badges, one line each,
  linked to Guarantees. Omitted when the model has none.
- A new section **proven** (L2): each property, proven / not proven / guarantee lost, with its
  missing premise when not proven.
- Incremental models (G-D): a section **incremental** with strategy, `unique_key`, lookback or
  `event_time`, `on_schema_change`, each as a key-value row; anything a G-D check flagged carries
  its badge on the row.

**Findings:**
- New checks slot into the existing navigator as groups: `fixed_finding_returned`,
  `float_sum_is_not_reproducible`, the five incremental checks. Their panes use `pane()`.
- `fixed_finding_returned` pane: a short timeline as key-value rows (agreed by, on; fixed at
  commit, run; came back at commit, run), each commit linked when the repo has a remote, and the
  original finding linked. Weight shows its parts as today.
- A finding RAISED by a broken premise (held back until now) shows a block **why it is back**:
  the premise, its badge, the date it broke, and the measurement.
- A finding from a rule in `RULES_PROVEN` shows a badge `proven rule` beside "found by", tip:
  "this rule is proven in Lean; the finding depends only on the parsed structure and the premises
  listed".
- `float_sum_is_not_reproducible` pane shows the column, its type, the aggregate, and the one-line
  recommendation `cast(<col> as decimal(18, 2))`.

**Overview:**
- The loop tiles gain **regressed** beside fixed and still open, `bad` when above zero. (G-B)
- A tile **guarantees**: "N premises not holding · M broken", linking to Guarantees.
- Once L2 exists, a tile **proven**: "X of Y properties proven", tip naming the trust boundary.

**Monitoring:** a never-ran or skipped test that backs a premise says so in its row detail:
"backs the grain of 3 models", linked to Guarantees.

**Spend:** warehouse statements from the L2 round-trip and L4 conformance runs appear under their
own callers (`assay.prove`, `assay.conformance`) in the existing by-caller table.

**Areas, Claims, Answers, Questions, Config:** no layout change. The arrival-time question for G-D
appears in Answers and Questions as any family does.

### The review form (review.html)

- New finding types appear in the Findings navigator like any other and are ruled the same way.
- A `fixed_finding_returned` card shows the timeline block from the page, and its verdict meanings
  are specific: agree ("it is back and must be fixed again"), disagree ("it is not the same
  defect"), accept ("it is back on purpose").
- A card raised by a broken premise shows the **why it is back** block.
- Premises are never ruled in the form: they are measured. An `assumed` premise's card-free route
  is the judgment it rests on, which Answers already shows.

### Terminal and MCP (also UI)

- `assay check` prints, after the loop line, premises whose status changed since the previous run
  ("broke: `parcel_id` unique in `stg_co_parcels_composite` · 118 duplicates") and the regressed
  count. Nothing printed when nothing changed.
- `assay prove` prints one line per model: proven properties, not proven with the missing premise,
  parse proven / measured / unproven, and at the end the totals and where the files were written.
- MCP `premises(model)`, `proofs(model)`, `proof_goal`, `check_proof` return the same fields the
  page shows, so an agent and a person read the same thing.

### Checks that gate the UI work

- The one-row tab strip test passes at 960 to 1920px with the Guarantees tab added.
- `docs/audit/assay_page_checks.py` (sunny-data) still passes 7 of 7.
- A browser test opens Guarantees, picks a broken premise, and finds its evidence and its
  dependents in the pane; another opens a `fixed_finding_returned` finding and finds both commits.

## Build order

One commit per item, tests with each. Each item ships with its UI from section 4 in the same
commit, screenshotted at 1500, 1100 and 420px.

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
11. L4: the fragment's meaning in Lean, the proven printer and tokeniser, per-parse checking, and
    the engine conformance suite against Snowflake and DuckDB.

Items 1 to 7 need no Lean. Item 8 needs Lean only in CI. Items 9 and 10 need it wherever
`assay prove` runs.
