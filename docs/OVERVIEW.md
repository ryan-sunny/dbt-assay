# assay: a lead data engineer that lives in your warehouse

*I say, I say — assay.*

Your dbt project has types nobody declared. Every model has a grain. Every number has a unit. Every
nullable column has a meaning for null. Every model assumes something about what its parents
already did. None of it is written down, all of it is load-bearing, and the only copy lives in the
head of whoever last debugged it.

When that person leaves, or just forgets, the warehouse keeps running and starts lying.

`assay` recovers those semantics from the code, stores them as data you can query, and finds the
places where they contradict each other. [sqlglot](https://github.com/tobymao/sqlglot) reads the
structure. [TypeSafe's Jev](https://docs.typesafe.ai) reads the meaning. SQL does the rest.

---

## The thing it is actually for

A senior data engineer joining your team does four things in their first month. They read every
model and work out what it really produces. They notice the tests that cannot fail. They notice the
comments that stopped being true. And they remember all of it, so that six months later they can
say "careful, that column is not what it says".

`assay` does those four things, on every model, every time you run it, and writes the answers into
your warehouse as tables your own models can join to.

That last part is what makes it a colleague rather than a linter. A linter tells you about today's
diff and forgets. `assay export` puts the inventory, the findings, every stored judgment and every
human verdict into `transform/seeds/assay` as real relations. Your warehouse then knows what it
means, and so does every agent and every person who queries it afterwards.

---

## Start with one command

```bash
uvx dbt-assay onboard --target path/to/dbt/target
```

No install, no config, no account. It reads your `manifest.json`, works out what it can and cannot
see, runs everything that is free, runs the judged tier if you have a key, writes an `audit.yml`
that gates nothing, and prints the exact next command.

It leads with the honest part, because a first run is where a tool is most likely to be quietly
wrong:

```
1. what assay found
  project         sunny_data  (dbt 1.11.12, duckdb → duckdb)
  models          356   sources 210   tests 1282   edges 942
  compiled SQL    265 read  (265 from disk, 0 from manifest)
  not audited     91 models have no compiled SQL. Run `dbt compile` to include them.
  parsed          265/265
```

No compiled SQL, no catalog, an assumed dialect: each of those degrades the answers without
changing how confident the output looks, so `assay` says which are true for you before it shows a
single finding.

**You never pass a dialect.** Your manifest carries `metadata.adapter_type`, so Snowflake parses as
Snowflake and BigQuery as BigQuery on its own.

---

## The free tier: structure, no key, no network

Everything here comes from your manifest and your compiled SQL. It costs nothing and sends nothing.

**Tests that cannot fail.** A `not_null` on a `coalesce(x, 0)`. A `unique` on the sole `group by`
key. An `accepted_values` covering every branch of a `CASE`. These pass forever and protect
nothing, and they are the most dangerous kind of test because the dashboard is green.

On a real 356-model warehouse: **38 of them.** In a public dbt repo it found `'BA' as sigla_uf`
carrying a `not_null` test.

**Arbitrary picks.** `row_number()` with no unique tiebreak, so a capped query draws a different
subset every build and nobody can reproduce last week's number.

**Ranking by a function that returns degrees.** `ST_Distance` on lat/long is degrees, and a degree
of longitude compresses by cos(latitude). At Colorado's 39°N that is 0.78. This shipped here and
snapped 11.3% of stream termini to the wrong segment.

**Dialect traps**, like `~` meaning full match in DuckDB rather than partial. That one took a
production run down with a `ZeroDivisionError` because a filter matched zero rows.

**Joins that fan out**, where a child joins a parent on part of the key that parent's own
`unique_combination_of_columns` test declares. One row per (section, case) silently becomes six.

**Columns dropped at a model boundary**, per edge. **Blast radius** for every finding, so a defect
feeding nine marts outranks the same defect on a leaf.

And a rule that runs through all of it: **a check that cannot see must not read as a pass.** On one
public package every one of 38 tests sat on a column `assay` could not resolve, and reporting "no
findings" would have looked exactly like a clean bill of health. It says `274 test(s) could not be
evaluated` instead.

---

## The judged tier: meaning, where code runs out

Some questions have no syntactic answer. A `where` clause is either domain logic, a patch over a
bad feed, or the thing that makes the model mean what it means — and the SQL is identical for all
three.

[Jev](https://docs.typesafe.ai) is a **System One** model: built to make fast, structured decisions
software consumes directly. It does not write replies, produce code, or explain its reasoning. You
define the answers; it returns one with a calibrated probability.

- `choice` — one of your options, plus the full distribution. Confidence is **how concentrated that
  distribution is**, a statistic about the probabilities. It is not permission to act.
- `noul` — the probability the answer is yes. **There is no confidence field.** 0.5 means yes and
  no are equally likely, not "medium".
- `score` — a position on ordered levels, and the answer may land **between** two of them, so every
  level must name a concrete situation. "Medium" describes nothing.

It cannot return anything outside what you defined, so there is no parse step and no retry loop.

**It is also narrow on purpose, and TypeSafe publishes exactly where.** *"Jev is not a
calculator."* It cannot do arithmetic reliably and reads dates as text rather than ordered
quantities. Unrelated detail in the state acts as a distractor. Multi-hop reasoning costs accuracy.
Every one of those shapes this tool: arithmetic and dates are settled by sqlglot or by SQL and
never asked, states are the smallest thing that can answer the question, and there is one noul per
rule rather than one over a list of them. The [README](../README.md) carries the measurements.

Twelve question families ship. The one worth seeing first:

### Does the description still describe the code?

Prose is written once. The SQL changes forty times around it. Nothing in a warehouse tests a
sentence, so it drifts in silence and everyone downstream keeps believing it.

The first one this found, on the warehouse it was built for:

> `stg_boulder_permits` — *"Boulder commercial building permits (residential filtered out)."*

The filter excludes exactly two substrings, `%single family%` and `%dwelling%`. Of the 14,150 rows
that survive it:

| | |
|---|---|
| explicitly non-residential | 373 |
| explicitly `building permit - multifamily` | 157 |
| trade permits with no commercial distinction at all | 13,620 |

Valid SQL. Passing tests. A lead product shipping residential roofing jobs as commercial.

It then found the general form: the macro behind all ten city models documents a rule — *"use that
city's real commercial/residential field, NOT keyword-guessing on the class text"* — and five of
the ten cities keyword-guess. Jev flagged all five.

**No structural check reaches any of that.**

### The rest of the bank

**Column role and null meaning.** What each column actually is — `identifier`, `foreign_key`,
`measure`, `dimension`, `event_time`, `audit_time`, `status_flag`, `free_text`, `attribute`,
`geometry`, `other`. What a null in it would mean — `unknown_value`, `not_applicable`, `not_yet`,
`means_zero`, or `cannot_tell`. Chunked so repeated criteria stay inside the token budget, and
every family carries a no-match option because the answer being off the list is itself a finding.

**Grain.** Code proposes the candidate columns; one noul per column decides which identify a row.
Where code cannot settle it, `assay probe` counts it through your own dbt.

**Predicate intent.** `business_rule`, `data_quality_workaround`, `scope_limit`,
`performance_prefilter`, or `cannot_tell`. A filter read as a workaround is one whose real fix is
upstream and which goes stale the moment the feed improves — and which nobody dares delete eighteen
months later because no one remembers why it is there.

**Same concept.** Two columns in different models that mean the same thing under different names.

**Severity fit**, **practice exceptions**, **feed drift**, **row coherence** and **row explanation**
round it out.

### What it costs

$0.042 per million input tokens, output free, cached on a hash of the state so an unchanged model
is free forever.

Measured on a 265-model warehouse: **60 calls, 15 seconds, $0.0026.** A full pass over every
documented model is about a cent.

```bash
assay config              # provider, spend cap, where your key came from
assay config --check      # one real call to prove it works, ~$0.00001
```

The key lives in your environment or a `.env`, never in `audit.yml`, because `audit.yml` belongs in
git. There is a hard spend cap per invocation, estimated **before** the call, because a cap that
fires after the spend is not a cap.

---

## Nothing gates until it has been measured

This is the part most tools get wrong, and it is why `assay` is safe to put in CI on day one.

A judged finding **cannot fail your build** until that question has recorded human verdicts.
Not a warning in the docs — an actual downgrade to `queue`, in code.

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
already given at 0.99 teaches almost nothing and one at 0.45 is where the question is actually
being decided. The evidence is on screen because a verdict nobody can reach in five seconds does
not get given.

`assay config` shows how far each question is from its floor. **Three of the twelve families have a
finding resting on them today**; the other nine fill the inventory and the page but move no gate
yet, and `review -i` tells you that before the keypresses start rather than after.

---

## How to actually use it

### Day one

```bash
uvx dbt-assay onboard -t target        # see everything, change nothing
assay inventory --html inventory.html  # one page, every model, commit it
```

Open the page. It is one card per model: the grain, every column with its role, where each value
came from, what a null means, and a warning banner above any description that no longer matches its
code. Every cell says whether it was **declared** by a human, **observed** by counting,
**derived** from the SQL, or **judged** by a model — with the probability attached. A judgment is
not a fact and nothing here pretends otherwise.

### Every pull request

```yaml
- uses: ryan-sunny/dbt-assay@v0.2.0
  with:
    target: target-head
    baseline: base/target
    store: assay.duckdb
```

It posts what changed about **what your models mean**, not what changed in the text. A model whose
grain moved, a column that stopped being an identifier, a contract that narrowed. It does not gate
by default.

### While you type

```bash
assay watch --target target
```

Reruns on save and prints only what your edit changed.

### As your agent's colleague

This is the part that compounds.

```bash
assay onboard --agent
```

writes `.claude/skills/dbt-assay/SKILL.md` and prints the MCP line:

```bash
claude mcp add assay -- assay mcp --target target
```

The MCP server gives an agent the **ability** to check itself: it can ask what a model means, what
a column is, what the grain is, and what would break before it writes a line. The skill file gives
it the **obligation** — without it an agent checks when it remembers, and with it, checking is the
procedure.

That is the "lead data engineer in the warehouse" part, literally. Your agent stops guessing what
`building_key` is and asks. It stops writing a `not_null` test on a coalesced column because the
check runs before the commit. And every judgment it relies on was either derived by a parser,
counted in your warehouse, or ruled on by you.

### Into your warehouse

```bash
assay export transform/seeds/assay && dbt seed
```

The inventory, the findings, every judgment and every verdict become real relations. Now your own
models can join to them. You can build a mart of "every column whose meaning is unsettled", alert
on a grain that moved, or ask your BI tool which dashboards rest on a judgment nobody has confirmed.

---

## What it will not do

**Reading the rows needs your dbt, and is worth wiring up.** A model can be flawless and still be
fed a column that means something other than its name, so three parts of `assay` reach the data
and all of them go through `dbt show --inline` — your adapter, your auth, no credential held here.
`assay probe` counts keys to settle a grain code could not. The **feed** family samples raw columns
and asks whether a field still matches its name and whether its units are what the column claims,
which is the one defect that passes every schema test and every volume monitor ever written. The
**row** family adjudicates what `store_failures` already wrote to `dbt_test__audit`: dbt built the
candidate generator, and a test returning 3,229 rows stops being a gate nobody reads and becomes a
list a judgment triages down to the handful that need a person.

**It will produce false positives.** On the permit models above, five of seven flagged descriptions
were clearly right, and two were models that follow the documented rule. The tool narrows; a person
adjudicates. That is the whole design, and it is why nothing gates until you have ruled on it.

**It never holds your credentials.** Warehouse access goes through `dbt show --inline`, so every
adapter and auth scheme your dbt already handles works unchanged and `assay` never sees a secret.

**Nothing leaves your machine until you turn the judgment tier on.** The structural tier is
entirely local. When the judged tier runs, what goes out is the compiled SQL of the model under
judgement and the question — never your data, never your rows.

---

## Where it came from

It was built while auditing a Colorado water rights warehouse, where a case number is only unique
inside a water division, three models joined on the number alone, and 73.9% of 189,654 decree/right
pairs crossed divisions. Nothing in the stack could tell me that. dbt tested what I told it to test.
The linter checked style. The types were fine.

Every check in `assay` is a defect that actually shipped somewhere, with the incident attached. That
is what separates it from a generic rule set, and it is why the question bank is worth maintaining.

---

## Install

```bash
uvx dbt-assay onboard --target target      # no install
pip install dbt-assay                      # or the usual
pip install 'dbt-assay[jev,mcp]'           # with the judged tier and the MCP server
```

No `dbt-core` dependency. `assay` reads `manifest.json` as data, so it works across dbt versions and
adapters and can never break your dbt.

MIT. Issues and forks welcome.
