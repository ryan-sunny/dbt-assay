# Review: what to fix, ranked

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
  does not honour it.

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
