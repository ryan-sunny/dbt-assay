---
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
