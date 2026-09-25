# assay, in plain words

<img src="cuts/furnace.gif" align="right" width="170" alt="">

A lead data engineer in your warehouse, working next to your coding agent.

---

## ELI5

Your dbt project knows what your models **do**. Nothing knows what they **mean**.

dbt can tell you a model built and its tests passed. It cannot tell you that the test was
incapable of failing, that a join quietly tripled your row count six models upstream, or that a
column's description stopped being true two pull requests ago. Those are the failures that do not
break anything. They just make every number downstream wrong, silently, for months.

assay reads your compiled SQL and recovers the meaning. It does it with two things:

- **sqlglot** parses the SQL into a tree. Anything a parser can settle exactly, a parser settles.
  Is this test comparing a column to a constant? Does this window function order by a column that
  was already filtered away? Is this hop a union or a join? Those are facts, and facts are free.
- **Jev** answers the rest. Jev is a TypeSafe System One model: you give it a small piece of state
  and a question with named options, and it returns a typed answer with a probability. No prose,
  no reasoning trace, no essay to parse. A judgment you can put in an `if`.

And then the part that makes it a tool instead of a demo: every answer is **stored**, a person can
**rule on it**, and those rulings are what decide whether a check is allowed to fail your build.

> **The one idea.** The structural tier is what makes the judged tier safe. If code can answer it,
> Jev is never asked. Jev only sees the questions that genuinely need meaning, on the smallest
> state that can answer them, and a person's verdict is the only thing that turns an answer into
> authority.

---

## How it works next to an agent

You already have a coding agent. It is good at writing SQL and bad at knowing what your warehouse
means, because that knowledge is not in the files it reads. It is in your head, in a Slack thread
from March, and in the shape of the data.

assay ships an **MCP server with 12 tools**. Point your agent at it and the agent stops guessing.

Here is the actual loop, end to end:

**1. Before it edits anything, the agent asks what the model IS.**

`contract(model)` returns fifteen lines instead of two hundred: the grain, the columns, each
column's role, and where each value came from. `claims(model)` returns what your project
*asserts* about that model, extracted from your own descriptions and comments, with whether the
code currently supports each claim.

That second one matters more than it sounds. The agent now knows what the edit must keep **true**,
not just what it must keep **compiling**.

**2. It checks what it is about to break.**

`blast_radius(model)` says how many marts read this. `lineage(model, column)` follows one column
back through the DAG to the hop that produced its value. `traversal(model)` says how the parents
reach it and whether any hop multiplies rows without declaring it.

**3. It makes the change.**

**4. It checks whether it changed what anything MEANS.**

`changed_contracts()` compares against a baseline and reports only what moved. A reformat, a
renamed CTE, a join rewritten as a subquery: nothing. A grain that changed: reported.

**5. It checks whether it would fail your build.**

`violations()` applies your own `audit.yml` and splits findings into what would fail, what is
queued for a person, and what is only annotated. Same policy your CI applies, so a clean answer
here means a green pipeline rather than an opinion that it should be.

**6. It rules on what it read.**

`rule(finding, verdict, why)` records what the agent concluded, **including when it
concludes the finding is wrong** — which is the most useful answer it can give, because a false
positive nobody reports stays in the list forever. `review_queue()` shows what is still waiting
for a person, agent-read items first.

An agent ruling is **evidence and never authority**. It cannot gate a build, cannot satisfy the
verdict floor, cannot anchor the regression check, and cannot move the ruled-on number. That
separation is the whole design: an agent is the only thing in the loop that could write a hundred
rulings, so the number that says whether your warehouse is *understood* has to be one it cannot
touch.

That is the lead data engineer. Not because it writes better SQL than your agent. Because it holds
the context, states what must stay true, and refuses to let a machine sign off on its own work.

---

## What it actually checks

### Tier 1: what the parser settles, for free

No API key, no network, no spend. `assay check`.

| check | what it catches |
|---|---|
| `description_contradicts_the_code` | the schema.yml description says something the SQL does not do. The description ONLY -- the comment block is judged per-sentence by `code_contradicts_a_claim` -- and a description under ten words is not judged at all, because there is nothing in four words for SQL to contradict |
| `test_cannot_fail` | a `not_null` on `COALESCE(x, 0)`, an `accepted_values` on a hardcoded literal. The test passes on every row and asserts nothing |
| `arbitrary_pick` | one value taken from a multi-valued field, non-deterministically |
| `first_match_pick` | the same class, different spelling |
| `window_after_where` | a window function ranking over rows a `WHERE` already removed |
| `ranks_by_degrees` | ordering by latitude/longitude as if degrees were distance |
| `bbox_as_radius` | a bounding box standing in for a radius |
| `duckdb_full_match` | `~` is a full-string match in DuckDB, not a partial one |
| `variant_column` | dlt's `__v_double` split, where one column silently became two |
| `test_outruns_its_source` | a test asserting something the column has no right to promise: a `not_null` on a column carried from a LEFT-joined parent, padded with `CAST(NULL AS ...)` in a UNION arm, or produced by a NULL-preserving aggregate. `count()` over a group is 0; `min()` over an all-NULL group is NULL. An aggregate whose input cannot be NULL — a column declared `not_null` upstream, or a `coalesce` with a literal tail — is not reported |
| `narrow_read` | a model reading far fewer columns of a parent than it could, where the ones it skips carry the meaning |
| `join_fans_out` | a join whose key is not unique on the far side, so one row becomes several |
| `key_started_holding` | a column that now holds unique where it did not before. Nothing is wrong today; it is the moment to declare the key, before something depends on an accident |
| `key_column_stopped_mattering` | the mirror: a key that held last week and does not now. Invisible to every check that describes the present, and the failure that corrupts a warehouse |

`test_outruns_its_source` is the mirror of `test_cannot_fail`. That family answers "here is
something nothing asserts"; this one answers "here is something asserted that was never true
upstream", and it is the one a real outage produced: a `not_null` that passed for months and then
failed on **one row of 49,034**, where the obvious repair was to delete the row — fixing the data to
protect an assertion the data never supported.

Alongside those, `assay check` reports **unevaluable tests** separately: tests dbt counts as
passing that could never have run at all. They are not a finding about a model, so they are not in
the table above; they are their own section, and in `--json` their own key.

`assay tests --count-defaults` goes one further. A test that cannot fail is the dead bug; the
**live** one is the column behind it. On one warehouse, 170,730 of 172,695 rows of a status column
were the string `'not looked up'`. The test passed on every row and said nothing about whether the
lookup ever ran.

### Tier 2: what the graph settles

`assay traverse`, `assay inventory`. Every hop in the DAG, what each one carries and drops, and
whether a parent row stays one child row. This is the defect class no single-model check can see:
a fan-out introduced at one hop and consumed three models downstream inflates every count past it
while nothing fails, because each individual row is valid.

### Tier 3: what counting settles

Anything that needs the warehouse goes **through your own dbt**, so assay never holds a credential.

- `assay practices --verify` proposes the uniqueness test each model is missing, then **counts the
  proposed grain before recommending it**. A proposal with 1,045 rows and 7 distinct values does
  not become a recommendation.
- `assay patch tests/assay` writes the ones that will pass, as singular tests that collide with
  nothing and can be deleted by deleting them. It refuses to write a test on an empty table,
  because that passes for the wrong reason.
- `assay check --verify` counts each flagged hop's join key. A join onto a key that is unique **in
  the data** cannot fan out, and dbt only knows which keys are *declared* unique.

### Its own config, read against its own store

| check | what it catches |
|---|---|
| `config_comment_contradicts_the_store` | a comment in `audit.yml` that states a count -- "38 of 38 agreed", "which none do" -- and the store no longer bears it out. A claim dated to a version is history and is left alone |

### Standard practice: dbt-project-evaluator's rows, as cards

When `dbt_project_evaluator` is installed and built, `assay check --verify` reads its `fct_` tables
(the schema comes from the manifest; `practices: {evaluator_schema: ...}` overrides it) and turns
its rows into cards: one per (subject, fact), with the evaluator's rule names on the card. One fact
often arrives as several rows: on one project 1,033 rows became 303 cards. Where assay already
says the same thing about the same subject, the rule is named on assay's card instead
(`evidence.evaluator.also_flagged_by`), so one ruling covers both. The page and the form say how
many rows became how many cards. Exceptions are an accept or a waiver in the form; nothing is
written to the evaluator's exceptions seed. `assay practices` adds the judged reading for the rules
with real exceptions, and `--dry-run` says what that will cost first.

| check | from | what it says |
|---|---|---|
| `reads_raw_source_outside_staging` | direct_join_to_source, marts_or_intermediate_dependent_on_source, multiple_sources_joined, source_fanout | one card per model reading raw sources itself, naming them and the rules |
| `source_read_directly_by_many_models` | source_fanout | a source whose readers carry no card above: one card per source, listing them |
| `no_primary_key_test` | missing_primary_key_tests | no uniqueness test; where assay read a grain from the SQL, the card names the columns |
| `model_has_no_description` | undocumented_models, undocumented_public_models | folded into `column_has_no_description` when that model has one |
| `source_freshness_undeclared` | sources_without_freshness | the same card assay writes itself when the evaluator is absent |
| `source_is_never_used` | unused_sources | folded into `source_reaches_nothing` when assay says it too |
| `source_has_no_description` | undocumented_sources, undocumented_source_tables | nothing says what the source is |
| `source_declared_twice` | duplicate_sources | two source entries for one table |
| `model_has_many_leaf_children` | model_fanout | several leaves read it directly |
| `too_many_joins` | too_many_joins | the join count |
| `model_refs_nothing` | root_models | no ref() and no source() |
| `model_name_breaks_convention` | model_naming_conventions | the prefix its layer expects |
| `staging_reads_staging` | staging_dependent_on_staging | a staging model built from staging |
| `staging_reads_downstream` | staging_dependent_on_marts_or_intermediate | a cycle in layers |
| `rejoins_an_upstream_concept` | rejoining_of_upstream_concepts | one card per model, naming each relation joined twice |
| `hard_coded_reference` | hard_coded_references | a literal table name dbt cannot see |
| `long_chain_of_views` | chained_views_dependencies | every read re-computes the chain |
| `public_model_without_contract` | public_models_without_contract | public, no enforced contract |
| `exposure_rests_on_private_models` | exposures_dependent_on_private_models | one card per exposure, not per model |
| `exposure_rests_on_views` | exposure_parents_materializations | one card per exposure |
| `file_in_unexpected_directory` | model/source/test_directories | where the layout expects the file |
| `evaluator_config_does_not_fit` | any | rows about installed packages' models, or a convention rule failing most of a layer: the setting to change, and those rows are not listed as cards |
| `evaluator_rule` | a rule this assay has no card for | carried as the evaluator wrote it, never dropped |

### Completeness: do we have all of it?

`assay completeness`. Coverage of what the project itself declares, and nothing more.

| check | what it catches |
|---|---|
| `source_reaches_nothing` | declared, loaded on every run, and no model or test refers to it |
| `source_only_a_test_reads` | you are paying to test data nothing consumes |
| `source_freshness_undeclared` | nothing says how current it should be. When `dbt_project_evaluator` is installed, the card comes from its `fct_sources_without_freshness` instead, under the same name |
| `source_freshness_stale` | the project states how current it should be and the last load does not meet it |
| `source_volume_not_monitored` | a source with no row-count monitor whose change reaches a mart with nothing watching on the way. One per source, never per model: a model built only from monitored relations is covered. Carries the marts it reaches and the yml to add; `assay volume --judge` asks once per source whether it is worth watching, and the answer is the reading on this finding. Silent without Elementary |
| `hop_drops_most_rows` | a child with no filter, no group by and no collapse that still emits a fraction of the parent. A join that is not matching |
| `seed_reaches_nothing` | a file you maintain, loaded on every build, that no model and no test reads |
| `exposure_undeclared` | a model of yours that no model reads and no dbt exposure covers: dead, or read by a dashboard, app or report the project never named. assay proposes the candidate; the exposure's name, owner and URL are yours to write |
| `key_stopped_holding` | a column that WAS unique in an earlier observation and is not now. The failure that corrupts a warehouse, and it needs two probes to exist |
| `key_column_started_mattering` | a column that used to be determined by the others and now adds identifying power, so the minimal key has grown |
| `column_has_no_description` | columns nobody wrote a sentence for, loudest in an ingestion model where there is no upstream to ask. One finding per model, because it is one decision |
| `models_disagree_about_a_column` | one column name described two different ways in two models. The `section_id` problem, generalised: invisible to every check that reads one model at a time |
| `description_promises_what_the_column_cannot_keep` | a description saying "always populated" over a COALESCE default. The column is never NULL because a literal was substituted, so the sentence is true of the column and false of the data |

The last one needs `--verify` and it ships with its refusals, because most edges drop rows on
purpose. On a 357-model warehouse that is 45 candidate hops out of 358 models, and two findings.

`assay probe -n 8` takes the **least-recently-observed** relations, so a bounded probe on a
schedule walks the project instead of re-reading the front of the list, and a relation that could
not be counted still records the attempt so one unreadable relation cannot starve the cycle. A
drift check needs two observations of a relation, so on 275 relations at `-n 8` that is about 35
passes to first coverage and about 70 before anything can fire.

**And `observed_keys` keeps its series.** Every other table in the store that records a
measurement keeps one; this was the only one that overwrote, so assay could say a key holds today
and could never say a key that held last week has stopped. Probe twice and the comparison exists.

**One defect written in several places is one edit.** Findings of one check whose construct is
the same -- the same `CASE`, the same window, the same join condition, with the finding's own
column masked -- are grouped, and when a project macro every one of them is built with carries the
construct, the group names the macro and the line. `check` says how many findings are how many
constructs, `plan` collapses the agreed ones into one row with its call sites, and the form and
the page say so on each finding. A group is never ruled on: every verdict stays on the finding it
names. On the field warehouse, seven `test_cannot_fail` findings are one edit in
`macros/permit_stg.sql`, and two Denver models write the same `CASE` inline.

**Areas rather than findings.** `assay clusters` groups what code can prove is written alike --
the same filter shape in three or more models, the member of a family of filters that differs from
what most of it writes, the same claim made about several models -- and `--judge` asks only what
code cannot settle, in four families:

| check or family | what it answers |
|---|---|
| `one_rule_or_a_coincidence` | one filter shape in several models: one rule repeated, or separate decisions written alike. Read as one rule, it is a finding on EACH member, ruled one at a time |
| `where_the_fix_belongs` | at the source, in a shared macro, upstream in one model, or correct where it is -- read from counts code computes (layers, sources behind each model, a macro that already writes it). Shown beside the cluster; never a finding |
| `the_odd_one_out` | the one model whose filter differs from its family's: a deliberate exception, or drift. The state carries the others' filters as written and any line of this model's own file or description that mentions the differing value |
| `claims_are_the_same_assertion` | two differently worded claims about two models: one assertion or two. Pairs are edges; the connected components are printed |

A cluster a project macro already writes once is settled by code and never asked. Nothing is ruled
on as a group, and a cluster answer below 0.5 is not a finding. On the field warehouse: 16 filter
shapes written alike (one of them already a macro), 6 odd ones out, 39 claims made about more than
one model, for $0.0027.

**The line that keeps this in scope:** assay can say a column is 99% its default. It cannot say
whether that is bad. The first is a fact about code and rows; the second is intent, and the ruling
loop already exists for it. No funnels, no conversion rates, no anomaly on a trend.

### Tier 4: what only meaning settles

**17 question families**, asked through Jev. A few of them:

| family | question |
|---|---|
| `description_contradicts_the_code` | does the prose assert something the SQL does not do? |
| `claim_alignment` | does the code support this specific claim, one claim at a time? |
| `edge_preserves_the_grain` | does one row of the child still mean one of the same thing as one row of the parent? |
| `column_role` | is this an identifier, a measure, a qualifier, a timestamp? |
| `same_concept` | do two columns in different models mean the same thing? |
| `severity_fit` | does this test's severity match what it protects? |
| `practice_exception` | is this standard-practice violation actually fine here, and why? |

---

## The part that stops it rotting

Every judged tool decays the same way. It ships, it produces some wrong answers, people stop
reading the output, and it becomes a linter everybody has muted. assay has four mechanisms against
that, and they are the reason to care.

**1. Rulings are stored with provenance.** `assay review -i` is a / d / u / s, least certain
first. Every verdict records who gave it and whether they were a person, an agent, a label derived
from your own tests, or a replay. Only `source='human'` counts anywhere that matters.

**2. A question cannot fail your build until it has been measured.** `min_adjudications` refuses
`fail` for a judged question with too few human verdicts and downgrades it to `queue`.
`min_agreement` is the other half: a count of wrong answers is still a count, so a question people
read twenty-five times and disagreed with twelve times has earned nothing.

**3. `assay regress` catches an upgrade that moves a verified answer.** Measured in the field: a
change to the subject state moved two of eight verified answers while the answer *distribution*
barely moved, 79 of the same answer either side. Invisible in any summary. Visible because eight
rulings were on record.

**4. `assay effectiveness` measures whether the questions got better.** Agreement per family, per
version of the question — because a verdict about v1 says nothing about v4. It carries a second
axis too, `model_version`, which is the only way you would ever see *"our agreement fell and we
changed nothing"* when Jev ships a new model.

And `assay disagreements` groups the findings people rejected. Twelve rejections on one warehouse
turned out to be three separate bugs. Code groups the identically-worded ones for free; `--judge`
asks whether differently-worded reasons are one defect, for about six hundredths of a cent.

Neither one can close a disagreement. That only happens when the check changed and a person re-read
it.

> **The number that cannot be gamed.** "Ruled on" is the only figure in the system a release cannot
> move. A better check finds more, a fuller state raises a confidence, the DAG moves the blast
> radius. None of that moves this, because it moves when somebody reads SQL and at no other time.
> A good release makes it look **worse**, since finding more raises the denominator. Treat that as
> the design working.

---

## The page

`assay page assay.html` writes **everything assay knows about your warehouse**, as one file you
open by double-clicking. Ten tabs:

| tab | what is on it |
|---|---|
| **Models** | every model, and on one screen: what one row of it is and who settled that, every column with its role and where its value came from, every hop in and out, what the project claims about it, every finding, and every answer ever given |
| **The chain** | every hop in the DAG, what it carries, what it **drops**, what it joined on, whether it drives, and how much of the parent survived |
| **Claims** | every sentence the project says about itself, where it was written down to `path:line`, and what the code said back |
| **Findings** | ranked by reach, each with its evidence and whether a person has read *this finding* or only its model |
| **Monitoring** | whether anybody would notice if what this SQL produces changed tonight: how often the project actually builds, each monitor's own freshness, what the declared tests are doing, the tests whose last result was a FAILURE and which have not run since, and the models with a mart downstream and no row-count history. Needs `--monitoring volume.json`; without it the tab says the measurement was never taken rather than showing zeros |
| **What to configure** | the join between a long findings list and four lines of YAML, each row carrying the measurement that produced it |
| **Spend** | one row per call, what the thinking cost and what the warehouse cost, kept apart because they are priced by different people in different units |
| **Answers** | the live answer to every question asked about this project, with its confidence and the runner-up |
| **Questions** | all the question banks in full: the instructions and every option, exactly as they are sent |
| **Config** | what was actually resolved, the vocab, the runs, and what assay could not read |
| **Understood** | the record, unchanged |

`--plain` writes **the record** on its own: the small report answering *is this warehouse
understood, and by whom*, at about 15 KB. Those are two different jobs and conflating them was
costing both. The record is small, committed, diffed across commits, and handed to a colleague;
its whole argument is that it accrues, which needs it to stay small. The explorer is for the
person who owns the warehouse.

**No tab opens on a flat list.** 5,794 claims in one scroll is not more information than 358
models in one scroll, it is less: the first screen tells you nothing about the shape of what is
there and gives you nowhere obvious to click. So the high-volume tabs open on a grouped summary
you can read in one screen, and the rows are one click in, already filtered. Claims group by
model, answers by the question family that asked them, findings by check. And a model name in any
table is a link to that model, so the tabs are one thing rather than ten islands.

**The chain is drawn, not listed.** You never draw 573 hops; that is the whole graph. A drawing is
always one model's neighborhood, and those are small: on a 358-model warehouse the median is 3
boxes, p95 is 12, the worst is 37. So it is three bands and straight lines, with each edge's join
kind, keys and dropped count written on the parent box rather than on the line, because eight
lines converging on one focus turn line labels into mush. Past nine in a band it degrades to a
list, since 36 boxes with 36 converging lines is the hairball the drawing exists to avoid.

**One file, and that is not a style preference.** Browsers block `fetch` on `file://`, so a
directory of HTML plus JSON that loads a model when you click it cannot be opened from disk. That
option does not exist locally: an artifact you double-click has to carry its data inside it, and
the moment lazy loading is wanted a server is required. There is no middle rung. It costs roughly
24 KB per model, so a 358-model warehouse is about 8 MB and opens instantly.

**But the page is not the thing you commit.** 8 MB of markup diffs as one unreadable blob. The
same content as JSON Lines is the same size, measured at 8.12 MB either way, and it diffs as **one
line per entity**, so a commit reads as *"these 3 models changed, these 12 findings appeared"*.
Every run writes `assay-data/` beside the page: one `.jsonl` per table, `meta.json` and
`config.json` whole, and `record.html`, which is the 15 KB report. Commit that directory and
gitignore the page. `assay page out.html --from assay-data/` re-renders it with no dbt target, no
store and no manifest, so any past commit's artifact renders as the page it was.

It is written every run rather than behind a flag, because a page and an artifact that can
disagree is the same one-fact-two-spellings defect this tool exists to find.

Everything renders from one object called `DATA`, embedded here and one `fetch` on a server, so
this already builds most of a served version if a warehouse ever outgrows a file. There is no
server command today; the page is a file.

And it is still **deterministic**: it carries the manifest's own `generated_at` and never a wall
clock, and every array is sorted in the assembly layer rather than in the browser, so a rerun that
changes nothing writes an identical file. That is the whole argument for a file over a dashboard.
A file that diffs accrues; a server shows you today and forgets.

## Two ways in: MCP, or a skill

The MCP server gives an agent the tools. `assay skill --write .claude/skills/dbt-assay/SKILL.md`
gives it the **procedure**, and the skill works with or without the server: every tool has a
command that answers the same question, and the CLI's `--json` carries the same `finding` ids, so
ruling works either way.

## Every command

```bash
# Start here
assay guide               # HOW TO SET IT UP: vocab, questions, waivers, policy
assay guide start         # ...the order for a project that has never run assay
assay onboard             # look at the project and say what to run, in order
assay onboard --compile   # ...and run `dbt compile` first where models lack compiled SQL
assay config              # what was resolved: provider, spend cap, where your key came from
assay init                # write an audit.yml and nothing else

# Reading what you have. No key, no network, no spend.
assay scan                # parse coverage, and what could NOT be read
assay check               # every finding, structural and judged, ranked by blast radius
assay check --verify      # ...and count each flagged hop's join key through your own dbt
assay check --json        # an object, not a list: {coverage, parse_failures, findings, ...}
assay inventory           # what every model IS; --html writes a page you can commit
assay trace <column>      # where one column's value actually came from
assay tests               # tests that cannot fail, and what nothing asserts at all
assay tests --count-defaults   # ...and how often each COALESCE default actually wins
assay practices           # standard-practice violations, with judged exceptions
assay clusters            # one filter written in several models, the one that differs, one claim made twice
assay history             # when each open finding was first seen, at which commit; what changes most
assay clusters --judge    # ...one rule or a coincidence, where the fix belongs, and the odd one out
assay patch tests/assay   # WRITE the uniqueness tests it can prove will pass

# The judged tier. Needs a key.
assay claims --extract    # turn your prose into atomic claims
assay verify              # check each claim against the code
assay traverse            # judge every hop in the graph
assay columns             # what each column MEANS
assay semantics           # why each filter is there; whether descriptions still hold
assay infer               # grain, where code could not settle it
assay align               # two columns in different models that mean the same thing
assay feeds               # has a source column changed its meaning?
assay adjudicate          # triage the rows a dbt test already failed
assay ask                 # run any question that declares a `subject:`, including your own

# Ruling, and measuring whether it is working
assay review -i           # a / d / u / s, least certain first
assay effectiveness       # did the questions get better? per family, per version
assay calibration         # when it is confident, is it right more often than when it is not?
assay disagreements       # N rejected findings, how many separate bugs?
assay regress             # did an upgrade move an answer a person verified?
assay banks               # every question, where it came from, whether its shape is sound
assay banks --judge       # ...and whether any two options could both be right

# On a branch, and over time
assay diff --baseline <main target>     # what changed about what models MEAN
assay backtest --repo .                 # would this have caught YOUR past bugs?
assay version-check --baseline <target> # does anything owe a version bump?
assay watch                             # rerun on save; print only what your edit changed

# Wiring it in
assay mcp --target target # the MCP server: 12 tools for your agent
assay skill               # write the skill file that tells the agent how to use them
assay export <dir>        # the tables, as seeds your own models can join to
```

---

## Your warehouse, and what leaves your network

assay reads a local DuckDB file freely. Any other warehouse (Snowflake, BigQuery, Databricks,
Postgres, Redshift, MySQL, MotherDuck) gets **nothing**, not even `select 1`, until you allow it:
a yes at a terminal, `ASSAY_ALLOW_WAREHOUSE=1`, or, for a schedule, in audit.yml:

```yaml
warehouse:
  target: assay           # a read-only output in profiles.yml; every dbt call assay makes uses it
  allow_queries: true
  max_queries: 200        # per command, stopped before the statement past it
  max_spend_usd: 1.00     # per command, by assay's estimate, stopped before
governance:
  metadata_only: true     # never send a row value to the model provider
```

The refusal says what would run, where and as whom, read from profiles.yml (never a secret).
Every query goes through your own dbt, so the adapter's own caps apply: set `query_tag` on
Snowflake, `maximum_bytes_billed` and `job_execution_timeout_seconds` on BigQuery, a small SQL
warehouse of its own on Databricks, a read-only role everywhere. `assay onboard` checks which of
those your `assay` output sets, `--check-warehouse` says who assay is once connected, and it
prints what leaves the machine:

| what | where it goes |
|---|---|
| read-only statements: counts, distinct counts, small samples | your warehouse, through your dbt, after you allow it |
| compiled SQL, names, your own descriptions and claims, counts and ratios | the model provider, per judged question |
| row values | only from `assay feeds` (a sample per source) and `assay adjudicate` (failing rows dbt stored); `metadata_only` refuses both before sending |
| the Lean toolchain | downloaded once from GitHub by `assay prove --setup`; nothing about your project is sent |
| anything else | nothing: no telemetry, and the page and the form load nothing from the network |

What differs by adapter, from the code:

| | DuckDB | Snowflake | BigQuery | Databricks / Spark | Postgres / Redshift | MySQL | Trino / Athena / T-SQL |
|---|---|---|---|---|---|---|---|
| parse, structural checks, judged tier, Lean certificates | yes | yes | yes | yes | yes | yes | yes |
| Lean parse proofs | the fragment is DuckDB-shaped | NULL order per dialect; more models unproven | backquoted names read | as Snowflake | as Snowflake | backquoted names read | as Snowflake |
| run check on generated rows | yes | on its round-tripping DuckDB translation | as Snowflake | as Snowflake | as Snowflake | as Snowflake | as Snowflake |
| engine parse check (`EXPLAIN`, runs nothing) | yes | yes | no EXPLAIN statement: the round trip only | yes | yes | yes | yes |
| probes batched into one dbt call | yes | yes | yes | yes | Postgres yes, Redshift one per statement | one per statement | one per statement |
| listing before counting (a missing column never splits a batch) | yes | yes | per dataset | no | yes | yes | no |
| sampling | yes | yes | yes | yes | exact counts | exact counts | exact counts |
| run end to end on a real engine | yes | not yet | not yet | not yet | not yet | not yet | not yet |

## What it costs

Jev is **$0.042 per million input tokens, output free**. In practice:

- Tiers 1, 2 and 3 cost **nothing**. No key required.
- A full judged sweep of a 300-model project runs in cents, and `assay ask --dry-run` prints the
  subject count and the estimate **before** spending anything.
- `jev.max_spend_usd` in `audit.yml` is a hard cap, checked before the call rather than after.
- Every answer is cached on a state hash, so a rerun where nothing changed asks nothing. A cached
  answer whose state moved is a **miss**, not a hit, because serving the old one is how a cache
  starts lying about the present.

The grouping run above: 17 questions, 13,930 input tokens, **$0.00059**.

---

## What it does not do

It reads code and rows, never intent. It cannot tell you whether a business rule is correct, only
whether your code does what your documentation says it does.

It does not edit your models. `assay patch` writes test files and nothing else, never overwrites a
file it did not write, and refuses to write a test it cannot prove will pass.

It does not decide anything a person should. Every judged gate is refused until enough humans have
ruled, and an agent can never be one of them.

And `docs/VERIFICATION.md` says, per family, whether a person has actually read its findings
against real data — including the ones where the answer is no, and the ones measurement proved
weak. Ten of seventeen so far.

---

## Where it goes next

The half of assay that knows about dbt is `parse`, `manifest`, `infer`, `relate` and `practices`.
The other half — claims, question linting, the ruling store, policy, regression, the MCP shape —
does not know what dbt is. That is the portable half, and it is most of the value.

Point the same machine at a **codebase** instead of a warehouse and the tiers line up exactly:
tree-sitter where sqlglot was, a call graph where the DAG was, docstrings where descriptions were.
[Graphify](https://github.com/Graphify-Labs/graphify) already ships the structural tier for thirty
languages and labels edges it cannot resolve as `AMBIGUOUS`, flagged for human review, with nothing
servicing that queue.

Same bet, one layer over: the structural tier is what makes the judged tier safe.
