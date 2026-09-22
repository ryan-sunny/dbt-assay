# The store

One DuckDB file, `assay.duckdb`, written by `assay check` and read by everything else. Nine
tables. The whole design turns on one split, so it is worth stating before the diagram:

**Some of these cost nothing and some of them cost money or somebody's afternoon.** A table
carrying `run_id` is exactly one `assay check` rebuilds for free, offline, in seconds. A table
without one holds something that was paid for — a model call, or a person reading SQL. `assay
prune` deletes only the first kind, and the split is declared in code rather than inferred:

```python
PRUNABLE     = ("findings", "edge_facts", "unreadable")
NEVER_PRUNED = ("model_decisions", "claims", "adjudications", "observed_keys", "runs",
                "states")
```

A new table belongs to one list or the other and a test fails until it does, so nothing becomes
silently prunable.

---

## The tables

![the store's nine tables](store-schema.png)

<details>
<summary>the same thing as Mermaid source</summary>

```mermaid
erDiagram
    RUNS ||--o{ FINDINGS : "one run produces"
    RUNS ||--o{ EDGE_FACTS : "one run measures"
    RUNS ||--o{ UNREADABLE : "one run could not read"

    STATES ||--o{ MODEL_DECISIONS : "one state, many answers"
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
        int unreadable
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
        varchar call_id
        varchar caller
        int input_tokens
        timestamp decided_at
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
    }
```
</details>


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
| `model_decisions` | what a judged question answered | **a model call** |
| `states` | what that answer was computed FROM | free once the call is made |
| `adjudications` | what a person or agent concluded | **somebody's afternoon** |
| `claims` | what this project asserts, as data | **a model call** |
| `observed_keys` | what a count actually found, over time | **a warehouse query** |

---

## Two properties that are load-bearing

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
