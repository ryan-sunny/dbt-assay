# assay

<img src="docs/foghorn.jpg" align="right" width="210" alt="">

*I say, I say — assay.*

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

## Status

Early. The no-key tier works; the judgment tier is landing next.

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

## While you type, and for your agent

```bash
assay watch --compile --project-dir transform   # a pane that stays quiet until meaning moves
assay mcp                                       # assay as tools an agent can call
```

`watch` diffs your working tree against a snapshot taken when it started, so a reformat, a renamed
CTE or a join rewritten as a subquery says **nothing**. Break something and fix it before the next
save and it never speaks, because nothing ended up different. A file that does not parse is "still
typing", never a finding.

`mcp` serves `contract`, `lineage`, `blast_radius`, `findings`, `changed_contracts` and `rebase`.
A contract is fifteen lines where the SQL is two hundred, so an agent can hold a project's meaning
in about what reading four models costs it now. `changed_contracts` is the self-check to run after
an edit and before moving on: *did that change what anything MEANS?*

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
assay skill --write .claude/skills/dbt-assay/SKILL.md
```

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
dbt seed --select assay_*            # now it is a relation
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
uvx dbt-assay scan --target path/to/dbt/target
```

No install step, no API key, no configuration.

## What runs without an API key

Structure is exact and free. These need nothing but your `manifest.json` and compiled SQL:

- the project graph, blast radius, and per-edge facts
- **tests that cannot fail** — `not_null` on a `coalesce(x, 0)`, `unique` on the group by key,
  `accepted_values` covering every branch of a CASE
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
auth scheme your dbt already handles works unchanged. One statement per relation, one scan, and
`count(*)`, `count(col)` and `count(distinct col)` together, because `count(distinct)` ignores NULLs
and a mostly-null column would otherwise look unique.

A result says `unique`, `has_duplicates`, `has_nulls` or `unknown`. A permissions error, a missing
table or a timeout records **unknown**, never "not unique". And an observation is stored with its
row count and timestamp, because unique in today's data is not a constraint.

## What the judgment tier adds

**Provenance needs no judgment at all.** Every column is classified as constant, defaulted, ranked,
aggregated, computed, carried or from_source, from the AST and the DAG. `assay` traces a column
back through the graph until it reaches the hop that actually did something to the value, which is
the answer to "where did this number come from" that no warehouse can give you today.

**Grain**, where code proposes the candidate columns and one noul per column decides which of them
identify a row. **Column role** and **null meaning**, chunked so repeated criteria stay inside the
token budget.

Opt-in, cached so an unchanged model is free forever, and roughly a third of a cent for sixty
models.

## Nothing gates until it has been measured

`assay review` records human verdicts, and config **refuses** to let a question fail a build until
that question has enough of them. Not a warning in the docs, an actual downgrade to `queue`.

Two families can be calibrated on day one against tests the project already contains, and the
measurement is honest about its own limits: role agreed with 57 of 61 such labels, and reading the
four disagreements showed three were the *label* being wrong.

## What leaves your machine

Nothing, until you turn the judgment tier on.

- The structural tier is entirely local. No network, no telemetry, ever.
- With judgments enabled, a **digest of compiled SQL** is sent: expressions, joins, predicates. Not
  your data.
- The row-adjudication layer sends actual rows and is **off by default**. `init` never enables it.
- `--print-state` on any command renders exactly what would be sent, without sending it.
- The warehouse connection is opened read-only.

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

## Where did this number come from

```bash
assay trace water_rights.water_right_id
```

Follows a column back through the DAG to the first hop that did something to the value, and stops
honestly at a source, because what happened outside dbt is not knowable from a manifest.

## Maintenance

Maintained for my own use. PRs read when convenient, issues may sit, fork freely.

Developed against DuckDB. Verified on Snowflake: a 25-model public package assay had never seen,
parsed 21/25 from raw SQL with no warehouse connection at all. `--dialect snowflake | bigquery |
postgres | redshift | databricks`.

Where there is no compiled SQL, assay strips the Jinja and says so. That is not a compile, and a
macro-generated model will not survive it, but it means a project can be audited by somebody with
no credentials -- a reviewer, a security team, or you evaluating this tool.

## Licence

Apache-2.0
