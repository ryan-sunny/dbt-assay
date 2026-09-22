# Where assay is blind, and what to do about it

Written 2026-09-21 against `sunny_data` (358 models, 254 findings, 9,945 judged decisions). Every
number below was measured on that store or read out of the code, and where something is an opinion
it says so.

Four gaps. Two are bookkeeping assay should already do, one is a rollup of facts it already holds,
and one is somebody else's job that assay should read rather than rebuild.

---

## 1. Cost is a cap, not a ledger

**What exists.** `jev.max_spend_usd` stops a runaway mid-run. `Budget.spent_usd` accumulates it.

**What is missing.** That object lives in memory and dies with the process. The store keeps
`input_tokens` per decision and **no dollars at all**, no output tokens, no model name, no latency.
So the total is derivable and never shown:

```
45,806,589 input tokens / 4,946 calls  ->  $1.92
  assay.claims     5,794 answers   35,927,768 tok   $1.51
  assay.verify     1,742 answers    4,188,177 tok   $0.18
  assay.traverse   1,629 answers    3,698,411 tok   $0.16
```

$1.92 for the entire judged tier across 358 models. That is the answer to "what does this cost
me", it is a good answer, and nothing can currently give it.

**Design.** Three columns on `model_decisions`: `output_tokens`, `usd`, `model_name`. `usd` is
computed at write time from the rate that was in force, not derived at read time — a rate change
must not silently rewrite history. Then `assay cost`:

```
assay cost                  # by caller, by day, by question family
assay cost --since 2026-09-01
```

**What it must not do.** Not estimate. A call whose usage the provider did not return records
`usd` as NULL and the report says how many, rather than filling in an average and presenting the
sum as measured. An absent measurement is not a zero — the rule this codebase applies everywhere
else.

**Jev's role: none.** This is bookkeeping and should cost nothing.

**Size.** Small. Three columns, one migration, one command.

---

## 2. Nothing knows when its own knowledge went stale against the code

**The gap.** `live_decisions` counts an answer stale by `prompt_version` — whether the QUESTION
changed. Nothing asks whether the CODE changed. A model edited after being judged serves its old
answer with no signal, and the only way to find out is to pay to re-ask.

**And the fix is already sitting in the manifest.** Every dbt node carries a sha256 of its source
file:

```json
"checksum": {"name": "sha256", "checksum": "80dff5c259c242a7e912d4bb7be70f34ad2b35601e9a6c98..."}
```

**358 of 358 models have one. assay reads none of them.**

**Design, two tiers, both free of API cost.**

*Cheap and necessary:* record `file_checksum` on each `model_decisions` row at decide time.
Comparing it to the current manifest is a dict lookup. It answers "this model's source moved" —
necessary, not sufficient: a comment edit trips it, and a change to a PARENT does not.

*Exact:* `state_hash` is already stored. Rebuilding a subject's state and re-hashing it costs CPU
and no calls, and it is precisely what `decide()` does before deciding whether to call. That
answers "the thing this answer was computed from is different now."

```
assay stale                 # N judged answers are about SQL that has since changed
assay stale --cost          # ...and what re-asking them would cost, before you spend it
```

**Why this is the best-value one.** It converts an unknown spend into a quoted one. Today
re-running the judged tier is "I don't know, some money". After this it is a number you see first.

**Jev's role: none for detection, and that is the point.** Knowing what is stale must not require
asking anything.

**Size.** Small-to-medium. One column, one comparison, one command. The exact tier reuses the
state builders that already exist.

---

## 3. Coverage counts files, not understanding

**What exists.** `project.coverage()` returns models, sources, tests, edges, readable, unreadable,
and where the SQL came from. That is a parse report.

**What is missing.** Every fact needed for "how well do we understand this model" is already in the
store and nothing rolls them up:

| fact | where it lives |
|---|---|
| is the grain known, and on what evidence | `ModelEntry.grain.source` — declared / judged / derived |
| is every column's provenance resolved | `provenance.kind` — `unknown` is the gap |
| are its claims verified | `claims` joined to `model_decisions` |
| has a person ruled on any of its findings | `adjudications` where `source='human'` |
| has anything been counted about it | `observed_keys` — 24 of 358 relations today |
| how much rests on it | `descendants`, `marts` |

**Design.** `assay understanding` — one row per model, each component and a band, ordered by blast
radius. The number a person acts on is not the score, it is *"these 12 models carry 19 marts and
nobody has ever looked at them"*.

**What it must not do.** Not one blended number. A model with a declared grain and no human
verdict is in a different situation from one with a judged grain and eight verdicts, and averaging
them into 0.62 destroys exactly the distinction that matters. Components stay visible, and the
ordering is by reach, because that is what decides where to spend a person's afternoon.

**Jev's role: none in the score.** But this is what tells you where Jev is worth spending — the
score exposes the gaps, the judged tier fills them.

**Size.** Medium. A join over facts that all exist, plus a surface.

---

## 4. Volume is absent, and it is Elementary's job

**The gap.** `observed_keys` covers **24 of 358** relations and counts *keys* — uniqueness and
nulls. Nothing tracks row counts over time, so "this table halved last night" is invisible.

**And Elementary is already installed and already does it.** In `sunny_data`:

```
main_elementary.data_monitoring_metrics        7,759 rows   row_count per table per bucket
main_elementary.alerts_anomaly_detection         476 rows   volume anomalies already computed
main_elementary.dbt_source_freshness_results     105 rows   the freshness gap, already measured
main_elementary.dbt_run_results               49,765 rows
```

**assay reads none of them**, and the only mention of Elementary in this codebase is a field note
about its 30 models being unparseable.

**Design: read, do not rebuild.** assay does not implement volume monitoring. It reads
`data_monitoring_metrics` and `alerts_anomaly_detection` where they exist, exactly as `practices`
already reads dbt-project-evaluator's `fct_*` tables rather than reimplementing its 23 checks. It
is optional and absent-tolerant: no Elementary means those checks report *not measured*, never
*fine*.

### And this is where the two tools become one thing neither is alone

Elementary detects volume anomalies with no semantics: *"`water_parcels` row count fell 41%."*

assay has the semantics and no volume: it knows that model's **declared grain**, the **claims its
own prose makes** about it, that its description says *"the FULL assessor roll"*, and that **19
marts** read it.

Joined, the sentence becomes:

> `water_parcels` fell 41% overnight. Its description claims "the FULL Maricopa assessor roll",
> a claim `code_contradicts_a_claim` already flags because line 27 drops rows missing an address
> or an owner. 19 marts read it. The row that moved is the one the claim is about.

**That is the Jev part, and it is the only one of the four where a judged question earns its
place.** The question is not "is this anomaly real" — Elementary settled that by counting. It is
*"does this movement contradict what this project says about itself?"*, which needs the claim, the
grain and the drop in one state, and is exactly the shape the claim families already handle.

A volume alert that knows what the table is supposed to be is a different product from one that
does not. Neither tool can produce that sentence alone, and assay does not have to rebuild
anything to get it.

**Size.** Medium for the read, small for the join. The judged question is one new family.

---

## 5. Freshness is partial, and Elementary closes it too

`feeds` sees a source changing its mind. Nothing sees a source going **quiet**, which is the
failure mode — documented as the one uncovered responsibility in `RESPONSIBILITIES.md`.

`dbt_source_freshness_results` has 105 rows in this warehouse already. Same treatment as volume:
read it, join it to what assay knows about what depends on that source, and report reach. A stale
source feeding two marts and one feeding nineteen are different emergencies.

---

## Ordering, and why

1. **Cost ledger.** Smallest, and it answers a question with a real scar behind it.
2. **Stale-vs-code.** Free, and it converts an unquoted spend into a quoted one — which makes
   everything judged easier to authorize.
3. **Understanding rollup.** Uses only facts that exist; tells you where 2 is worth spending.
4. **Elementary read + the claim-aware anomaly question.** Biggest, and the one that produces
   something neither tool has.

Freshness rides along with 4.

## What this list deliberately does not propose

- **Reimplementing volume monitoring.** Elementary is good at it and is already in the project.
- **A single health score.** Blending grain, provenance, claims and verdicts into one number
  destroys the distinction that decides what to do next.
- **Estimating cost where the provider returned no usage.** NULL and a count, not an average.
- **Asking Jev anything to find out what is stale.** Detection must be free or it will not run.
