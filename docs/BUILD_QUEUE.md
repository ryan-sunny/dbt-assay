# Build queue — SHIPPED. Do not build from this file.

Everything below landed on one branch (see `docs/FIELD_NOTES.md`, "The build queue, P0 through P5c,
and what verifying it changed"). This file is kept for the evidence and measurements, not as work.

**Verified shipped**, by reading the source on 2026-09-23: `check --select` (and `--new-only`),
`assay read`, `VERDICTS = ("agree","disagree","unclear","accept")`, `contract()` returning `health`
with open findings / waived / grain measurement, explanations `{applies_to:, options:}` named sets,
`assay import`, the blind-compile detector, warehouse-unreachable commands stopping instead of
reporting what they did not see, and the `backtest` symlink fix (`abspath` + `lexists`).

**Still open, verified by grep on 2026-09-23:**

1. **`audit.yml` has no connection settings.** `project_dir`, `dbt_bin`, `profiles_dir`, `target`
   and `store` are all absent from `config.py`, so every warehouse-touching call repeats them on
   the command line. `d61e72c` makes the omission fail loudly rather than silently, which removes
   the danger but not the boilerplate. A `project:` block would remove both.

2. **`run_sql` still takes one statement.** Signature is `run_sql(sql: str, ...)`. Callers:
   `probe.py` 6, `practices.py` 5, `cli.py` 1, `rows.py` 1. Note the queue's P0.4 premise was
   partly wrong: FIELD_NOTES records that bank parsing was 13 of `check`'s 22 seconds, so the ten
   minutes measured on the box was mostly bank parsing rather than dbt startup. The value of
   batching the remaining callers is lower than P0.4 claimed and should be re-measured before
   anybody spends time on it.

**Correction to §15/§21 triage**, from the manifest rather than assumption: 4 of the 12 flagged
water `accepted_values` columns already carry a sibling `not_null` that catches the CASE
fall-through (`int_az_section_boundaries.subflow_weight` and `.ama_status`,
`int_water_irrigation_trend.irrigation_trend`, `int_water_diversion_history.representative_af_basis`),
as does `stg_mesa_permits.permit_class`, which the queue used as its worked example. The other eight
are unprotected but measured zero nulls in production, so nothing is falling through today.

**How this document was wrong, so the next reader does not repeat it.** Six of its proposals were
for features that already shipped, because absence from `--help` was read as absence from the tool.
`--help` lists flags. The source answers capability questions in one grep and is local. Anything
claimed missing here should be re-checked against `src/` before work starts on it.

---

# Build queue, from a day of using 0.47.2 through 0.49.1 on a real warehouse

For a session that has not been in this conversation. Everything here is actionable and deduped.
`docs/FIELD_REPORT.md` has the long form with evidence; section numbers below point into it.

## How this was produced

`sunny_data`: 356 models, 210 sources, a 33GB DuckDB warehouse on a Contabo box under Dagster.
Run through `uvx --refresh --from dbt-assay==<version>` inside the container. Store at
`transform/.assay-run.duckdb`. Current state: 477 findings, 18,079 judged answers, $1.32 total spend,
**63 findings agreed by a person and 0 fixed**.

Two cautions from the report that cost real time:

- **`uvx --refresh`, always.** Without it uvx serves a cached environment while reporting the version you
  asked for. This produced a blank 12MB page from a pin whose published wheel was correct (7.4).
- **A warehouse-touching command without `--project-dir` and `--dbt` is blind, not clean.** `probe`,
  `volume`, `feeds`, `practices`, `adjudicate`, `completeness`, `patch` and `tests --count-defaults` all
  need both (22.2).

## Do not build these. They already ship

Six proposals in the field report were for features that exist. Section 21 has the detail.

| looked missing | actually is |
|---|---|
| measure the COALESCE default share | `assay tests --count-defaults` |
| cost preview on judged commands | `assay ask --dry-run`, plus `jev.max_spend_usd` as a hard cap |
| per-run cost split | `assay cost --since` |
| custom question banks, overriding shipped families | `assay_questions/`, `ASSAY_QUESTIONS`, name collision replaces |
| validate a question before running it | `assay regress --family` |
| write the agent skill and MCP config | `assay onboard --agent` |

Also already fixed in 0.49.x and not to be redone: chain node clicks, "severity decides" wording, document
overflow on page and form, the form header position, page/form cross-linking, `--sample` semantics,
`exec_ms` split from `wall_ms`, probe batching 271 to 23 statements, probe column hallucination, the stale
monitor-observation guard, and all user-facing water-specific example text.

---

## P0 — changes what the product is

### 1. `assay check --select <model>`, and a hook recipe

`check` takes `--check` but no `--select`/`--model`, so it always runs the whole project: ten minutes here.
That single missing flag is why assay cannot be enforced. With it, the enforcement is four lines:

```
PostToolUse / Edit|Write on models/**.sql
  -> assay check --select <edited model>
  -> non-zero, finding text as the reason, the edit does not stand
```

Evidence it matters: sunny-data has exactly one hook, blocking hand-rendered reports, and it works whether or
not an agent read CLAUDE.md. Every piece of assay's enforcement is skill prose asking an agent to remember,
and this session measured what that is worth: the review skill's "drain the queue before you emit" was
skipped, and the user caught it. §18, §22.3

Ship the hook from `assay onboard --agent` rather than describing it.

### 2. `assay read --out reads.json`

`assay review --reads` consumes `{'<subject>::<check>': {verdict, why}}` and **nothing in the codebase
writes one** — `reads_path` is only ever read. The skill says "an agent writes that file once, offline",
which means a conversation, one finding at a time.

Measured price from this project's own ledger: `practices` judged 171 findings for $0.0057, so $0.000033
each. The 212 cold cards on the current form cost **$0.007**.

So the most expensive human-facing step in the loop is the only one with no judged path. Output is a file a
person reviews, never a store write, which preserves agent-is-not-authority. §19.2

### 3. A fourth verdict: `accept`

`store.py:762` allows `agree | disagree | unclear`. There is no way to record "the finding is correct and I
accept it", which is what a waiver means. So a correct-but-accepted finding must be recorded as `agree` (and
sit in "63 agreed, 0 fixed" forever) or `disagree` (a lie that permanently deletes a true finding).

The damage is not cosmetic: `disagree` feeds the agreement rate, and the agreement rate is the only thing
deciding whether a check may ever gate. `code_contradicts_a_claim` reads 33% here and
`identifier_outside_grain` 44%. Any accepted-but-recorded-as-disagree finding is telling a working check it
was wrong.

`accept` should: not count against the agreement rate, suppress the finding, write a waiver proposal with
reason and expiry, and be excluded from "agreed and still here". §24.1

Then fix the waivers pane, which currently proposes waivers from `disagree` rulings — the opposite claim.
Candidates should come from `accept`. §24.2

### 4. Move batching from `probe.py` into `run_sql`

`run_sql`/`run_via_dbt` are the only two places assay touches a warehouse. The 0.49.0 batching landed inside
`probe.py`, so `probe` went 271 invocations to 23 and 88 minutes to 10, and every other caller got nothing:
`cli.py` 5 call sites, `practices.py` 5, `rows.py` 2.

Measured on `feeds`: 3m53s for 5 sources, 47s each, of which the warehouse sees 12–96ms. All 210 sources is
~2.7 hours, essentially all dbt startup. Batched at the chokepoint it is ~12 minutes.

Bound the batch width, isolate per-statement failures, and label rows back to their relation — all three are
already implemented in `probe.py`. On BigQuery batching buys wall clock and nothing on the bill, since a
batched statement still scans every column. §13, §17

---

## P1 — correctness and honesty

### 5. `contract(model)` should carry the model's health

Returns shape only: grain, columns, reads, descendants, marts, description. No findings, no waivers in force,
no human verdicts, no measurements. It is the tool CLAUDE.md tells every agent to call before editing, and it
is an anatomy chart with no chart notes. Add findings (with `ruled_by`), waivers in force, and the settled
grain's measurement from `observed_keys`. All three are already in the store. §20.1

### 6. Validate the skill's example commands in CI

`skilltext.py` contains seven example invocations of warehouse-touching commands and **none** carries
`--project-dir` or `--dbt`. Both flags appear zero times in 42,001 characters. This produced a blind
`practices` run whose "23 of 23 standard check(s) were NOT LOOKED AT" was read as a finding about the project.

Parse every `assay <cmd> <flags>` out of `skilltext.py`, assert each flag exists and that warehouse-touching
commands carry a connection. `guide.py` already proves the technique: it reads lint rules, families and
config keys from source rather than restating them. §22.2, §24.4

### 7. `config_comment_contradicts_the_store`

`audit.yml` here says "38 of 38 agreed" against a store holding 90, and "nothing gates until a question
clears min_adjudications, which none do" when one now does. That is
`description_contradicts_the_code` occurring in assay's own config, on a tool that ships that check.

A comment asserting a count is checkable against the store exactly as a `schema.yml` description is checkable
against SQL. §18.2

### 8. Finish two small ones

- `explorer.py:2143` renders `114` then `6 read` as `114 6 read`. Line 1112 already uses `·` correctly
  in the check dropdown. §1.4
- **70 of 136 human rulings are stamped `(unversioned)`.** One keypress writes a model-scoped row and a
  finding-scoped row 1.5ms apart; only the model-scoped one gets a version, so every finding-level ruling is
  excluded from before-and-after comparison forever. Separately, `assay.0.20.0` is stamped on 5 rulings and
  no row in `runs` can produce it. §1.2, §1.3

---

## P2 — the config surfaces

### 9. Give `waivers` and `explanations` the selector `vocab` already has

`vocab` takes a validated `applies_to` (string or `{select:, exclude:}`) that refuses syntax it does not
understand rather than matching everything. `waivers` is an exact-name dict; `explanations` is
`data.get("explanations") or {}` with no validation at all.

The cost is visible in this project's config: two of four waivers say in prose that they are the same shape
as each other, and the form renders 40 near-identical explanation cards. The implementation exists and is
tested. §23.2, §23.3

### 10. Draft, never write, the three hand-authored surfaces

Vocab `means:`/`implies:`, waiver reasons, and column descriptions (129 `column_has_no_description` here) are
all hand-authored restatements of evidence assay already holds. Draft each as a **proposal a person edits**,
keyed to the evidence it came from.

The rule to follow is the one `claims` already enforces: "Extraction is SELECTION, never generation: code
splits the prose, and a judgment says what job each sentence is doing. The model never writes a claim." The
measurement behind it: a compound claim judged whole split 0.51/0.47 and flipped between runs; atomised, the
sharpest read `contradicts` at 0.82. §19.3, §23.4, §24.3

---

## P3 — new question families

### 11. `default_is_a_measurement_or_an_absence`

`tests --count-defaults` already measures the share. Nothing decides what the default *means*.
`COALESCE(wells_drilled, 0)` defaults to a real count; `COALESCE(dwr_analysis_status, 'not looked up')`
defaults to a marker meaning nobody checked. Both raise the same finding today.

The calibration set exists: 13 defaulted columns here, 10 at least half default, measured. §21.2

### 12. `what_would_break_silently` — and a redesigned `patch`

`patch` writes only uniqueness tests it can prove will pass, which by construction are the tests that are
already true. The better question is **which columns are worth testing and why**, and every input is already
in the store: `column_role` (5,252 answers), `in_key`, provenance, which columns carry joins, which feed
aggregates downstream, whether the expression is a CASE/regex/coalesce/window, and reach.

Output should be a ranked list with reasoning, recorded and versioned like any other answer, so an agent
writes a real test against a real risk. Live example here: `stg_mesa_permits.permit_class` comes out of a
CASE with no ELSE, feeds 15 marts, and carries an `accepted_values` test that cannot fail. Assay says the
test is vacuous; nothing says the column deserves a real one.

### 13. A monitoring bank

Monitoring has one judged family, `volume_contradicts_a_claim`, which narrows to nothing here. Four more,
each with live cases in this project's `volume.json`:

- `movement_is_expected_for_this_kind_of_table` — `mesa_code` +2061%, `tempe_code` +23%
- `monitor_covers_what_matters` — 233 models with a mart downstream and no row-count history
- `stale_monitor_still_matters` — 63 monitors last failed and have not run since, oldest 73 days
- `test_never_ran_is_a_gap_or_a_leftover` — 459 declared tests have never produced a result

All judge the monitoring, never the data, preserving the line the tier already draws. §20.3

### 14. Five more with no family at all

`filter_is_complete` (the nontributary exclusion is a hand-written list of ten aquifer names),
`units_agree_across_models`, `time_grain`, `tie_break_is_total` (the structural `arbitrary_pick` has no
judged counterpart), `sentinel_is_not_a_value` (-9999, future-dated April 1). §19.4

---

## P4 — MCP parity

21 tools, 19 of which read. 37 of 47 commands have no tool: `check`, `page`, `review`, `probe`, `volume`,
`feeds`, `columns`, `semantics`, `align`, `tests`, `ask`, `banks`, `config`, `onboard`, `scan`, `cost`,
`effectiveness`, `regress` and more.

So MCP is not a second route to the CLI, it is a strict subset, and the skill exists to cover the gap with
bash. An agent without shell access cannot run assay at all.

Highest value five, being the ones an agent needs mid-task and cannot get: `check` (scoped, per P0 item 1),
`ask`, `banks`, `config`, `cost`. §22.1

---

## P5 — presentation, still open

Verified fixed in 0.49.x and listed above. Still open:

- **Overview needs a rework.** It leads with a findings-by-check inventory. It should lead with what nothing
  else could have done: 47,491 claims read, 5,794 sentences classified, 18,079 answers across 356 models for
  $1.32, finding 477 defects no dbt test can express. §4.1
- **Answers tab** shows a 10-column table of 5,794 rows without the question text on screen. Group by
  question, show the question. §4.4
- **Undifferentiated repetition**: 44 vocabulary cards, 21 per-check policy cards, all rendering identically
  with the same empty YAML block. Group by the reason rather than stacking. §3.2
- **Form tab order**: words, explanations, waivers, monitoring, settings, findings. Settings should be last.
- **A waiver should be settable from the finding card**, where the evidence is, not only from a separate
  pane. Depends on P0 item 3. §24.2

---

## The number that matters

`assay check` prints one line no release can move:

```
of the 63 finding(s) a person agreed with, 0 are gone and 63 are still here.
```

Everything in this queue raises the denominator or makes the finding easier to act on. Only somebody reading
SQL and changing it moves the numerator. Items 1, 2 and 3 are the three that most directly reduce the cost of
moving it.

---

## P5b — `backtest --compile` crashes on a relative `--repo`

Found while running the full-history backtest. Two lines, and it blocks the only external validation assay
has.

```
FileExistsError: [Errno 17] File exists: './transform/dbt_packages' ->
'/var/folders/.../assay-replay-nxuvxo6n/transform/dbt_packages'
```

`backtest.py:213`:

```python
def _link_packages(self) -> None:
    src = os.path.join(self.repo, self.project_subdir, "dbt_packages")
    dst = os.path.join(self.dir, self.project_subdir, "dbt_packages")
    if os.path.isdir(src) and not os.path.exists(dst):
        os.symlink(src, dst)
```

Invoked as `--repo .` from inside the repo, which is the natural way to run it, `src` is relative. The
symlink written into the temp worktree therefore carries a **relative target**, resolved from the symlink's
own directory to `<tmp>/transform/./transform/dbt_packages`, which does not exist. The link is broken.

`os.path.exists()` follows symlinks, so on a broken one it returns `False`. The guard concludes the
destination is absent, `os.symlink` disagrees, and the run dies on the second commit.

Fix, either or both:

```python
src = os.path.join(os.path.abspath(self.repo), self.project_subdir, "dbt_packages")
...
if os.path.isdir(src) and not os.path.lexists(dst):
```

Confirmed: the identical invocation with an absolute `--repo` proceeds normally and prints "compiling where
the strip fails, in a detached worktree. Your working tree is untouched."

Worth fixing because `backtest` is the only number in assay that assay does not grade. Every other figure is
self-reported findings or human agreement, and on this project the "human" verdicts were produced by an
agent (§18.1). Backtest's ground truth is external: somebody hit a defect, somebody fixed it, and the
question is whether the check fired before and went quiet after.

Result over commits since 2026-08-01, with `--compile` and an absolute `--repo`:

```
caught        2   fired before, quiet after: the check works
still_firing  3   fires at both: the commit did not address what assay sees
silent       80   assay saw nothing either side
no_pair      44   the model was added or removed here

26 replay(s) were recovered by a real compile.
a check was firing in 5 replay(s); a later commit silenced it in 40% of them.
```

The same run **without** `--compile` reported 26 of 85 comparable replays unreadable (31%), correctly not
counted as clean. With it, the `unparseable` row disappears: all 26 were recovered and all 26 were silent.
So the compile did not move the catch rate, it removed the caveat. 40% over 100% of the corpus, nothing
unread.

Both catches were `ranks_by_degrees`, on `int_water_isf_reach` and `int_water_reach_streamflow`, at a commit
whose message never says "fix" — which is why `--fix-like-only` is off by default and why turning it on
hid both.

The 31% unparseable is what `--compile` exists to close, and what this bug prevents.

### One related note for the skill

`--compile` failed **loudly** here, with a traceback. That is correct. Contrast `practices` run without
`--project-dir`/`--dbt`, which failed **quietly** and reported "23 of 23 standard check(s) were NOT LOOKED
AT" in a shape indistinguishable from a finding about the project (§22.2). Warehouse-touching commands
should fail the way `--compile` does.

---

## P5c — CI: the structural tier is CI-safe in theory and not in practice

Two blockers, both concrete. Neither is hard, and neither is currently solved.

### Blocker 1: a compile without a warehouse produces wrong SQL, silently

The structural tier reads `manifest.json` and compiled SQL, so it looks CI-safe. It is not, for any project
using introspective macros.

`sunny-data/transform/macros/dlt_col.sql`:

```jinja
{%- if execute -%}
    {%- for c in adapter.get_columns_in_relation(source_relation) -%}
```

Guarded on `execute` because adapter calls are unavailable during dbt's parse pass. 115 files in that project
reference introspective macros.

Without a warehouse connection the guard returns an **empty column list**, and the model compiles to SQL that
is syntactically valid and semantically wrong. assay then reads and judges that SQL, and nothing anywhere
says it was built blind.

That is worse than failing. `assay onboard` already reports "no compiled SQL" as a degradation; it cannot
detect "compiled SQL built without a connection", which looks identical to the real thing.

Needed: a way to know the compile was connected. dbt records enough to tell (the manifest's adapter
metadata, or a probe of whether any introspective macro resolved to empty). Report it the way `onboard`
reports the others: before any finding, not after.

### Blocker 2: verdicts do not survive a fresh checkout, so CI can never gate

`min_adjudications` gates on **human** verdicts, which live in the store. The store is a 70MB DuckDB file
that is not in git.

`assay export <dir>` writes the tables as CSV seeds, and on this project they are committed:

```
transform/seeds/assay/
  assay_adjudications.csv     145 KB    <- the verdicts
  assay_model_decisions.csv   8.8 MB
  assay_findings.csv          5.0 MB
  assay_edge_facts.csv        5.1 MB
  assay_observed_keys.csv      41 KB
  assay_runs.csv              3.6 KB
```

**There is no import.** Nothing in the codebase reads those back into a store; `export` is one-way. So a CI
checkout has every verdict sitting in git as CSV and no way to load them, starts from an empty store, clears
no floor, and can never gate anything.

`review --from-labels` partially helps: it synthesises verdicts from assertions the project already makes
(a `unique` test says a column is an identifier, a declared key says what one row is, a join says two columns
hold the same concept). But those record as `source = 'label'`, and only `human` gates.

Needed: `assay import <dir>`, the inverse of `export`. The pattern already exists elsewhere in the tool --
`assay page --data` writes committable JSONL and `--from` re-renders from it, explicitly so a diff reads as
"these 3 models changed". The store wants the same round-trip.

### What CI looks like once both are fixed

```yaml
- run: assay import transform/seeds/assay --store ci.duckdb   # verdicts from git
- run: assay check --store ci.duckdb --target target/          # structural only, no warehouse
```

with `target/` either committed from a connected box run or rebuilt in CI against a warehouse.

Until then the honest options are:

1. **Run assay on the box**, inside the existing pipeline, where the warehouse and the store both live. This
   works today and needs nothing built.
2. **CI as advisory only** -- run `check` on a fresh store, report findings, gate nothing. Correct, and it
   is what `min_adjudications` will enforce anyway.

Option 1 is the answer for now, and it pairs with the P0 hook: the hook catches an edit as it is written, the
box run catches what reaches the warehouse. GitHub CI is the wrong place for either until `import` exists.
