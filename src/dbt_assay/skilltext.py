"""The agent procedure assay ships for itself.

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

7. `findings(model)` — the contradictions assay currently sees in what you just wrote, including
   any claim your edit has just made false.

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
- **Report what assay said, not what you concluded from it.** If it was uncertain, say it was
  uncertain.

## Checking the whole project

- `assay check` — structural and judged findings in one stream, ranked by blast radius.
- `assay diff --baseline <main target>` — what changed about what models MEAN, for a review.
- `assay version-check --baseline <main target>` — whether anything owes a version bump.
- `assay practices --keys-only` — models with no uniqueness test, and the grain a test should cover.
- `assay claims --extract` then `assay verify` — pull every claim out of this project's own prose
  and check each one against the code.
- `assay traverse` — judge every hop in the graph for a fan-out nobody declared.

## What assay is not

It reads code and rows, never intent. It cannot tell you whether a business rule is correct, only
whether the code does what the documentation claims. Where it is uncertain it says so, and an
uncertain answer is a question for a person, not a number to round off.
'''
