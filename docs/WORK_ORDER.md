# Work order: the package, the outcomes, and the confidence nobody can check

> A record: a work order written from one session. It describes assay as it was then; the [README](../README.md) and [OVERVIEW.md](OVERVIEW.md) describe it now.

Written 2026-09-21 from a session that used assay 0.28.1 on the sunny_data warehouse (358 models,
236 findings) while that warehouse was in the middle of a real outage. Everything below is either a
measurement from that store or a fact checked against the code. Where something is an opinion it
says so.

The ordering is by what the evidence supports, not by size.

---

## 1. Make confidence answerable. Nothing else on this list is blocked on it, and nothing else
##    changes what assay IS as much.

`effectiveness` measures agreement per family per version. `calibrate` measures the grain judgment
against declared keys. Neither answers the question a person actually asks of a probability:

> when this thing is confident, is it right more often than when it is not?

On sunny_data that question is **unanswerable**, and the reason is structural rather than a lack of
verdicts. 195 adjudications exist. Joined to the decision each one ruled on:

| confidence band | ruled | agree | disagree | agreement |
|---|---|---|---|---|
| < 0.55 | 3 | 3 | 0 | 100% |
| 0.55–0.70 | 2 | 2 | 0 | 100% |
| 0.70+ | 3 | 3 | 0 | 100% |
| **null** | **82** | 56 | 26 | **68%** |

Every disagreement in the store sits on a decision carrying no confidence. Three separate causes,
each fixable:

1. **105 agent rulings attach to a finding, not to the decision behind it.** `rule(finding, …)`
   files against `finding_id`. The confidence lives on the `model_decisions` row that produced the
   finding. So the most numerous verdicts carry no probability and can never contribute to a
   calibration curve.
2. **82 label verdicts land on `key__*` decisions that store `confidence = null`.** The grain
   family answers without one. Those 82 are the only verdicts with real disagreement in them (26),
   and they are precisely the ones with nothing to correlate against.
3. **8 human verdicts fan out to 6 decision rows each**, one per retired `water.prio` prompt
   version, because the join is `(decision_key, question)` and nothing picks the current version.
   A naive join over this store returns 130 rows for 90 adjudications. Any calibration report must
   take the latest decision per key, the way `live_decisions` already does.

**What to build.** A report — `assay calibration`, or a section of `effectiveness` — that bands the
latest decision per key by confidence and shows agreement in each band, per family. Then the
sentence "mean confidence 0.50" is replaced by something load-bearing: either the bands separate,
in which case the number is worth gating on, or they do not, in which case `min_agreement` is
measuring the wrong axis and should say so.

**Do not report mean confidence as a headline.** It was the first thing this session reached for
and it is a bad summary of a distribution. `edge` at mean 0.50 over 1,629 answers is compatible
with a perfectly calibrated judge and with a coin. Only the bands tell them apart.

---

## 2. Ship a dbt package. The export is six relations and one example query.

`assay export` writes six tables plus `assay.yml` documenting every column, plus one example query
over `assay_findings`. On sunny_data, after a dbt seed:

| seed | size | models that read it |
|---|---|---|
| assay_model_decisions | 4.7 MB | **0** |
| assay_edge_facts | 1.6 MB | **0** |
| assay_observed_keys | 83 B | **0** |
| assay_findings | 1.4 MB | 1 |
| assay_adjudications | 76 KB | 1 |
| assay_runs | 1.3 KB | 1 |

80% of the payload is loaded on every build and nothing consumes it. That is
`source_reaches_nothing`, assay's own check, pointed at assay's own output — and it does not fire,
because the check covers dbt *sources* and these arrive as seeds. **Extending that check to seeds
is a small change and it would have caught this.**

The instinct to fix it by exporting less is wrong. The tables are not waste; they hold things that
exist nowhere else, and two queries written in ten minutes against this warehouse found:

- **`edge_facts` is the join surface, aggregated.** `traversal` shows one model's hops. Nothing
  sums them. `water_section_fingerprint` joins across **11 distinct keysets over 36 hops**;
  `int_lead_contact` 9 across 10. That is a grain-risk ranking, derived from the DAG, exact and
  free, and today it exists only if the user writes the SQL.
- **`model_decisions` is the uncertainty surface.** Per-family answer counts with how many fall
  under the gate, joined to blast radius, answers "which high-reach models rest on answers nobody
  is sure of". Also invisible without hand-written SQL.
- **`observed_keys` is empty** for anyone who has not run `probe`, and exports anyway. Either skip
  empty tables or document that it stays empty until probed.

**What to build.** Models over assay's own seeds, shipped, so the relations have readers by
construction:

- `assay_debt` — findings rollup, grouped by `check_name`. The shipped example already explains why
  a hand-written CASE drifts: on sunny_data one did, and 121 of 236 rows landed in `other` after a
  release added a check.
- `assay_uncertainty` — confidence distribution per family with under-gate counts, joined to marts.
- `assay_join_surface` — keysets and dropped columns per model, off `edge_facts`.

Elementary is the closest precedent and the shape is the same.

---

## 3. The over-assertion check. This is the one today's outage proves.

`assay tests --gaps-only` found 94 real coverage gaps on sunny_data, ranked by blast radius, each
naming the exposure and the test that would close it. It answers the **absence** direction: here is
something nothing asserts.

Both test failures in today's outage were the **opposite** direction — a test asserting something
it had no right to.

`not_null` sat on `int_water_well_parcel.parcel_id` and failed on one row of 49,034. The column is
`carried` from `water_parcels`, where **7,226 of 2,732,262 rows are null**. The test could only ever
have been one bad row from failing; it took a long time to find that row, and when it did the
obvious repair was to drop a legitimately matched well.

assay already derives that the column is carried, and already has `probe` to count through the
user's own dbt. So the check is structural:

> for a `not_null` on a carried column, does the parent it carries from contain nulls in that
> column?

Same shape for `accepted_values` against the parent's observed distinct set, and `unique` on a
column the parent does not hold unique. It is the mirror of `test_cannot_fail`, which is this
project's most reliable family at 38 of 38 agreed. Call it a test that cannot pass, or one that
outruns its source.

The `not_null` case needs no count if the parent's own schema declares nullability; the other two
need `probe`, which pulls in §5.

---

## 4. Read `run_results.json`. assay reads structure and the failing population, not outcomes.

Today: `manifest.json` for structure, `dbt_test__audit` via `rows.py` for the failing population
(that is already the basis of the row-adjudication family), `catalog.json` for columns,
`sources.json` for freshness. `run_results.json` appears once, in `cli.py`, purely to warn that
`dbt compile` overwrites it.

That file holds outcomes, and outcomes answer things structure cannot:

- **A test that has never fired.** `test_cannot_fail` proves it from the SQL. Outcomes prove it
  empirically — "passed in 40 consecutive builds and asserted nothing". Independent evidence for
  the same conclusion, and the second needs no parser.
- **A test that always fires.** A permanently red test is a waiver nobody wrote down. assay already
  has the waiver machinery to receive it, with a required reason.
- **A test that was SKIPPED.** The important one. Today two model errors skipped **108 models** in
  a 643-node build, and the alerting reported "112 of 645 failed" with no distinction between
  failed and never ran. A skipped test is not a pass — the same sentence `completeness` already
  opens with. assay knows the DAG, so given run_results it can compute what nothing else here
  computes: **of the models in this project, how many had an assertion actually execute in the last
  build.**
- **The cap.** `+limit` is in the manifest node config. dbt applies `limit` to the test query, so a
  failure count equal to the configured limit *is* the cap, not the count. On sunny_data
  `assert_water_address_resolves` reported "Got 500 results" against a real 6,251 — an order of
  magnitude, reported as exactly the cap, on every test in the project at once, with a comment
  beside the setting asserting the opposite. Detecting `count == configured limit` is exact and
  free and turns "500" into "500 or more".

**Where outcomes come from is the awkward part, and it is a real constraint rather than a detail.**
`run_results.json` lives wherever dbt ran. On this setup dbt runs on the production box; assay runs
on the laptop and **the box has no assay installed at all**. So "which tests executed in the last
build" is a question about a machine the tool is not on. Either assay runs there too, or
run_results ships into the store the way the seeds ship the other way.

---

## 5. Probe, and the line that MCP must not cross

**Rejected, deliberately: an ad-hoc query tool over MCP.** `count(model, predicate)` and
`distribution(model, column)` were proposed in this session and should not be built. Same
credential, completely different risk: an agent composing SQL against a 33 GB production warehouse
is a query console wearing a schema.

The line is not "can it query", it is **who composes the SQL**. `probe` is assay writing fixed
shapes derived from the manifest and handing them to the user's dbt. That is bounded by
construction and auditable. The rule for the MCP surface:

> **MCP exposes measurements, never a query interface.** The agent sees numbers it did not author.

**When probe may run is an open decision.** It touches the warehouse, and DuckDB is single-writer.
This session hit `Conflicting lock is held` twice just reading while jobs ran. If the checks in §3
need counts, probing during a build will collide on the box. Options: stay opt-in as now, or add a
snapshot-copy mode — a pattern that already exists in this stack for the command centre.

---

## 6. Write surface and permissions: less than it looks

Checked, not assumed:

- **assay never holds a warehouse credential.** `probe.run_via_dbt` shells out to
  `dbt show --inline <sql> --output json --limit 1` in the project dir. Its docstring gives the
  reason: parsing `profiles.yml` means reimplementing every auth dance, SSO included.
- **The store is always DuckDB**, hardcoded in `store.py`, always a local file.
- **`export` writes files** — CSV or parquet, to a directory. Nothing more.
- **The tables reach a warehouse only when the user runs `dbt seed`**, under dbt's own identity,
  into wherever `dbt_project.yml` sends seeds. On BigQuery or Snowflake they live there exactly as
  they do here.

So the whole warehouse write surface is **one schema**, and on an adapter with roles that is the
only place anything assay-related needs write access. Worth documenting, since on sunny_data the
seeds currently land in `main` next to `water_rights`.

A service account is not a dbt concept and cannot exist on this adapter. DuckDB 1.5.4:

```
REFUSED  create role assay_ro   ->  Parser Error: syntax error at or near "role"
REFUSED  grant select on t ...  ->  Parser Error: syntax error at or near "grant"
REFUSED  create user bob ...    ->  Parser Error: syntax error at or near "user"
```

No roles, no grants, no users. Access control is filesystem permission plus `read_only=True`. dbt's
`grants` config grants on objects dbt creates and is adapter-specific; it is not an identity. For
Snowflake or Postgres users a read-only role is ordinary practice and dbt's part is a second target
in `profiles.yml` — worth a paragraph in the guidance, not a feature.

---

## 7. Retention, after §2 lands

The schema already encodes the answer. Tables carrying `run_id` are exactly the ones a parser
regenerates for free; tables without it are exactly the ones that cost money or a keypress.

| table | rows | run_id | MB | regrown by |
|---|---|---|---|---|
| findings | 2,103 | yes | 1.80 | `assay check`, free |
| edge_facts | 5,157 | yes | 2.32 | `assay check`, free |
| unreadable | 270 | yes | 0.05 | `assay check`, free |
| runs | 9 | yes | 0.00 | nothing. it is the clock |
| model_decisions | 9,945 | no | 6.85 | re-asking every question, paid |
| claims | 5,794 | no | 2.66 | `claims --extract`, paid |
| adjudications | 195 | no | 0.11 | a person, one keypress at a time |
| observed_keys | 0 | no | 0.00 | `probe`, needs the warehouse |

**Rule: prune by `run_id`, keep last N, configurable. Nothing without a `run_id` is ever pruned.**
Over-pruning costs one `assay check` — three seconds, no network — and cannot touch the 8 human
verdicts, which are the only thing in the store a release can never rebuild.

Scale is not urgent: the whole store is **13.8 MB over 9 runs**, growing ~0.46 MB per run, all of
it in the three free tables. `model_decisions` is the largest table and does **not** grow per run —
it grows when questions are asked, which is correct.

**One caveat that depends on §2.** If the package ships models over `model_decisions` and
`edge_facts`, then latest-run-only export is wrong for them: confidence over time is the point, and
`effectiveness` already rests on that idea. Keep run history for anything a model reads.

---

## Operational notes for whoever picks this up

- **PyPI is at 0.24.1 while the repo is at 0.28.1.** Any `.mcp.json` using
  `uvx --from dbt-assay[mcp]` hands an agent a build four releases old, without the `guide` topics.
  Either publish or point the MCP config at the checkout.
- **`claude mcp add` writes machine-local state** in `~/.claude.json` keyed to one absolute
  directory. It is what assay's docs and onboard output both recommend, and it is why the server
  was invisible from one directory up and would be invisible to a fresh clone. `--scope project`
  writes a committed `.mcp.json` instead. The docs were fixed in 0.26.0; worth confirming onboard's
  closing line matches.
- **sunny_data's `ops_assay_debt`** is a user-written model, not shipped by assay. It is the
  evidence for §2: the one exported table with a reader has one because a person built it, and that
  version then drifted into 121 of 236 rows landing in `other`.
- **`assay tests --gaps-only` is genuinely good** and was not discovered until late in a long
  session with the skill loaded. Whatever ships from §3 should land next to it rather than beside
  it.

---
---

# Part two: closing the loop from findings to configuration

Written 2026-09-21, later the same day, against 0.32.2 on the same warehouse. Part one was written
before any of it shipped; this records what did, and then the one thing it exposed.

## What shipped from part one

| § | state |
|---|---|
| 1. Make confidence answerable | **shipped** (0.29.0) as `assay calibration`. The premise above was wrong and is left standing as written: `column_is_part_of_the_key` is a `noul`, whose answer IS the probability, so banding it by `confidence` finds nulls and concludes "unanswerable". Banding by the answer works. Left uncorrected because a work order that quietly edits its own bad reasoning teaches nothing |
| 2. Ship a dbt package | **shipped as queries, not models** (0.29.0). Three examples over the previously unread tables |
| 3. The over-assertion check | **shipped** (0.33.0) as `test_outruns_its_source`. Structural only; counts stay behind `--verify`. Two earlier versions were rejected from their own numbers: firing on "the parent does not declare `not_null`" caught 148 of 227 carried-column tests, and narrowing to the join/UNION cases found zero — correctly, because the field case had moved into `min()` over a grouped CTE after the model was rewritten to an INNER join. **7 of 646** `not_null` tests, the outage among them |
| 4. Read `run_results.json` | **shipped** (0.29.0) as `assay tests --run-results`. Verified against the real artifacts of a production outage: it named the capped count, the 108 skipped nodes, and "398 of 1,282 tests executed, covering 105 of 326 models" |
| 5. Probe and the MCP line | **decided** (0.31.0). No ad-hoc query tool, recorded as rejected. Probe walks least-recently-observed, reach-ranked, deterministic. Cross-process read-only is blocked too, so the DuckDB lock is guidance, not tool shape |
| 6. Write surface | **documented**. One schema; no roles exist on DuckDB |
| 7. Retention | **shipped** (0.33.0) as `assay prune`. Explicit, never automatic. `PRUNABLE` and `NEVER_PRUNED` are declared rather than inferred, so a new table belongs to one list or the other and a test fails until it does. 3,563 findings to 730, every paid table byte-identical |
| 8. `assay suggest` | **shipped** (0.33.0), amended in 0.33.1. Seven rules, 69 candidates on this warehouse; it reproduced both figures §8 derived by hand (`section_id` 65 hops / 24 models, `xmin`/`xmax` 14 / 9). Four refusals, each because the first attempt did the thing and was wrong — see 0.33.1 in FIELD_NOTES for the one that ranked repaired clusters above live ones |

Also shipped and not in part one: `seed_reaches_nothing`, the `observed_keys` timestamped series and
its two drift checks, the unconfigured-checks panel, the Overview, stored decision states with
`assay evidence`, and the offline review form (`assay review --emit` / `--load`) — which is the
answer to a thing this order did not raise: one turn per finding is 159 turns, and the number of
findings ruled on by a person had not moved off **0 of 159**.

---

## 8. `assay suggest` — the half of onboarding that assumes you built the tool

`guide` teaches what a vocab term is for. `init` writes defaults. `config` shows what resolved.
**Nothing goes from what the check found to what this project should therefore configure.** So
onboarding currently reads: here are 236 findings, and here is an essay on how config works, now
make the connection yourself. A person who built the tool makes it in an afternoon. Nobody else
does.

It is derivable. Two worked examples, both done by hand on sunny_data in one session, both from
data already sitting in the store.

### Evidence one: the top vocab candidate is the most-joined column in the warehouse

`section_id` is joined on in **65 hops across 24 models** and is absent from a 15-term vocab.
`section_key` is present, under a different name. And the non-obvious fact about it was produced by
a production incident the same day: **6,251 address rows resolve to `section_id` values carrying an
`LGC` prefix — land-grant cells, not PLSS sections — which `int_water_sections` does not contain**,
concentrated in Saguache, Costilla, Las Animas, Archuleta and Huerfano.

That is the `case_number` shape exactly: a key whose membership is not what the name suggests. The
signal that finds it is arithmetic over `edge_facts` plus a set difference against `vocab`.

### Evidence two: two waivers were saying one sentence

`audit.yml` waives `bbox_as_radius` on `stg_blm_plss_sections` and on `int_water_land_grant_cells`,
and both reasons say the same thing: the envelope is a grid cell built from bounds stored on the
row, not an approximation of a radius. `xmin/xmax/ymin/ymax` are joined in **14 hops across 9
models**, so the next model to do it needs a third waiver.

One vocab term replaces both and covers the next one. The signal is a reason appearing in more than
one waiver.

### And the decisive one: this is already how the config got written

That first waiver is a **verbatim descendant of an agent ruling** in `adjudications` — same
`ST_MakeEnvelope(cx0, cy0, cx1, cy1)`, same "the envelope is a GRID CELL". Somebody read a ruling
and typed it into `audit.yml`. The command automates a path that has already produced good config
on this warehouse; it does not invent a new one.

### The tri-partition that makes it more than a findings list

The store holds 195 adjudications. Split by verdict they point at three different files:

| verdict | n here | what it means | where it belongs |
|---|---|---|---|
| `disagree` with a reason | 12 | the check is right to look and wrong here | a **waiver**, reason already written |
| the same reason on N subjects | 2 pairs | not N waivers — one missing thing | **vocab**, or the check itself |
| `unclear` | 17 | the state does not carry what the question asks | the **question**. Never config |

That last row is why `unclear` must stay out of an agreement denominator, which `calibration`
already does: disagreement means the criteria are wrong, unclear means the question cannot be
answered from what it was given, and those are different edits.

The repeated-reason row has a second reading worth keeping: "A UNION MEMBER EDGE CANNOT MULTIPLY"
appears on two models, and the right response was not two waivers — it was the structural fix that
landed in 0.15.0 and 0.21.1. A reason repeating is the signal that something upstream of the config
is missing.

### Derivation rules

| section | signal, all from the store after a `check` |
|---|---|
| vocab | columns joined in many hops, absent from vocab, ranked by hops × models |
| vocab | any column whose **observed** uniqueness contradicts its name — an `*_id` reading `has_duplicates` |
| vocab | one reason appearing across ≥2 waivers or ≥2 rulings |
| waivers | `disagree` rulings, agent's reason as the draft, expiry required |
| questions | checks firing that `audit.yml` does not name. The panel exists; it needs to emit the YAML block |
| questions | per-check agreement from `calibration` — a family at 68% should not be `fail`, and the number says so |
| explanations | recurring "the world is like that" reasons on row adjudications |

### The line that keeps it honest

**It proposes the candidate and the measurement. It never proposes the meaning.**

It may say `section_id` is joined in 65 hops, is absent from vocab, and that 6,251 of its values
resolve to nothing. It may not say what `section_id` means. `means:` and `implies:` arrive **empty**
with the evidence underneath them.

This is not a style preference. The skill already carries the rule — an agent must not invent
configuration on someone's behalf, because a plausible vocab block written from model names looks
like knowledge, is not, and then goes out with every judged question from that point on. A
`suggest` that fills in `means:` would be that failure shipped as a feature, and it would be the
most confident-sounding output the tool produces.

### The loop

```
check      ->  236 findings, 3 checks unconfigured, 15 vocab terms
suggest    ->  6 candidates, each with what was measured about it
             (a person writes the meanings. This step cannot be automated)
check      ->  fewer findings, and the ones left are the real ones
```

### One success criterion, so it cannot become a firehose

**A suggestion is good if accepting it moves a number.** Track proposals against what the next
`check` does. A vocab term that changes no judgment is a term paid for on every call that bought
nothing, and it should be visible as such rather than counted as configuration coverage.

### Not in scope

Do not generate `means:` or `implies:`. Do not propose a waiver without a reason drawn from a real
ruling. Do not suggest a check action from its shipped default alone — the measured agreement is
the input, and where there is none, say there is none.
