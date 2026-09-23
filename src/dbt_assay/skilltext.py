"""The agent procedures assay ships for itself.

*** MCP GIVES AN AGENT THE ABILITY TO CHECK ITSELF; THIS GIVES IT THE OBLIGATION. ***
Without a written procedure an agent checks when it remembers, which is not a guarantee. With one,
checking is the step, and the warehouse's own accumulated judgment -- not the agent's opinion about
someone else's project -- is what decides whether an edit was safe.
"""

_EDIT_BODY = '''---
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

**The edit gate.** `assay hook install --dbt "<how dbt runs here>"` writes a hook into
`.claude/settings.json` that compiles a model after you edit it and stops you on any finding the
edit introduced (`onboard --agent` installs it too). It compares against the last full
`assay check`, so run one first. When it stops you, fix the SQL -- or, if the finding is correct
and should stand, tell the person; your own ruling does not clear it.

**A command that reaches the warehouse is blind without its connection, not clean.** `probe`,
`volume`, `feeds`, `practices`, `adjudicate`, `completeness`, `patch`, `tests --count-defaults` and
`check --verify` all run the project's own dbt, so every one of them takes
`--project-dir <the dbt project>` and `--dbt "<how dbt runs here, e.g. uv run dbt>"`. Without them
they run dbt in the wrong place and report "NOT LOOKED AT" -- which is a fact about the
invocation, never about the project.

## Before you touch a model

1. `contract(model)` — what one row is, what each column does, where each value comes from.
   Fifteen lines instead of two hundred of SQL. Its `health` says what is already decided: open
   findings and who ruled on each, what is waived or accepted (a decision already made -- do not
   re-litigate it), and whether the grain was counted in the data or only inferred.
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
| `practices(model)` | `assay practices --keys-only --no-verify --model <model>` |
| `lineage(model, column)` | `assay trace <model>.<column>` |
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
| `monitoring(volume_json)` | `assay volume --json --project-dir <dbt project> --dbt "<dbt>" > volume.json` |

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
- `practices(model)` / `assay practices --keys-only --project-dir <dbt project> --dbt "<dbt>"` —
  models with no uniqueness test, and the grain a test should cover, each grain counted through
  their dbt first. `--no-verify` is the pure-code half. A patch, not a nag.
- `assay claims --extract` then `assay verify` — pull every claim out of this project's own prose
  and check each one against the code.
- `assay traverse` — judge every hop in the graph for a fan-out nobody declared.
- `assay patch tests/assay --project-dir <dbt project> --dbt "<dbt>"` — write the uniqueness
  tests assay can PROVE will pass. It counts each
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
assay volume --json --project-dir <dbt project> --dbt "<dbt>" > volume.json   # their connection, free
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
'''


# *** THE SECOND PROCEDURE, AND IT IS THE ONE THE NUMBER WAS WAITING ON. ***
#  covers editing a model. Nothing covered the other half: sitting with the person whose
# warehouse it is and turning findings into verdicts. On the field warehouse that number was
# **0 of 159 models with findings**, and it was never a lack of tools --  has
# existed for most of this project's life. Ruling meant leaving the conversation, so it did not
# happen, and every gate that needs human verdicts stayed shut.
#
# It is a separate skill rather than a section of the first because the two are invoked at
# different moments by different intents. An agent in the middle of an edit must not start walking
# a review queue, and a person asking to review findings is not editing anything.
# A RAW literal: the worked example quotes real SQL, and `\d` in a normal string is a
# SyntaxWarning that ships a mangled regex to whoever reads the skill.
REVIEW_SKILL_MD = r'''---
name: assay-review
description: >-
  Walk assay's findings with a person, one at a time, and record their verdicts. Use when they
  say review findings, rule on findings, go through assay, or /assay-review. Do the SQL reading
  FOR them so their call is cheap; never rule on their behalf.
---

# Reviewing assay findings with a person

The point of this is to move findings out of "nobody has looked" and into a verdict. As of the day
this was written that number was **0 of 159 models with findings**, and the reason was never a lack
of tools. It was that ruling meant leaving the terminal.

So it happens here, in the conversation, and they never open anything.

## What this records, and what it does not prove

`assay review --verdict` writes `source = 'human'`. **That is a label, not a proof.** Any process
that can run the binary can write it, including you. There is no cryptographic binding between a
verdict in this store and an actual person.

`--by` is free text and defaults to `unknown`. It fills `decided_by`, a note about who answered; it
does not decide `source` and nothing validates it. So `source = 'human'` is set by the code path,
not by anything about who ran it, and the only thing separating "human" from meaning nothing is the
paragraph below being followed. That is a deliberate choice, left as it is because making the label
unforgeable is a different piece of work than making it honest. Do not read the label as
load-bearing, and do not leave a future reader to assume somebody checked.

Say this once, plainly, the first time you run this in a session, and then stop mentioning it:

> Recording these as human verdicts. That's a label an agent could also write, so it rests on me
> not doing that. I won't record anything you didn't answer.

**Then do not record anything they did not answer.** Not a default, not an inference from "sounds
fine", not a batch they waved through without seeing. If they say "just do the obvious ones", ask
which ones they mean and show them first. A verdict they did not give is worse than no verdict,
because it gates a build and it carries their name.

## Two modes, and the count decides which

**More than about ten to get through: emit the form.** One turn per finding is one turn per
finding, and a backlog of 159 is 159 turns that nobody will sit through. The reading batches; the
answering does not have to happen in a conversation at all.

**Read everything in the queue and rule on it BEFORE you emit.** Reported from the field: 167
cards, 91 carrying a reading, so 76 said "nothing on this question" to the person sitting down to
answer them. *"isnt that YOUR job when YOURE using the skills to make the report?"* — yes. Drain
`review_queue()` and `rule()` on every item first, or write a `--reads` file, and only then
render. A form where a quarter of the cards are a cold start is asking somebody to do the reading
you were there to do.

```bash
assay review --emit review.html --target <target/> --store assay.duckdb \
  --report assay.html      # so the form and the report link to each other
```

One self-contained file, opened from `file://`, no server and nothing left running. Twenty cards at
a time, highest blast radius first, answers kept in the browser so the tab can be closed. The
download button writes `verdicts.json`, and:

```bash
assay review --load verdicts.json --store assay.duckdb
```

records every verdict at once. A card nobody answered is never submitted and never recorded, and
`--load` names each row it did not record rather than reporting a total that hides them.

**Do not end the turn on "open this file".** The download writes `handback.json` and NOTHING
happens until it is loaded — the most valuable work in this whole system, sitting in a downloads
folder. Ask for the path the moment they say they have filled it in, and load it:
`load_handback(path)` over MCP, or the `--load` line above. It is the only path that files
`human` verdicts, and it files only what the file carries.

**The expensive half is what makes each card cheap, and the judged tier does it.** `assay read`
reads every unruled card once and writes the file the form takes -- a verdict in the form's own
vocabulary and a reason selected from the question's criteria, never written. It costs about a
third of a hundredth of a cent a card, prices itself first, and records NO verdict:

```bash
assay read --out reads.json --target <target/> --store assay.duckdb --dry-run   # count, price
assay read --out reads.json --target <target/> --store assay.duckdb
assay review --emit review.html --reads reads.json --target <target/>
```

That ships the form with a reading already on each card. Where you have read a card's SQL yourself
and disagree with the file, change that entry before emitting: the file is yours to edit, the click
is still theirs. The emit line says how many cards carry a reading and how many are a cold start.

**A handful, or they want to talk through them: the loop below.** It is the right shape for a
call. It is the wrong shape for a project.

## The loop

### 1. Pull the queue, ranked

```bash
assay check --target <target/> --store assay.duckdb
```

Then read the findings from the store, newest run, ordered by `marts desc`. Skip any `(subject,
question)` pair that already has a `human` verdict — a verdict covers the model and question, not
the single finding, so one ruling clears every finding of that check on that model.

**The pair, and only the pair.** A verdict on one check of a model says nothing about its other
checks, and treating it as if it did drops questions nobody answered out of the list whose job
is showing what nobody has answered. Both the queue and the form had that bug.

### 2. Present ONE finding, with the reading already done

Never paste a list. A list is the thing that made this get dropped. One finding, and before you show
it, **go read the SQL it names** and say what you found. The whole value here is that their call
costs ten seconds because you spent two minutes.

The shape that worked:

```
stg_phoenix_permits · 24 marts · code_contradicts_a_claim

CLAIM   "final_date is the same /Date(ms)/ format as issue_date"
CODE    lines 4 and 18-23 apply the byte-identical expression to both:
        regexp_extract(…, '(\d{10,})', 1) -> epoch_ms -> date
MY READ disagree. The claim is exactly true.
```

Rules for that block:

- Quote the actual claim and cite line numbers. "The description is wrong" is not evidence.
- If an agent already ruled on this model, show its reason — but check whether it **answers this
  question**. A ruling is stored per model, so the same note lands on every finding that model has,
  and it often addresses a different one. Say so when it doesn't fit rather than passing it off.
- Give your own read and say `agree` or `disagree`. Do not hedge. They are confirming or
  overturning, which is fast; weighing an open question is not.
- `agree` means the finding is RIGHT. `disagree` means the finding is WRONG. Say which way round in
  the first one of a session, because it inverts on a false positive and that trips people.
- `accept` means the finding is RIGHT and they are leaving it on purpose. If they say "yes, but
  that's intended", that is `accept`, not `disagree` -- and it needs their reason.

### 3. Record exactly what they said

```bash
assay review \
  --subject <the model's unique_id, e.g. model.<project>.<model>> \
  --question <check_name> \
  --verdict agree|disagree|unclear \
  --note "<their reason, or yours if they agreed with your read>" \
  --by "<their name>" \
  --target <target/> --store assay.duckdb
```

An `accept` is about one finding, so it names the finding (the id `check --json` prints):

```bash
assay review --finding <id> --verdict accept \
  --note "<why it is correct and still stays>" --until <YYYY-MM-DD> \
  --by "<their name>" --target <target/> --store assay.duckdb
```

Then go straight to the next finding. No summary between items, no "great, that's recorded!" — the
rhythm is the point and commentary is what makes six findings feel like sixty.

If they say something that isn't a verdict — a question, "what does that even do", "leave it" — that
is not a verdict. Answer it, or move on, and record nothing.

`unclear` is a real answer and means the finding does not carry enough to decide. It is evidence
about the QUESTION rather than about the model, and it never gates. Offer it when they are stuck
rather than pushing for a yes.

### 4. Stop when they stop

Every few findings, say how many are left, in one line. When they are done, run `check` again and
tell them what moved. That is the only thing that makes the next session happen.

## What each verdict actually does

Say this the first time, because it changes how carefully somebody answers.

- **`disagree` removes the finding.** Permanently, from `assay check` and from every surface that
  reads it. It is not a note — it is a dismissal, and it is the thing that makes reviewing
  compound instead of tax. It lapses by itself if the model is later edited into a genuinely
  different defect, because the dismissal is keyed to the finding and the finding's identity
  includes its evidence.
- **`agree` removes nothing.** The finding is real, so it stays. What it does is make the finding
  answerable later: `assay check` reports how many of the agreed findings are now gone, and that
  needs the FINDING, not the model. It also puts it in `assay plan`.
- **`unclear` removes nothing and gates nothing.** It is evidence the QUESTION could not be
  answered from what it was given, which is fixed by adding to the state rather than by rewording
  an option.
- **`accept` removes the finding from the open list, and counts as the check being right.** The
  finding is correct; they are leaving it on purpose. It needs a reason, takes an `--until` after
  which the finding comes back, never lands in "agreed and still here", and the form's Waivers tab
  proposes it for audit.yml so the decision is in git. Recording a correct finding as `disagree`
  to make it go away tells a working check it was wrong -- that is what `accept` is for.

Agreeing and then later dismissing the same finding does not count as fixed. That is a retraction,
and it is excluded on purpose: the one honest number on the board must not be movable by changing
your mind.

## When they want the agreed ones fixed

```bash
assay plan -t target/       # writes assay_plan.jsonl
```

Only the findings they agreed with, only ones still present, highest blast radius first. Each row
carries `fix_shape` — the KIND of change, looked up from the check name, so it is exact — plus
`how`, `their_reason`, and the evidence.

**`fix_shape` gives you the shape, never the words.** For a prose finding the shape is "edit the
claim at its file:line", and what the sentence should say instead is a judgment about their
warehouse. Propose it; do not write it as though you knew.

Then `assay check` again, and tell them the one line that matters:

    of the 12 finding(s) a person agreed with, 5 are gone and 7 are still here.

Every other number moves when assay improves. That one moves when somebody read SQL and then
changed it, and neither a release nor you can touch it. It is the only evidence the session was
worth their time.

## What NOT to do

- **Do not batch.** "Here are ten, tell me which are wrong" is the failure mode this replaces.
- **Do not rule on models you have not read.** If the file is long, read the part the finding names
  and say what you read.
- **Do not argue.** If they overturn your read, record theirs and move on. They have context the
  warehouse does not carry, and the disagreement is data about the check, not about them.
- **Do not chase completeness.** 159 models is not a backlog to burn down. The high-mart ones are
  the ones where a wrong verdict costs something.
'''


# --------------------------------------------------------------- the complete surface, generated

# *** A PROCEDURE THAT NAMES HALF THE TOOL TEACHES HALF THE TOOL. ***
# Measured against the app itself: 21 of 47 commands appeared nowhere in either shipped skill, and
# 85 flags appeared in neither the skills nor the docs -- including `page --monitoring`, which is
# the whole reason the report can say anything about whether the warehouse is watched. Anybody
# onboarding through the skill could not find them, and there was no signal that they existed.
#
# *** SO IT IS READ OFF THE APP, NEVER TYPED. ***
# A hand-written table of 47 commands is a table that is wrong by the next release, and the
# failure is silent: the reader believes what it says. This one is generated from the registered
# commands and their registered parameters, so a flag that exists is in it and a flag that is in
# it exists. The checked-in copies are regenerated by `assay skill all --write .`, and a test
# fails when they drift.
def _command_reference() -> str:
    """Every command, what it is for, and every flag it takes, read from the app."""
    import typer.main

    from .cli import app

    rows = []
    for c in app.registered_commands:
        name = c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")
        doc = (c.help or c.callback.__doc__ or "").strip()
        # The first sentence only. These docstrings run to forty lines of argument, which belongs
        # in `--help` and would drown a reference.
        first = doc.split("\n\n")[0].replace("\n", " ").strip()
        if "." in first:
            first = first[:first.index(".") + 1]
        params = typer.main.get_params_convertors_ctx_param_name_from_function(c.callback)[0]
        args, opts = [], []
        for p in params:
            got = [o for o in getattr(p, "opts", []) if o.startswith("-")]
            if not got:
                # A positional: the thing the command acts on, which a reference that lists only
                # flags leaves you guessing at.
                args.append(f"<{p.name}>")
                continue
            if "--help" in got:
                continue
            opts.append("/".join(sorted(got, key=lambda o: (not o.startswith("--"), o))))
        rows.append((name, " ".join(args), first, opts))

    out = ["", "## Every command, and every flag it takes", "",
           "Generated from the app itself, so it cannot drift from what is installed.",
           "`assay <command> --help` has the long form of any of these.", "",
           "| command | what it answers | flags |", "|---|---|---|"]
    for name, args, first, opts in sorted(rows):
        flags = " ".join(f"`{o}`" for o in opts) or "—"
        out.append(f"| `assay {name}{(' ' + args) if args else ''}` | {first} | {flags} |")
    out.append("")
    return "\n".join(out)


# *** AND THE REFERENCE IS PART OF THE DOCUMENT, NOT A SEPARATE THING TO GO AND FIND. ***
# A skill that says "see the docs" for the other half of the tool is a skill whose reader stops at
# its last line.
SKILL_MD = _EDIT_BODY + _command_reference()
