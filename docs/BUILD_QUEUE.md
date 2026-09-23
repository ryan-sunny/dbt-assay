# Build queue for 0.51, from FIELD_REPORT section 25

Sections 1 to 24 of `docs/FIELD_REPORT.md` shipped in 0.50.0. This queue covers section 25 only.
Every item below was checked against `src/` on 2026-09-23, and the method is given for each one.
Where the report's diagnosis was wrong, the entry says so. Items marked NEW are not in the report;
they turned up while checking it.

Reproductions ran against `sunny-data/transform/target` with scratch stores. Store counts come from
a copy of `sunny-data/assay.duckdb` in the scratchpad, queried read-only.

## Decisions (Ryan, 2026-09-23)

- Scope: all of it. F1 to F18, then X1, X2, X3 and X4.
- F12 / `read`: a `disagree` below 0.5 is filed `unclear`; the card shows confidence; a second Choice in the same call picks the locator from code-listed candidates, with a NONE option.
- F7 / `--check`: the scope is recorded on the run and kept out of the baseline diff, the loop count and ruled-model counts. A one-time backfill marks existing partial runs.
- F15 / `arbitrary_pick`: the written partition and its line go into evidence as a reading aid outside the id. Ids and rulings do not move.
- X3: paid calibration capped at $0.50 in total. `where_the_fix_belongs` annotates and never becomes a finding.
- X4: the compiled-SQL cache is a table in the store.
- The defaults listed below stand.

## Fixes

Fixes come first, because each one would distort the re-test that follows.

| # | item | report | verified how | what is actually wrong |
|---|---|---|---|---|
| F1 | `_n` shadowed in `claims` and `verify` | 25.19 | read `cli.py:1407`, `:1581` | Both loops bind `_n`. `claims --extract` crashes after `save_claims` has run; `verify` crashes whenever a claim is unanswerable. `--write` is only reached on the non-extract branch. Fix: rename both loop variables, honour `--extract --write`, and make sure the reporting tail after a store write cannot fail the command. |
| F2 | MCP `suggestions` is dead | 25.22 | `mcp_server.py:950` reads `self.state().findings`; `LiveState` has no such field | One line: `live.findings_for(st, None)`. |
| F3 | No test calls each MCP tool | 25.22 | `tests/test_mcp_server.py` and `test_mcp_parity.py` list tools but call none of the semantic ones | Add a smoke test that calls every semantic tool once on the fixture project, with a store, and asserts no exception. |
| F4 | A locked store shows up as twelve broken tools | 25.20 | `state()` at `mcp_server.py:92` opens `Store` with no handling; the tool wrappers pass exceptions straight to the SDK | Catch `StoreLocked` in `state()`. Wrap every tool so an exception comes back as `{"error": <message>}` and never as a bare tool name. Give the MCP server a short default lock wait (see Defaults). |
| F5 | NEW: `check` under-reports `column_has_no_description` | 25.13 | reproduced: `run_all` with schema gives 208, without gives 129; `live.py:127` calls `structural_checks(project, digests)` with no schema | **The report had this backwards.** `onboard`'s 208 is the right number. `check`, MCP and every `all_findings` caller drop `schema`, so derived columns are invisible to them. The `arbitrary_pick` gap (37 vs 33) goes the other way: `onboard` skips `_distinct`. Fix: `all_findings` passes schema, and `onboard` reads from `all_findings`. **Effect on the field store: the next full `check` reports 79 new `column_has_no_description` findings.** Those findings are real; they were being missed. |
| F6 | NEW: `all_findings` gets its arguments swapped at five call sites | none | `cli.py:1801, 3828, 4023, 4727, 5442` pass `(…, entries, store, threshold)` to a signature of `(…, entries, threshold, store)` | `probe.changes(0.8)` raises and the bare `except` swallows it. The result: `key_stopped_holding` and the other key-change findings never reach `plan`, `suggest`, `review --emit` or `read`, and nothing says so. Fix: make `threshold` and `store` keyword-only, and fix the callers. |
| F7 | `check --check <name>` writes a partial run | 25.3, 25.15 | `cli.py:417` filters before `write_findings`; `onboard` teaches the flag at `:929` and `:1037` | The field store still holds the partial run `5c17c6fa0f68` (21 findings). **Needs a decision (Q3).** |
| F8 | Installed-package models counted as unreadable | 25.5 | `Model.is_installed_package` exists and is ignored by `coverage()`, `completeness` (`cli.py:2054`), the page table (`:2209`) and the onboard panel | Leave them out of the unreadable count, and name the packages that were left out. |
| F9 | Counts with no names | 25.11, 25.24d.1 | `completeness` prints `models that are EMPTY` as a count (`cli.py:2067`); `backtest` prints `26 of 85… could not be read` with no models (`:5321`) | Name the empty models. Name the unreadable replays by model, with how many times each was touched. |
| F10 | `inf` in sampled rows is retried as a provider fault | 25.17 | `jev.py:303` builds the JSON inside the retry `try`, so the encoder's `ValueError` is retried 3 times, then reported as "jev failed" | Coerce non-finite floats to null in `jev.Client.ask` (one choke point, so every family is covered) and report the count. If the body still cannot be encoded, fail once and name the encoder. |
| F11 | The plan line ignores the cache, has no ETA, and nothing shows progress | 25.10, 25.2 | `semantics` plans from subjects (`cli.py:5896`); `read` prices `sub.state` from a 25-card sample (`_estimate`, `:2445`), not the state `states.make` sends; spinners with no count in 12 places | `decide` looks cache hits up by `(key, question, prompt_version, state_hash)`, so the calls still to make can be counted up front. Add `jev.plan()` returning to-ask, cached, dollars priced from the real states, and seconds from `model_calls` history for this caller. Add one shared progress line (`n/N · cached · $ · ETA`). Every judged command uses both. |
| F12 | `read` shows the option text, has no confidence, and suggests dismissals it is unsure of | 25.1 | `reads.py:why()` returns the option's own criterion; the card renders `verdict — why` (`reviewform.py:854`) | Correction to the report: the form does **not** pre-select the radio (`:890` checks only the person's own answer). The reading is shown as a suggestion. **Needs a decision (Q2).** |
| F13 | The vocab law lint matches state names | 25.4 | `lint.py:677`: `_STATES` substring match plus a statute regex | It flags `geography` ("e.g. a Colorado or Arizona metro") and misses `case_number` ("a water court case… assigned per division"). See Defaults. |
| F14 | `banks` asks for a `forked_from` that is already there | 25.14 | `lint.py:618` never reads `forked` | When `forked_from` is declared and current, keep the copy count and drop the request. |
| F15 | `arbitrary_pick` evidence names the resolved alias, not the column as written | 25.9 | `parse.py:589` maps partition columns through `alias_of`; the evidence prints the mapped name | Evidence is part of the finding id (`_identity`), and 37 rulings sit on these findings (13 human, 24 agent). **Needs a decision (Q4).** |
| F16 | Drift in the `calibrate` comment in `audit.yml` goes unseen | 25.7 | `selfaudit.py:31-37` only parses "N of M agreed"-style sentences | Also parse `calibrate` figures ("N exact of M", "N flagged uncertain", "N disagreeing") and compare them with a free `calibrate` replay. |
| F17 | Two CLI rough edges | 25.8 | no `--version` callback; `disagreements` has no `-t` | Add `assay --version`. Have `disagreements` accept `-t/--target`; it ignores the value and says so. |
| F18 | Docs | 25.6, 25.16, 25.18 | none; these record measurements | Document that `read` goes after the last judged command. Add the VERIFICATION.md rows: `column_role` 173/187 with 5 of the misses being wrong labels; `traverse` independently reproducing the `wdid` fan-out; `finding_is_correct` putting `code_contradicts_a_claim` at 15/82, which agrees with `effectiveness`' 4/12. |

## Features, in order of evidence

| # | item | report | verified how | what building it means |
|---|---|---|---|---|
| X1 | Exposures | 25.23d, 25.24c | nothing reads `manifest["exposures"]`; the field manifest has **1 exposure** and **48 root models no model reads** | Load exposures and map each model to the exposures downstream of it. Carry that into `contract`, `blast_radius` and inventory. Add a `findings.exposures` column and rank on it above `marts`, which changes `check`, `plan`, the review form and the page together. Add an `audit.yml` policy `when_exposed: true` for gating. Add `exposure_undeclared`: candidates proposed from evidence (models no model reads), never the name, owner or URL. Keep exposures out of judged state. On this project the bootstrap does most of the work, since there is 1 exposure today. |
| X2 | Collapsing one defect written in many places | 25.21, 25.23b | **Correction: the evidence does not carry a macro `file:line`.** The nine permit findings carry the compiled `CASE` expression; 7 of 9 are identical and 2 (Maricopa, Denver) are written inline | Group findings by (check, expression with the column name masked). Attribute a group to a macro when every member's `depends_on.macros` includes that macro and its raw code calls it. `plan`, the review form and the page show one row with its call sites. No verdict lands on a group: a ruling still goes on one finding. |
| X3 | The cluster subject and four families | 25.23a,c, 25.24a,b | `same_defect` and `same_name_measure` exist as precedents; `predicate_cluster`, `cluster_member` and `claim_pair` builders do not | Structural grouping for free first: shared normalized predicate, shared macro, shared source. Then send Jev only the pairs code cannot settle, and take connected components. Families are YAML (`one_rule_or_a_coincidence`, `the_odd_one_out`, `where_the_fix_belongs`, `claims_are_the_same_assertion`). The discriminator for `where_the_fix_belongs` (layer, provenance, count of distinct sources) is computed in code and put into state. Each family needs `banks --strict`, OVERVIEW and VERIFICATION rows, a caller, and a paid calibration on the scratch store copy. |
| X4 | History | 25.24d | `backtest` strips jinja per commit | Add a `commits` table (sha, date, message, files, models touched) joined to runs, giving finding age and first appearance. Cache compiled SQL keyed on the manifest checksum so `backtest` replays exactly. Later: co-change as a clustering signal, and whether a `version-check` bump landed in the same commit as the change. Skip full per-commit `--compile`. |

Deferred and not proposed: snapshot checks (this project has 0 snapshots), seed drift, and
`unevaluable_tests` as a check.

## Defaults I will use unless you say otherwise

- **F4 lock wait:** the MCP server defaults `ASSAY_LOCK_TIMEOUT` to 5 seconds when unset. That
  covers a CLI command briefly holding the store. A 40-minute sweep still gets the named-PID
  message straight away after 5 seconds.
- **F13 vocab lint (free, no judgment):** a place name inside an example clause (`e.g.`, `such as`,
  `for example`) no longer counts. Legal-institution words in `means` or `implies` do count: court,
  decree, statute, ordinance, regulation, division, adjudicat*, water right, and case numbers.
- **F8:** installed packages are excluded from the unreadable count, and the line says
  `30 model(s) from installed packages (elementary) not counted`.
- **X1:** ranking is `exposures`, then `marts`, then `descendants`. `exposure_undeclared` is a
  coverage finding with base 1, the same shape as `column_has_no_description`.
- **UI:** each change that shows up on a surface lands on the page, the review form and MCP in the
  same commit, with a Playwright screenshot read before the item is called done.

---

# Previous queue (0.50.0), SHIPPED. Kept for its evidence.

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
