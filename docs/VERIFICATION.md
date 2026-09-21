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
| `row_explanation` | **a finding read in the field** | called wells `genuinely_wrong` at 0.50–0.70 on a **290-foot well with a water level of 26,018 feet**. The `accepted_range` test that surfaced them catches 12 rows; the real invariant `water_level_ft <= well_depth_ft` holds on **708**. It found a data defect *and* an inadequate test, from a sample of six rows |
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

### `null_meaning` is weak, and its documented remedy does not work

The bank says it answers at ~0.49 over six options without a null rate from `assay probe`, which
implies a rate fixes it. **Measured on three real columns, with and without a true rate:**

| column | null rate | without the rate | with it |
|---|---|---|---|
| `water_parcels.zoning` | 87.0% | `unknown_value` @0.75 | @0.78 |
| `water_parcels.land_acres` | 37.3% | `not_applicable` @0.29 | @0.34 |
| `water_rights.last_decree_case` | 0.3% | `not_applicable` @0.17 | **@0.14** |

The rate moves almost nothing and made the third case *worse*. Confidence ranges 0.14 to 0.78
across three columns, and the middle two are the wrong answer — a parcel with no recorded area is
`unknown_value`, not `not_applicable`.

So the remedy in the docs is not supported by measurement. Use this family's answers as a prompt to
look, never as a determination, and do not expect `assay probe` to fix it.

### The grain findings need column roles first

`measure_inside_grain` did not fire on `int_water_diversions`, whose grain is
`['wdid', 'record_first_year', 'record_last_year']` — years are summaries of an entity, not part of
which entity it is. The finding needs `assay columns` to have run. `assay infer` alone will not
surface it.

---

## Not verified

| family | why not |
|---|---|
| `practice_exception` | needs `dbt-project-evaluator` built. It IS installed on the field warehouse, so this one is closeable there and has not been |
| `row_is_internally_coherent` | needs `store_failures` rows; the audit schema exists on the field warehouse and no finding has been read |
| `same_defect` | built AFTER measuring, which reversed the plan. Two other shapes were specced and neither had a corpus: no family had both an agree and a disagree, so "these two rulings contradict" had **zero** eligible pairs, and "this reason does not match its finding" had no negative control. This one had 12 of 12 disagreements carrying a reason. Not yet read against a pair a person disagrees with |
| `options_overlap` | verified against the pair that prompted it, and it independently flagged `predicate_intent` — the family already proven weak by hand. Not yet read against a question it should PASS but does not |
| `sentence_is_a_claim` | extraction was read by hand on one model (16 sentences, every high-confidence answer correct, every low-confidence one a genuinely ambiguous header) — but only one model |

### `hop_drops_most_rows` finds nothing, and the NULL RESULT is the evidence

Three refusals, then a fourth (the parent was pre-aggregated) took it from 8 findings to 2, then
the candidate set was narrowed to what can actually be judged: **an INNER join on the driving
edge**. A LEFT join cannot lose the rows it drives on, and a lookup's size says nothing about the
child's. That took 35 candidate hops to **9**.

What those nine measured is the verification, and it is stronger than a finding would have been:

```
100%   int_acquisition_targets   -> mart_acquisition_targets     13,694 of    13,694
100%   int_eco_basin             -> water_eco_basin                 172 of       172
100%   int_water_sections        -> water_section_hazards       108,917 of   108,917
100%   prospects                 -> prospects_enriched              993 of       993
100%   stg_adwr_sections         -> az_section_summary          114,305 of   114,305
100%   stg_cdss_groundwater_wells-> water_monitoring_wells       23,000 of    23,000
100%   stg_cdss_surfacewater_st. -> water_stream_gauges            2,387 of     2,387
100%   stg_cdss_well_permits     -> water_wells                  591,548 of   591,548
3048%  stg_adwr_sections         -> int_az_parcel_sections    3,483,870 of   114,305
```

Eight of nine keep **exactly** 100%: every driving row survived its join. That is what a correct
warehouse looks like on this dimension, and scattered ratios are what a wrong candidate set would
have produced. The ninth is a fan-out, which is `hop_multiplies_rows`'s job.

Still not verified in the field, because it has not caught a real defect. It is verified against a
**planted control**: a driving INNER edge that does lose its rows, which fires. And this warehouse
is a poor place to find the real thing — the models that would trip it legitimately narrow in
sibling CTEs, so their driving edge is already the narrow one.

*The first version of this refusal was a sibling-size heuristic: refuse when the child is the size
of SOME parent. It gave the right answer on both field cases and would have masked the real defect
for exactly the reason above. The parser already knew the join kind and the FROM clause.*

### `options_overlap` has a known false negative

An option whose description **routes to another option by name** — "the answer is `<other>`, not
this" — is read by the judged check as a disjointness guarantee. Measured in the field: that
question scored `no_overlap` 0.63 against an overlap mass of 0.35, while the answering model put
one subject under both options at 0.55 and 0.63.

The judged check still misses this. `option_routes_to_another` catches the known cause statically,
where no model is asked to be consistent about anything, and it does flag the question that
prompted it. Treat `--judge` as one of two checks, not as the check.

**Nine of fifteen families have no finding resting on them**, so ruling on them records evidence and
moves no gate. `assay config` marks which, and `assay review -i` says so before the keypresses
start.

---

## The measure that matters most, and that this page cannot improve

Ten of seventeen families have had findings read by a person. That number, and the `ruled on` column
in any warehouse this is exported to, are the only figures in the system a release cannot move.
Every other one responds to better code: a sharper check finds more, a fuller state raises a
confidence, the DAG moves the blast radius.

**A good release makes it look worse**, because finding more raises the denominator and no release
raises the numerator. Treat that as the design working, not as a regression.

## How to redo this

Nothing here was a proxy metric. Every row is a person reading the thing the model judged:

1. Run the family on real data.
2. Read the top findings against the SQL, the data, or both.
3. For anything with no findings, **plant a negative control** — a check that has never said *no*
   has not been verified.
4. When it fails, fix the **question** before suspecting the model. Every failure above was the
   question's shape, and `assay banks` now encodes each one as a lint rule.
