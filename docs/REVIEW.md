# Review: what to fix, ranked

> A record: a review of versions 0.5.0 to 0.11.0. It describes assay as it was then; the [README](../README.md) and [OVERVIEW.md](OVERVIEW.md) describe it now.

From running assay 0.5.0 → 0.11.0 against a 357-model Colorado water-rights warehouse and ruling on
every one of its 99 findings. Each item below is something measured, with the fix that follows from
it. The narrative and the evidence are in `FIELD_NOTES.md`; this is the actionable list.

Current state on that warehouse: `agent 99, human 8` rulings, `agree 70 / disagree 12 / unclear 17`,
`regress` 8/8 off `source='human'` after a hundred agent writes.

---

## 1. Two structural blind spots cause every disagree

**A union-member edge cannot multiply.** Ten of twelve disagrees are this. One parent row becomes
exactly one child row; the child having more rows than any single parent is a different fact.
`dim_business` (16 marts) unions eleven staging feeds and ends `group by geography, business_key,
building_key`. `int_water_section_match` (19 marts) uses `distinct on (entity_type, entity_id)`.
Both are `deliberately_coarser` and both were reported `silently_multiplied`.

> **Fix:** if the parent is referenced inside a `UNION` arm of the child, the hop is never silent
> multiplication. Readable from the AST, no judgment, no call.

**An envelope built from stored bounds is a tessellation, not a radius.** Both `bbox_as_radius`
disagrees. `stg_blm_plss_sections` builds `ST_MakeEnvelope(cx0, cy0, cx1, cy1)` from columns on the
same row and intersects a land-grant polygon with it; the box is the intended geometry and its own
comment says so. The check's detail argues "a box is not a circle", which only holds when the
envelope approximates a radius.

> **Fix:** corners that are columns → tessellation, skip. Corners that are a point ± a constant →
> the proximity case this check is for.

## 2. Seventeen unclears are one problem: the finding does not carry enough to settle it

In every case the missing piece is something assay already computes.

| family | what is missing | why the ruling stalls |
|---|---|---|
| `hop_multiplies_rows` | **which hop** the `GROUP BY`/`DISTINCT` sits on | the child has a collapse somewhere; proving it is on *this* path needs the file read |
| `description_contradicts_the_code` | **which clause** contradicts | the detail is the same generic paragraph on every finding |
| `test_cannot_fail` | **how often the default wins** (see §3) | "cannot fail" is true and not the actionable sentence |

`water_address_sections` is the clearest case: its model description and its SQL header are the
**same sentence verbatim**, so the finding must rest on a column description or the body, and
nothing in the output says which. Naming the contradicted clause would convert most of these
seventeen into rulings.

> **Fix:** `description_contradicts_the_code` already knows which sentence it judged — print it.
> `hop_multiplies_rows` already has the parsed path — say where the collapse was or was not found.

## 3. `test_cannot_fail` finds a live bug and reports the dead one

Every one of the forty is correct as stated. But `not_null` on a `COALESCE` ending in a literal is
not a dead guard — it is a **live guard pointed at the wrong column**:

```
water_rights.dwr_analysis_status      170,730 of 172,695  (99%) are the default 'not looked up'
water_parcels.irrigated_acres…      2,623,519 of 2,732,101 (96%) are 0
az_section_summary.n_well_depth        95,650 of 114,305  (84%) are 0
az_section_summary.parcel_count        85,264 of 114,305  (75%) are 0
water_section_summary.rights_late…     45,842 of  64,433  (71%) are 0
```

The test passes on every row while saying nothing about whether any lookup ran. The coalesce
conflates "none" with "not measured".

> **Fix:** when the pattern is `COALESCE(x, <literal>)`, count the share that ARE the literal and
> put it in the finding. "This test cannot fail" is true; "this default is 99% of your rows" is what
> someone acts on, and it is one query — the same batching `which_have_failures` already does.

Related sub-case worth its own name: `water_outreach_agents.contact_role` is
`case when max(w.email) is not null then 'brokerage_office' end` — one branch, no else. An
`accepted_values` test on a **constant**, which cannot fail by construction rather than by today's
data. Stronger than the general finding and currently indistinguishable from it.

## 4. `practices --keys-only`: "can be written" is still not "would pass"

0.9.3's output-column intersection and 0.9.4's counting are both right. Two gaps remain.

**Custom schemas are invisible.** Everything in `main` counts; everything in `main_water` and
`main_water_az` comes back `(not counted)` — including `stg_water_resume_entry_facts`, the 149×
case the release leads with. dbt's `+schema:` is common past a certain project size and the manifest
carries `schema` per node.

**`1.56x` prints as `2x`.** `business_leads` is 73,608 rows over 47,144 distinct. Rounding up
overstates a number someone acts on.

## 5. MCP

**The documented start line cannot start.** `uvx dbt-assay mcp` → `CONNECTION_CLOSED`, because a
bare `uvx` installs the base package without the optional extra. `uvx --from "dbt-assay[mcp]" assay
mcp` connects. `serve()`'s import guard raises the right message, but over stdio nobody sees it.

> **Fix:** document the extra, and check the import *before* opening the transport so the error
> lands on the terminal.

**A locked store reports the wrong cause.** `rule()` returns *"no store to write to. Run any judged
command once to create one."* when a DuckDB reader is open elsewhere in the process. The store
exists; it is locked. That message sends someone to create a file they already have, and the two
cases have opposite fixes.

**The review half is missing.** `rule()` closed the write gap, and the remaining bottleneck is that
a human still has to leave the conversation to confirm anything. The schema already supports the
answer: `adjudications` carries `decided_by` and `source`.

> **Proposal:** `rule(..., on_behalf_of='human', decided_by='<name>')`. The agent presents the
> finding and its reading in chat, the person says yes or no, the agent records **their** verdict as
> `source='human'`. The property survives — a person still has to say the word — and the part that
> does not scale, them typing it, goes away. Pair it with a `review_queue()` read that returns what
> is unruled, ranked, with any agent reading attached.

## 6. Smaller, all confirmed

- `dbt seed --select "assay_*"` matches nothing though `dbt list` shows all five nodes;
  `path:seeds/assay` works. That is the last line `export` prints.
- `assay practices` reported one standard-practice category against a **partially built**
  `dbt_project_evaluator` (5 `fct_` models of many) with nothing saying the rest were absent rather
  than clean. Fixed in 0.5.1 for `adjudicate`; worth checking the same path here.
- `onboard --compile` needs `--dbt "uv run dbt"` on a uv project and prints one line about it above
  a success summary. Lockfile detection would remove a wasted run.
- `banks --judge` puts four shipped families within 0.11 of the 0.60 threshold
  (`claim_alignment` 0.50, `field_matches_its_name` 0.54, `edge_preserves_the_grain` and
  `units_are_what_the_column_claims` 0.61). Caching stops the flapping; a one-word rewording near
  the line still crosses it.
- The overlap judge scored one five-option family at mass **0.35** where a reconstruction scored
  0.74. The likeliest difference is an option that **routes to another by name** — "the answer is
  `something_else`, not this" — which reads as disjoint to a text check while the answering model
  does not honor it.

---

## What is working and should not be touched

- **`vocab`.** Fifteen terms measured in the warehouse made `traverse` flag `wdid` joins into
  `water_rights` without anyone writing a water question. Knowledge written once reaching questions
  nobody wrote is the promise, and it is kept.
- **The `source='human'` separation.** A hundred agent writes, `regress` unmoved, no thought
  required after the first check. This is what makes agent writes safe.
- **Refusal language.** *"NOT COUNTED — and an uncounted grain is not a passing one."* *"The table
  is EMPTY, so any uniqueness test on it passes for the wrong reason."* Both are the tool getting
  right the exact failure this project kept finding elsewhere.
- **`min_adjudications`, and the footer that says an agent cannot raise the ruled-on figure.** The
  claim is made where the result is read, not buried in docs.

---

# Round two: items 1–5 verified, item 6 worked

Re-run against the same warehouse on 0.13.0.

## Verified fixed

```
stg_water_resume_entry_facts   main_water      149x        was (not counted)
int_water_call_exposure_basis  main_water      1.31x       was 1x
az_section_parcel_sales        main_water_az   HOLDS       was (not counted)
fact_permit                    5.81x                       was 6x
business_leads                 1.56x                       was 2x
export's closing line          path:seeds/assay + why the glob fails
locked store                   names DuckDB's single-writer rule
```

The schema fix corrected **me** as well: I had measured `az_section_parcel_sales` as fanning out
2.23x on three columns, and the grain is four. With the full key it holds at 944,604/944,604. My
number was wrong because I truncated the key — which is the same defect the intersection fix exists
to prevent, made by hand.

`option_routes_to_another` closed item 6e in the best possible way. It fired on exactly the two
families where I had guessed the routing sentence explained the overlap judge's false negative.
Rather than acknowledge it I measured it: removing the sentence kept **8/8 recorded verdicts,
regress exit 0, answer mix identical at 75/4/3**, and cleared the warning. The rule was right and
the sentence was not load-bearing. One run, because eight verdicts existed.

## Still open

**`practices` and `patch` disagree about an empty table.** Same model, same run:

```
practices:  int_azcc_owners  owner_key  (holds: 0 rows, 0 distinct)
patch:      int_azcc_owners: the table is EMPTY, so any uniqueness test on it
                             passes for the wrong reason
```

`patch` is right and `practices` is the one `onboard` points at first, with `holds` sitting in the
column a reader scans for green. The 0.10.2 refusal should reach both.

**The lockfile detection looks in the wrong directory.** `onboard --compile` still says
*"'dbt' is not on PATH. Pass --dbt with the command you use"* with no `uv run dbt` suggestion,
because it looks beside `dbt_project.yml`:

```
dbt_project.yml   ./transform/dbt_project.yml
uv.lock           ./uv.lock           <- repo root, one level up
```

A dbt project in a subdirectory (`transform/`, `dbt/`, `warehouse/`) is the common layout in a repo
that is not only dbt. Walking up to the git root would find it.

**The dbt-binary flag is named two ways.**

```
practices   --dbt-bin
adjudicate  --dbt-bin
probe       --dbt-bin
patch       --dbt
onboard     --dbt
```

It also flipped between releases — `practices` took `--dbt` on 0.9.4 and takes `--dbt-bin` on
0.13.0, so a script written against one version breaks on the next. Worth accepting both with one
as the documented spelling.

## On `rule(decided_by=...)`

Rejecting `on_behalf_of='human'` was right and my proposal was wrong. *The one field an agent fills
in itself cannot be the field that decides authority* — that is the whole property, and I had
proposed handing the agent the key to it. `decided_by` as provenance with the tier still fixed at
`agent` is correct.

`review_queue()` is the other half and matters more than it looks: an agent that can see the queue
it is building can rank its own next reading by blast radius, which is the difference between
ruling on 99 findings and ruling on the 99 that matter in an order somebody would choose.

---

# Round three, 0.16.0: the release is good and `rule()` orphaned all 99 rulings

## Verified fixed

`traverse` re-run: **`silently_multiplied` 177 → 78**, `same_thing` 94 → 273, and only 274 of 543
edges re-asked because the rest cached. Five of the eight models I disagreed on are now correct —
`buyer_leads`, `fact_residential_permit`, `multifamily_leads`, `water_stream_gauges` and
`buyer_leads_enriched` all read `same_thing` or `deliberately_coarser`.

`effectiveness --source all` is the best new thing here. It turns the rulings into a measurement of
the CHECKS:

```
hop_multiplies_rows   23 ruled   0/10 agreed (0%)   13 unclear   10 open
join_fans_out          4 ruled   4/4  (100%)
test_cannot_fail      38 ruled   38/38 (100%)
water.prio             8 ruled   8/8  (100%)
```

and its closing line is the right rule stated once: **"Reword an option to fix a disagreement; add a
field to fix an unclear."**

## THE BUG: every agent ruling is orphaned

```
findings.subject        'model.sunny_data.int_azcc_owners'    fully-qualified unique_id
adjudications.subject   'int_azcc_owners'                     the bare name rule() accepted
```

```
agent rulings                                     99
rulings that join to a finding on `subject`        0
rulings that join on `subject_name` instead      249   <- the join that would work
```

`rule()` took a bare model name, returned `recorded: true`, and wrote 99 rows that can never reach
the findings they are about. It is the same shape as the eight other absence-reads-as-success
defects in `FIELD_NOTES.md`, this time in the write path of the feature built to close the loop.

**The visible symptom is `review_queue()`.** It returns 20 items with **0 agent readings attached**
while its own note says *"Findings an agent has read are first: a person confirming a reading is one
keypress."* The feature is described in its own output and does not work.

> **Fix:** normalize in `rule()` — resolve a bare name to the model's `unique_id` — and reject a
> subject that matches no finding and no decision. A ruling nobody can join is not evidence, which
> is the same argument the tool already makes for a waiver without a reason.

## The granularity gap underneath it

`rule(subject, question)` identifies a MODEL and a CHECK. Findings are finer than that:

```
az_section_summary        test_cannot_fail       8 findings
fact_residential_permit   hop_multiplies_rows    6 findings
water_section_summary     test_cannot_fail       6 findings
```

One verdict lands on all of them. **That is how I got `dim_business` wrong.** I read its union arms,
ruled `disagree` on the model, and two of its six edges were not union arms at all —
`crime_leads` and `business_leads` read `dim_business` on `(geography, building_key)` while its
grain is `(geography, business_key, building_key)`. Measured: **69,966 rows over 47,178 distinct
pairs, a 1.48× fan-out.** `silently_multiplied` at 0.45 was correct and my blanket disagree was not.

So one of the twelve disagrees in the last report was mine, not the checker's — and the reason is
that the API let me rule on six different edges with one keypress and one reading.

> **Fix:** `rule()` should take the finding's own identity. `review_queue()` already returns
> `check + model + summary` per item; returning an id and accepting it back would close both this
> and the orphaning at once.

## Still open from round two

- `practices` prints `holds: 0 rows, 0 distinct` where `patch` refuses the same table as
  `EMPTY ... passes for the wrong reason`.
- Lockfile detection looks beside `dbt_project.yml`; here that is `./transform/dbt_project.yml`
  while `uv.lock` is at `./uv.lock`, one level up.
- `--dbt-bin` on `practices`/`adjudicate`/`probe` versus `--dbt` on `patch`/`onboard`, and it
  flipped between 0.9.4 and 0.13.0.

---

# Round four, 0.17.0: the repair works, and MCP shows an agent 20 findings of 162

## Verified on the real store

```
assay review --repair          99 subject(s) re-pointed
rulings joining to a finding    0  ->  249
review_queue()                  0 of 20 carrying a reading  ->  20 of 20
rule() on a bare name           "resolved_as": "resolved from the bare name 'crime_leads'"
```

Each queue item now carries `finding: "fba0bdd8a45a"` with the verdict and reasoning inline, so a
person sees the finding and the reading in one place. That is the loop the whole thing was for.

## My `dim_business` self-criticism was half wrong, and the half that was right is worse

The summary format is `parent -> child`, so the three findings stored under `dim_business` are its
union arms — `stg_mesa_business`, `stg_care`, `stg_childcare` — and my `disagree` on those was
correct.

The fan-out belongs to **`crime_leads`**, which I had bucketed `unclear` with the reason *"probably
a union false positive, not certain — the model contains a collapse somewhere."* It does contain
one. The collapse is in the **parent**, not on this hop. `crime_leads.sql:30` joins `dim_business`
on `(geography, building_key)` while that model ends `group by geography, business_key,
building_key`, so the join omits `business_key`. Measured through dbt: **69,966 rows over 47,178
distinct pairs, 1.48x**. Corrected to `agree`.

So the honest version: one of my twelve disagrees was fine; one of my seventeen `unclear`s was a
real finding I hedged on. The hedge was the right shape — the reason names exactly what I had not
checked — and it was still wrong.

## THE REMAINING GAP: judged families are invisible through MCP

```
findings in the latest run   162   across 7 families
MCP findings() returns        20   across 2  (arbitrary_pick, test_cannot_fail)
review_queue() checks              arbitrary_pick, test_cannot_fail
```

The structural shortfall is the granularity fix **working** — `arbitrary_pick` is 28 in the run and
11 in the tool because my rulings were per-model and findings are per-finding, so it correctly shows
what I have not actually read.

The judged families are missing outright:

```
hop_multiplies_rows                58 findings   0 visible through MCP
description_contradicts_the_code   18 findings   0 visible
```

I could only reach them through direct SQL against the store. **An agent working through MCP sees
20 of 162 findings**, and the 76 it cannot see are the ones where the disagree rate is highest
(`hop_multiplies_rows` at 0/10 agreed) and where a reading is worth the most — a description that
contradicts its code needs prose read against SQL, which is precisely what an agent is for and a
parser is not.

> **Fix:** `findings()` and `review_queue()` should read the latest run from the store, not only the
> live structural pass. The rulings already prove the tier separation holds; there is no reason the
> agent can write about a family it cannot read.

## Still open from earlier rounds

- `practices` prints `holds: 0 rows, 0 distinct` where `patch` refuses the same table as `EMPTY`.
- Lockfile detection looks beside `dbt_project.yml`; the lockfile is at the repo root one level up.
- `--dbt-bin` versus `--dbt` across commands, and it flipped between 0.9.4 and 0.13.0.

---

# Round five, 0.17.1: the judged stream reaches MCP, and `review_queue` hides 122 items silently

## Verified

```
findings()                    showing: "20 of 142", all seven families in the breakdown
findings(check=...)           reads one family end to end
violations()                  74 annotated, and the note names the human-verdict gate
review_queue(limit=200)       142 items, all seven families, 142 of 142 carrying a reading
hop_multiplies_rows           30 here too, against 58 in my stored run -- 0.15.0's unique-key
                              counting cut it on this warehouse as well
```

The single-path fix is right and the guard that compares both surfaces is the right guard. A
source-level check would not have caught two call sites computing one fact.

## `review_queue()` is the same reporting gap `findings()` just closed

Default `limit=20` returns 20 items across three families and says nothing about the rest:

```
default    20 items   description_contradicts_the_code, arbitrary_pick, test_cannot_fail
limit=200  142 items  + hop_multiplies_rows 30, join_fans_out 3, bbox_as_radius 2,
                        ranks_by_degrees 2
```

Response keys are `waiting_for_a_person`, `already_ruled_by_a_person`, `note`,
`pass_the_finding_id_back` — **no `showing`, no total, no breakdown.** I concluded from a default
call that `hop_multiplies_rows` was excluded from the queue entirely, wrote that up as a bug, and it
was four families sitting below the cut.

`findings()` fixed exactly this in the same release by adding `showing: "20 of 142"` and
`every_check_in_this_project`. The queue is the surface an agent uses to decide **what to read
next**, so a silent truncation there chooses its reading order for it.

> **Fix:** the same two fields. `showing: "20 of 142"` and the per-check breakdown.

## One smaller thing

`findings()` carries no reading — `an_agent_already_said` exists only on `review_queue()` items. That
is defensible as a division of labor, but an agent that calls `findings(check='hop_multiplies_rows')`
to read a family end to end gets 30 findings with no indication that 9 of the 11 models already
have a reading recorded. It re-reads what it already read.

## Still open from earlier rounds

- `practices` prints `holds: 0 rows, 0 distinct` where `patch` refuses the same table as `EMPTY`.
- Lockfile detection looks beside `dbt_project.yml`; the lockfile is at the repo root one level up.
- `--dbt-bin` versus `--dbt` across commands, and it flipped between 0.9.4 and 0.13.0.

---

# Round six, 0.19.0: the page, and completeness earning its keep on the first run

## The page

Deterministic, verified: two runs byte-identical at 9,282 bytes, and it carries the manifest's
`generated_at` rather than a wall clock. It opens with the right number and the right sentence:

> **0 of 149** — *"The only number here a release cannot improve… A good release makes it look
> worse. That is the design working."*

`100 agent rulings, kept apart` beside it, with the property stated in full. **0% of what assay
currently sees has been read by a person** is the honest headline for this warehouse and it should
stay uncomfortable.

## `completeness` found real things immediately

```
source_reaches_nothing    5    69,946 rows loaded on a schedule that no model reads
                               adwr_gwsi_sites 46,897 · adwr_monitoring_sites 9,962
                               adwr_pumping_wells 9,702 · adwr_townships 3,385
hop_drops_most_rows       2    keeps 1% of stg_adwr_sections, 0.4% of dim_owner
models that are EMPTY     3
```

The AZ sources are a genuine finding — declared, loaded every run, consumed by nothing.

**And it surfaced a limit worth stating in the tool.** `enriched.well_documents` (2,606 rows) is
reported as read by nothing, which is true of the dbt graph and false of the warehouse:
`enrichment/well_scans.py` reads it in Python and writes `enriched.well_scan_reads`, which models
do read. A source consumed only by an out-of-band enrichment step is invisible to a manifest-based
check, and this project has several. Not a defect — a limit the finding should name, because the
obvious action on "nothing reads this" is to delete it.

## `hop_drops_most_rows` has the sibling-CTE blind spot

Both hits are intentional narrowing that the check cannot see, because the filter is not on the hop:

`multifamily_leads` joins a roster of **apartment buildings only** → `dim_building` → `dim_owner`,
so keeping 13,694 of 3,156,986 owners is the model being scoped to multifamily. The narrowing lives
in a sibling CTE that the join runs against.

Same family as the union blind spot fixed in 0.15.0: **the thing that makes the hop legitimate is
upstream of the hop.** A join whose other side is itself a filtered CTE is not "a join that is not
matching" — it is a join against a deliberately small set. That is checkable: if the opposite
relation in the join is a CTE carrying a WHERE or a narrow source, the drop is explained.

## Still open

- `practices` prints `holds: 0 rows, 0 distinct` where `patch` refuses the same table as `EMPTY`.
- Lockfile detection looks beside `dbt_project.yml`; the lockfile is at the repo root one level up.
- `--dbt-bin` versus `--dbt` across commands, and it flipped between 0.9.4 and 0.13.0.

---

# Round seven, 0.20.0: `verify` at scale, and absence is reading as disagreement again

1,780 claims checked, 1,719 calls, **$0.1735**. Extraction was 2,988 checkable claims across 349
models for $0.2049. Both cheap enough that the whole loop is a sub-dollar operation on a 358-model
warehouse, which is the thing that makes it usable at all.

**389 of 1,780 claims came back contradicted — 22%.** Two read by hand, both false positives at
high confidence, both the same cause.

```
int_water_diversion_history   p=0.95
  claim   "the FULL diversion record per structure, 1886 to 2026"
  actual  record_first_year min 1886, record_last_year max 2026, 39,308 rows
```

The claim is exactly true. The years appear once in the file — in the comment making the claim. The
SQL derives `record_first_year`/`record_last_year` from a source and never states them, so the
judge sees specific numbers against code that does not mention them.

```
stg_cdss_dams   p=0.95
  claim   "Findings already say whether an impoundment carries a decreed storage right
           (ponds_covered, ponds_permitted, ponds_undecreed)"
```

`ponds_covered` appears **only** in that comment. The claim is about the findings layer, which is
Python, not about this model's SQL. The evidence cannot speak to it at all.

Both are *"the model could not see what it was asked about"* — the shape `VERIFICATION.md` records
as fixed in `claim_alignment` v4 with *"criteria: absence is not disagreement"*. It is back, and at
1,780 claims the rate is high enough to matter: a 22% contradiction rate that a reader cannot trust
is worse than no verify pass, because the true contradictions are buried in it.

**Two things would separate the cases, and assay can compute both:**

- **A claim whose subject does not appear in the evidence is not a contradiction.** `ponds_covered`
  occurring zero times in the model's SQL is checkable before the call, and the right answer is
  `says_nothing` — or the claim should not be sent at all.
- **A claim about values is not answerable from SQL alone.** "1886 to 2026" is a statement about
  rows. `completeness --verify` already counts through dbt; a claim carrying a literal number could
  be routed there instead of to a text judge, which is the same split that fixed
  `units_are_what_the_column_claims`.

**Duplicates are also inflating the count.** `int_water_diversion_history` appears at 0.95 and 0.93
with near-identical sentences, `stg_cdss_dams` at 0.95 and 0.93 — the same claim extracted from a
model header and from its schema description. Deduplicating on normalized text would cut the list
before anyone reads it.

## Smaller

`assay page` on a locked store prints the full DuckDB error naming the conflicting PID, then
advises *"Pass --store with a writable path, or run from a writable directory."* The store is
writable; it is locked. Same shape as the `rule()` message fixed in 0.13.0, one surface over.

---

# Round eight, 0.22.0: repair verified, and a replacement is a fork

## Verified

```
review --repair    water.prio -> seniority_ordered_by_the_wrong_date
config             seniority: human 8, "12 more"      was human 0, "20 more"
                   column_is_part_of_the_key: labels 82, still "20 more"
```

Both behaviors right: the eight verdicts count toward a gate floor, and the 82 labels correctly do
not. `meta.read_by` on `well_documents` suppresses its completeness finding and the wording is now
*"nothing in this dbt project reads"*, which is the true statement.

## A replacement inherits nothing after the day it is made

`assay_questions/water_edges.yml` replaces `edge_preserves_the_grain` and still carries
`case_number`, `permit` and `contractor` in its examples — the exact strings 0.22.0 removed from the
shipped bank. On a water warehouse those examples are apt and I am keeping them, so this is not a
bug here. The general shape is the problem:

**I copied the four shipped options word-for-word on purpose**, so that the hand verification
recorded for that family would still largely apply. That same copying is what stops every later
improvement. A fork made to preserve one property silently forfeits another, and nothing says so at
any point — `banks` reports `yours, replacing` and that is all.

> **Suggestion, and assay already holds both halves:** when a replacement's copied options differ
> from the shipped family it replaces, say so. Not an error — a fork is legitimate and usually
> deliberate — but "this replaces `column_role`, and 3 of its 5 options are byte-identical to a
> version two releases old" is the kind of thing a person wants told once. It is the same argument
> as the stale-skill check: a copy nobody knows is stale reads as current.

## The bank audit was the right question to ask

Worth recording what prompted it: I noticed the shipped `edge_preserves_the_grain` example matched
this warehouse's exact bug class (`case_number` unique only within a division) and could not tell
whether that was a coincidence, a generality problem, or evidence the check was built from a real
case. It was the middle one, and only a second project could have surfaced it. Every other finding
in this file came from running assay on one warehouse; this is the only one that came from asking
what it would look like on somebody else's.
