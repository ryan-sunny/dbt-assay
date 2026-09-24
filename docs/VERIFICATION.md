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
| `claim_alignment` | read against the SQL it judged, four rounds | the QUESTION, yes. Its FINDINGS, no -- see below |
| `edge_preserves_the_grain` | top hit read against the model | found a bounding-box **overlap** join feeding a mart: `join irr i on p.xmin <= i.xmax and p.xmax >= i.xmin`. One parcel matches many polygons. At 0.50.0, `traverse` called six hops into `water_rights` on `wdid` silently multiplied -- the fan-out the project's own vocabulary had already measured by hand (15,834 of 132,175 wdid carry more than one right). An independent reproduction of a counted fact |
| `volume_contradicts_a_claim` | **not checked against real data by a person** | five answers read on a live warehouse: 3 *claim survives*, 2 *says nothing about volume*, 0 contradictions. Sane, and five is not a verification |
| `column_role` | 61 free labels from the project's own tests | agreed with 57; reading the 4 disagreements showed **3 were the label being wrong**. At 0.50.0 on the field warehouse: 173 of 187, and the five disagreements read by hand were all the label -- three serialized `geom_json` blobs and two row counts, each tested as an identifier |
| `description_contradicts_the_code` | top finding read against the data | `stg_boulder_permits` claims "residential filtered out"; of 14,150 surviving rows, 157 are explicitly multifamily and 13,620 are trade permits |
| `field_matches_its_name` | 4 planted cases on real column values | 4/4. Real counties `matches` @0.92; city names in a `county` column `holds_something_else` @0.80; river names 0.99 vs numeric codes 0.96 |
| `same_concept` | negative controls | `section_id ~ case_number` **0.02**, `owner_name ~ contact_name` **0.13**, `wdid ~ wdid` **1.98**. The project's own 14 joined pairs: 14/14 |
| `severity_fit` | two planted cases | a not-null on a primary key 24 dashboards read scored **1.59**; the same on a note column nobody reads scored **0.14** |
| `units_are_what_the_column_claims` | **8 real column names, after a rewrite** | 8/8 at confidence **1.00**. It failed first — see below |
| `row_explanation` | **a finding read in the field** | called wells `genuinely_wrong` at 0.50–0.70 on a **290-foot well with a water level of 26,018 feet**. The `accepted_range` test that surfaced them catches 12 rows; the real invariant `water_level_ft <= well_depth_ft` holds on **708**. It found a data defect *and* an inadequate test, from a sample of six rows |
| `test_cannot_fail` | findings read on two public repos | found `'BA' as sigla_uf` carrying a `not_null` test in `basedosdados` |
| `test_outruns_its_source` | **the finding read against the data it is about** | `int_water_well_parcel.parcel_id` is `min(parcel_id)` over a grouped CTE. Parent: **5,876 null of 2,732,101**. Child: **0 of 48,648**. The test passes today and is one all-NULL group from not passing — which is what the outage was, one row of 49,034 after months of green |
| `test_outruns_its_source` | **all seven of its own findings ruled by hand, then counted** | 3 were wrong and all for one reason: the aggregated input cannot be NULL. `bool_or(is_sfha)` where `is_sfha` carries a `not_null` test; `listagg(coalesce(x, 'literal'))`. Reading those two declarations takes it to **5 of 5 defensible**, and the two verified true positives are `letter_date` (1,300 null of 17,193, with **1,300 groups entirely null** upstream) and `parcel_id` |

### Where `same_concept` is ambiguous rather than wrong

It read `land_acres ~ land_sqft` as **1.9 (same)**. Defensible — they *are* the same concept — but
"same concept" and "safe to equate numerically" are different questions, and only the first is
being asked. Do not use this family to authorize a join.

### What `test_outruns_its_source` does NOT claim

It says the column **can** be NULL by construction, from the AST. It does not say the parent
contains nulls today — that is a count, and counts arrive with `--verify` like every other counted
check here, absent rather than guessed.

Two versions of it were rejected before this one, and both failures are the measurement:

- firing when the parent merely did not declare `not_null` on the column caught **148 of 227**
  carried-column tests (65%), because not declaring `not_null` on every parent column is ordinary
  practice rather than a defect. A check that fires on the normal case is how a list of exceptions
  becomes a list;
- narrowed to the two join/UNION cases it found **zero**, and zero was correct: every carried
  `not_null` column in a LEFT-joined model on that warehouse is carried from the *driving* table,
  which a LEFT join does not make nullable. The field case had moved into an aggregate.

Telling the aggregates apart is the whole check: **7 findings, not 23**, because 16 of the 23
aggregated `not_null` tests are `count()`, which is 0 and never NULL.

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

### `code_contradicts_a_claim` is the largest thing shipping and the least confirmed

**213 of 344 findings on the field warehouse, and not one has been confirmed by a person.** Two
have been read by hand and both were false, at p=0.95:

```
int_water_diversion_history   claim "the FULL diversion record, 1886 to 2026"
                              true. The SQL DERIVES those years and never states them.
stg_cdss_dams                 claim about `ponds_covered`, which appears only in that comment.
                              It is about the Python findings layer, not this model's SQL.
```

The distinction the table above now makes matters: `claim_alignment` the **question** was verified,
over four rounds, each one a fix to its shape. `code_contradicts_a_claim` the **finding** rests on
that question and has a separate error rate, which nobody has measured. A verified question does
not make a verified finding, and this page said otherwise for four releases.

Both false positives are the same shape — *the model could not see what it was asked about* —
and 0.23.0 stopped asking them: a claim whose every named identifier is absent from the model, and
a claim asserting a literal value, are refused before the call. Contradictions went **389 to 240**
and both hand-read cases are gone.

**0.24.2 applies the same refusal to the answers that predate it.** The refusal ran at the call
site and nowhere else, so a stored answer given before 0.23.0 was still the live answer, was not
stale, and still became a finding. One fact in two places, silent when they disagree. The read
path refuses too now, and prints what it refused, because a shrinking findings count that nobody
explains reads as a warehouse getting better.

Until somebody reads a confirmed one, treat this family the way this page treats
`null_meaning`: a prompt to look, never a determination.

**At 0.50.0 two independent readers put it at about a third.** `assay read` found 15 of 82 cards
correct as stated, and `effectiveness` counts 4 of 12 from people. Neither is a verification of
any one finding; together they are the strongest evidence on this page that the check, not the
reading, is where the error lives. The `read` floor now holds back every dismissal of it below 0.5,
which on a 17-card sample was all six.

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
| `default_is_a_measurement_or_an_absence` | asked on the 191 defaulted columns of six marts on the field warehouse ($0.007) and the named ones read by hand. `dwr_analysis_status`, `COALESCE(.., 'not looked up')`, read `an_absence_marker` at 1.00 -- the case that motivated it. The counts the field report worried about (`n_water_level`, `wells_production`, `wells_household_only`) read `a_true_value` at 0.94–0.99, which is defensible: a section a join found no wells for has zero wells, and whether the LOOKUP covered the section is a different question from what the value means. `parcel_count` and `species_on_reach` split ~0.53/0.43, honestly. **No person has ruled on its answers** |
| `tie_break_is_total` | all 51 dedupes on the field warehouse: 38 `ties_are_possible`, 13 `total`. Three `total` answers read against their ORDER BY: two end in `transaction_id`, declared unique; one ends in a flowline `comid`, unique in fact and declared nowhere -- which the structural `arbitrary_pick` cannot see and this can. **No person has ruled on its answers** |
| `sentinel_is_not_a_value` | all 8 sentinel literals on the field warehouse. `sentinel.v1` called two `COALESCE(x, 99999)` "no limit" comparisons a sentinel used as a value; read by hand they are an unbounded stand-in, never output. `v2` added that option and reads 3 stand-in, 3 handled, 2 genuine, 0 defects. **No person has ruled on its answers** |
| `what_would_break_silently` | 8 answers read on the field warehouse: 7 `joins_would_fan_out_or_drop` on the parts of a composite key two children join on, which is right. The queue's own example, `stg_mesa_permits.permit_class`, is NOT asked, correctly: beside its vacuous `accepted_values` it carries a `not_null`, which catches the CASE-with-no-ELSE fall-through. **Not verified beyond that** |
| `filter_is_complete` | 8 answers read: 23 hand-typed structure codes and four hand-typed facility types called `a_hand_list_of_an_open_set` -- defensible, a new code is silently excluded. **Not verified beyond that** |
| `units_agree_across_models`, `time_grain` | 8 and 4 answers on the field warehouse, all `same_unit` / `the_same_grain` or `cannot_tell`: no defect there to find, so **these have not been seen to catch anything** |
| `movement_is_expected_for_this_kind_of_table`, `monitor_covers_what_matters`, `stale_monitor_still_matters`, `test_never_ran_is_a_gap_or_a_leftover` | built from structures already verified in `assay volume`, and tested against a synthetic Elementary report. **Never run against a real Elementary schema** -- the field warehouse's lives on the box, which this build was not run on |
| `finding_is_correct` | read against 20 real cards while it was built, and changed twice by what that showed. `read.v1` answered 4 of 5 `models_disagree_about_a_column` cards `cannot_tell` at ~0.4: the sixteen sentences were only in `evidence`, so evidence joined the state. It then answered all five `misreads_the_sql`, because the check's `described_in` is the first eight alphabetically and on `building_key` those were eight IDENTICAL sentences under "described 3 different ways" -- fair, from what it was shown. `one_of_each` (one sentence per variant, excluded from the finding's identity) fixed the evidence, and `read.v2` stopped framing every option around the SQL: 6 of 6 then read `correct` at 0.62–0.69, which is right, they are counts. Open: `int_eco_basin`'s `join_fans_out` reads `misreads_the_sql` at 0.56 because the finding's `joined_on` omits the `state` column the join also uses -- defensible from its state, and an imprecision in that check rather than in this question. **No person has ruled against its readings yet** |
| `reading_rests_on` | built from 25.1 of the field report, where 407 readings carried four distinct sentences and named no line. The options are constructs code copied from the card's own SQL, keyed by their text, so the answer cannot invent one or be re-pointed by a cache. 17 cards across five checks on the field warehouse, read by hand against the SQL: 12 on the construct that decides the finding (the window, the join, the `group by`, the expression the claim is about), 2 `none_of_these` that are right (a claim about a comment, a card read at 0.06), 3 `none_of_these` where a construct exists -- two of them `arbitrary_pick` cards whose evidence names the resolved alias rather than the column in the SQL (25.9). Offered line by line it pointed at the tail of a nine-line window; constructs are now joined to their closing parenthesis. The floor held back all six `code_contradicts_a_claim` dismissals, every one below 0.5. **No person has ruled against its answers** |
| `one_rule_or_a_coincidence` | 15 clusters on the field warehouse, read by hand against their filters and columns. Right: the future-date guard (`<= CURRENT_DATE`, 10 models) as one rule, the 120-day window and the `> 50000` sale price at 0.99-1.00, `= 'CO'`, the two Colorado coordinate bounds; `<> ''` across unrelated columns as independent. Its limit: a cluster is answered as a whole, and one of three members of the `COALESCE(NULLIF(TRIM(..)))` cluster is a different use than the other two. A cluster a macro already writes once is never asked. **No person has ruled against its answers** |
| `where_the_fix_belongs` | the same 15 clusters: `in_a_shared_macro` for most, `at_the_source` for the coordinate bounds on four staging models over their own feeds -- defensible, and the project already has a region macro those four do not call. Annotation only, never a finding. **No person has ruled against its answers** |
| `the_odd_one_out` | 6 candidates on the field warehouse. v1 called `int_recent_permits`' 540-day window `undeclared_divergence` at 0.90: the file's own first line says "(last 540d)" and the state did not carry it. v2 carries the others' filters as written and any line of the model's own file or description mentioning the differing value: that case then reads `deliberate_exception` at 0.93, and `> 0` on acres against `> 50000` on a sale price reads deliberate. The two left `undeclared` (0.49, 0.35) are both wrong by hand and both under the finding floor. **No finding on this warehouse, and no person has ruled against its answers** |
| `claims_are_the_same_assertion` | 49 near pairs on the field warehouse, every one read against both sentences: the 11 answered true are the same assertion (the irrigation measured on the parcel, the `'Water'` class, the coordinate outside Colorado); the rejections are right (a foreign key that adds `gc_key`, a grain on `city` against one on `section_id`). 28 more were word-for-word and grouped by code for nothing. **No person has ruled against its answers** |
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

Still not verified in the field, because it has not caught a real defect. It is verified
**end to end against a control project** as of 0.24.2, which is a stronger statement than the
planted candidate dict it rested on before: a dbt project on disk, parsed by the real loader,
counted against real rows in a real DuckDB, with three outcomes that must differ.

```
int_parcel_owner     100 of 1,000 driving rows survive an INNER join    FIRES
int_parcel_zone    1,000 of 1,000 survive                               SILENT
int_parcel_where      10 of 1,000, and the model declares a WHERE       REFUSED
```

The third is the one that matters. It loses MORE than the first and produces nothing, so the
threshold is not what is doing the work — the refusals are. Candidate selection narrows four hops
to two before a single row is counted, which is the half a planted dict skips entirely.

And this warehouse is a poor place to find the real thing — the models that would trip it legitimately narrow in
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
