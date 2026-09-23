# assay

<img src="docs/cuts/condensers.gif" align="right" width="260" alt="two condensing trains rising off a still, with the cooling barrel beside them">

**Recover the semantics your warehouse never wrote down.**

Your dbt project has types nobody declared. Every model has a grain, every number has a unit, every
nullable column has a meaning for null, and every model assumes something about what its parents
already did. None of it is written anywhere, all of it is load-bearing, and the only copy lives in
the head of whoever last debugged it.

`assay` infers those semantics from the code, stores them as data, and finds the places where they
contradict each other.

[sqlglot](https://github.com/tobymao/sqlglot) reads the structure. [TypeSafe's
Jev](https://docs.typesafe.ai) reads the meaning. SQL does the rest.

> Built while auditing a Colorado water rights warehouse, where a case number is only unique inside
> a water division and nothing in the stack could tell me that.

**New here? [docs/PRODUCT.md](docs/PRODUCT.md) is the whole thing in plain words** — what it checks, how it works next to a coding agent, what it costs, and what it deliberately will not do.

<img src="docs/cuts/atwork.gif" align="right" width="150" alt="">

## What it finds

A staging model in that warehouse described itself like this:

> *"Boulder commercial building permits (residential filtered out)."*

The filter excludes exactly two substrings, `%single family%` and `%dwelling%`. Of the 14,150 rows
that survive it, 373 are non-residential, **157 are explicitly `building permit - multifamily`**,
and 13,620 are trade permits with no commercial distinction at all — so residential roofing jobs
were going out as commercial leads through 19 downstream models.

Valid SQL. Passing tests. A description that is simply false. **No linter reaches that**, and the
judgment that did cost $0.0026 across the whole 265-model project.

The same run found a `join irr i on p.xmin <= i.xmax and p.xmax >= i.xmin` — a bounding-box
*overlap* join feeding a mart, where one parcel matches many polygons and every count past it is
inflated while each individual row stays valid.

```bash
uvx dbt-assay onboard --target path/to/dbt/target
```

On a project `assay` has never seen: it reads your manifest, says what it can and cannot see, runs
the structural checks, runs the judgment tier if a key is present, writes an `audit.yml` that gates
nothing, and prints the next command. `--agent` also writes the skill file your coding agent
follows.

<img src="docs/cuts/apparatus.gif" align="right" width="130" alt="">

## How it works

<img src="docs/how-jev-fits.svg" alt="assay: a parser settles what it can, Jev judges the rest, you rule on it, and it lands in your warehouse as relations">

**The structural tier is what makes the judged tier safe.** Blast radius comes off the DAG, so a
judged finding can be *ranked* without trusting the judgment. `assay patch` refuses a proposed test
by *counting* it rather than by asking. Everywhere this has been right, code did the deciding and
judgment did the noticing.

**So the parser and the judgment are not two products.** They are one division of labor, and it is
the whole design: *if a parser can answer it, Jev is never asked.* A grain, a column's provenance,
a test that cannot fail — those are facts, settled exactly and for free, and putting them to a
model would be spending money to make a certainty approximate.

What is left over is not a gap in the parser. It is a different kind of question.

```sql
where status != 'CANCELED'
```

That is domain logic, a patch over a bad feed, or the thing that makes the model mean what it
means. **The SQL is identical for all three**, no parser will ever separate them, and which one it
is decides whether the line gets deleted next quarter or guarded forever.

That is the question `assay` exists to answer, and it is why Jev is not an add-on.

## What it remembers, and what happens when the code moves

A linter re-derives its opinion from scratch on every run, so nothing you ever concluded survives
the next one. `assay` is built the other way round: what you and your project already know goes
into a store, and every later run is checked against it.

Six kinds of thing accrue, and five of them cost something to produce:

| what | where | on a real 358-model warehouse |
|---|---|---|
| **your verdicts** — a finding you read and called real, wrong, unclear, or right-and-staying (`accept`) | `adjudications` | 136 human, 105 from an agent, 82 derived from assertions already in the project |
| **your vocabulary** — the words this project uses for its own concepts, and *where each one is true* | `audit.yml` | 16 terms; 6 asserted one state's law to a project a quarter of which is in another |
| **your own questions** — families you wrote, in YAML, no code change | `assay_questions/*.yml` | asked, stored, and (since 0.38.0) able to produce findings like any shipped one |
| **what the project claims about itself**, as data rather than prose | `claims` | 5,794 atomic claims, each with an id that survives a paragraph being reflowed |
| **what was actually counted**, as a series rather than a snapshot | `observed_keys` | 224 observations over 48 relations — a key that held last week and does not now |
| **every answer ever given, and what it was computed from** | `model_decisions`, `states` | 19,707 answers over 7,797 subjects, $1.32 all-in |

**A verdict is about a version of a question, not about the question forever.** `prompt_version` is
in the primary key, so rewording a question after people disagreed with it does not silently
inherit their verdicts — and whether the rewrite worked becomes a measurement instead of a guess.

**A dismissal sticks across runs and lapses by itself.** A finding's identity is a hash of its
check, subject, summary and non-measured evidence; probabilities and counts are excluded on
purpose. A number moving by 0.01 does not mint a new finding, and a model edited into a genuinely
different defect no longer carries your old dismissal.

**And when the SQL moves, it says so.** dbt already records a sha256 of every model's source file,
and `assay` stores that hash on the answer:

```bash
assay stale            # judged answers about SQL that has since changed
assay stale --cost     # ...and what re-asking them would cost, before you spend it
```

No API call, no warehouse connection, one dict lookup per answer. It is necessary and not
sufficient and says so — a comment edit trips it, a change to a *parent* does not — and a stale
answer is still served everywhere it was served before, because hiding it leaves you with nothing,
which is strictly worse than serving it dated. An answer `assay` cannot check at all reports as
**cannot be checked**, in its own column, never folded into the ones that are current.

<img src="docs/cuts/tubs.gif" align="right" width="150" alt="">

## Volume, read from Elementary rather than rebuilt

```bash
assay volume                          # what Elementary counted, joined to what you claim
assay volume --judge                  # ...and whether a movement contradicts a claim
assay volume --json > volume.json     # the same numbers as a file
```

Elementary detects with no semantics — *row count fell 41%*. assay has the declared grain, the
claims the project makes in its own prose, and the blast radius off the DAG, and has never tracked
a row count. `volume_contradicts_a_claim` is the join, and it is the only question neither tool can
answer alone. The counting is taken as fact, never re-litigated, and Elementary's results are never
ingested as assay findings — that would corrupt the one number measuring the loop.

Six absence states are told apart and **none of them reads as "fine"**, including the three a spec
did not predict: a monitoring table nothing has written to for months, a test whose last result was
a failure and which has not run since, and a warehouse assay could not reach at all.

Taking the measurement needs your dbt connection, so `--json` writes it once and everything else
reads the file: `assay page --monitoring volume.json` renders it as the report's Monitoring tab,
`assay review --emit --monitoring volume.json` puts the derived staleness threshold in the form
beside the cadence it came from, and the `monitoring()` MCP tool hands an agent the same summary.
**assay still holds no credential anywhere in that chain.**

## Read next

- **[Full overview](docs/OVERVIEW.md)** — every command, every question, every config block, and
  how to run this as a standing part of a warehouse rather than a one-off audit.
- **[The store's schema](docs/SCHEMA.md)** — an ER diagram of the ten tables, what each one answers, and the string grammar the joins are made of. Also the split that decides what `assay prune` may delete: a table carrying `run_id` is one `check` rebuilds for free, and one without it holds something that cost a model call or somebody's afternoon.
- **[What has actually been verified](docs/VERIFICATION.md)** — per family, whether a person has
  read its findings against the real thing. Two families failed their own controls and were
  rewritten; it names the ones nobody has checked.
- **[Field notes](docs/FIELD_NOTES.md)** — someone else using it on a 357-model warehouse, kept as
  reported. Most entries found a defect in the checker rather than in the warehouse.

## Status

Both tiers work. **Run it with a key.** The structural tier needs nothing but your manifest and is
genuinely useful — it found 50 tests that cannot fail in a 357-model warehouse — but it is a very
good linter, and a linter is not the point. The point is a warehouse that knows what it means, and
meaning is the half a parser cannot reach.

Ten of seventeen question families have had their findings read against real data by a person; two
failed that and were rewritten; [docs/VERIFICATION.md](docs/VERIFICATION.md) says which, and which
five have not been checked at all. Nothing gates a build in either tier until a question has recorded your
verdicts, and `assay` refuses rather than warns.

## The inventory

```bash
assay inventory                     # every model: grain, columns, reach
assay inventory --model water_rights  # one model, in full
assay inventory --write contracts.yml # a SEPARATE file; your schema.yml is never touched
```

Not a findings list. *Here is what every model in your project actually is.* On a 356-model
warehouse: grain settled for 250, 4,684 columns classified.

**Every cell says where it came from.** `declared` (a human wrote it), `observed` (the probe counted
it), `derived` (code worked it out) or `judged` (with the probability). A fact resting on an
unresolved premise says so rather than inheriting confidence it did not earn.

<img src="docs/cuts/tower.gif" align="right" width="140" alt="">

## The inventory page

```bash
assay inventory --html docs/warehouse.html
```

One self-contained file: every model, what one row is, what each column does, where each value came
from, and **who said so**. Color-coded, searchable, no build step, opens from a `file://` URL.
Commit it and a change in what your warehouse MEANS shows up as a diff.

dbt docs shows you lineage. This shows you meaning.

## The report

```bash
assay page assay.html -t target/                            # everything assay knows
assay page assay.html -t target/ --form review.html         # the two link to each other
assay page assay.html -t target/ --monitoring volume.json   # ...and what is watching it
```

Everything assay knows, as one file you double-click. Ten tabs: the Overview, every model, every
hop, every claim, every finding, what to configure, every answer ever given, what each call cost,
every question in full, the resolved config — and **Monitoring**, which is the one that asks
whether anybody would notice if what this SQL produces changed tonight:

- how often this project actually builds, which every threshold on the tab is derived from rather
  than picked;
- each monitor's own freshness, because a monitor that stopped reads exactly like one that finds
  nothing;
- what the declared tests are doing. On the warehouse this was built against: 1,291 declared,
  1,098 that have ever produced a result, and 1,846 results that are `skipped` rather than passed;
- tests whose last result was a **failure** and which have not run since — neither a live failure
  nor a pass, and indistinguishable from a live failure in any view that sorts by status;
- the models with a mart downstream and no row-count history at all.

Without `--monitoring` that tab says the measurement has not been taken and prints the command that
takes it. **It never renders zeros**, because a zero there reads as *nothing is wrong* and means
*nobody looked*.

It writes a data artifact beside the page — one JSONL line per entity — and that is the thing worth
committing: a diff reads as *these 3 models changed*, and `assay page --from assay-data/` renders
any past commit's artifact as the page it was, with no warehouse, no store and no manifest.

No network, no build step, deterministic: it carries the manifest's own `generated_at` and never a
wall clock, so a rerun that changes nothing writes an identical file.

## On the pull request

```yaml
- uses: ryan-sunny/dbt-assay@v0.49.1
  with:
    target: target-head
    baseline: base/target
    store: assay.duckdb      # optional: commit or cache it and judged findings post too
```

Posts what changed about what your models mean, who consumes it, and how many of those aggregate
over it. It does **not** gate by default: nothing should fail a build until its question has
recorded verdicts, and assay refuses to anyway.

There is no `dialect:` line because the manifest names its own adapter. Pass one only to override.
An earlier version of this action defaulted it to `duckdb`, which silently misparsed every other
warehouse: on a BigQuery project that turned 12 parse failures into 128 and lost two real findings,
without changing how confident the output looked.

<img src="docs/cuts/assayer.gif" align="right" width="180" alt="">

## Ruling on findings, one keypress each

```bash
assay review -i
```

```
column_role  zip_tiers
  role = measure  confidence 1.00
  models/marts/zip_tiers.sql · 0 downstream, 0 marts
  monthly_price = CAST(GREATEST(5, ROUND(12 * a.leads_per_week * pr.price_multiplier))
  comes from: computed — derived here by an expression
```

`a` agree, `d` disagree, `u` unclear, `s` skip. Least certain first, because a verdict on an answer
already given at 0.99 teaches almost nothing and one on a 0.45 is where the question is actually
being decided.

The evidence is on screen because a verdict nobody can reach in five seconds does not get given.

A finding has a fourth answer, and it is the one a correct-but-intended finding needs:

```bash
assay review --finding <id> --verdict accept --note "why it stays" --until 2027-01-01
```

`accept` says the finding is **right** and you are leaving it on purpose. It leaves the open list,
counts as the check being correct, never sits in "agreed and still here", and comes back on its
own when `--until` passes. Without it the only ways to clear a true finding were `agree`, which
leaves it outstanding forever, or `disagree`, which tells a working check it was wrong and pulls
down the agreement rate that decides whether that check may ever gate. The form offers it on every
card, and its Waivers tab proposes each accept as a waiver for `audit.yml`, so the decision is in
git and not only in the store.

### ...or all of them, away from the terminal

One keypress each is still one turn each, and a backlog of 159 is 159 turns nobody sits through.
On the warehouse this was built against, findings ruled on by a person sat at **0 of 159** for
months — not for want of the loop above, which has shipped for most of this project's life.

```bash
assay review --emit review.html -t target/   # the form, with everything already in it
assay review --load verdicts.json            # every verdict at once
```

The form is not only findings. Three more tabs carry the parts of `audit.yml` that are pure domain
knowledge, and **Words comes first**: a verdict settles one finding, while a vocabulary term
reaches every judged answer about every model it applies to.

| tab | what it holds |
|---|---|
| **Words** | their vocabulary, plus candidates ranked by how often this warehouse joins on them. assay fills in what it measured — how many models name the word, which directories they sit in, what the lint says, a scope that resolves — and leaves `means:` empty, because a definition written from a model name looks exactly like one they chose |
| **Explanations** | the per-mart options for failing-row adjudication. `config.py` calls this "the part of the file worth maintaining" in its own comment, and nothing had ever let anybody maintain it |
| **Waivers** | findings somebody already called fine, carrying the reason *they* typed. assay never invents one |
| **Monitoring** | the staleness threshold, derived from how often this project actually builds rather than picked, with the cadence it came from beside it. Needs `--monitoring volume.json` |
| **Settings** | the rest of `audit.yml` a person acts on — gating floors, the row-loss threshold, the spend cap, the rate card — each with what assay ships beside what this project set |
| **Findings** | the cards, as before |

What they write comes back as a **proposal**, never a write. `assay review --load handback.json`
records the verdicts and prints the `audit.yml` changes as a diff; `--apply` writes them. It edits
lines rather than re-serialising, so comments, key order, blank lines and quoting all survive —
measured on a real 253-line file: 53 comment lines, identical before and after. A path it cannot
place unambiguously is refused with the YAML to paste, because a config editor that writes
something approximately where it belongs is worse than one that says it could not.

It writes `vocab`, `explanations` and `waivers` and nothing else. Gating thresholds want the
measured agreement rate in front of you, and `assay effectiveness` is that surface.


One self-contained file that opens from `file://` — no server, no port, nothing left running.
Twenty cards at a time, highest blast radius first, each carrying what assay found, the claim it
quotes, the model's own SQL with line numbers, and any reading an agent already recorded. Answers
are kept in the browser as you go, so the tab can be closed and come back to. The download button
writes `verdicts.json`.

**A card nobody answered is never submitted and never recorded**, and `--load` names every row it
did not record rather than printing a total that hides them. One card per `(model, check)`, because
that is what a verdict covers: 260 findings are 212 cards.

`--reads <json>` pre-fills a reading of each finding. That is the expensive half and it is what
makes a card cheap to answer, and `assay read --out reads.json` writes it: every unruled card, once,
by the judged tier (`finding_is_correct`), at about $0.00003 a card. The verdict is in the form's own vocabulary and the
reason is selected from the question's criteria rather than written. Nothing is recorded as a
verdict -- the file is for a person, and the click on each card is still theirs.


```bash
assay review --from-labels     # verdicts from assertions already in your project
```

A `unique` test says a column is an identifier; a declared key says what one row is; a join says two
columns are the same concept. Those are real human judgments, made earlier, and they are recorded
as `label` rather than `human` — evidence about a question, never permission for it to fail a
build, because the label can itself be the thing that is wrong.

## While you type, and for your agent

```bash
assay watch --compile --project-dir transform   # a pane that stays quiet until meaning moves
assay mcp                                       # assay as tools an agent can call
assay hook install --dbt "uv run dbt"           # the edit gate, written into .claude/settings.json
```

`hook` is the one that is not advisory. After an agent edits a model, Claude Code runs
`assay hook post-edit`: it compiles that model, runs `assay check --select <model> --new-only`
against the last full `check` in the store, and if the edit introduced a finding the agent is
stopped with the finding as the reason. Findings that were already there do not block an edit, and
a file that is not a model passes untouched. It cannot undo a write; it makes the agent deal with
what it wrote before moving on. `assay onboard --agent` installs it, and it needs one full
`assay check` to compare against — until there is one it refuses edits rather than passing them.

`watch` diffs your working tree against a snapshot taken when it started, so a reformat, a renamed
CTE or a join rewritten as a subquery says **nothing**. Break something and fix it before the next
save and it never speaks, because nothing ended up different. A file that does not parse is "still
typing", never a finding.

`mcp` serves `contract`, `lineage`, `blast_radius`, `findings`, `changed_contracts` and `rebase` --
and every command besides, as `assay_<command>` with its flags as one string, so an agent with no
shell can run all of assay. A tool runs the real command; a run longer than its wait comes back as
a job to follow with `job_status`.
A contract is fifteen lines where the SQL is two hundred, so an agent can hold a project's meaning
in about what reading four models costs it now. `changed_contracts` is the self-check to run after
an edit and before moving on: *did that change what anything MEANS?*

## Eleven questions no parser can answer, asked only where a parser found the shape

```bash
assay ask --family default_is_a_measurement_or_an_absence --dry-run   # count and price first
assay ask --family tie_break_is_total
assay volume --judge --project-dir transform --dbt "uv run dbt"      # the monitoring bank
```

`COALESCE(orders, 0)` and `COALESCE(status, 'not looked up')` parse identically; one is a count and
one means nobody checked. Code finds every default, hand-typed IN-list, unit-named column computed
twice, join on a date, dedupe and sentinel literal, and a judgment is asked only about those:
`default_is_a_measurement_or_an_absence`, `what_would_break_silently`, `filter_is_complete`,
`units_agree_across_models`, `time_grain`, `tie_break_is_total` and `sentinel_is_not_a_value`.
`volume --judge` adds four about the MONITORING, never the data: whether a movement is routine for
that kind of table, which unwatched models are worth watching, which stale failed monitors still
matter, and whether a test that never ran is a gap or a leftover. Every defect answer is a finding
in `assay check` that rests on its own family, so none can fail a build until people have ruled on
it. On a 356-model warehouse all seven code-derived families together price at about two cents.

`assay patch --worth-testing` turns `what_would_break_silently` into a list: the untested columns
whose shape invites a silent failure, ranked by how many marts read them, each with the failure
named and the kind of test that catches it. `patch` alone still writes only the uniqueness tests it
can prove will pass; this is the list of tests worth writing because they can fail.

## Drafts of what you would otherwise write by hand

```bash
assay suggest -t target/ --section descriptions --out drafts.yml
```

`column_has_no_description` counts the columns nobody described; this drafts them. Every line says
where its words came from, and there are only three places: a sentence a person already wrote
upstream of the column, quoted; the one sentence the rest of the project uses for that column,
quoted; or assay's recorded facts -- the judged role as one word, the column's own expression
quoted from the compiled SQL, and the judged meaning of a NULL in that question's own words. No
word is written for the occasion. It goes to a separate file and never into `schema.yml`.

A vocab candidate's `means:` is filled the same way, only when most of the models that describe
the column use one sentence, and it cites them. An `accept` on the form drafts the first half of its
reason from the finding and leaves "it stays because" for you; a reason left at the draft is not
recorded.

## The rest of the bank

```bash
assay feeds --project-dir transform    # has a source changed its mind while its schema held still?
assay align                            # do two columns in different models mean the same thing?
assay tests --gaps-only                # what is a model exposed to that nothing asserts? (no key)
assay adjudicate                       # rows a dbt test flagged: does the row explain itself?
```

**feeds** samples ~20 rows per source, because the defect is uniform across a load. Half of it is
arithmetic and never asked: a numeric column spiking at `-9999`, or a date whose max sits years in
the future, is a placeholder found by counting.

**align** calibrates itself. Every join in your project is somebody asserting two columns hold the
same concept, so the labels are already written: measured **100/100 agreement** against pairs a
real project already joins. Routed by rounding to the nearest level, no threshold to tune.

**tests** finds coverage gaps with no API key at all — 182 on a real project, the worst being a
model with 24 marts downstream exposed to a fan-out that nothing asserts against. Severity fit is
a judgment, and your current `severity:` setting is the weak label it argues with.

**adjudicate** reads `dbt_test__audit`, so `store_failures` is the candidate generator. A test
returning 3,229 rows becomes a triage list instead of a reason to switch the test off. The
explanation options are domain knowledge: `explanations:` in audit.yml holds one set per mart.

## Standard practice, and your agent following it

```bash
assay practices --keys-only     # models with no uniqueness test, and the grain a test should cover
assay practices                 # the full standard set, adjudicated
assay skill all --write .       # both agent procedures, under .claude/skills/
```

Two procedures, because they run at different moments. `dbt-assay` is what an agent follows around
an edit — `contract` and `claims` before, `changed_contracts` and `violations` after. `assay-review`
is walking the findings *with* the person whose warehouse it is and recording their verdicts, and
it says plainly what the label does not prove: `source = 'human'` is set by the code path, not by
anything about who ran it, and `--by` is free text that defaults to `unknown`. Nothing validates
either, so the procedure — record nothing that was not actually answered — is the whole mechanism.

assay does **not** reimplement dbt-project-evaluator. It reads that package's own `fct_*` tables
and splits its 23 checks three ways: **enforce** (exact, essentially no exception — a staging model
reading downstream, a hard-coded table name), **recommend** (conventions; enforcing them is how a
tool gets muted), and **adjudicate** (real candidates with real exceptions — a dimension with
twelve children *is* what a dimension is).

What it adds is consequence, since evaluator has no notion of blast radius, and a judgment for the
eight checks everyone currently ignores.

And where it beats the standard check outright: `missing_primary_key_tests` says "no PK test", while
assay knows the inferred grain and says **which columns it should cover**.

`assay skill` writes the procedure an agent follows: call `contract` before editing, call
`changed_contracts` after, never hand back work where the grain moved silently. The MCP server
gives an agent the ability to check itself; the skill gives it the obligation.

## It becomes part of your warehouse

```bash
assay export transform/seeds/assay   # CSV seeds + a generated schema.yml
dbt seed --select path:seeds/assay  # now it is a relation
```

The inventory, every finding, every stored judgment with its full probability distribution, every
human verdict, and one row per DAG edge. Seeds work on every adapter with no external-table setup.
Then assay's knowledge is just data you can join to:

```sql
select check_name, count(*) as findings, count(distinct subject_name) as models
from {{ ref('assay_findings') }}
group by 1 order by 2 desc
```

A report is read once. A table accrues: a probability per question per model per commit is
something you can chart and diff.

## Install

```bash
uvx dbt-assay onboard -t target              # no install at all
pip install dbt-assay                        # or the usual
pip install 'dbt-assay[jev,mcp]'             # judged tier and the MCP server
```

**No `dbt-core` dependency.** assay reads `manifest.json` as data, so it works across dbt versions
and adapters and can never break your dbt. Python 3.10+.

**The `[mcp]` extra is not optional for the MCP server**, and a bare `uvx dbt-assay mcp` will not
have it:

```bash
claude mcp add assay --scope project -- uvx --from 'dbt-assay[mcp]' assay mcp --target target --store assay.duckdb
```

**The key is never in a config file.** assay reads `TYPESAFE_API_KEY` or `OPENROUTER_API_KEY` from
the environment or from a `.env` in your project or any parent directory, and an exported variable
always beats the file. `assay config` prints what it resolved and where the key came from;
`assay config --check` makes one real call to prove it works, for about $0.00001. This existed as a
bug first: assay read only `os.environ`, so a key sitting in a `.env` was invisible and every judged
command reported the tier as off.

**You do not pass a dialect.** `manifest.json` carries `metadata.adapter_type`, so assay parses
Snowflake as Snowflake and BigQuery as BigQuery on its own; `--dialect` is an override for the rare
project whose manifest does not say. This was a flag once, and forgetting it was expensive: parsed
with the wrong dialect, basedosdados went from 12 parse failures to 128 and silently lost two real
findings. Nothing spurious appeared — the failure mode was a clean-looking run that had quietly
stopped looking.

## What a parser settles, before Jev is asked anything

Exact, free, local, and never put to a model. These need nothing but your `manifest.json` and
compiled SQL, and they run first precisely so the judged tier is only ever asked the questions it
is the only thing that can answer:

- the project graph, blast radius, and per-edge facts
- **tests that cannot fail** — `not_null` on a `coalesce(x, 0)`, `unique` on the group by key,
  `accepted_values` covering every branch of a CASE
- **tests that outrun their source** — the mirror: a `not_null` on a column carried from a
  LEFT-joined parent, padded with `CAST(NULL AS ...)` in a UNION arm, or produced by a
  NULL-preserving aggregate. `count()` over a group is 0; `min()` over an all-NULL group is NULL,
  and the GROUP BY still emits the row. **7 of 646** `not_null` tests on the warehouse this was
  built against, including the one a real outage produced — passed for months, then failed on one
  row of 49,034, where the obvious repair was to delete the row and protect an assertion the data
  never supported
- ranking by a function that returns degrees, after alias resolution
- window functions positioned where they can only see post-filter rows
- dialect traps, like `~` meaning full match in DuckDB rather than a partial one
- columns dropped at a model boundary, per edge
- joins that fan out: a child joining a parent on only part of the key that parent's own
  `unique_combination_of_columns` test declares

Column knowledge is built parents-first through the DAG, so a `select *` is expanded from what its
parents were found to offer. `target/catalog.json` is used when present and every column list says
whether it was derived from SQL, read from the catalog, or declared in `schema.yml`.

## Counting what SQL cannot settle

Grain propagates through the DAG parents-first and has no base case: a model reads a source, the
source declares no key, and propagation stops. `assay probe` settles it by counting.

```bash
assay probe --dry-run          # print the SQL it would run, run nothing
assay probe                    # run it through YOUR dbt
assay probe --emit > probe.sql # or run it yourself and --load the results
```

It shells out to `dbt show --inline`, so **assay never sees a credential** and every adapter and
auth scheme your dbt already handles works unchanged. Each relation is counted once, with
`count(*)`, `count(col)` and `count(distinct col)` together, because `count(distinct)` ignores NULLs
and a mostly-null column would otherwise look unique.

**Relations are batched into one statement, because the startup is the cost.** dbt takes seconds
to boot and the count itself is milliseconds, so a statement per relation spends nearly all of its
time starting dbt up again. Measured on a 358-model warehouse: 271 statements became 23, and 24
relations went from about seven minutes to 46 seconds. A batch that fails is bisected rather than
discarded, so one bad column cannot throw away the good ones beside it.

**And it asks only for columns the warehouse actually has.** The catalog is read first — a
metadata query that scans nothing — and the target list is intersected with it. Before that, 70 of
271 statements on that warehouse were asking for columns that do not exist, and every one came
back `unknown`, which is indistinguishable from a permissions error. A target nothing can confirm
is marked `UNVERIFIED` instead of being guessed at.

`--sample` counts a sample rather than the whole relation, when the whole relation is more than you
want to pay for.

A result says `unique`, `has_duplicates`, `has_nulls` or `unknown`. A permissions error, a missing
table or a timeout records **unknown**, never "not unique". And an observation is stored with its
row count and timestamp, because unique in today's data is not a constraint.

## What Jev is, and why it is not a chat model in a trench coat

[Jev](https://docs.typesafe.ai) is TypeSafe's flagship **System One** model — a class of model
"built to make fast, structured decisions that software can use directly". It does not write
replies, produce code, or explain its reasoning. You define the possible answers, and it returns
one of them with a calibrated probability.

It takes a **state** — named JSON fields, not a prompt — and a map of **typed questions**.

```python
# this is the real question assay ships, copied from questions/semantics.yml
noul("Does the documentation -- `description` and `documentation_in_the_file` together -- "
     "assert something about this model that the code in `contract` and `filters` does not do?",
     true_means="The description states something the code does not do, or states the opposite.",
     false_means="Everything the description claims is true of the code. It may be incomplete, "
                 "terse, or silent about details; that is not a contradiction.")
```

Three primitives, and choosing right is most of the work:

| | returns | the distinction that matters |
|---|---|---|
| `choice` | `choice`, `probabilities`, `confidence` | confidence is **how concentrated the distribution is**, a statistic *about* the probabilities — never permission to act |
| `noul` | `noul` only | **no confidence field exists.** 0.5 means yes and no are equally likely, *not* medium intensity |
| `score` | `score`, `legend`, `probabilities`, `confidence` | the answer may land **between** two levels, so each level must name a concrete situation |

It cannot return anything outside what you defined. That is the difference from asking a chat model
for JSON and hoping: no parse step, no retry loop, and no answer outside the option set, because
the option set *is* the type.

**Question ids are never sent to the model.** `assay` names them `role__zip`, `desc`, `pred__0` for
its own bookkeeping; the model sees only `instructions` and `criteria`, so those have to carry the
complete question.

**And it is cheap enough to run on every model.** $0.042 per million input tokens, output free. A
265-model warehouse: 60 calls, 15 seconds, **$0.0026**. Cached on a hash of the state, so an
unchanged model is free forever and a rebuild only re-asks what moved.

### What Jev is bad at, and what assay does about it

TypeSafe publishes the failure modes, which is the most useful page they have. Four of them shape
this tool directly:

**"Jev is not a calculator."** It cannot reliably do arithmetic, and it reads **dates as text
rather than ordered quantities**. Measured here: asked whether
`date_trunc('month', d) + interval 3 month - interval 1 day` implements *"the last day of the
second month following the month in which the application is filed"*, it scored the **correct**
implementation 0.39 and a **wrong** one 0.62. Every date, count and comparison in `assay` is
therefore settled by sqlglot or by SQL, never asked.

**"Unrelated detail acts as a distractor."** A larger state is a worse state. Measured on one
question: the structural claim alone read **0.96**; the same claim plus a *correct* worked date
example read **0.47**. Nothing was wrong with the extra sentence — it was simply extra. This is
also why stripping comments before judging code improved results (a false positive fell 0.50 →
0.14): assay sends the smallest state that can answer the question, and no more.

**It interprets literally**, reading "scoping words, negations, and implied conditions at face
value". So every criterion here is written as a concrete situation rather than a standard to live
up to, and every `choice` carries a no-match option.

**Multi-hop reasoning costs accuracy.** So one noul per rule, never one over a list of them. Asked
as a single lumped question across three rules, a known defect read 0.64; split, the rule that
applied read **0.85** and the two that did not read 0.02 and 0.05. The split is sharper where it
applies, correctly near zero where it does not, and it kills a false positive the lumped version
produced on clean code.

### The two design rules that decide everything else

**Keep deterministic work in code.** TypeSafe's own guidance: *"Keep code in control and give
System One narrow, structured decisions."* A grain, a column's provenance, a test that cannot fail
— those are facts, settled exactly and for free. If a parser can answer it, Jev is never asked.

**A question the state cannot answer returns a confident non-answer.**
`keys_on_a_non_unique_column` read **0.73 to 0.85 on every model tested, clean or broken** —
because a column's uniqueness is a property of the DATA, not of the SQL. It was not a bad question,
it was in the wrong layer. It is now `count(*) = count(distinct k)` and needs no model at all.
Before adding a question, ask what in the state could make the answer *no*.

### Prior art: this is the citation-check pattern

TypeSafe's [citation check](https://docs.typesafe.ai/cookbooks/citation_check.md) cookbook is the
same architecture, one level down. Stage one is exact string matching, which catches a fabricated
quote with no model call at all. Stage two puts a `choice` to the surviving candidates:
**supports**, **contradicts**, or **says_nothing**.

That third option is the one worth stealing. "The evidence neither supports nor contradicts this"
is a real state, and collapsing it into "false" is how a findings list earns a reputation for
noise.

## What the judgment tier adds

**Provenance needs no judgment at all.** Every column is classified as constant, defaulted, ranked,
aggregated, computed, carried or from_source, from the AST and the DAG. `assay` traces a column
back through the graph until it reaches the hop that actually did something to the value, which is
the answer to "where did this number come from" that no warehouse can give you today.

**Grain**, where code proposes the candidate columns and one noul per column decides which of them
identify a row. **Column role** and **null meaning**, chunked so repeated criteria stay inside the
token budget.

### Claims: what this project says, checked against what it does

```bash
assay claims --extract     # every sentence in your prose, classified
assay claims --write claims.yml   # audit them, edit them, suppress them
assay verify               # check each claim against the code
```

Your prose is not one claim, it is many, and judging it whole produces a coin flip. Measured:
*"Boulder commercial building permits, residential filtered out"* put to a single question split
**0.51 supports / 0.47 contradicts** and flipped between runs, because one half is true and the
other is not. Split into atomic claims, the sharpest read `contradicts` at **0.82**.

So code splits the prose and a judgment says what job each sentence is doing — a claim about
output, a claim about a rule, rationale, an incident note, an instruction to maintainers, or not a
statement at all. **Extraction is selection, never generation**, because Jev is not trained to
generate. The model never writes a claim; it picks from what your team already wrote, which is why
every claim points at the file and line it came from and why `claims.yml` is a real audit surface.

Each claim is then checked on its own, against evidence chosen *for it*: **supports**,
**contradicts**, or **says_nothing**.

Four rounds of measurement on one slice, every change to how the question was asked rather than to
the model:

| | contradicts | says_nothing | supports |
|---|---|---|---|
| generic evidence, compound claims | 10 | 10 | 6 |
| split on semicolons too | 10 | 10 | 6 |
| evidence chosen **by** the claim | 8 | 8 | 10 |
| criteria: **absence is not disagreement** | **5** | 14 | 7 |

The third row is the one worth reading twice. A claim about `d_class_cn` read `contradicts` at
**0.97** purely because the evidence listed thirty other columns and not that one — the model could
not see the thing it was asked about, so it did what TypeSafe document it does and returned a
confident non-answer. Now each claim names its own evidence.

### Traversals: the defect class no single-model check can see

```bash
assay traverse             # judge every hop in the graph
```

Every other question reads one model. A fan-out introduced at one hop and consumed three models
downstream is invisible to all of them: every count past it is inflated, each individual row is
valid, and nothing fails. It is the defect a person finds by chasing a number by hand, months later.

The graph facts are free — what each edge carries, what it drops, what it joins on — so code
narrows to the 395 edges of 457 where something actually changes, and the judgment answers the one
thing code cannot: **does one child row still mean one of the same thing as one parent row?**
`same_thing`, `deliberately_coarser`, `silently_multiplied`, or `different_entity`.

The top hit on the warehouse this was built for:

> `stg_co_parcels_composite → int_water_parcel_irrigation`, joined on `xmin, xmax, ymin, ymax`

which is `join irr i on p.xmin <= i.xmax and p.xmax >= i.xmin` — a bounding-box **overlap** join.
One parcel matches many irrigation polygons. Around $0.02 for the whole graph.

**And the one that needs no `assay` vocabulary to read: does the description still describe the
code?** Prose is written once and the SQL changes around it. Nothing in a warehouse tests a
sentence, so it drifts silently and everyone downstream keeps believing it.

The first one this found on the author's own warehouse:

> `stg_boulder_permits` — *"Boulder commercial building permits (residential filtered out)."*

The filter excludes exactly two substrings, `%single family%` and `%dwelling%`. Of the 14,150 rows
that survive it, 373 are non-residential, 157 are explicitly `building permit - multifamily`, and
13,620 are trade permits with no commercial distinction at all. Valid SQL, passing tests, false
prose, and a lead product shipping residential roofing jobs as commercial. No structural check
reaches that.

**A description that many models share is excluded before anything is asked.** On that same
warehouse 84 of 343 descriptions were boilerplate repeated across models, and they produced half
the first run's findings. Every one was true and worthless: "Staging model: light cleanup of one
raw source" reads as contradicting any model that also filters, because template prose never
mentions what the model does. Repetition is the general form of a placeholder and needs no
vocabulary to detect.

Opt-in, cached so an unchanged model is free forever. Measured on a 265-model warehouse: 60 calls,
15 seconds, $0.0026.

## The key, and where assay looks for it

```bash
assay config              # provider, model, spend cap, and where the key came from
assay config --check      # one real call to prove it works, about $0.00001
```

`TYPESAFE_API_KEY` or `OPENROUTER_API_KEY`, from your environment or from a `.env` in your project
or any parent directory. An exported variable always beats the file. **The key never goes in
`audit.yml`**, because `audit.yml` belongs in git.

This existed as a bug first: `assay` read only `os.environ`, so a key sitting in a `.env` was
invisible and every judged command reported the tier as off. A capability check that can be wrong
needs a way to show what it decided, which is what `assay config` is for.

## Your own questions, and whether their shape is sound

```bash
assay banks     # every question, where it came from, and a lint of its shape
```

A `.yml` in `assay_questions/` here or in any parent adds a family, or replaces a shipped one by
name. `assay banks` then checks it against every shape already measured to fail — arithmetic Jev
cannot do, dates it reads as text, a choice with no way to decline, options described so alike
there is nothing to cut on. [The full list is in the overview](docs/OVERVIEW.md).

It checks the shape, not the answer, and it was calibrated the only honest way: run against
assay's own fifteen hand-tuned banks it flagged four questions, and all four were the linter being
wrong.

## Nothing gates until it has been measured

`assay review` records human verdicts, and config **refuses** to let a question fail a build until
that question has enough of them. Not a warning in the docs, an actual downgrade to `queue`.

Two families can be calibrated on day one against tests the project already contains, and the
measurement is honest about its own limits: role agreed with 57 of 61 such labels, and reading the
four disagreements showed three were the *label* being wrong.

**Getting a question to the gate takes about ten minutes of keypresses**, and there is no way to
skip it that is not a lie:

```bash
assay columns --limit 40      # ask, so there is something to rule on
assay review -i               # a, d, u, s -- least certain first
```

`min_adjudications` is 20 per question, counted per question rather than overall, so gating on
three families is sixty verdicts and not twenty. Only verdicts marked `human` count.
`--from-labels` is real evidence and is deliberately excluded, because a `unique` test can itself
be the thing that is wrong, and letting a project's own assertions authorize a gate over those
assertions is circular.

```bash
assay config       # how far each question is from its floor, and which gate nothing
```

**Three of the twelve question families have a finding resting on them.** The other nine are worth
asking -- their answers fill the inventory, the page and `trace` -- but no finding derives from
them yet, so ruling on them records evidence and moves no gate. `assay config` marks them and
`assay review -i` says so before the keypresses start, because an afternoon spent on a question
that authorizes nothing is an afternoon nobody gets back.

This was a bug before it was a feature. Verdicts are recorded per question and the gate counted
them per *finding*, so nine of ten families satisfied nothing, silently. The one that worked did so
because its finding happened to share its question's name.

Until then every judged finding is an annotation. That is the intended resting state, not a
limitation to work around: a threshold set before anything was measured is a guess wearing a
number.

## What leaves your machine

Nothing, until you turn the judgment tier on.

- The structural tier is entirely local. No network, no telemetry, ever.
- With judgments enabled, a **digest of compiled SQL** is sent: expressions, joins, predicates. Not
  your data.
- The row-adjudication layer sends actual rows and is **off by default**. `init` never enables it.
- `--print-state` on any command renders exactly what would be sent, without sending it.
- The warehouse connection is opened read-only.

**And who may keep it.** When the provider is a router, assay sends a routing policy with every
judged call — `data_collection: deny`, `require_parameters: true`, `allow_fallbacks: false`. Deny
restricts routing to endpoints that do not collect prompts; `require_parameters` keeps the request
away from a provider that would silently drop what it was sent, which for a typed decision changes
the answer rather than failing; and refusing fallbacks means a request fails instead of routing
somewhere you did not choose, so a 503 is the policy working rather than an outage. Add
`zdr: true` for zero-retention endpoints only. It is `jev.routing` in `audit.yml`, and
`routing: {}` sends none.

**assay sends it and does not claim it.** OpenRouter documents the `provider` object for *chat
completions*, and assay posts to their decisions endpoint. Nothing says it is honoured there, so
`assay config` prints it as **not confirmed** rather than as a protection assay has — and if the
endpoint ever rejects it, the policy is dropped for that call and the rejection is recorded, so
assay cannot go on believing it asked for something it never sent. For a guarantee rather than a
request, use `TYPESAFE_API_KEY` and talk to TypeSafe directly.

**What the key has left, from the key.** `assay cost` reads the calls assay made; it cannot see a
key shared with something else, a credit limit, or a balance near the floor. `assay config` asks
the provider:

```
this key: $49.45 of $50.00 left, $2.00 used all time
```

A key with **no** limit is called out, because a limit per key is the blast radius — a runaway run
exhausts its own budget and gets a 402 while everything else keeps serving. So is a balance under
$10: OpenRouter runs extra billing checks and expires caches faster below that, so calls get
slower before they stop, and their documented working floor is $10–20.

## Contract diff

```bash
assay diff --baseline ../main/target            # what changed about what your models MEAN
assay diff --baseline ../main/target --markdown # the paragraph, for a PR comment
```

> **int_water_section_irrigation**: the SQL's grain moves from section_id, irrigation_year to
> section_id. 1 model consumes it, and 1 of them aggregates over it (water_section_summary).
> 9 marts downstream. Nothing in the SQL diff says this.

A grain change looks like somebody edited a GROUP BY. This says one row stopped being one row per
(section, case), who consumes it, and which of those aggregate over it and are now inflated. A
rewrite whose contract is unchanged produces nothing, which is exactly right.

It tracks the SQL's grain separately from the declared one, so a GROUP BY drifting away from a live
`unique` test is caught and named: *one of the two is now wrong*.

## Backtest: does it work on YOUR repo?

```bash
assay backtest --repo .
```

Replays every commit touching model SQL, reads the blob before and after with `git show`, and asks
whether a check fired before and went quiet after. No checkout, no stash, nothing that can collide
with other work in the clone.

On the repo it was built against it found the two models a spatial-ranking defect was removed from,
at a commit whose message never contains the word "fix" — which is why the message is a label here
and never a filter. Three of the four commits that removed that defect would have been missed by
matching on wording.

Historical compiled SQL does not exist, so by default `ref()` and `source()` are resolved and
control blocks stripped. That is fast and reads 83% of blobs. For the rest:

```bash
assay backtest --compile --project-dir transform
```

checks each commit out into a **detached worktree** (never your working tree), links this
checkout's `dbt_packages` so nothing is fetched, generates a throwaway DuckDB profile so dbt can
connect without touching your warehouse, and really compiles — but only for the blobs the strip
could not read, so you pay the per-commit parse just where it buys something. On a real repo that
took unreadable replays from 3 to 0.

The remaining caveat, stated rather than hidden: today's packages and dbt version are not
guaranteed to render exactly what that commit rendered years ago, and on a Snowflake or BigQuery
project the throwaway profile compiles through DuckDB's adapter, so an adapter-dispatching macro
can differ. Pass `--profiles-dir` to use your real one.

## Version when the meaning changed, never when it did not

```bash
assay version-check --baseline ../main/target          # flags models owing a bump, exits non-zero
assay version-check --baseline ../main/target --bump   # prints the exact edit
assay version-stamps --recommend                        # do rows say which logic produced them?
```

Every "you must bump the version" check ever written fires on whitespace, nags on a reformat, and
gets switched off within a fortnight. assay is the one thing in the stack that can tell a renamed
CTE from a grain change, so a reformat, a rewritten join and a tidied comment owe **nothing**.

The level comes from what the change does to a consumer. **Major**: the grain moved, a column left,
a column's role changed, or its provenance changed in a way that alters nullability — `from_source`
becoming `defaulted` means NULLs silently became zeros and every average downstream shifts.
**Minor**: a column was added.

`--bump` prints the edit; `--bump --write` applies it as a targeted text edit. Measured on a real
2,327-line schema.yml: two lines added, all 94 comment lines intact. Inferred contracts still go to
their own file — a bump is a decision, a contract is a guess, and only one of them belongs in a
file you maintain by hand.

## Configuration actually configures

`assay init` writes an `audit.yml`. It scopes questions, waives findings, and decides what an
answer is allowed to *do*:

```yaml
questions:
  ranks_by_degrees:
    action: fail            # EXACT check: a parser decided it, so it may gate immediately
  grain_contradicts_test:
    when: { select: "path:models/water+" }
    act:
      queue: "p > 0.60"     # JUDGED: written as an expression, so its direction is readable
      fail:  "p > 0.85"     # ...and refused until the question has recorded verdicts

waivers:
  int_water_section_county:
    - question: ranks_by_degrees
      reason: "ST_AREA among candidates at one latitude preserves the ordering"
```

`assay check` exits non-zero only when something earns `fail`. A selector assay does not understand
is an **error**, never a silent match-all.

## What to configure, drawn from what was found

```bash
assay suggest -t target/          # candidates, each with the measurement behind it
assay suggest --section vocab     # one section at a time
```

`guide` explains what a vocabulary term is for and `init` writes defaults; nothing went from 257
findings to the four lines of YAML that settle sixty of them. Seven rules, each reporting what it
measured: columns shared across the most models and absent from the vocabulary (`section_id`: 24
models, 65 hops), columns named like a key that are *nearly* unique and so pass every spot check
(`incident_id`: 19,566 distinct in 19,628 rows), `disagree` rulings that nothing waives, and
per-family actions backed by agreement you already recorded.

**It proposes the candidate and the measurement. It never proposes the meaning.** `means:` and
`implies:` arrive empty with the evidence underneath them. A plausible vocabulary block written
from model names looks exactly like knowledge, is not, and then rides along with every judged
question from that point on.

Two rules refuse to finish the job on purpose. A reason repeating across subjects points at a
missing term *or* at a check that is wrong, and those go in different files — on this project the
answer was the second, and the fix was structural rather than a third waiver. And where a family
has no measured agreement, it says so and proposes nothing rather than falling back to the shipped
default, which would read as measured.

## Where did this number come from

```bash
assay trace water_rights.water_right_id
```

Follows a column back through the DAG to the first hop that did something to the value, and stops
honestly at a source, because what happened outside dbt is not knowable from a manifest.

## Where an ANSWER came from

```bash
assay evidence -q <question> -s <model>
```

The exact state a judged answer was computed from, as it was sent. Every judged answer is a
function of a state assay assembled and then threw away, so a disagreement could not be resolved
into "the judge is wrong" or "it was handed the wrong facts" — opposite repairs, one editing the
question and one editing what gets sent. States are stored by hash, so one reused across a thousand
answers is stored once. An answer from before state storage says so, in those words; its absence is
never rendered as an empty state.

## Fixing what you agreed with, and knowing whether it worked

```bash
assay plan -t target/            # what to DO about the findings a person agreed with
```

`check` finds it, `review` settles whether it is real, and then there was nothing. The fix **shape**
falls out of the check name exactly — `arbitrary_pick` is always "add a tie-break column" — so it is
a lookup that costs no calls. What it will not write is the **words**: what a sentence should say
instead is a judgment about a real warehouse. It writes `assay_plan.jsonl`, one object per thing to
do, because the consumer is an agent rather than a person reading a report once.

Then `assay check` reports the one number that measures the loop rather than the tool:

```
of the 12 finding(s) a person agreed with, 5 are gone and 7 are still here.
```

"N resolved" could never answer that — four fewer findings might be the four you agreed about or
four unrelated ones that moved while those sat there. A release cannot move this number and neither
can an agent. Agreeing and later dismissing does not count as fixed; that is a retraction.

## Keeping the store from growing forever

```bash
assay prune --dry-run     # what it would drop, and what it would never touch
assay prune
```

Explicit, never automatic. It drops only what a later `check` re-derives for free from the same
manifest — findings, edge facts, the unreadable list — and will not touch anything that cost money
or a keypress: rulings, claims, adjudications, observed keys, the run log. On a real store that is
3,563 findings down to 730, with every paid table byte-identical afterwards. The split is declared
in `store.PRUNABLE` and `store.NEVER_PRUNED` rather than inferred, so a new table is not silently
prunable — it belongs to one list or the other, and a test fails until it does.

## Releasing

```bash
scripts/release.sh            # release whatever __version__ says
scripts/release.sh 0.47.0     # bump to this, commit the bump, then release
```

One command, because a two-step ritual whose second step is optional gets skipped. **Eight
versions were bumped, committed and pushed without a tag** — 0.25 through 0.45 exist as commits
and as nothing else. The release is tag-driven, so bumping `__version__` felt like releasing and
published nothing, and PyPI only ever shows the *last successful upload*, so the gap was invisible
from outside until somebody looked. A thing that stopped happening, reported the same way as a
thing that is fine, which is the defect this whole project exists to find.

The script refuses rather than guesses: a dirty tree, a version already tagged (PyPI is
append-only — a released version can be yanked but never replaced), a red suite, a failed lint, or
`pyproject.toml` and `__init__.py` disagreeing about the version. The commit and the tag are pushed
together, because pushing the commit first is exactly how main comes to carry a version nothing
published.

And CI guards it on main: a version sitting on `main` with no matching tag fails the build and says
which command fixes it.

## Maintenance

Maintained for my own use. PRs read when convenient, issues may sit, fork freely.

Developed against DuckDB. Verified elsewhere, all with no warehouse connection at all:

| dialect | project | models | parsed |
|---|---|---|---|
| bigquery | `basedosdados/pipelines`, never seen | 1,632 | 1,603 |
| snowflake | `get-select/dbt-snowflake-monitoring` | 25 | 23 |
| snowflake | `fivetran/dbt_netsuite` | 41 | 39 |
| postgres | `elementary-data/dbt-data-reliability` | 30 | 6 |

On the 1,632-model BigQuery project, from raw SQL with no warehouse: **10 seconds**, 7,858 tests
read, 272 coverage gaps, and two `not_null` tests that can never fire because the column is the
literal `'BA'`.

The elementary number is the honest limit and not a dialect problem: its models are `{% set %}`
blocks calling macros, so there is almost no SQL to read until dbt compiles them. assay reports
what it could not read rather than counting it as clean.

`--dialect snowflake | bigquery | postgres | redshift | databricks | duckdb`.

Where there is no compiled SQL, assay strips the Jinja and says so. That is not a compile, and a
macro-generated model will not survive it, but it means a project can be audited by somebody with
no credentials -- a reviewer, a security team, or you evaluating this tool.

## License

Apache-2.0

---

<img src="docs/cuts/chain.gif" align="right" width="110" alt="">

### The plates

The engravings are apparatus from the 17th-century alchemical and assaying literature: furnaces,
retorts, condensing trains, an assayer at his forge. They are long out of copyright, and they are
here because the work is the same work. You charge a sample, you drive it, and you read what comes
off.

The type is IM Fell English, Igino Marini's digitisation of the types cut for the Oxford
University Press in the 1670s, under the Open Font Licence. Both the report and the review form
carry the cuts and the type inside the file, so a page that has been emailed, moved or committed
still looks like itself with no network at all.
