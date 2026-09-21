"""The agent procedures assay ships for itself.

*** MCP GIVES AN AGENT THE ABILITY TO CHECK ITSELF; THIS GIVES IT THE OBLIGATION. ***
Without a written procedure an agent checks when it remembers, which is not a guarantee. With one,
checking is the step, and the warehouse's own accumulated judgment -- not the agent's opinion about
someone else's project -- is what decides whether an edit was safe.
"""

SKILL_MD = '''---
name: dbt-assay
description: >-
  Use before and after editing any dbt model. assay knows what every model in this project MEANS --
  its grain, what each column is, where each value comes from, and what breaks downstream. Call it
  instead of guessing, and call it again to check your own work before handing it back.
---

# Working on dbt models in this project

assay is running as an MCP server. It has already derived what every model means. Use it; do not
re-derive it by reading SQL, and do not guess.

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
- `disagree` rulings that nothing waives, with the reason already written by whoever ruled;
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
| `lineage(model, column)` | `assay trace <column> --model <model>` |
| `findings(model)` | `assay check --json` — one object with a `findings` list |
| `changed_contracts()` | `assay diff --baseline <main target>` |
| `violations()` | `assay check --json`, then read `action` |
| `rule(finding, …)` | `assay review --subject <s> --question <q> --verdict <v> --note <why>` |
| `review_queue()` | `assay review` |
| `suggestions()` | `assay suggest -t target/`, or `--section vocab` |
| `evidence()` | `assay evidence -q <question> -s <model>` |
| `guide(topic)` | `assay guide <topic>` |

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
- `assay practices --keys-only` — models with no uniqueness test, and the grain a test should cover.
- `assay claims --extract` then `assay verify` — pull every claim out of this project's own prose
  and check each one against the code.
- `assay traverse` — judge every hop in the graph for a fan-out nobody declared.
- `assay patch tests/assay` — write the uniqueness tests assay can PROVE will pass. It counts each
  grain first and refuses to write one that would fail on its first run.

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
name: assay-review description: >- Walk assay's findings with a person, one at a time, and record
their verdicts. Use when they say review findings, rule on findings, go through assay, or
/assay-review. Do the SQL reading FOR them so their call is cheap; never rule on their behalf.
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

## The loop

### 1. Pull the queue, ranked

```bash
assay check --target <target/> --store assay.duckdb
```

Then read the findings from the store, newest run, ordered by `marts desc`. Skip any `(subject,
question)` pair that already has a `human` verdict — a verdict covers the model and question, not
the single finding, so one ruling clears every finding of that check on that model.

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

## What NOT to do

- **Do not batch.** "Here are ten, tell me which are wrong" is the failure mode this replaces.
- **Do not rule on models you have not read.** If the file is long, read the part the finding names
  and say what you read.
- **Do not argue.** If they overturn your read, record theirs and move on. They have context the
  warehouse does not carry, and the disagreement is data about the check, not about them.
- **Do not chase completeness.** 159 models is not a backlog to burn down. The high-mart ones are
  the ones where a wrong verdict costs something.
'''
