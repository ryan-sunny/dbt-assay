# What has actually been verified, and what has not

Every question in `assay` returns an answer. An answer is not evidence that the question works.
`keys_on_a_non_unique_column` read 0.73 to 0.85 on every model tested, clean or broken, and looked
like a working check for weeks.

So this page records, per family, whether a **person has read its findings against the real thing**
— and where the answer is no, it says so rather than leaving you to assume.

Verified on a 357-model Colorado water-rights warehouse and two public dbt repos, September 2026.

---

## Verified working

| family | how | result |
|---|---|---|
| `claim_alignment` | read against the SQL it judged, four rounds | see below |
| `edge_preserves_the_grain` | top hit read against the model | found a bounding-box **overlap** join feeding a mart: `join irr i on p.xmin <= i.xmax and p.xmax >= i.xmin`. One parcel matches many polygons |
| `column_role` | 61 free labels from the project's own tests | agreed with 57; reading the 4 disagreements showed **3 were the label being wrong** |
| `description_contradicts_the_code` | top finding read against the data | `stg_boulder_permits` claims "residential filtered out"; of 14,150 surviving rows, 157 are explicitly multifamily and 13,620 are trade permits |
| `field_matches_its_name` | 4 planted cases on real column values | 4/4. Real counties `matches` @0.92; city names in a `county` column `holds_something_else` @0.80; river names 0.99 vs numeric codes 0.96 |
| `same_concept` | negative controls | `section_id ~ case_number` **0.02**, `owner_name ~ contact_name` **0.13**, `wdid ~ wdid` **1.98**. The project's own 14 joined pairs: 14/14 |
| `severity_fit` | two planted cases | a not-null on a primary key 24 dashboards read scored **1.59**; the same on a note column nobody reads scored **0.14** |
| `units_are_what_the_column_claims` | **8 real column names, after a rewrite** | 8/8 at confidence **1.00**. It failed first — see below |
| `test_cannot_fail` | findings read on two public repos | found `'BA' as sigla_uf` carrying a `not_null` test in `basedosdados` |

### Where `same_concept` is ambiguous rather than wrong

It read `land_acres ~ land_sqft` as **1.9 (same)**. Defensible — they *are* the same concept — but
"same concept" and "safe to equate numerically" are different questions, and only the first is
being asked. Do not use this family to authorise a join.

---

## Fixed because verification failed them

### `units_are_what_the_column_claims` — was unsound, now split

v1 asked whether **magnitudes** were plausible for the unit a name implies. Against real values:

| column | values | v1 said | truth |
|---|---|---|---|
| `land_acres` | 5.01, 3.76, 5.51 | consistent @0.96 | correct |
| `land_acres` | 218235.6, 163785.6 | **consistent @0.82** | square feet. Wrong by 43,560× |
| `amount_af` | 12.5, 3.2, 88.0 | consistent @0.34 | correct |
| `amount_af` | 4073925.0, 1042800.0 | **consistent @0.54** | gallons. Wrong by 325,851× |

TypeSafe publish the reason: the model *"cannot reliably judge whether two values are near each
other."* The instructions contained no arithmetic **word**, so the linter's calculator rule missed
it and a `numeric_magnitude` rule now exists.

**The fix was to split the question.** The model names the unit the NAME claims — language, which
it does at 1.00 — and `feeds.range_conflicts` compares the observed range in code, where it is
exact and free. Both failures above are now caught, and real values are not flagged.

### `claim_alignment` — four rounds, each a fix to the question

| change | contradicts | says_nothing | supports |
|---|---|---|---|
| generic evidence, compound claims | 10 | 10 | 6 |
| split on semicolons too | 10 | 10 | 6 |
| evidence chosen **by** the claim | 8 | 8 | 10 |
| criteria: **absence is not disagreement** | **5** | 14 | 7 |

A claim about `d_class_cn` read `contradicts` at **0.97** because the evidence listed thirty other
columns and not that one — the model could not see what it was asked about.

### The claim splitter — found by running this method on assay's own Python

> "dbt reports that a test passed. It never reports that a test was INCAPABLE of failing."

Split on the full stop, the second sentence reads as a claim about the function. The subject is
**dbt**, one sentence back. Judged `contradicts` at 0.73. A sentence opening with a bare pronoun is
now rejoined to what it refers back to.

---

## Known weak, and why

### `predicate_intent` on a bare null filter is a coin flip

Eight `NOT <col> IS NULL` filters across four models:

```
data_quality_workaround @0.38  [workaround 0.51 / business_rule 0.25]  maricopa: full_street_address
business_rule           @0.29  [business_rule 0.44 / workaround 0.36]  gilbert:  issued_date
data_quality_workaround @0.32  [workaround 0.46 / business_rule 0.39]  boulder:  issued_date
```

**Corroborated independently.** `assay banks --judge` asks whether any two options of a question
could both be right about the same subject, and it flagged this family at **p=0.85** without being
told anything about the hand measurement below. Two methods, one conclusion.

**The same predicate on two models gets opposite labels.** Confidences are 0.26–0.38 throughout, so
the model is reporting that it cannot tell — which is honest, because the SQL cannot say *why* a
null exists. Nothing is reported as a finding (the gate is 0.6), but the summary table prints these
as determinations. Read the confidence, not the label.

### `column_is_part_of_the_key` can propose a grain that is not in the output

On `my_prospects`, documented as "one row per company + city", it proposed `['city']`. The model
dedups on `partition by name_key, city` and then does `select * exclude (name_key)`. The real grain
**cannot be expressed in the surviving columns**, and `city` alone is false.

That is a defect assay does not yet name: *a model that dedups on a column it then drops*, so
nothing downstream can verify its own uniqueness.

### The grain findings need column roles first

`measure_inside_grain` did not fire on `int_water_diversions`, whose grain is
`['wdid', 'record_first_year', 'record_last_year']` — years are summaries of an entity, not part of
which entity it is. The finding needs `assay columns` to have run. `assay infer` alone will not
surface it.

---

## Not verified

| family | why not |
|---|---|
| `null_meaning` | no finding rests on it, and without a null rate from `assay probe` it answers at ~0.49 over six options. The bank says so at the call site |
| `practice_exception` | needs `dbt-project-evaluator` built, which the test warehouse does not have |
| `row_explanation` | needs `store_failures` rows; the audit schema exists but no finding has been read |
| `row_is_internally_coherent` | as above |
| `options_overlap` | verified against the pair that prompted it, and it independently flagged `predicate_intent` — the family already proven weak by hand. Not yet read against a question it should PASS but does not |
| `sentence_is_a_claim` | extraction was read by hand on one model (16 sentences, every high-confidence answer correct, every low-confidence one a genuinely ambiguous header) — but only one model |

**Nine of fifteen families have no finding resting on them**, so ruling on them records evidence and
moves no gate. `assay config` marks which, and `assay review -i` says so before the keypresses
start.

---

## How to redo this

Nothing here was a proxy metric. Every row is a person reading the thing the model judged:

1. Run the family on real data.
2. Read the top findings against the SQL, the data, or both.
3. For anything with no findings, **plant a negative control** — a check that has never said *no*
   has not been verified.
4. When it fails, fix the **question** before suspecting the model. Every failure above was the
   question's shape, and `assay banks` now encodes each one as a lint rule.
