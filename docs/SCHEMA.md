# The store

One DuckDB file, `assay.duckdb`, written by `assay check` and read by everything else. Twenty-one
tables. The whole design turns on one split, so it is worth stating before the diagram:

**Some of these cost nothing and some of them cost money or somebody's afternoon.** A table
carrying `run_id` is exactly one `assay check` rebuilds for free, offline, in seconds. A table
without one holds something that was paid for — a model call, or a person reading SQL. `assay
prune` deletes only the first kind, and the split is declared in code rather than inferred:

```python
PRUNABLE     = ("findings", "edge_facts", "unreadable", "premises", "premise_uses")
NEVER_PRUNED = ("model_calls", "model_decisions", "claims", "adjudications", "observed_keys",
                "runs", "calibrations", "compiled_sql", "commits", "states", "warehouse_calls",
                "test_status", "observed_lateness", "proofs", "parse_checks",
                "conformance", "claim_checks")
```

A new table belongs to one list or the other and a test fails until it does, so nothing becomes
silently prunable.

---

## The tables

![the store's ten tables](store-schema.png)

<details>
<summary>the same thing as Mermaid source</summary>

```mermaid
erDiagram
    RUNS ||--o{ FINDINGS : "one run produces"
    RUNS ||--o{ EDGE_FACTS : "one run measures"
    RUNS ||--o{ UNREADABLE : "one run could not read"
    RUNS ||--o{ PREMISES : "one run's ledger"
    PREMISES ||--o{ PREMISE_USES : "what rests on it"
    TEST_STATUS |o--o{ PREMISES : "a declared test's last result is evidence"
    OBSERVED_KEYS |o--o{ PREMISES : "a count is evidence"
    OBSERVED_LATENESS |o--o{ PREMISES : "how late rows arrive is evidence"
    PREMISES ||--o{ PROOFS : "a certificate assumes them"
    PARSE_CHECKS |o--o{ PREMISES : "parse_faithful"
    CONFORMANCE |o--o{ PROOFS : "what the engine does to a rule's constructs"
    PROOFS ||--o| CLAIM_CHECKS : "the claim, run against the model"

    STATES ||--o{ MODEL_DECISIONS : "one state, many answers"
    MODEL_CALLS ||--o{ MODEL_DECISIONS : "one call, many answers"
    MODEL_DECISIONS |o--o{ ADJUDICATIONS : "decision_key, when judged"
    CLAIMS |o--o{ MODEL_DECISIONS : "subject::claim::id"
    FINDINGS |o--o{ ADJUDICATIONS : "subject::finding::id"

    RUNS {
        varchar run_id PK
        timestamp started_at
        varchar project
        varchar assay_version
        varchar dbt_version
        int models
        int readable
        int unreadable "this project's own; installed packages are counted apart"
        varchar scope "NULL for a full run; a narrowed run is history, never the current one"
        varchar git_sha "the commit HEAD was on, +dirty when uncommitted"
    }
    FINDINGS {
        varchar run_id PK, FK "carrying this is what makes it free to rebuild"
        varchar check_name PK
        varchar subject PK
        varchar summary PK
        varchar finding_id "sha1(check/subject/summary/evidence)"
        varchar file
        varchar detail
        int base "1 low 2 med 3 high"
        double weight "base x reach"
        int descendants
        int marts
        varchar evidence "json"
    }
    EDGE_FACTS {
        varchar run_id PK, FK "carrying this is what makes it free to rebuild"
        varchar parent PK
        varchar child PK
        int available "columns the parent offers"
        int carried
        int dropped
        varchar joined_on "json list"
        varchar dropped_cols "json list"
    }
    UNREADABLE {
        varchar run_id PK, FK "carrying this is what makes it free to rebuild"
        varchar subject PK
        varchar file
        varchar reason
    }
    MODEL_DECISIONS {
        varchar decision_key PK "see the grammar below"
        varchar question PK
        varchar prompt_version PK
        varchar model_version PK
        varchar kind "choice / noul"
        varchar answer
        double confidence "null for a noul"
        varchar probabilities "json"
        varchar state_hash FK
        varchar call_id FK "which call produced it"
        varchar caller
        int input_tokens "THE CALL'S count, repeated on every answer -- never SUM this"
        varchar file_checksum "sha256 of the model's source when this was decided"
        varchar state_builder "which registered builder made the state"
        varchar state_inputs "json: the identifiers it was built from, so it can be built again"
        timestamp decided_at
    }
    MODEL_CALLS {
        varchar call_id PK "the provider's id, or one assay minted"
        varchar id_source "provider / minted / reconstructed"
        varchar caller
        varchar model_name "what ANSWERED"
        int input_tokens "null when the provider returned no usage"
        int output_tokens "shown, never priced -- Jev does not bill output"
        double usd "input only, null when usage was absent"
        double usd_per_input_token "the rate in force, ON the row"
        timestamp called_at
    }
    STATES {
        varchar state_hash PK
        varchar state "json, exactly what was sent"
        timestamp first_seen
    }
    ADJUDICATIONS {
        varchar subject PK
        varchar question PK
        varchar prompt_version PK
        varchar family
        varchar verdict "agree / disagree / unclear"
        varchar source "human / agent / label / replay"
        varchar decided_by "free text, nothing validates it"
        varchar note
        varchar correction
        varchar answered "what was ruled on"
        varchar decision_key FK "empty for a structural finding"
        varchar model_version
        timestamp decided_at
    }
    CLAIMS {
        varchar claim_id PK "sha256(subject, normalized text)"
        varchar subject
        varchar text
        varchar kind
        double kind_conf
        varchar source_kind "comment / schema.yml"
        varchar source_ref "file:line"
        varchar citation
        varchar status
        timestamp extracted_at
    }
    OBSERVED_KEYS {
        varchar relation PK
        varchar column_name PK
        timestamp observed_at PK
        bigint row_count
        bigint non_null
        bigint distinct_ct
        varchar status "unique / has_duplicates / has_nulls / unknown"
        varchar via "dbt-show / loaded"
        varchar minimality
        varchar detail
        boolean sampled "a sampled count is not a settled one"
        double sample_pct "0 when it was counted exactly"
    }
    PREMISES {
        varchar run_id PK, FK "rebuilt by every check"
        varchar premise_id PK "sha1(property/relation/sorted columns/param), stable across runs"
        varchar relation "unique_id of the model or source"
        varchar name
        varchar columns "json list"
        varchar property "unique / not_null / unique_per_batch / max_lateness"
        varchar param
        varchar status "broken / holding / unchecked / assumed / unknown"
        timestamp since "first run of the current status streak"
        varchar evidence "json list of kind, detail, status, at"
    }
    PREMISE_USES {
        varchar run_id PK, FK
        varchar premise_id PK, FK
        varchar dependent_kind PK "grain / held_back / raised_on / proof"
        varchar dependent_id PK "a model uid, or check:model:construct"
        varchar model "the model it is about"
        varchar detail
    }
    OBSERVED_LATENESS {
        varchar relation PK
        varchar event_column PK "the column an incremental filter or microbatch reads"
        timestamp observed_at PK
        varchar arrival_column "when a row landed: a loader column, or the judged one"
        double max_late_seconds "max(arrival - event) over the table"
        bigint rows_read
        varchar via
    }
    PROOFS {
        varchar model PK "unique_id"
        varchar property PK "no_fanout:<parent> / pick:<n> / grain / incremental"
        varchar written_by PK "assay / agent"
        varchar model_name
        varchar model_checksum "the file the certificate is about"
        varchar statement "the property in words"
        varchar theorem "its name in Lean"
        varchar rule "the shipped theorem it applies"
        varchar premises "json: the ledger premises it assumes"
        varchar status "proven / not_proven / not_attempted"
        varchar detail "Lean's error and goal, or what is missing"
        varchar missing
        varchar source "the Lean text"
        varchar lean_version
        timestamp proved_at
    }
    PARSE_CHECKS {
        varchar model PK
        varchar model_checksum PK
        varchar via PK "duckdb / warehouse / lean"
        varchar status "holding / broken / unchecked"
        varchar detail
        bigint rows_compared
        timestamp checked_at
    }
    CONFORMANCE {
        varchar construct PK "left_join_null_key, rule_op:inner_join, ..."
        varchar engine PK "duckdb / snowflake / ..."
        varchar engine_version PK
        varchar status "conforms / differs / unchecked"
        varchar detail "the rows that differ"
        timestamp checked_at
    }
    CLAIM_CHECKS {
        varchar model PK "unique_id"
        varchar property PK "grain, no_fanout:<parent>, ..."
        varchar model_checksum "the file the run was of"
        varchar premises_key "the premises the inputs were generated to meet"
        varchar status "holds / contradicted / unchecked"
        varchar detail "what broke it, or why it could not run"
        timestamp checked_at
    }
    TEST_STATUS {
        varchar test_id PK "the dbt test's unique_id"
        timestamp ran_at PK
        varchar status "pass / fail / warn / error / skipped"
        timestamp observed_at "when assay read it"
        varchar via "elementary / run_results"
    }
    WAREHOUSE_CALLS {
        varchar call_id "sha1 of the STATEMENT, so a rerun is identifiable"
        varchar run_id "empty when the command minted no run"
        varchar caller "assay.probe.keys, assay.practices.collect, ..."
        varchar relation "empty when the statement spans several"
        varchar statement_kind "key_scan / profile / sample / count / metadata"
        varchar dialect
        int columns_touched
        varchar column_names "json array"
        bigint rows_returned
        bigint rows_scanned "the relation's KNOWN row count, not a measurement"
        bigint bytes_estimated
        bigint bytes_measured "null unless an adapter gave a real number"
        varchar estimate_basis "declared_types / adapter / unknown. Never optional"
        boolean sampled
        bigint sample_rows
        int wall_ms
        double usd_estimated "null when no configured rate can justify a number"
        varchar rate_card "WHICH rate produced usd_estimated"
        boolean failed "a failed statement is not an empty one"
        varchar detail
        timestamp called_at
    }
    COMPILED_SQL {
        varchar checksum "dbt's own: sha256 of the model file's text, whitespace stripped"
        varchar model
        varchar sql "what that exact version compiled to"
        timestamp first_seen
    }
    COMMITS {
        varchar sha
        timestamptz committed_at
        varchar subject
        varchar body "kept whole: the message is signal"
        varchar files "json array"
        varchar models "json array of the models it touched"
    }
    CALIBRATIONS {
        timestamp ran_at
        varchar assay_version
        int n "labeled models the grain judgment was measured on"
        int exact
        int uncertain "the judgment said it could not tell"
        int kept_too_many
        int dropped_too_many
        int disagrees
        int code_exact "what code alone got, for the comparison"
    }
```
</details>


`WAREHOUSE_CALLS` joins to `RUNS` by `run_id` when the command that issued the statement minted
one, and to nothing when it did not — `assay probe` opens a store and is not part of a check, and
stamping its cost with the newest `run_id` would credit it to a run that did not cause it.

It is the only table here with **no primary key at all**, and that is deliberate. `call_id` is a
hash of the statement rather than of the call, so the same statement issued twice carries one id;
a key would mean `insert or replace`, and two identical statements in one pass would collapse into
one row — money spent, silently unrecorded. An append-only ledger cannot lose a row that way.

`OBSERVED_KEYS` joins to nothing. It is keyed on `(relation, column_name, observed_at)` because it
is a **series**: a key that held last week and does not now is the failure that corrupts a
warehouse, and it is invisible to every check that describes the present. It needs two
observations to say anything, which is why the timestamp is in the key rather than being
overwritten.

---

## The joins are by convention, not by constraint

There are no foreign keys in the DDL. Two of these relationships are string grammar, which is
worth knowing before writing a query against them.

**`decision_key` names what a question was asked ABOUT.** It is a model's `unique_id`, optionally
with a suffix saying which of several questions on that model:

| shape | asked about |
|---|---|
| `model.p.orders` | the model itself — grain, role, provenance |
| `model.p.orders::claim::a1b2c3` | one atomic claim, joining `claims.claim_id` |
| `model.p.orders::edge::model.p.items` | one hop into it |
| `model.p.orders::desc` | its schema.yml description |
| `model.p.orders::pred::41` | a filter it applies |

**`adjudications.subject` is the same grammar plus one more.** `model.p.orders::finding::4f2a` is
a verdict on ONE finding rather than on the model, and it is the shape that makes dismissal and
the loop measurement possible — a verdict filed against the bare model cannot say which of that
model's eight findings was the real one.

`adjudications.decision_key` is the path back to the probability a verdict ruled on. It is **empty
for a structural finding**, and that is correct rather than missing: a parser decided it, no
question was asked, and there is no number to calibrate. On one real store, 97 of 105 agent
rulings were structural, so a calibration report has to exclude them by construction.

---

## What each table answers

| table | the question | cost to rebuild |
|---|---|---|
| `runs` | what has been run here, and against what | free, but it IS the log |
| `findings` | what is wrong right now | free |
| `edge_facts` | what each hop carries and drops | free |
| `unreadable` | what assay could not parse, and why | free |
| `model_calls` | what each call to the provider cost | **the money itself** |
| `model_decisions` | what a judged question answered | **a model call** |
| `states` | what that answer was computed FROM | free once the call is made |
| `adjudications` | what a person or agent concluded | **somebody's afternoon** |
| `claims` | what this project asserts, as data | **a model call** |
| `observed_keys` | what a count actually found, over time | **a warehouse query** |
| `warehouse_calls` | what each statement cost the warehouse | **the money itself** |
| `calibrations` | what `assay calibrate` measured, so a comment quoting it can be checked | free from the cache, **a model call** without it |
| `compiled_sql` | what a past version of a model compiled to, by dbt's checksum, so `backtest` replays it exactly | **a compile**, or a build that is gone |
| `premises` | what each fact, suppression and proof rests on, its evidence and status | free |
| `premise_uses` | which grain, held-back finding or proof rests on which premise | free |
| `observed_lateness` | how late rows arrive after their event time, for an incremental model's lookback | **a warehouse query** |
| `proofs` | what Lean proved about each model, from which premises, and what it could not | **Lean time**, and an agent's proof cannot be written again for free |
| `parse_checks` | whether a model's parse is what its SQL says, by round trip | free in DuckDB, **a warehouse query** otherwise |
| `conformance` | whether an engine does what assay's meaning of SQL says, per construct, and whether the definitions the rules are proven about (`rule_op:*`) run as the engine does | free in DuckDB, **a warehouse query** otherwise |
| `claim_checks` | whether each proven claim held when its model ran on inputs meeting its premises | nothing: recomputed in memory, one row per claim |
| `test_status` | each dbt test's last actual result, from Elementary or a build's `run_results.json` | **a build that is gone** |
| `commits` | every commit touching the project, and the models it touched; with `runs.git_sha`, when a finding was first seen | free from git |

---

## Three properties that are load-bearing

**A `state_hash` is only worth having if the state can be built again.** Every judged answer
stores a hash of what was sent, and for eighteen releases nothing could reproduce one: each of the
eighteen call sites assembled its own dict, two of them inline, two merging the project's
vocabulary in at the point of the call. Measured on the field warehouse, rebuilding all 871 model
and edge subjects matched **0** stored hashes — not drift, just a state nothing could produce
twice. `state_builder` and `state_inputs` fix that: the builder is registered by name, the inputs
are identifiers rather than content, and `make()` and `rebuild()` are the same function call. The
same measurement now returns **871 of 871**.

Three states cannot be rebuilt and are declared so, with the reason on the builder: a feed's
sample, a failing row dbt stored, a practice check's output. Each carries rows read out of the
warehouse at a moment in time. `assay stale --exact` reports them as *not comparable* rather than
as unchanged.

**The thing with a price is the CALL, and `model_decisions` is one row per ANSWER.** A batch of
eight questions about one state is one call and eight rows, and `input_tokens` is the call's
number written onto every one of them. So `sum(model_decisions.input_tokens)` counts a batched
call once per answer. Measured on the field store: it reads 101,163,351 tokens against 31,426,560
actually spent, and $4.25 against $1.32. Three separate figures in one spec came from that sum,
including the number written down as the anchor to check the first implementation against.

`model_calls` holds one row per call, and every total assay prints goes through it. The column
stays on `model_decisions` because it is a true fact about that row's call, and a test asserts
nothing in the codebase sums it.

Two things made that possible. A call now always has an id: the provider returned none for 9,762
of 19,707 decisions, so assay mints its own and `id_source` says which happened. And dollars are
built from integer tokens grouped by rate and multiplied once, because `sum()` over a DOUBLE
depends on row order and the same store could print two different lifetime totals.

**A finding's identity survives a rerun and changes when the substance changes.** `finding_id` is
`sha1(check | subject | summary | evidence-minus-measured-values)`. Floats and counted values are
excluded on purpose, so a probability moving by 0.01 does not mint a new finding. This is what
lets a dismissal stick across runs and lapse by itself when the model is edited into a genuinely
different defect.

**A verdict is evidence about a question AND the state it was given.** `prompt_version` is in the
primary key of both `model_decisions` and `adjudications`, so a verdict recorded against v1 is a
different row from one against v4 and cannot silently authorize it. The suffix on a version
(`+scoped+description_only`) records the STATE SHAPE rather than the question text — a known gap
is that `stale_decisions` compares only the base, so changing what gets sent is not counted by
anything.

---

## Why there are no foreign keys

Fair question to ask of this tool in particular. The answer is partly "structurally impossible",
partly "not worth a destructive migration", and partly "this already went wrong once". All three
are measured rather than argued.

**What is actually broken today.** Zero orphans on every join that has a real target:

| join | orphans |
|---|---|
| `findings.run_id` → `runs` | 0 of 4,940 |
| `edge_facts.run_id` → `runs` | 0 of 11,460 |
| `adjudications.decision_key` → `model_decisions` | 0 of 195 |
| `adjudications` `::claim::` → `claims` | 0 |
| `model_decisions.state_hash` → `states` | 9,945 — but `states` is empty; state storage shipped in 0.33.0 and these predate it |

**It has gone wrong before.** 99 agent rulings once existed under a subject that resolved to no
model, so they attached to nothing and counted toward nothing. That is exactly the failure a
foreign key prevents. It was fixed by refusing the write — `rule` will not record a subject it
cannot resolve — and by `assay review --repair` for the ones whose model name was unambiguous.
Validation at the write, not a constraint at the table.

**Two of these cannot be foreign keys at all**, and it is not a matter of effort:

- `adjudications.subject` is **polymorphic**. It is a model `unique_id`, or that plus
  `::finding::<id>`, or `::win::<n>`, or `::claim::<id>`. There is no single table to point at.
- `adjudications.decision_key` → `model_decisions.decision_key` is refused by DuckDB: the parent's
  primary key is `(decision_key, question, prompt_version, model_version)`, and a foreign key
  needs a unique target. One column of a composite key is not one.
- Separately, the column uses `''` to mean "structural finding, no question was asked". A foreign
  key requires `NULL` for that; `''` is a value and would be an orphan.

**The `run_id` ones could be, and are not.** They work — verified: the constraint refuses an orphan
insert, and `prune` still runs, because prune deletes children and keeps the runs. What stops it is
that **DuckDB has no `ALTER TABLE ADD CONSTRAINT`**, so adding one means rebuilding tables in every
existing store, on a file holding model calls and human verdicts. That is a destructive migration
to prevent a class of orphan that currently has zero instances and whose only writer is this
codebase.

So: a real smell, two instances that are structurally impossible, one that is a deliberate trade
with a stated reason. Written down here rather than left for somebody to notice.

**And one thing this section found.** It said, in an earlier draft, "a new table belongs to one
list or the other and a test fails until it does." No such test existed, and `states` was in
neither list. A documentation claim the code did not support, in the project whose largest check
family is `code_contradicts_a_claim`, written the same day. The test exists now and `states` is
`NEVER_PRUNED` — a state is what was sent to a call somebody paid for, and a decision without the
state it was computed from is an answer nobody can check.
