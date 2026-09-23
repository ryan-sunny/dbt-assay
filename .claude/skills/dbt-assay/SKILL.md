---
name: dbt-assay
description: >-
  Use before and after editing any dbt model. assay knows what every model in this project MEANS --
  its grain, what each column is, where each value comes from, and what breaks downstream. Call it
  instead of guessing, and call it again to check your own work before handing it back.
---

# Working on dbt models in this project

assay is running as an MCP server. It has already derived what every model means. Use it; do not
re-derive it by reading SQL, and do not guess.

## If it is not set up in this repo yet

Three commands, in this order. Nothing here costs anything: no key, no network, no spend.

```bash
uvx --refresh --from 'dbt-assay[mcp]' assay onboard --target target/
claude mcp add assay --scope project -- uvx --refresh --from 'dbt-assay[mcp]' assay mcp --target target
uvx --refresh --from 'dbt-assay[mcp]' assay skill all --write .
```

`onboard` reads the project and says what would degrade the answers here -- no compiled SQL, no
catalog, a guessed dialect -- BEFORE it shows a finding, and prints the command that fixes each
one. `skill all --write .` lays these procedures into `.claude/skills/` so the next agent has
them. **`uvx --refresh`, always**: without it uvx serves a CACHED environment while the process
reports itself as the version you asked for, and the symptom is a page that renders blank.

`assay --help` lists every command; the table at the end of this file has all of them with every
flag.

## Before you touch a model

1. `contract(model)` — what one row is, what each column does, where each value comes from.
   Fifteen lines instead of two hundred of SQL.
2. `blast_radius(model)` — who reads it, and how many marts are downstream. If this number is
   large, say so before you change anything.
3. `claims(model)` — **what this project SAYS this model does**, sentence by sentence, with what
   its own code did to each claim. Reading the SQL tells you what the code does; this tells you
   what a person asserted it does, which is the thing your edit is most likely to quietly break.
   A claim marked `supports` is a promise you are now responsible for keeping.

4. `traversal(model)` — every hop into this model, what each one joins on, and whether any of them
   multiplies rows without declaring it. This is the defect class you cannot see by reading one
   file, so do not try to.

5. `lineage(model, column)` when you are about to change a column — it tells you the hop that
   actually produces the value, which is often several models upstream.

## After you edit, before you hand anything back

6. `changed_contracts()` — **this is the step that matters.** It says whether your edit changed
   what anything MEANS, as opposed to how it reads. A reformat, a renamed CTE, a join rewritten as
   a subquery come back empty, and that silence is correct.

If it reports a grain change, stop. Either the change was unintended and you should undo it, or it
was intended and it needs a version bump. Do not hand back work where the grain moved silently.

7. `findings(model)` — the contradictions assay sees in what you just wrote, including any claim
   your edit has just made false.

   **Read `must_stay_true` before you change anything.** It lists the claims this project makes
   that the code currently supports, and the verdicts a person has recorded. A fix is not finished
   when the finding goes away — it is finished when those are still true. Satisfying a check while
   making a claim false is a worse state than the one you started in, and it is the specific
   failure an agent is most likely to produce.

   Each finding carries `file`, `evidence`, `downstream` and `marts`. `evidence` names the exact
   construct: the partition and sort keys of the window, the predicate, the columns. Use it to
   find the code rather than re-deriving it, and use `marts` to decide how carefully to tread.

## If the project has not been set up yet

`guide(topic)`, or `assay guide <topic>`. **Read it before you write anything into their
`audit.yml` or `assay_questions/`.** Every other tool here reports what assay found; this one
carries the rules for configuring it, and those rules were measured rather than reasoned out.

`guide("start")` is the order for a project that has never run assay, and steps 1 to 4 cost
nothing at all -- no key, no network, no spend. Do not skip to the judged tier to look clever.

The four things a project configures, and what you must know before helping with each:

- **`guide("vocab")`** — what their words mean HERE, sent with every judged question. A term earns
  its place when a competent stranger would read it WRONGLY, not merely because it is
  domain-specific. Fifteen terms on one warehouse made `traverse` flag a join class nobody had
  written a question about.
- **`guide("questions")`** — how to frame one this model can answer. It cannot do arithmetic and
  will not say so; absence must be stated as not-disagreement or it comes back as a confident
  contradiction; every option needs to be able to lose; a choice with no way to decline turns a
  shrug into a determination. Run `assay banks --lint` on anything you write: each rule in it is
  a question that failed in a measurable way.
- **`guide("waivers")`** — a reason is required and the reason is a MEASUREMENT. "Looks fine" is
  how a real finding gets silenced. Before waiving, check whether the finding is telling you the
  check is blind, which is usually a `meta:` declaration rather than a waiver.
- **`guide("policy")`** — what each check does on a build. Keyed by the CHECK a finding carries,
  never by the question family: a family name where a check name belongs configures nothing and
  nothing tells you. A judged check cannot fail a build until humans have ruled on it.

**Do not invent configuration on their behalf.** Vocabulary, waiver reasons and adjudication
options are domain knowledge you do not have. Ask, draft from what they say, and show them the
measurement that would justify a waiver rather than asserting one.

`suggestions()` does the reading for you. It is the join between "257 findings" and "these four
lines of YAML", and every row carries the measurement that produced it, so you can show them the
evidence instead of an opinion:

- columns shared across the most models and absent from the vocab, with the counts (`section_id`:
  24 models, 65 hops, on the warehouse this was built against). Ranked on models rather than on
  joins: a term is worth writing when it is SHARED, and the payoff is every judged question that
  carries it;
- columns named like a key that are **nearly** unique -- `incident_id` at 19,566 distinct in
  19,628 rows. A spot check passes. That is the point;
- a reason given on several subjects **that still fire**, which means something upstream of the
  config is missing.
  **It refuses to say whether that is a vocab term or a broken check, and so should you.** Ask
  them: would a reader who knew this term still call the finding correct? Yes means vocab, no
  means the check is wrong. On the project this was built against the answer was the second one,
  and the fix was structural rather than a third waiver. **Call it with a target.** Without one
  it cannot tell a live cluster from one that was already repaired, and it will say so;
- `accept` rulings that nothing waives yet, with the reason already written by whoever accepted;
- per-check actions backed by measured agreement, and an explicit "no measurement here" where
  there is none. **Do not propose an action from the shipped default alone** -- it reads as
  measured and is not.

**Every `means:` and `implies:` comes back empty, and you must hand it over empty.** This is the
one instruction in this file most worth following exactly. A definition you write from a model
name is plausible, is not knowledge, and looks identical in `audit.yml` to one they decided on --
and from then on it is sent with every judged question about that warehouse. There is no later
step that catches it.

`evidence(decision_key=..., question=..., subject=...)` returns the exact state a judged answer
was computed from. Call it before you disagree with an answer: if the answer is wrong **and** the
state is wrong, what gets sent needs fixing; if the answer is wrong and the state is right, the
question does. Those are different files, and without the state you are guessing which.

## Before you hand it back

8. `violations()` — **what would actually fail a build**, under this project's own `audit.yml`.
   `findings` lists everything wrong; most of it is configured to annotate and only some of it
   stops CI. Deciding which is which by reading the list is exactly the judgment you should not
   be making. This applies the same policy the pipeline applies, so `"this would pass"` means a
   green build rather than your opinion that it ought to be one.

   An empty `would_fail_the_build` can also mean nothing has earned the right to gate yet, because
   a judged question cannot fail a build before it has recorded human verdicts. That is the design,
   not a gap.

## When you have read a finding, rule on it

9. `rule(finding, verdict, why)` — **record what you concluded, including when you
   conclude the finding is wrong.** That is the most useful answer you can give, because a false
   positive nobody reports stays in the list forever.

   It is filed as an agent ruling. **It does not gate a build, does not count toward the verdicts
   a question needs before it may fail one, and does not anchor `assay regress`** — those require
   a person, deliberately. What it does is put this finding in front of whoever reviews next, with
   your reason beside it, ranked above the ones nobody has read.

   Rule only on what you actually read. A ruling with no reason is refused, and one with a
   reason you did not form by reading the SQL is worse than none.

   **Pass the `finding` id** that `findings()` and `review_queue()` give you. A verdict on a
   MODEL lands on every finding that model has, and one real model carries eight of the same
   check. It is also how a correct finding gets ruled wrong: a model whose hops are mostly union
   arms can still have two that genuinely fan out, and one keypress covered all six.

   A subject `rule` cannot resolve to a model is **refused**, not recorded. It used to accept a
   bare model name, answer `recorded: true`, and write rows that joined to nothing.

   If a **person** told you the answer, pass their name as `decided_by`. It records who decided
   it, so a reviewer can tell "the agent thinks" from "they said, and the agent typed it". It is
   still filed as an agent ruling: a person's ruling is their own keypress in `assay review -i`,
   which is one keystroke once your reason is on screen.

10. `review_queue()` — **what is still waiting for a person**, agent-read items first. Call it
    before ruling, to see whether a subject has already been read, and after, to see the queue you
    are building. Your ruling never clears an item from it.

11. **Drain the queue BEFORE you hand anybody a form.** `assay review --emit` renders every
    finding, read or not, and a person opening a form where a quarter of the cards say "nothing
    on this question" is being asked to do the reading you were there to do. Rule on everything
    in `review_queue()` first, then emit.

12. `load_handback(path)` — **the moment they say they have filled the form in.** The form
    downloads `handback.json` and nothing happens until it is loaded; a form that is downloaded
    and never loaded is the most valuable work in this system sitting in a folder. Ask for the
    path rather than guessing at a downloads directory. This is the only tool that files `human`
    verdicts and it can only file what the file carries — you are the courier, not the reviewer.

## When they have agreed with findings and want them fixed

```bash
assay plan -t target/       # writes assay_plan.jsonl
```

**If the plan is empty, nobody has reviewed anything yet — that is not a clean warehouse.** A plan
is built only from findings a person agreed with. Getting those is the `assay-review` skill:
The form is not only findings. It carries three more sections, and they are the parts of
`audit.yml` that are pure domain knowledge: **Words** (their vocabulary, with what assay measured
about where each one is used and a suggested scope), **Explanations** (the per-mart options for
failing-row adjudication) and **Waivers** (findings somebody already called fine, with the reason
they gave). Words is the first tab, because a term reaches every judged answer about every model
it applies to, while a verdict settles one finding.

**Fill in what you measured and leave the sentence empty.** Same split as MY READ on a card: which
models use the word, where they sit, what the lint says, a scope that resolves. Never the `means:`
— a definition written from a model name looks exactly like one they chose, and then rides along
with every judged question forever.

What they write comes back as a PROPOSAL. `assay review --load handback.json` records the verdicts
and prints the `audit.yml` changes as a diff; `--apply` writes them, in place, without touching a
comment or reordering a key. Say what changed before you run it with `--apply`.

`assay review --emit review.html -t target/` writes a form they fill in at their own pace, and
`--load verdicts.json` records it. Do not start editing off the raw findings list; most of what is
in it has never been read by anyone, and on a real warehouse a judged family can run 12% agreement
until somebody looks.

**Read that file before you edit anything.** One object per thing to do, only findings a PERSON
agreed with, only ones still present, highest blast radius first. Each carries:

- `fix_shape` — what KIND of change this is. It comes from a lookup keyed by check name, not from
  a judgment, so it is exact: `arbitrary_pick` is always "add a tie-break column".
- `how` — what that means, in a sentence.
- `their_reason` — what the person said when they agreed. Read it. It is the only part of the row
  that knows anything about this warehouse.
- `evidence` — the construct itself: the partition and sort keys, the hop, the claim.

**`fix_shape` tells you the shape. It does not tell you the words.** For a prose finding the shape
is "edit the claim at its file:line" and what the sentence should say instead is a judgment about
a real warehouse — ask, or propose and let them decide. Writing a replacement sentence yourself is
the same failure as writing a vocabulary definition yourself: plausible, not knowledge, and
permanent.

A `fix_shape` of `unknown` means assay has no shape for that check. That is a gap in the tool, not
a judgment about the model, and it says so rather than omitting the row.

After the edit, run `changed_contracts` and `violations` as always, then `assay check`. It reports
how many of the agreed findings are now gone — the only number that measures whether the loop did
anything, and one neither a release nor an agent can move.

## Rules that are not negotiable

- **Never guess a model's grain.** Ask for the contract. A wrong grain assumption is how an
  aggregate silently inflates.
- **Never remove a filter you do not understand.** Filters are domain logic, a patch over a bad
  feed, or the thing that makes the model mean what it means. `contract` and the model's own
  comments say which. Removing the wrong one deletes a rule nobody can reconstruct.
- **A column that arrives `from_source` has no explanation inside this project.** Do not invent one.
- **If `changed_contracts` shows a grain change with aggregating consumers, that is a breaking
  change.** Say so plainly in your summary, name the consumers, and do not describe it as a
  refactor.
- **If you change what a model does, change the sentence that says what it does.** A claim and the
  code are one artefact. Leaving the prose behind is how the next person is misled, and assay will
  report it as a contradiction against your name.
- **Do not add a claim you have not made true.** A sentence in a comment becomes a checked claim
  the next time anyone runs `assay claims --extract`.
- **When you change what a model does, run `assay regress`.** It replays every answer a person
  already agreed with and tells you which ones your edit moved. Nothing else in this project can
  tell you that, and a moved verdict is a regression whichever direction it went.
- **Report what assay said, not what you concluded from it.** If it was uncertain, say it was
  uncertain.

## Without the MCP server

**Every tool above has a command that answers the same question**, and the CLI's `--json` carries
the same `finding` ids, so `rule` works either way. If the MCP server is not connected, use these
and nothing is lost:

| tool | command |
|---|---|
| `contract(model)` | `assay inventory --model <model> --json` |
| `blast_radius(model)` | `assay inventory --model <model> --json` (`descendants`, `marts`) |
| `claims(model)` | `assay claims --model <model>` then `assay verify --model <model>` |
| `traversal(model)` | `assay traverse --model <model>` |
| `practices(model)` | `assay practices --keys-only --model <model>` |
| `lineage(model, column)` | `assay trace <column> --model <model>` |
| `findings(model)` | `assay check --json` — one object with a `findings` list |
| `changed_contracts()` | `assay diff --baseline <main target>` |
| `violations()` | `assay check --json`, then read `action` |
| `rule(finding, …)` | `assay review --subject <s> --question <q> --verdict <v> --note <why>` |
| `review_queue()` | `assay review` |
| `load_handback(path)` | `assay review --load handback.json` (add `--apply` to write audit.yml) |
| `plan()` | `assay plan -t target/` (writes `assay_plan.jsonl`) |
| `suggestions()` | `assay suggest -t target/`, or `--section vocab` |
| `evidence()` | `assay evidence -q <question> -s <model>` |
| `guide(topic)` | `assay guide <topic>` |
| `spend()` | `assay cost`, or `assay cost --json` |
| `stale(exact)` | `assay stale`, `assay stale --exact`, `assay stale --cost` |
| `vocabulary()` | `assay config --target <target/>` |
| `monitoring(volume_json)` | `assay volume --json > volume.json` |

The one difference worth knowing: the MCP tools reload when the manifest moves, and a CLI run
reads whatever `target/` holds at that moment. Run `dbt compile` first if you have edited SQL.

## Checking the whole project

- `assay check` — structural and judged findings in one stream, ranked by blast radius.
- `assay completeness` — do we have all of it? Sources nothing reads, feeds behind their own
  declared freshness, hops that lose most of the parent. Add `--verify` to count empty models and
  row loss through the project's own dbt; without it those are **not counted**, which is not a
  pass.
- `assay effectiveness` — whether the questions themselves are any good: agreement per family per
  version, and how many disagreements are still open.
- `assay disagreements` — group the findings people rejected. N rejections are usually far fewer
  than N bugs.
- `assay page assay.html` — one self-contained page answering "is this warehouse understood, and
  by whom". Deterministic, so it can be committed and diffed.
- `assay diff --baseline <main target>` — what changed about what models MEAN, for a review.
- `assay version-check --baseline <main target>` — whether anything owes a version bump.
- `practices(model)` / `assay practices --keys-only` — models with no uniqueness test, and the
  grain a test should cover. A patch, not a nag.
- `assay claims --extract` then `assay verify` — pull every claim out of this project's own prose
  and check each one against the code.
- `assay traverse` — judge every hop in the graph for a fan-out nobody declared.
- `assay patch tests/assay` — write the uniqueness tests assay can PROVE will pass. It counts each
  grain first and refuses to write one that would fail on its first run.
- `assay cost` — what the judged tier has cost here, by caller and by day. Free, and it is the
  number to put in front of somebody BEFORE proposing a judged run.
- `assay stale` — judged answers that are about SQL which has since changed, from the sha256 dbt
  already records. `--exact` rebuilds the state each answer was computed from and compares it,
  which catches a change to a PARENT that a checksum by definition cannot. `--cost` quotes what
  re-asking them would cost before you spend it. Neither makes an API call.
- `assay config --target <dir>` — lints their **vocabulary**, which nothing used to check at all.
## Whether anything is WATCHING the warehouse

`monitoring(volume_json)` answers the one question every other tool here assumes somebody else
answered: if what this SQL produces changed tonight, would anybody notice? It reports the build
cadence, whether each monitor is still being written to, how many declared tests have ever
produced a result, tests whose last result was a FAILURE and which have not run since, and the
models with a mart downstream and no row-count history at all.

**It reads a file and never a warehouse.** Taking the measurement needs their dbt connection and
assay never holds a credential, so they run the measurement and you read it:

```bash
assay volume --json > volume.json                      # their connection, free, no judgment
assay page assay.html --monitoring volume.json         # the same numbers on the report
assay review --emit review.html -t target/ --monitoring volume.json
```

Called with no path it hands back that command rather than a set of zeros, and you should pass it
on rather than reporting that the monitoring is fine. **A zero here reads as "nothing is wrong"
and means "nobody looked".** A `stale_failure` is neither a live failure nor a pass: it is an
answer that has gone out of date, and it reads as a live failure in any view that sorts by status.

- `assay volume` — what Elementary counted, joined to what the project claims, plus five checks on
  the MONITORING itself: a monitor configured and never run, one that ran and stopped, models that
  feed marts with no row-count history, tests that have never fired, and results that are `skipped`
  rather than passed. Free of judgment; needs their dbt connection. `--judge` adds the one question
  neither tool can answer alone, for a fraction of a cent.

  **assay never measures volume or freshness itself.** The moment it does it is a second
  monitoring tool with a second opinion. It asserts the monitor exists, is current, and covers what
  matters; everything measured stays Elementary's.

## A vocab term is true SOMEWHERE, and it is sent EVERYWHERE unless it says otherwise

This is the widest blast radius in the whole config and it is worth knowing before you touch it.
Every term in `vocab:` goes into the state of every judged question. A term that is false in part
of the project is therefore false in every answer about that part, at once, with no signal.

Measured on a real warehouse: sixteen terms, six asserting one state's water law, sent to all 358
models — and 4,997 of 19,707 judged answers, 25% of everything ever paid for there, were about
models in a different state.

```yaml
vocab:
  water_division:
    means: "a Colorado water court region, 1 through 7"
    applies_to:
      select:  "path:models/water"
      exclude: "path:models/water/az"     # the exception lives INSIDE the rule
```

**Do not write or edit a term without calling `vocabulary()` and `guide('vocab')` first.** A term
about their DATA is usually true everywhere and should stay unscoped; one that asserts a law, a
regulatory regime or a regional convention needs `applies_to`. The lint will tell you which of
theirs look like the second kind, and it writes the selector for you.

**Propose `applies_to` by default on any project past a couple of dozen models, and say why.**
Measured on the same warehouse: adding a scope to 12 of 16 terms took the lint from 13 warnings
to 1, and it was the highest-value config change of the whole run. At that size the default of
writing an unscoped term is the wrong default -- the blast radius of a wrong one is every judged
answer about the part of the project where it is false, and there is no signal when it happens.
An unscoped term should be a decision somebody made, not what happened because nobody said.

## Running it on a box rather than a laptop

Three things that cost a day between "assay works on my laptop" and "assay ran against the real
warehouse". None of them look like what they are.

- **`uvx --refresh`, always.** `uvx --from dbt-assay==<version>` will serve a CACHED environment
  while the process reports itself as the version you asked for. It produced a completely blank
  report page from a pin whose published wheel was correct, and the page stamped the new version
  on itself the whole time. `uvx --refresh --from dbt-assay==<version>` fixed it. The same cache
  also reports a just-published version as unsatisfiable while the PyPI JSON API already lists
  it.
- **`docker compose up -d`, not `restart`.** Environment is baked at container CREATE, so a key
  added to `.env` and followed by a restart is not in the container. The failure reads as "assay
  cannot see my key".
- **The store has to be on a path the container can see.** assay will happily create an empty one
  at a path that is not bound, and an empty store and a clean warehouse print the same zeros. It
  now says "this store is NEW and holds nothing" on the first run against one, in the terminal
  and on the page -- if you see that sentence and expected history, the path is the reason.

## When `changed_contracts` is noisy

`rebase()` takes a fresh baseline. Use it when you have deliberately changed what several models
mean and have already reported that, so the next check compares against your new normal rather
than repeating what you have already said.

## Completeness findings are coverage, never a judgment about the business

`source_reaches_nothing`, `source_only_a_test_reads`, `source_freshness_stale` and
`hop_drops_most_rows` are all coverage of what **this project itself declares**: it declared a
source, so something should read it; it declared a freshness, so something should meet it.

assay can say a column is 99% its default. **It cannot say whether that is bad.** Do not treat one
of these as a defect to fix on your own initiative — read it, and rule on it. Whether a gap matters
is a question about intent, and intent is the thing this tool refuses to guess at.

## What assay is not

It reads code and rows, never intent. It cannot tell you whether a business rule is correct, only
whether the code does what the documentation claims. Where it is uncertain it says so, and an
uncertain answer is a question for a person, not a number to round off.

## Every command, and every flag it takes

Generated from the app itself, so it cannot drift from what is installed.
`assay <command> --help` has the long form of any of these.

| command | what it answers | flags |
|---|---|---|
| `assay adjudicate` | Rows a dbt test flagged: does the rest of the row explain it? | `--target/-t` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--per-test` `--store` `--config` |
| `assay align` | Do two columns in different models mean the same thing? | `--target/-t` `--select/-s` `--max-pairs` `--store` `--config` |
| `assay ask` | Run every question that declares a `subject:`, including your own. | `--target/-t` `--store` `--config` `--family/-f` `--select/-s` `--limit/-n` `--dry-run` |
| `assay backtest` | Replay this repo's own history and measure whether the checks catch what it already fixed. | `--repo/-r` `--limit/-n` `--fix-like-only` `--since` `--show` `--compile` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` |
| `assay banks` | Every question assay will ask, where it came from, and whether its shape is sound. | `--lint` `--strict` `--judge` `--config` `--store` |
| `assay calibrate` | Measure the grain judgment against the keys this project already declares. | `--target/-t` `--limit/-n` `--store` `--config` |
| `assay calibration` | When this thing is confident, is it right more often than when it is not? | `--store` `--source` |
| `assay check` | Run the structural checks. | `--target/-t` `--json` `--store` `--limit/-n` `--check` `--config` `--dialect` `--verify` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--select/-s` `--new-only` |
| `assay claims` | What this project ASSERTS about its models, as data you can read, edit and rule on. | `--target/-t` `--store` `--config` `--extract` `--select/-s` `--limit/-n` `--min-confidence` `--write` `--model/-m` |
| `assay columns` | Judge each column's role and what a NULL in it would mean. | `--target/-t` `--print-state` `--limit/-n` `--store` `--config` `--with-null` `--dialect` `--control` |
| `assay completeness` | Do we have all of it? Coverage of what this project itself declares. | `--target/-t` `--store` `--config` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--verify` `--dialect` `--json` |
| `assay config` | What assay resolved: the config file, the provider, where the key came from, the cap. | `--config` `--store` `--check` `--target/-t` `--strict` |
| `assay cost` | What the judged tier has cost, by caller, by family and by day. | `--store` `--since` `--json` |
| `assay diff` | What changed about what your models MEAN. | `--baseline/-b` `--target/-t` `--store` `--markdown` `--limit/-n` |
| `assay disagreements` | Group the open disagreements. | `--store` `--config` `--source` `--judge` `--json` |
| `assay effectiveness` | Did the questions get BETTER? Agreement per family, per version of the question. | `--store` `--source` `--config` `--target/-t` `--json` |
| `assay evidence` | The exact state a judged answer was computed from, as it was sent. | `--key` `--question/-q` `--subject/-s` `--store` `--limit/-n` `--json` |
| `assay export <directory>` | Put assay's tables in your warehouse, as data your own models can join to. | `--store` `--format` `--no-docs` |
| `assay feeds` | Has a feed changed its mind while its schema held still? | `--target/-t` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--sample` `--limit/-n` `--store` `--config` |
| `assay guide <topic>` | How to SET ASSAY UP, for somebody who has never used it. | — |
| `assay hook <action>` | The edit gate. | `--target/-t` `--store` `--config` `--project-dir` `--dbt/--dbt-bin` `--profiles-dir` `--compile` `--dialect` `--settings` `--assay-cmd` |
| `assay infer` | Infer each model's grain. | `--target/-t` `--print-state` `--limit/-n` `--store` `--config` |
| `assay init` | Write an audit. | `--force` |
| `assay inventory` | What every model in this project actually IS. | `--target/-t` `--model/-m` `--store` `--json` `--html` `--write` `--include-unadjudicated` `--limit/-n` `--dialect` |
| `assay mcp` | Serve assay as tools an agent can call instead of reading your SQL. | `--target/-t` `--store` |
| `assay onboard` | One command for a project assay has never seen. | `--target/-t` `--store` `--config` `--agent` `--compile` `--dbt/--dbt-bin` `--profiles-dir` `--judge` `--judge-limit` `--dialect` |
| `assay page <out>` | Everything assay knows about this warehouse, as one file you can open. | `--target/-t` `--store` `--config` `--dialect` `--plain` `--data` `--from` `--form` `--monitoring` |
| `assay patch <out_dir>` | Write the uniqueness tests assay can prove will pass. | `--target/-t` `--store` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--dry-run` `--dialect` |
| `assay plan` | What to DO about the findings a person agreed with. | `--target/-t` `--config` `--store` `--out` `--dialect` `--json` |
| `assay practices` | Standard dbt practice: deferred to where it exists, adjudicated where it is noisy. | `--target/-t` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--evaluator-schema` `--dialect` `--verify` `--keys-only` `--store` `--config` |
| `assay probe` | Count what the SQL cannot settle. | `--target/-t` `--project-dir` `--profiles-dir` `--dialect` `--dbt/--dbt-bin` `--dry-run` `--emit` `--load` `--limit/-n` `--store` `--config` `--sample` |
| `assay prune` | Drop old runs from the tables a parser can regenerate. | `--keep/-k` `--store` `--dry-run` |
| `assay regress` | Re-ask every question a person already agreed with, and report what moved. | `--target/-t` `--store` `--config` `--family/-f` |
| `assay review` | List judgments nobody has ruled on, or record a verdict. | `--store` `--interactive/-i` `--from-labels` `--target/-t` `--dialect` `--limit/-n` `--subject` `--question` `--verdict` `--finding` `--until` `--correction` `--note` `--by` `--config` `--emit` `--load` `--report` `--monitoring` `--apply` `--reads` `--repair` |
| `assay scan` | Read the project and report what can and cannot be audited. | `--target/-t` `--dialect` |
| `assay semantics` | Why is that filter there, and does the description still describe the code? | `--target/-t` `--select/-s` `--families` `--print-state` `--limit/-n` `--store` `--config` |
| `assay skill <which>` | Emit an agent procedure. | `--write` |
| `assay stale` | Judged answers that are about SQL which has since changed. | `--target/-t` `--store` `--config` `--dialect` `--exact` `--cost` `--limit/-n` `--json` |
| `assay suggest` | What this project should configure, drawn from what the checks actually found. | `--target/-t` `--config` `--store` `--section` `--limit/-n` `--out` `--json` |
| `assay tests` | Is each test's severity right, and what is a model exposed to that nothing asserts? | `--target/-t` `--count-defaults` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--gaps-only` `--run-results` `--dialect` `--limit/-n` `--store` `--config` |
| `assay trace <column>` | Where did this number come from? | `--target/-t` |
| `assay traverse` | Judge every hop in the graph: does one row still mean the same thing on the other side? | `--target/-t` `--store` `--config` `--select/-s` `--limit/-n` `--model/-m` |
| `assay verify` | Check every extracted claim against what the code actually does. | `--target/-t` `--store` `--config` `--select/-s` `--limit/-n` `--min-confidence` `--model/-m` |
| `assay version` | Print the version. | — |
| `assay version-check` | A version bump is owed when the MEANING changed, and never when it did not. | `--baseline/-b` `--target/-t` `--store` `--bump` `--write` `--project-root` |
| `assay version-stamps` | Does each row say which version of the logic produced it? | `--target/-t` `--store` `--recommend` |
| `assay volume` | What Elementary counted, joined to what this project says about itself. | `--target/-t` `--project-dir` `--profiles-dir` `--dbt/--dbt-bin` `--elementary-schema` `--store` `--config` `--dialect` `--judge` `--threshold` `--limit/-n` `--dry-run` `--json` |
| `assay watch` | Stay quiet until something in your working tree MEANS something different. | `--target/-t` `--project-dir` `--compile` `--dbt/--dbt-bin` `--profiles-dir` `--interval` `--store` |
