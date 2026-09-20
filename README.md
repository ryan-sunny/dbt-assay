# assay

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

## Maintenance

Maintained for my own use. PRs read when convenient, issues may sit, fork freely.

Developed against DuckDB. sqlglot handles the parsing for other dialects; untested elsewhere.

## Licence

Apache-2.0
