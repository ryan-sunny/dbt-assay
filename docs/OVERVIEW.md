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
uvx dbt-assay guide [topic]       # how to CONFIGURE it: vocab, questions, waivers,
                          # policy, explanations, ruling. `start` for a new project
assay onboard --target path/to/dbt/target
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

Eighteen question families ship. The one worth seeing first:

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

### Claims, and traversals

**Claims** turn your prose into data. Code splits descriptions and comments into sentences; a
judgment says what job each is doing; the checkable ones become rows with a stable id, a file and
line, and a `claims.yml` you can edit. Then each is checked on its own against evidence chosen for
it: supports, contradicts, or says_nothing. Judging whole prose instead produced a coin flip —
0.51 / 0.47 on a claim whose two halves disagree with each other.

**Traversals** judge the hops. Every other question reads one model, so a fan-out introduced
upstream and consumed downstream is invisible to all of them: every count past it is inflated and
nothing fails. Code narrows to the edges where something changes, and the judgment answers whether
one child row still means one of the same thing as one parent row. On the warehouse this was built
for it found a bounding-box overlap join feeding a mart, for about two cents.

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

## Every question, and what rests on it

Eighteen families ship. `assay config` shows how many verdicts each has and which can gate;
`rests_on` on a finding names the family it derives from, and these are those names.

| family | type | finding it feeds |
|---|---|---|
| `claim_alignment` | choice | `code_contradicts_a_claim` |
| `sentence_is_a_claim` | choice | — *extraction: it decides what to ask* |
| `edge_preserves_the_grain` | choice | `hop_multiplies_rows` |
| `column_role` | choice | `identifier_outside_grain`, `measure_inside_grain` |
| `column_is_part_of_the_key` | noul | `grain_contradicts_test`, `grain_unresolved` |
| `description_contradicts_the_code` | noul | `description_contradicts_the_code` |
| `null_meaning` | choice | — |
| `predicate_intent` | choice | — |
| `same_concept` | score | — |
| `severity_fit` | score | — |
| `practice_exception` | choice | — |
| `field_matches_its_name` | choice | — *needs the probe* |
| `units_are_what_the_column_claims` | choice | — *needs the probe* |
| `row_explanation` | choice | — *needs warehouse rows* |
| `row_is_internally_coherent` | noul | — *needs warehouse rows* |
| `options_overlap` | choice | — *lints a question, not a project: `assay banks --judge`* |
| `same_defect` | noul | — *groups assay's own rejected findings: `assay disagreements --judge`* |

**A dash means no finding rests on it yet.** Those answers still fill the inventory, the page and
`trace`, and ruling on them records evidence — but it moves no gate, and `assay review -i` says so
before the keypresses start. That set is asserted by a test, so it cannot quietly become a lie.

## Every command, and when you reach for it

```bash
assay onboard             # a project assay has never seen. Start here.
assay onboard --compile   # ...and run `dbt compile` first, where models lack compiled SQL
assay config              # what was resolved: provider, spend cap, where your key came from
assay init                # write an audit.yml and nothing else
```

**Reading what you have** — no key, no network

```bash
assay scan                # parse coverage, and what could not be read
assay check               # every finding, structural and judged, ranked by blast radius
assay check --verify      # ...and COUNT each flagged hop's join key through your own
                          #   dbt. A join onto a key that is unique IN THE DATA cannot
                          #   fan out, and dbt only knows which keys are DECLARED unique.
assay check --json        # an OBJECT, not a list: {coverage, parse_failures,
                          #   unevaluable_tests, findings}. Iterate `["findings"]`.
assay inventory           # what every model IS; --html writes a page you can commit
assay trace <column>      # where one column's value actually came from
assay tests               # tests that cannot fail, and what nothing asserts at all
assay tests --count-defaults  # ...and how often each COALESCE default actually wins
assay practices           # dbt-project-evaluator violations, with judged exceptions
assay completeness        # do we have all of it? sources nothing reads, feeds behind their
                          #   own declared freshness, hops that lose most of the parent
assay completeness --verify   # ...and count empty models and row loss through your own dbt
assay patch tests/assay   # WRITE the uniqueness tests it can prove will pass
```

**The judged tier** — needs a key

```bash
assay claims --extract    # turn your prose into claims
assay verify              # check each claim against the code
assay traverse            # judge every hop in the graph
assay columns             # what each column MEANS
assay semantics           # why each filter is there; whether descriptions still hold
assay infer               # grain, where code could not settle it
assay align               # two columns in different models that mean the same thing
assay feeds               # has a source column changed its meaning? (needs the probe)
assay adjudicate          # triage the rows a dbt test already failed
```

**Turning findings into verdicts, without a turn each**

```bash
assay review --emit review.html -t target/   # the form, with everything in it
assay review --emit review.html -t target/ --monitoring volume.json --report assay.html
assay review --load verdicts.json            # every verdict at once
```

The form is not only findings. Five more tabs carry the parts of `audit.yml` that are pure domain
knowledge, and **Words comes first**: a verdict settles one finding, while a vocabulary term
reaches every judged answer about every model it applies to.

| tab | what it holds |
|---|---|
| **Words** | their vocabulary, plus candidates ranked by how often this warehouse joins on them. assay fills in what it measured — how many models name the word, which directories they sit in, what the lint says, a scope that resolves — and leaves `means:` empty, because a definition written from a model name looks exactly like one they chose |
| **Explanations** | the per-mart options for failing-row adjudication. `config.py` calls this "the part of the file worth maintaining" in its own comment, and nothing had ever let anybody maintain it |
| **Waivers** | findings somebody already called fine, carrying the reason *they* typed. assay never invents one |
| **Monitoring** | the staleness threshold, DERIVED from how often this project actually builds rather than picked, with the cadence it came from set beside it, so changing it is a disagreement with a measurement. Needs `--monitoring volume.json` |
| **Settings** | the rest of `audit.yml` a person acts on — the gating floors, the row-loss threshold, the spend cap, the rate card — each carrying what assay ships, what this project set, and what happens if it is wrong |
| **Findings** | the cards, as before |

What they write comes back as a **proposal**, never a write. `assay review --load handback.json`
records the verdicts and prints the `audit.yml` changes as a diff; `--apply` writes them. It edits
lines rather than re-serialising, so comments, key order, blank lines and quoting all survive —
measured on a real 253-line file: 53 comment lines, identical before and after. A path it cannot
place unambiguously is refused with the YAML to paste, because a config editor that writes
something approximately where it belongs is worse than one that says it could not.

It writes `vocab`, `explanations` and `waivers` and nothing else. Gating thresholds want the
measured agreement rate in front of you, and `assay effectiveness` is that surface.


The one number a release cannot move is findings a person has ruled on, and on the warehouse this
was built against it sat at **0 of 159** for months. Not for want of `assay review -i`, which has
shipped for most of this project's life. Ruling meant leaving the conversation you were already in,
and one turn per finding is 159 turns.

So the reading batches and the answering leaves the conversation. `--emit` writes one self-contained
file that opens from `file://` with no server and nothing running: twenty cards at a time, highest
blast radius first, each carrying what assay found, the claim it quotes, the model's own SQL with
line numbers, and any reading an agent already recorded. Answers are kept in the browser as you go,
so the tab can be closed. The download button writes `verdicts.json` and `--load` records the lot.

The round trip is `probe --emit` / `--load`, which this project already has, for the same reason one
layer over: assay never holds a credential, and it never holds a verdict it was not given. **A card
nobody answered is never submitted and never recorded**, and `--load` names every row it did not
record rather than printing a total that hides them.

**One card per (model, check), because that is what a verdict covers.** 260 findings are 212 cards
here. Cards per finding would have asked 48 of them twice and kept both answers.

`--reads <json>` pre-fills the agent's own read, keyed by `<subject>::<check>`. Reading the SQL for
every finding is the expensive half and it is what makes each card cheap to answer; done once,
offline, it turns a form somebody closes into a form somebody answers. The emit line says how many
cards already carry a reading and how many are a cold start.

**A finding you have read and called wrong does not come back**

This is what makes reviewing compound, and it did not work until 0.35.0. Recording a human
`disagree` and running `assay check` again gave the same count — 115 before, 115 after — because
`apply_policy` consulted waivers and scope and never looked at the rulings. The only thing that
ever removed a finding was a hand-written waiver in `audit.yml`. So reading 115 findings and
ruling every one of them wrong bought nothing.

Now a `disagree` from a **person** removes the finding, and the run says who dismissed it and why:

```
1 finding(s) dismissed -- read and called wrong, so `assay check` will not raise them again.
```

Four things it deliberately does not do:

- **`agree` does not remove anything.** It means the finding is RIGHT.
- **`unclear` does not remove anything.** It is evidence about the question, not a verdict on the
  model — disagreement means the criteria are wrong, unclear means the state does not carry what
  the question asks, and they are fixed by different edits.
- **An agent's ruling never dismisses.** An agent that could dismiss could silence a project by
  reading none of it carefully. Agent rulings triage what a person should read first.
- **A model-level ruling does not clear the model.** One model carries eight findings of one
  check, and `dim_business` was ruled a union false positive while two of its six edges really
  did fan out 1.48x. Dismissal is keyed to the FINDING.

**And it lapses on its own.** A finding's id hashes the check, the subject, the summary and the
non-measured evidence, so it survives a rerun and changes when the substance changes. Edit the
model into a genuinely different defect and the dismissal does not follow it — the guarantee a
waiver needs an expiry date to approximate, for free.

`assay review --load` writes both: the verdict against `(model, check)`, which is what
`calibration` and `effectiveness` measure, and the dismissal against the exact findings the card
showed.

**Your own questions become findings**

A family declared in `assay_questions/*.yml` names the answers that are defects with
`finding_when:`, and `assay check` produces a finding for each one — in the same stream, with the
same gate discipline, as a shipped family. It carries `rests_on`, so it cannot fail a build until
people have ruled on it.

Until 0.38.0 it could not. `judged.run_all` built findings from five hand-written functions, so a
YAML family was askable, answerable, storable, printable by `assay ask` — and invisible to `check`.
Meanwhile the linter made omitting `finding_when:` an error, on the grounds that without it a
question is "asked, paid for, stored, and produces no finding", which was true in `check` however
it was written. On the warehouse this was built against that was 42 and 21 findings nobody could
see.

**Settling things by counting, through your own dbt**

```bash
assay probe --dry-run     # the SQL it would run, run nothing
assay probe               # run it, via `dbt show --inline`. assay never holds a credential.
assay probe --sample      # count a sample rather than the whole relation
assay probe --emit        # ...or take the SQL, run it yourself, and --load the results
```

Relations are batched into one statement, because dbt's startup is the cost and the count itself
is milliseconds: 271 statements became 23 on a 358-model warehouse, and 24 relations went from
about seven minutes to 46 seconds. The catalog is read first — a metadata query that scans
nothing — so a statement can only ask for columns the warehouse actually has, and a batch that
fails is bisected rather than discarded.

**Ruling on what it found**

```bash
assay review -i           # a / d / u / s, least certain first
assay disagreements       # N rejected findings, how many separate bugs? free
assay disagreements --judge  # ...also asks whether differently-worded reasons are one defect
assay calibrate           # the grain judgment against the keys the project ALREADY declares.
                          # Every `unique` test is a human statement of a key, so the labeled
                          # set is free and already in the repo: a confusion matrix rather than
                          # an impression, which is the only thing that earns a question the
                          # right to fail a build.
assay calibration         # agreement BANDED by confidence, per family, per source.
                          # A choice bands by `confidence`; a noul by its ANSWER
assay effectiveness       # did the questions get BETTER? agreement per family, per version
assay effectiveness -t target/   # ...and reasons that STOPPED firing, which is the only
                          # measure here that moves without anybody re-reading
assay effectiveness --json
```

**From what it found to what you should therefore configure**

```bash
assay suggest -t target/          # candidates, each with the measurement behind it
assay suggest --section vocab     # one section at a time
assay suggest --out drafts.yml    # ...and a worksheet to edit in place
```

This is the step onboarding was missing. `guide` explains what a vocab term is for, `init` writes
defaults, and nothing went from 257 findings to the four lines of YAML that would settle sixty of
them. Seven rules, each reporting what it measured: columns **shared across the most models** and absent
from the vocab (`section_id`, 24 models, 65 hops on the field warehouse), columns named like a key
that are *nearly* unique and so pass every spot check (`incident_id`, 19,566 distinct in 19,628
rows), one reason given on several subjects **that still fire**, `disagree` rulings that nothing
waives, per-family agreement from the rulings you already gave, and checks firing that `audit.yml`
does not name.

Vocabulary candidates rank on models first and hops as the tiebreak, and the headline leads with the
number it sorts on. Ranked on `hops x models` the list ran 65, 32, 40 -- a correct ordering that
reads as a broken one. Fixing the legibility fixed the ranking, because the product was answering
the wrong question: a term is worth writing when it is SHARED, and the payoff is every judged
question that carries it. `city` at 40 hops across 4 models is one team's local habit; `geom` at 32
hops across 19 is nineteen places that need the same word to mean the same thing.

**It proposes the candidate and the measurement. It never proposes the meaning.** `means:` and
`implies:` arrive empty, with the evidence underneath them. A plausible vocab block written from
model names looks exactly like knowledge, is not, and then rides along with every judged question
from that point on.

**A repeated reason has to still be true.** A cluster needs at least one subject that produces a
finding *today*, and the first version left that check out. The result inverted the whole queue: a
cluster grew more prominent the more successfully it had been fixed, because it ranked on how many
subjects had once been ruled on. On the field warehouse the top two items were clusters of 8 and 2
subjects with zero live findings between them -- both already repaired, one by the structural fix
the item's own text cites -- while six models were firing that check and none was in the queue. It
is the mirror of the 0.24.0 defect: that one hid live evidence, this one promoted dead evidence.

Resolved clusters are not deleted, they are moved. `assay effectiveness -t target/` reports them:
"this reason was given on 8 models and fires on none of them today" is the only improvement measure
there that does not need anybody to re-rule. Everything else on that screen moves when a person
reads again; this moves when the check stops being wrong.

Without `--target`, `suggest` cannot tell a live cluster from a repaired one and says so, rather
than dropping them (which reads as nothing to decide) or keeping them (which reads as all of it
being live).

Two rules deliberately refuse to finish the job. A reason repeating across subjects points at a
missing vocabulary term *or* at a case the check gets wrong, and those go in different files; on
this project that distinction went the second way, and the fix was structural (0.15.0, 0.21.1)
rather than a third waiver. And where there is no measured agreement for a family, `suggest` says
so and proposes nothing, rather than falling back to the shipped default and reading as measured.

Rulings whose `source` is `label` are excluded from every rule that needs a reason: they are the
project's own declarations read back as verdicts, and their note is a generated stub. On the field
warehouse that is 26 of 38 disagreements, which left in would have drafted 26 waivers justified by
the sentence "the project asserts out".

**Reading an answer against what produced it**

```bash
assay evidence -q <question> -s <model>   # the exact state a judged answer was computed from
assay evidence --key <decision_key> --json
```

An answer without its input can only be believed, not checked. Every judged answer is a function
of a state assay assembled and then threw away, so a disagreement was unresolvable: nobody could
tell whether the judge was wrong or whether it had been handed the wrong facts. Those are opposite
repairs -- one edits the question, one edits what gets sent -- and picking between them was
guesswork. States are stored keyed by their hash, so one state reused across a thousand answers is
stored once.

An answer from before state storage says so, in those words. Its absence is never rendered as an
empty state.

**From "this is real" to "it is fixed"**

```bash
assay plan -t target/            # what to DO about the findings a person agreed with
```

`check` finds it, `review` settles whether it is real, and then there was nothing. Somebody
holding forty agreed findings has forty sentences about what is wrong and no statement of what to
change — the same gap `suggest` closed on the config side.

The fix **shape** falls out of the check name exactly, so it is a lookup and costs no calls:
`arbitrary_pick` is always "add a tie-break column", `test_cannot_fail` is always "the test
asserts nothing, remove or repair it". Nothing about the particular model changes the shape of its
repair. What `plan` will not write is the **words** — what a sentence should say instead is a
judgment about a real warehouse, which is the same two-tier split as everything else here.

It writes `assay_plan.jsonl`, one object per thing to do, because the consumer is an agent rather
than a person reading a report once. Only findings a person agreed with, and only ones still
present: a plan built from every finding is the findings list again.

**And whether the loop closed**

`check` has always printed "N new, N resolved" against the previous run, and that cannot answer
the question worth asking. Four fewer findings might be the four somebody agreed about, or four
unrelated ones that moved while those four sat there — from outside those look identical, and the
second is what it looks like when reviewing changes nothing.

So `check` also reports:

```
of the 12 finding(s) a person agreed with, 5 are gone and 7 are still here.
```

**The only number on that screen that measures the loop rather than the tool.** A release cannot
move it and neither can an agent. It needs the finding, not the model, which is why
`assay review --load` records an `agree` against each finding id a card showed — a verdict filed
against `(model, check)` cannot say which of that model's eight findings was the real one.

Agreeing and later dismissing does not count as fixed. That is a retraction, and counting it would
make the one honest number gameable by the person it measures.

The ten tables, what each answers, and how they join are in **[SCHEMA.md](SCHEMA.md)**, with an ER diagram.

**What it has cost, and what has gone stale**

```bash
assay cost                    # by caller, by question family, by day
assay cost --since 2026-09-01
assay cost --json

assay stale                   # judged answers about SQL that has since changed
assay stale --cost            # ...and what re-asking them would cost, before you spend it
```

`jev.max_spend_usd` caps a run and dies with the process. `assay cost` is the ledger, and
`assay page` carries the same figures as its **Spend** tab, beside the answers they bought: one row per
CALL in `model_calls`, written at decide time with the rate that was in force, so a price change
never rewrites what was already spent. It does not estimate -- a call the provider returned no
usage for is excluded and counted, and output tokens are shown and never priced because Jev does
not bill them.

**Warehouse spend is a separate ledger, and only one of its clocks is billable.** A statement's
wall time includes dbt's startup and your network, and calling that warehouse time overstated a
batch by 263x. `exec_ms` is its own column, recorded only where the adapter reports it, and it is
the only one a credit rate is ever multiplied by. Bytes scanned come from the catalog where the
engine exposes them; a statement nothing could estimate is counted as **unestimated** rather than
priced at zero, because a zero reads as *this was free*.

The obvious derivation is wrong and this is why the table exists. `model_decisions` is one row per
ANSWER and carries its CALL's token count on each of them, so summing it counts a batched call
once per answer: on a real store that is $4.25 against $1.32.

`assay stale` reads the sha256 dbt already records for every model's source file and compares it
to the one stored on the answer. No call, no warehouse connection. It is necessary and not
sufficient, and says so: a comment edit trips it and a change to a PARENT does not.

`--exact` is the one that catches the parent. It rebuilds the state each answer was computed from,
through the same builder that produced it, and compares the hash -- still with no call. That only
works because there is exactly one path that builds a state: `states.py` registers a builder by
name and records the identifiers it was built from, so `make()` and `rebuild()` are the same
function call. Before that module existed, rebuilding all 871 model and edge subjects of a real
warehouse reproduced **0** of the stored hashes -- not drift, just a state nothing could produce
twice. It is 871 of 871 now, and a test proves it for every builder rather than asserting it.
Three states carry rows read out of the warehouse -- a feed's sample, a failing row, a practice
check's output -- and are reported as *not comparable*, by name, with the reason.

A stale answer
is still served everywhere it was served before -- hiding it leaves the caller with nothing, which
is strictly worse than serving it dated. An answer with no recorded checksum reports as **cannot
be checked** and is counted in its own column, never added to the current ones.

**Where a word is true**

```yaml
vocab:
  section_id:
    means: "the geographic join grain, and NOT always a PLSS section"
    # no applies_to: true everywhere, which is what every term meant before this existed

  water_division:
    means: "a Colorado water court region, 1 through 7"
    applies_to:
      select:  "path:models/water"
      exclude: "path:models/water/az"
```

A vocab term goes into the state of EVERY question, which is why it improves answers to questions
you never wrote -- and why a term that is false here steers every answer wrong at once. Measured on
the warehouse this was built against: 16 terms, six of them asserting one state's water law
including a statute citation, sent to all 358 models. **4,997 of 19,707 judged answers -- 25% of
everything ever paid for there -- were about models in a different state**, and every one of them
was told that prior appropriation decides who gets water.

`applies_to` takes the same selector `--select` and `when.select` take, validated by the same
validator, which refuses syntax it does not understand rather than matching everything. It also
takes `{select:, exclude:}`, because the exception usually lives inside the rule: those Arizona
models are `models/water/az`, *inside* `models/water`, so a bare `path:models/water` would reach
every one of them and the scoping would read as working.

A state about several models at once -- a chunk of column pairs, a chunk of tests -- gets the terms
true of **all** of them. Not the union: a batch pairing one jurisdiction's model with another's
would otherwise be told both regimes in a single call, which is worse than saying nothing. Every
drop is counted and printed, because a vocabulary quietly thinning looks exactly like one that was
never wired up.

`assay config --target <dir>` lints it, and `--strict` exits non-zero:

- **error** -- a term with no `means`, or a scope that matches no model. A term scoped to nothing
  reads as defined and defines nothing.
- **warn** -- a term citing a statute or naming a state with no `applies_to`.
- **warn** -- a term whose word appears only under one directory while the term is sent to the
  whole project. This is the rule that catches a *doctrine*, which no keyword list ever will:
  `conditional` asserts one state's law and names no state. The message writes the selector for
  you, including the `exclude`, and a test parses the suggestion and runs it -- the first version
  proposed an exclusion with no `path:` prefix, which `selector` reads as a model name and which
  therefore subtracts nothing.
- **warn** -- a term no model in the project mentions at all. Not wrong, not free: it rides along
  in every state on every call.


## Volume: read what Elementary counted, never rebuild it

```bash
assay volume                       # what it counted, joined to what your project claims
assay volume --judge               # ...and whether a movement contradicts one of those claims
assay volume --dry-run --judge     # the state and the cost. Sends nothing.
```

`observed_keys` counts uniqueness and nulls on the relations somebody probed. Nothing in assay has
ever tracked a **row count over time**, so "this table halved last night" was invisible — and
Elementary, where it is installed, has been recording exactly that per table per bucket all along.
Same treatment `practices` gives dbt-project-evaluator: read the answer, join it to what assay
knows, reimplement nothing. Counted tier — it needs your dbt connection, the way `probe` does, and
assay still never holds a credential.

**The half assay adds is the half Elementary cannot have.** It detects with no semantics: *row
count fell 41%*. It has not read your prose, does not know the declared grain, and cannot see the
DAG. `volume_contradicts_a_claim` puts the counted movement, the sentence your project wrote about
itself, and the blast radius in one state and asks the only question neither tool can answer alone.
The counting is not re-litigated — it is given as fact.

**Six states, and none of them reads as "fine".** The spec named three; a real warehouse had six,
and the three nobody predicted are the ones that look most like success:

| state | what it means |
|---|---|
| package absent | volume is not measured here and nothing in this report covers it |
| installed, never run | Elementary is present and its models are not built — a different fix |
| one bucket | an anomaly needs two observations; one is no answer, not a small one |
| **abandoned** | the table is full and nothing has written to it for months. On the warehouse this was built against, `dbt_source_freshness_results` held 105 rows and had not been touched for 76 days while every other Elementary table was current to yesterday |
| **stale failure** | a test whose last result was a *failure* and which has not run since. In any Elementary view that is indistinguishable from something failing right now |
| **unreachable** | assay could not reach the warehouse, so nothing was measured. Found by shipping the other five and running it: `dbt` was not on the PATH, every statement failed, and the reader announced that nothing monitors volume — on a warehouse where Elementary had run an hour earlier |

**Five checks, all free of judgment**, about the monitoring rather than about your data:

| check | it says |
|---|---|
| `monitor_declared_but_never_run` | the monitor is configured and has never produced a result. Installed is not built, and both tools go quiet the same way |
| `monitor_ran_then_stopped` | the table has rows and nothing has written to it since. The threshold is **derived per relation** from its own write history, not picked |
| `volume_is_not_being_watched` | models that feed marts have no row-count history. One finding with the count and the worst by reach, not one per model |
| `test_declared_but_never_run` | declared tests that have never produced a result — 193 of 1,291 on the warehouse this was built against |
| `test_skipped_rather_than_passed` | `skipped` is not a pass. dbt skips a test whose model failed upstream, so a green run can hold a test that has not read your data in months |

**"Late" is longer than that relation has normally gone between writes, and nothing is multiplied
by an invented number.** A first version took the median gap and tripled it; three was made up, and
a made-up multiplier is a made-up threshold however it is dressed. The question is answerable from
the data: the 90th percentile of the gaps a relation has actually gone between writes. It has been
quiet that long before and carried on.

**Per relation, from its own history.** A source refreshed hourly and one refreshed monthly cannot
share a number. Where a relation has too little history of its own, it falls back to how often the
project builds — which is also measured, from distinct build *days*, because one pipeline run
issues many dbt invocations and the gaps between those describe how fast a job runs rather than how
often it runs. Where there is not enough history anywhere, assay derives nothing and says so: the
warehouse this was built against had a freshness table written on exactly one day, so nothing in
its own history can say what late means for it.

The rank rounds up, which matters more than the percentile on a short history: nearest-rank p90
over `[1,1,1,1,1,1,1,6]` returns 1, so a relation that has quietly gone six days would be called
late at two. Rounding up returns 6 there, and on a long history still steps below a single outage —
eighteen one-day gaps and one of seventy-three returns one day, so an outage does not license
another.

`assay volume` prints every threshold and where it came from, and
`monitoring.source_freshness.max_staleness_days` overrides all of them with one deliberate number.

These run inside `assay check --verify`, in the same findings stream as everything else, because
they need the warehouse the way `probe` does. **Without `--verify` they do not run and `check` says
so** — a deferral nobody is told about is a check that stopped looking, and a check that quietly
did not happen is indistinguishable from one that found nothing. `monitoring.enabled: false` turns
them off deliberately.

Run `assay volume --json > volume.json` and emit the review form with `--monitoring volume.json`,
and the derived number, the cadence it came from and the coverage appear as a **Monitoring** tab
somebody can disagree with in writing.

**It does not ingest Elementary's results as assay findings.** They are not assay's, and claiming
them would corrupt the one number that measures the loop — *of the N a person agreed with, M are
gone*. What assay does say is about the **monitoring**: a model with marts downstream that no
volume history covers, which only assay can say because only assay knows what rests on it.

Coverage is reported rather than assumed. On that warehouse, 120 relations have a row-count history
and **232 models with a mart downstream have none** — which is not "no volume problems".

**Keeping the store from growing forever**

```bash
assay prune --dry-run     # what it would drop, per table, and what it would never touch
assay prune               # drop findings, edge facts and unreadable rows from superseded runs
```

`prune` is explicit and never runs on its own. It drops only what a later run RE-DERIVES from the
same manifest -- findings, edge facts, the unreadable list -- and it will not touch the tables that
cost something to produce: rulings, claims, adjudications, observed keys, and the run log itself.
On a real store that is 3,563 findings down to 730 and 8,595 edge facts down to 1,719, with every
paid table byte-identical afterwards. The split is declared in `store.PRUNABLE` and
`store.NEVER_PRUNED` rather than inferred, so a new table is not silently prunable: it belongs to
one list or the other, and a test fails until it does.

A verdict is about a VERSION of a question, so the store keys on `(subject, question,
prompt_version)` and a re-ruling after a rewrite is kept rather than overwriting the old one. That
is what makes a before and after possible: `units_are_what_the_column_claims` went 2/4 to 8/8
across one rewrite, and until now that number lived in a markdown file somebody typed.

`model_version` is the second axis. It moves when Jev ships a new model, under questions nobody
touched, and it is the only way "our agreement fell and we changed nothing" is ever visible.

A disagreement is **open** until somebody agrees at a different version of the question. So the
count falls only when a question changed and a person re-read it, and no release can lower it.

`unclear` is not disagreement and is never in the agreement denominator. Disagreement means the
criteria are wrong. Unclear means the subject state does not carry what the question asks about,
which is what seventeen unclears on one warehouse turned out to be. Reword an option to fix one,
add a field to fix the other.

**On a branch, and over time**

```bash
assay diff --baseline <main target>        # what changed about what models MEAN
assay backtest --repo . --limit 200        # would this have caught YOUR past bugs?
assay version-check --baseline <target>    # does anything owe a version bump?
assay version-stamps                       # write the stamps
assay watch                                # rerun on save; print only what your edit changed
```

**Wiring it in**

```bash
assay page assay.html     # EVERYTHING assay knows: models, chain drawn as lineage, claims,
                          # findings, monitoring, answers, questions, config. One file, ten
                          # tabs. ALSO writes assay-data/ -- commit that, not the page
assay page assay.html --monitoring volume.json   # ...and whether anything is WATCHING it:
                          # build cadence, each monitor's freshness, what the declared tests
                          # are doing, and the models with no row-count history at all
assay page assay.html --form review.html         # the report and the form link to each other
assay page x.html --from assay-data/   # re-render from a committed artifact, no warehouse
assay page --plain        # ...just the record: is this warehouse understood, and by whom
assay export <dir>        # the tables, as seeds your own models can join to
assay mcp                 # the MCP server
assay skill --write       # the procedure your agent follows
```

## Configuration: audit.yml

`assay init` writes it commented, and every field is optional — the defaults are what runs without
the file at all.

| block | what it does |
|---|---|
| `jev:` | `provider`, `model`, `max_spend_usd`. The cap is per invocation and is estimated **before** the call, because a cap that fires after the spend is not a cap |
| `gating:` | `min_adjudications` — human verdicts a question needs before it may fail a build. 20 **per question**, not overall. `min_agreement` — how often those verdicts had to AGREE, measured on the version shipping now. Ships at 0, which is off: a count of wrong answers is still a count, but a floor set before anything was measured is a guess. Run `assay effectiveness` and pick one |
| `questions:` | per-**check** thresholds and actions. A key matching no check is reported, never silently ignored |
| `practices:` | how each dbt-project-evaluator rule is treated: enforce, recommend, adjudicate, off |
| `vocab:` | **what your words mean here.** Injected into the state for every question, which is why it improves answers to questions you never wrote |
| `explanations:` | the domain options for row adjudication, per mart. This is where your domain knowledge lives |
| `waivers:` | a reason is required and an expiry recommended. A waiver naming no real check is reported |

**Your key is never in this file**, because this file belongs in git. It comes from the environment
or a `.env`, and `assay config` says which.

**`--store` is assay's own file, not your warehouse.** It defaults to `assay.duckdb` in the working
directory and holds judgments, verdicts, claims and findings. `assay export` is how its contents
become relations *in* your warehouse.

## Your own questions

```bash
assay banks              # every question, where it came from, and whether its shape is sound
assay banks --strict     # exit non-zero on a warning too
assay banks              # ...and, for a question you replaced, which of its blocks are
                         #   byte-identical COPIES of the shipped one. A copy nobody
                         #   knows is stale reads as current. `forked_from: role.v2` in
                         #   your override and assay says when the shipped one moves.
assay banks --judge      # also ask whether any two options could both be right. ~a cent,
                         #   cached on the question, so it cannot flap in CI.
```

**Put a `.yml` in `assay_questions/`** — here or in any parent, or wherever `ASSAY_QUESTIONS`
points.

### A new family: declare a subject and `assay ask` runs it

```yaml
# assay_questions/mine.yml
version: 1
seniority_ordered_by_the_wrong_date:
  id_prefix: "senior"
  type: choice
  subject: window          # model | edge | column | predicate | expression | window
  # subject_state: minimal # omit `what_one_row_of_this_model_is` where your criteria already
                           #   reason about something narrower, like a window's partition
  finding_when: [ordered_by_adjudication]    # which answers are findings
  prompt_version: "senior.v1"
  instructions:
    question: >-
      Seniority runs from the APPROPRIATION date, not from the adjudication date, which is only
      when a court confirmed it. Does this window rank rows by the wrong one of those two?
  criteria:
    ordered_by_adjudication: {what: "It orders by an adjudication, decree or court date."}
    ordered_by_appropriation: {what: "It orders by an appropriation or priority date."}
    not_about_seniority: {what: "This window ranks something that is not a right's priority."}
    cannot_tell: {what: "The ordering columns do not say which kind of date they hold."}
```

```bash
assay ask --dry-run    # count the subjects, estimate the cost, print one state, spend nothing
assay ask              # run every family that declares a subject
```

**Check the subject count before you run it without `--select`.** `subject: window` is tens of
subjects; `subject: expression` is thousands — 2,848 on a 265-model warehouse. `assay ask` prints
the count and an estimate for every family before it asks anything, and **refuses outright** when
the estimate exceeds `jev.max_spend_usd`, rather than discovering it mid-run.

```
mixes_conditional_with_absolute  expression · 2848 subject(s) · ~$0.0329
  refused before spending anything: ~$0.03 exceeds the $0.01 cap in audit.yml.
```

**`finding_when` is not optional in practice.** Without it a family is asked, answered, paid for
and stored, and produces no finding and gates nothing — the dead-question problem wearing a new
hat. `assay banks` reports its absence as an **error**, not a note. It is a real mode (the answers
still fill the inventory and `trace`), so it can be acknowledged deliberately:

```yaml
acknowledge:
  finding_when: "these feed the inventory; nothing should gate on them yet"
  multi_hop: "one hop cannot distinguish the shapes; kept deliberately"
```

**A reason is required**, exactly as it is for a waiver, and `assay banks` prints every
acknowledgment with its reason rather than hiding it. A rule silenced without a reason is how a
finding goes to die — and a lint with no way to say *"I know, and here is why"* gets muted
wholesale instead.

That exact question, on 51 windows of a real water warehouse: **$0.0015**, 3 flagged at p=1.00, 4
correctly read as ordered by appropriation.

`finding_when` is what makes it reach `assay check` alongside everything else. Without it the
answers are still stored and still fill the inventory — they simply gate nothing, and
`assay banks` says so.

**Subjects `expression` and `window` exist because the field asked for them by name** and there was
no call site for either. `assay ask --dry-run` prints the state a subject receives, which is the
fastest way to see whether your question can be answered from it at all.

### Or replace a shipped family

**Replace a shipped family. Do not invent a new name** unless you declare a `subject:`. Every call site asks for a shipped family
by name, so a family with a new name is loaded, linted, listed by `assay banks` — and never asked
by anything. It looks exactly like coverage. `assay banks` now prints `nothing asks this` in red
beside any such family, and this documentation used to show the wrong pattern.

The `about` column in `assay banks` tells you which state each family receives, and a replacement
can only ask about what its caller already builds. Pick the one whose subject matches yours:

| you want to judge | replace |
|---|---|
| a parent → child edge | `edge_preserves_the_grain` |
| a chunk of columns | `column_role` or `null_meaning` |
| a chunk of predicates | `predicate_intent` |
| one claim against its evidence | `claim_alignment` |
| a model's prose against its code | `description_contradicts_the_code` |
| one failing row | `row_explanation` |
| a column and a sample of its values | `field_matches_its_name` |

```yaml
# assay_questions/mine.yml -- REPLACING a shipped family, keeping its options and adding one
version: 1
edge_preserves_the_grain:
  id_prefix: "edge"            # keep the shipped prefix: verdicts file under it
  type: choice
  prompt_version: "edge.water.v1"   # your own; the cache is keyed on it
  instructions:
    question: >-
      Given what the child joins on, does one row of the child still mean one of the same thing as
      one row of the parent?
  criteria:
    same_thing: {what: "One child row is still one parent row."}
    deliberately_coarser: {what: "One child row is many parent rows, and a group by says so."}
    silently_multiplied: {what: "One parent row becomes several, and nothing declares it."}
    wrong_scope_entirely:
      what: >-
        One row, the right COUNT, the wrong INSTANCE: the match used an identifier that is only
        unique inside a scope the join condition left out.
      examples: ["joined on case_number where a case number is unique only within a division",
                 "joined on section without the principal meridian"]
    cannot_tell: {what: "The join keys are not visible enough to decide."}
```

That last option is the thing a replacement does that `vocab` cannot: **no amount of vocabulary
makes a model pick an option that is not on the list.** Over 543 edges on the warehouse this was
written for, it fired three times, all correctly, at 0.21–0.33.

### The lint is every shape already measured to fail

`assay banks` checks your questions against what Jev is bad at, and it is **not** style advice.
A badly shaped question does not error — it answers confidently and uselessly, which is worse.
`keys_on_a_non_unique_column` read 0.73 to 0.85 on every model tested, clean or broken, and looked
like a working check for weeks.

| rule | why |
|---|---|
| `not_a_calculator` | *"Jev is not a calculator."* Asked whether a date expression implemented "the last day of the second month following", it scored the **correct** one 0.39 and a **wrong** one 0.62 |
| `dates_are_text` | it reads dates as text, not as ordered quantities, so comparisons and durations are unreliable |
| `multi_hop` | one lumped question over three rules read 0.64 where the split rule that applied read 0.85 |
| `no_match_option` | without one the model must pick a wrong answer. A real division bug surfaced **only** because it could say "not on the list" |
| `options_not_separated` | two options described alike give the model nothing to cut on; assay's own pair sat at 0.36–0.39 until they were merged |
| `option_routes_to_another` | an option whose description names ANOTHER option is routing, and the answering model may not honor it. Measured: a question doing this passed the judged overlap check at 0.63 while the model put one subject under both options at 0.55 and 0.63 |
| `options_overlap` *(`--judge`)* | the static rule compares WORDS. Two options can share a **situation** and no vocabulary: a real pair scored 0.25 against a 0.75 threshold and passed, while both correctly described the same window. This asks Jev instead, and it independently flagged `predicate_intent` — already proven weak by hand |
| `state_size` | unrelated detail is a distractor: one correct extra sentence took a claim from 0.96 to 0.47 |
| `id_prefix` | two families sharing a prefix means one silently absorbs the other's verdicts |
| `level_names_nothing` | a score answer can land **between** levels, so "medium" describes nothing |

**It checks the shape, not the answer.** Only running a question against cases you have already
ruled on tells you whether it is right — and the linter was itself calibrated that way: run against
assay's own fifteen hand-tuned banks it flagged four, and all four were the linter being wrong.

## From flagging to changing something

Most of assay tells you. One part of it writes:

```bash
assay patch tests/assay --dry-run   # what it would write, and why it refuses the rest
assay patch tests/assay             # write them
dbt test --select path:assay        # they pass today
```

**Every grain is counted through your own dbt before a file exists.** `count(*)` against
`count(distinct <grain>)`, batched. A grain that was not counted, or was counted and did not hold,
does not become a file and the reason is printed:

```
  water_rights: it does not hold: 1,045 rows, 7 distinct (149x). A test here fails on its first run
  int_azcc_owners: NOT COUNTED -- and an uncounted grain is not a passing one
  fact_sale: the grain names parcel_id, which the model does not emit
```

That is the whole difference between a patch and a nag. Before the counting landed, a real project
got 15 proposals and **0 of them held**.

**Singular tests, not `schema.yml` entries.** On that project 344 of 358 models already had a
schema yml entry, and a second entry for the same model is a dbt compilation error — a `schema.yml`
patcher would have broken 96% of what it touched. A `.sql` file needs no entry anywhere, collides
with nothing, and needs no `dbt_utils`.

**It never overwrites a file it did not write.** Every generated file carries a marker; one without
it is left alone and reported. The only thing worse than a bad generated test is one that ate a
good handwritten one.

And each file says what justified it, so whoever finds it in six months can judge it without
finding assay:

```sql
-- assay proposed this grain and COUNTED it before writing this file: 412,889 rows,
-- 412,889 distinct. It held, so this test passes today. It is here to catch the day it
-- stops holding.
--
-- assay did not decide this was the RIGHT grain -- it inferred it from the SQL and checked
-- the arithmetic. If one row is something else, delete this file and say so in the model's
-- own documentation, where `assay claims` will pick it up.
```

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

```bash
assay regress    # re-ask every answer a person agreed with; report what moved
```

**And verdicts are the only regression test assay has against a real bank.** Reported from the
field: an upgrade to the subject state moved **two of eight** verified answers on one family — and
the answer *distribution* barely moved, 79 of the same answer either side. No summary this tool
prints would have shown it. Eight rulings on record did. Run `assay regress` after upgrading assay,
after editing a question, and after changing `vocab`. Unchanged states are cached and cost nothing;
it exits non-zero when something moved.

### An agent can rule. It cannot rule with authority.

`rule()` lets an agent record what it concluded after reading a finding and its SQL. That matters
because **rulings are the only thing in this system that do not compound**: more checks find more,
better states judge better, the warehouse accrues — and none of it raises the number that says
whether anything was *understood*.

An agent ruling is filed as `agent` and **cannot** gate a build, satisfy `min_adjudications`,
anchor `assay regress`, or move the ruled-on figure. All four filter on `source = 'human'`, and
that is the point: the ruled-on number is the one nobody can game, and an agent able to raise it
would destroy exactly the property worth printing.

What it does is triage. Sixty-nine models with a finding and none read is a wall; six an agent
believes are real is a place to start. `assay review -i` shows its reason beside the finding, and
your keypress is still the only one that counts.

**A verdict is a regression test for the question, before it is ever a license to gate.** Reported
from the field: after rewriting a question's criteria, 8 of 8 recorded verdicts still agreed — the
first time that session could change a question and know immediately what it had *not* broken. That
cost eight keypresses. `min_adjudications` is the second reason to record them; this is the first.

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
- uses: ryan-sunny/dbt-assay@v0.49.1
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

writes **two** procedures and prints the MCP line:

| skill | when it runs |
|---|---|
| `.claude/skills/dbt-assay/SKILL.md` | what an agent follows around an edit: call `contract` and `claims` before, `changed_contracts` and `violations` after |
| `.claude/skills/assay-review/SKILL.md` | walking the findings WITH the person whose warehouse it is, one at a time, and recording their verdicts |

The second one exists because of a number that would not move. `assay review -i` has shipped for
most of this project's life, and the field warehouse still read **0 of 159 models ruled on by a
person**. The tool was never missing. Ruling meant leaving the conversation you were already in, so
it did not happen, and every gate that needs human verdicts stayed shut. The procedure does the SQL
reading first and presents one finding at a time with claim, code and line numbers, so the call
costs ten seconds; it never records anything that was not actually answered.

It also says plainly what the label does not prove. `assay review --verdict` writes
`source = 'human'` because of the code path taken, not because of who ran it, and `--by` is free
text that fills `decided_by` and defaults to `unknown`. Nothing validates either. The honesty of
whoever runs it is the whole mechanism, and the skill says so rather than leaving a reader to
assume something checked.

Either can be emitted on its own:

```bash
assay skill review            # to stdout
assay skill all --write .     # both, under .claude/skills/
```



```bash
claude mcp add assay --scope project -- uvx --from 'dbt-assay[mcp]' assay mcp --target target
```

`--scope project` writes `.mcp.json` **in the repo**, so the server travels with a clone and works
from any directory inside it. Without it the entry goes to `~/.claude.json` keyed to one absolute
path on one machine: invisible to a session started one directory up, and absent entirely from a
fresh clone. If you already added it the other way, `claude mcp remove assay` clears the local
duplicate, which otherwise wins in that one directory.

```bash
# the --from matters: a bare `uvx dbt-assay` skips the optional extra and the
# server exits before it can tell you why
```

The MCP server gives an agent the **ability** to check itself: it can ask what a model means, what
a column is, what the grain is, and what would break before it writes a line. The skill file gives
it the **obligation** — without it an agent checks when it remembers, and with it, checking is the
procedure.

Twenty-one tools. Most report; nine do something else:

| tool | what it is for |
|---|---|
| `guide(topic)` | how to SET assay up — vocabulary, questions, waivers, policy. Read before writing anything into someone's `audit.yml` |
| `plan(limit)` | **what to change**, for the findings a person agreed with. `fix_shape` is the KIND of change, looked up from the check name; the words are not in it |
| `suggestions(section)` | **what to put in their `audit.yml`**, derived from what the checks found, each row carrying its measurement. Every `means:` and `implies:` comes back empty and must stay empty |
| `evidence(question, subject)` | the exact **state** a judged answer was computed from. Call it before disagreeing with one: if the answer is wrong and the state is wrong, what gets sent needs fixing; if the state is right, the question does |
| `vocabulary()` | their words, **where each one is true**, and everything wrong with the list. A term goes into every judged question's state, so one asserted outside where it holds is wrong in every answer about that part of the project at once — measured at 25% of one real warehouse's answers. Call it before writing or editing a term |
| `spend()` | what the judged tier has **cost** here, by caller and by day. Put it in front of somebody before proposing a judged run |
| `load_handback(path)` | record the verdicts a **person** wrote in the review form. The only tool that files `human` verdicts, and it can only file what the file carries — the agent is the courier, not the reviewer. Without it the form downloads and the most valuable work in the system sits in a folder |
| `stale(exact)` | judged answers about SQL that has **since changed**, so an agent knows whether an answer it is about to rely on is still about the code in front of it. `exact=true` rebuilds the state and catches a change to a *parent*; neither form makes an API call |
| `monitoring(volume_json)` | **is anything watching this warehouse** — build cadence, whether each monitor is still being written to, how many declared tests have ever produced a result, tests whose last result was a FAILURE and which have not run since, and the models with a mart downstream and no row-count history. It reads a file from `assay volume --json` and never a warehouse; with no path it returns the command, because a zero here reads as *nothing is wrong* and means *nobody looked* |

Each has a CLI equivalent, because a skill with no MCP connection is still a procedure:
`assay guide`, `assay plan`, `assay suggest`, `assay evidence`, `assay config --target`,
`assay cost`, `assay stale`. The skill file carries the mapping.

### What a finding hands the agent

The point is not that assay writes the fix. It is that an agent asking `findings(model)` gets
everything needed to write it, and everything needed **not to break something else**:

| | |
|---|---|
| `file` | where to open |
| `evidence` | the exact construct — the window's partition and sort keys, the predicate, the columns. Not a description of it |
| `downstream`, `marts` | how carefully to tread. A leaf is not a model 19 marts read |
| `detail` | why it is wrong, and what shape the fix takes |
| `rule()` | **record what the agent concluded after reading.** Filed apart from a person's verdict: it triages what to read first and gates nothing |
| `violations()` | **what would actually fail a build**, under your own `audit.yml`, using the same policy CI applies |
| **`must_stay_true`** | **the claims this project makes that the code currently supports, and the verdicts a person recorded** |

That last row is the one that matters. **A fix is not finished when the finding goes away — it is
finished when those are still true.** Satisfying a check while making a claim false is a worse
state than the one you started in, and it is the specific failure an agent is most likely to
produce. `assay regress` then says which recorded verdicts the edit moved.

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
judgment and the question — never your data, never your rows.

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
