# What a data engineer in this warehouse owns, and which of it assay covers

Written from a night of using assay as that engineer on a 357-model warehouse. The question is not
"what else could assay do" — it is "what is this job, and where does the tool stop". A tool that
covers eight of nine responsibilities and says which one it does not is more useful than one that
implies it covers all nine.

---

## The nine

| # | responsibility | the question somebody actually asks | assay |
|---|---|---|---|
| 1 | **Grain** | "what is one row of this?" | **covered** — `contract`, `infer`, `column_is_part_of_the_key` |
| 2 | **Docs versus code** | "does it do what it says?" | **covered** — `claims`, `verify`, `description_contradicts_the_code` |
| 3 | **Do the guards guard?** | "these tests pass, does that mean anything?" | **covered** — `test_cannot_fail`, waivers-with-reasons, `min_adjudications` |
| 4 | **Provenance** | "where did this number come from?" | **covered** — `trace`, `lineage` |
| 5 | **Blast radius** | "what breaks if I change this?" | **covered** — `blast_radius`, `changed_contracts`, `must_stay_true` |
| 6 | **Completeness** | "do we have all of it?" | **NOT COVERED — the gap** |
| 7 | **Freshness** | "is it current?" | partial — `feeds` sees a source changing its mind, nothing sees a source going quiet |
| 8 | **Correctness against the world** | "is this what the county actually recorded?" | **not covered, and should not be** — needs a source of truth assay cannot have |
| 9 | **Cost** | "what did this pipeline spend?" | not covered — here that is a separate ledger, and rightly |

Five solid, one partial, one gap, two deliberate exclusions.

---

## 6 is the gap, and assay already finds completeness defects by accident

This is the argument for adding it rather than a feature request.

`water_rights.dwr_analysis_status` is `coalesce(ca.dwr_analysis_status, 'not looked up')`.
`test_cannot_fail` flagged the `not_null` test on it as unable to fire — a **semantics** finding.
Reading it produced a **completeness** fact: **170,730 of 172,695 rows (99%) are that default.**
The lookup has effectively not run, the test passes on every row, and nothing anywhere says so.

That was not a lucky accident. It is the same shape four more times on this warehouse:

```
water_parcels.irrigated_acres_on_parcel   2,623,519 of 2,732,101  (96%) are 0
az_section_summary.n_well_depth              95,650 of   114,305  (84%) are 0
az_section_summary.parcel_count              85,264 of   114,305  (75%) are 0
water_section_summary.rights_late_adjudicated 45,842 of   64,433  (71%) are 0
```

And the enrichment layer, which nothing in the project reports on at all:

```
well scans read by vision      463 of  2,606 documents   18%
decree cases read             4,760 of 26,586 cases      18%
resume filings read           1,076 of 18,381 blocks      6%
```

A person asked "is the water data complete?" has no way to answer it from this warehouse today. The
numbers exist; nothing collects them.

## What "completeness" should mean here — and what it must not

assay's discipline is *"it reads code and rows, never intent"*. That line is what keeps it from
becoming another observability product, and completeness can be done without crossing it.

**In scope — coverage of the warehouse's own declared intent.** Every one of these is computable
from what the project already states:

- **A default that dominates.** `COALESCE(x, <literal>)` where the literal is most of the column.
  assay already detects the pattern; only the count is missing, and it is one query.
- **A model that is empty.** Already detected — `practices` prints `0 rows, 0 distinct` and `patch`
  refuses it. Nothing aggregates "which models are empty" into an answer.
- **A source that reaches nothing.** A `source()` declared, loaded, and read by no model.
- **An edge that loses most of its rows.** Parent 2.6M, child 463 — that ratio is free from the DAG
  plus two counts, and it is exactly the enrichment gap above.
- **assay's own coverage.** It already reports this about itself — *"327 read, 30 not audited"*,
  *"1,071 tests had nothing stored"*, *"could not count any proposed grain"*. Extending that
  discipline from the tool to the data is the same argument, made once more.

**Out of scope — and the reason matters.** Funnel analysis, conversion rates, business metrics,
anomaly detection on a trend. Every one needs somebody to say what the funnel IS, what a normal
week looks like, which drop is a bug and which is January. That is intent, assay refuses to guess
at intent, and the refusal is why its findings are trustworthy. A completeness check that reports
"row count fell 12% week over week" is a different product and it competes with Elementary, which
this project already runs.

The distinction in one line: **assay can say a column is 99% its default. It cannot say whether
that is bad.** The first is a fact about the code and the rows. The second is a business question,
and the ruling loop already exists for exactly that — a person reads it and records what it means.

## On the HTML page

`inventory --html` already writes *"a self-contained page you can open, commit and diff"*, which is
the right shape and a better one than a localhost dashboard. A file that commits and diffs accrues;
a server shows you today and forgets. Same argument as findings-becoming-a-table.

What that page wants on it, in the order somebody reads:

1. **The ruled-on number**, first and largest. It is the only figure a release cannot improve, and
   a good release makes it look worse by finding more. Everything else on the page is downstream of
   whether anyone has actually read any of it.
2. **Findings by defect class and blast radius** — `ops_assay_debt` in this project is already that
   query, over the exported seeds.
3. **Coverage**, per §6 above: which sources reach nothing, which columns are mostly default, which
   models are empty, and what fraction of the enrichment corpus has been read.
4. **What moved since the last commit** — `diff` and `regress` already compute it; the page is
   where a person would look for it.

Not a dashboard of metrics. A page that answers *"is this warehouse understood, and by whom"*,
which is the one question the rest of the tool is already built to answer.
