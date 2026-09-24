---
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
vocabulary, how sure it was, a reason selected from the question's criteria, and the line of SQL
the reading rests on, chosen from lines code listed and copied, never written. A dismissal read
below 0.5 arrives as `unclear`: too unsure to suggest removing a finding. It costs about a
hundredth of a cent a card (measured: $0.0522 for 407), prices itself first net of what the store
already answers, and records NO verdict. Run it AFTER the last judged command (`traverse`,
`semantics`, `volume --judge`), or the findings those add arrive on the form cold:

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
