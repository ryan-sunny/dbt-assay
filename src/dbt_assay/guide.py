"""How to set assay up, for somebody who has never used it -- and for the agent leading them.

*** THE TOOLS SAY WHAT ASSAY FOUND. NOTHING SAID HOW TO CONFIGURE IT. ***
Twelve MCP tools, all read-and-rule, and a skill that mentioned `audit.yml` once. An agent could
read every finding on a warehouse and could not help anybody write a vocabulary term, frame a
question, waive a finding with a reason that holds up, or decide what a check should do on a red
build. That is most of what a new project actually needs.

*** AND THE GUIDANCE IS NOT GENERIC ADVICE. ***
Every rule below was MEASURED, and most of them are already enforced somewhere in this codebase --
as a lint rule, a refusal, or a gate. Where the machine already holds the fact, this module asks
the machine rather than restating it, because a second copy of a list is the copy that drifts:
that is the `else check_name` lesson from the field, and a guide that quietly disagrees with the
linter is worse than no guide.

So: the WHY and the HOW are written here, because they live nowhere else. The WHAT -- which rules
exist, which families ship, which config keys are read -- is read from the real thing.
"""
from __future__ import annotations

# *** THE INDEX AND THE TOPIC LIST WERE TWO COPIES OF ONE LIST. ***
# `TOPICS` was a tuple and `index()` was a hand-written markdown table, so a topic added to one
# rendered fine, answered fine, and was invisible in the only place anybody looks for it. That is
# how `loop` shipped unlisted. One mapping now; both are derived from it.
BLURBS: dict[str, str] = {
    "start": "the order to do things in, and what costs nothing",
    "loop": "findings -> verdicts -> fixes -> fewer findings, and what measures it",
    "vocab": "what your words mean HERE, sent with every question",
    "questions": "how to frame one the model can actually answer",
    "waivers": "findings that are fine here, and the reason that holds up",
    "policy": "what each check does on a build, and what may gate",
    "explanations": "the options for adjudicating failed rows",
    "ruling": "reading a finding and recording what you concluded",
}
TOPICS = tuple(BLURBS)


def _lint_rules() -> list[str]:
    """The question-shape rules, from the linter that enforces them."""
    import inspect
    import re

    from . import lint
    src = inspect.getsource(lint)
    return sorted(set(re.findall(r'Issue\([^)]*?["\']([a-z_]{5,})["\']', src))
                  - {"error", "warning", "acknowledged"})


def _families() -> list[str]:
    from .contracts import load_all_banks
    return sorted(load_all_banks())


def _config_keys() -> list[str]:
    import re

    from .config import DEFAULT_YML
    return sorted(set(re.findall(r"^([a-z_]+):", DEFAULT_YML, re.MULTILINE)))


START = """\
# Setting assay up on a project that has never run it

Nothing here spends anything until step 5. Do them in order; each one answers a question the next
one needs.

1. `assay onboard` — looks at the project and tells you what to run, in what order, for THIS
   project. If models have no compiled SQL it says so, because a model assay cannot read is
   absent from every result below and that is not a pass.

2. `assay check` — every structural finding. No key, no network, no spend. This is a parser
   reading your compiled SQL, so everything it says is a fact about code rather than an opinion.

3. `assay tests --count-defaults` — tests that cannot fail, and how often each COALESCE default
   actually wins. "This test cannot fail" is true; "this default is 99% of your rows" is the
   sentence somebody acts on.

4. `assay init` — writes `audit.yml` with the defaults. It enables no spend. Then configure it,
   in this order, because each section is worth more once the one before it exists:

     vocab         what words mean HERE          `assay guide vocab`
     waivers       findings that are fine, why   `assay guide waivers`
     questions     what a check does on a build  `assay guide policy`
     explanations  options for row adjudication  `assay guide explanations`

5. Only now, the judged tier. `assay claims --extract` then `assay verify`, or
   `assay ask --dry-run` first, which prints the subject count and the cost estimate BEFORE
   spending anything. `jev.max_spend_usd` is a hard cap checked before the call.

6. `assay review -i` — rule on what came back, least certain first. Nothing gates a build until
   a person has done this: `min_adjudications` refuses `fail` for a judged question with too few
   HUMAN verdicts and downgrades it to `queue`.

**The number to watch is `ruled on`, and a good release makes it look worse.** Finding more raises
the denominator and nothing a release does raises the numerator, because that moves when somebody
reads SQL and at no other time.
"""

VOCAB = """\
# vocab: what words mean HERE

```yaml
vocab:
  wdid: "A Colorado structure id. Unique per structure; NOT unique per water right."
  admin_number: "Seniority as a sortable number. Smaller is MORE senior, which is backwards."
```

**What it is for.** Every judged question is sent with this map. A general model reading
`admin_number` will assume bigger is later; on this warehouse smaller is more senior, and a
question about seniority answered on that assumption is wrong in a way no confidence score shows.

**Why it pays more than it looks.** Measured in the field: fifteen terms written once made
`traverse` flag `wdid` joins into `water_rights` without anybody writing a water question.
Knowledge written once, reaching questions nobody wrote, is the whole promise of the config file.

**How to write one.** A term belongs here when a competent stranger would read it WRONGLY, not
when it is merely domain-specific. Three shapes that earn their place:

- **A word that means something narrower here.** `structure` is a diversion point, not a building.
- **A convention that inverts the obvious reading.** Smaller is more senior. Zero means unmeasured.
- **A key whose uniqueness is not what the name suggests.** `case_number` is unique within a
  division and not across the state. That one sentence is what stops a grain question guessing.

Write the sentence you would say to a new engineer on their first day, and stop there. This is
sent with every question, so length is paid for on every call — say the non-obvious thing and
leave out what the name already says.
"""

QUESTIONS = """\
# questions: how to frame one the model can actually answer

Your own questions go in `assay_questions/`, in this directory or any parent, or wherever
`ASSAY_QUESTIONS` points. A family with a NEW name is added. One with a SHIPPED name REPLACES it,
which is the point — a warehouse whose `column_role` needs an extra option should not have to
fork the tool.

**Run `assay banks --lint` on anything you write.** Every rule it enforces was learned from a
question that failed in a measurable way, so the linter is the accumulated list of mistakes and
you do not have to make them again.

## The rules that come from measurement

**It cannot do arithmetic, and it will not say so.** `units_are_what_the_column_claims` v1 asked
whether magnitudes were plausible. Given `land_acres` holding 218,235 it answered *consistent* at
0.82. That is square feet, wrong by 43,560x. TypeSafe publish the reason: the model "cannot
reliably judge whether two values are near each other." The fix was to SPLIT the question — the
model names the unit the NAME claims, which is language and which it does at 1.00, and code
compares the observed range, which is exact and free. If your question needs two numbers compared,
it is two questions.

**Absence is not disagreement, and you must say so in the criteria.** A claim about `d_class_cn`
read `contradicts` at 0.97 because the evidence listed thirty other columns and not that one. The
model could not see what it was asked about, so it did what TypeSafe document it does: returned a
confident non-answer. Say explicitly that silence is not contradiction, and check that the
evidence contains the subject before asking at all.

**Choose the evidence BY the subject, not a generic dump.** Measured: 0.96 with the state the
claim needed, 0.47 with one extra correct sentence added. Smaller state, better answer.

**Give it a way to decline.** A choice with no `cannot_tell` forces a pick, and a forced pick at
0.3 is indistinguishable from a real answer at 0.3 to anything reading the answer alone.

**Options must be able to lose.** Two options a reasonable reader could both apply to one subject
produce a coin flip that looks like a judgment. `assay banks --judge` asks whether any two options
could both be right about the same subject; it flagged `predicate_intent` at p=0.85, which
hand-measurement had independently shown to be a coin flip.

**An option must not route to another by name.** "The answer is `something_else`, not this" reads
as a disjointness guarantee to a text check, and the answering model does not honor it: measured,
one subject placed under both options at 0.55 and 0.63. `option_routes_to_another` catches this
statically, where nothing has to be consistent about anything.

**One subject, one claim.** "Boulder commercial building permits, residential filtered out" split
0.51 / 0.47 and flipped between runs, because one half is true and the other is not. A finding
cannot rest on a coin flip, so `assay claims` breaks prose into atomic claims before anything is
asked.

**Dates arrive as text.** Do not ask it to compare them. Compare them in code and ask about what
the comparison means.

## The shape

```yaml
my_question:
  id_prefix: "mine"            # MUST match the id your caller emits, or verdicts file under
                               # another family and count toward the wrong gate
  prompt_version: "mine.v1"    # moves whenever the TEXT moves, or `effectiveness` mixes
                               # answers to two different questions under one number
  subject: "..."               # what one call is about, so `assay ask` can drive it
  instructions:
    question: >-
      One question. Name the field being judged.
  criteria:
    an_option:
      what: "What this option means, in a sentence a person would use."
      not_for: "The case somebody will wrongly put here."
      examples: ["a real one from your warehouse"]
    cannot_tell:
      what: "The state does not carry what this question needs."
```

**A verdict is about a VERSION of a question.** Reword an option and bump the version, or
agreement over time silently averages answers to two different questions.
"""

WAIVERS = """\
# waivers: findings that are fine here, and why

```yaml
waivers:
  stg_blm_plss_sections:
    - question: bbox_as_radius
      reason: >-
        The envelope is built from stored corner columns and intersected with a land grant.
        The box IS the intended geometry; the model's own comment says so. Measured: 0 of
        4,812 rows change under a true radius.
```

**A reason is required, and the reason is a measurement.** Both waivers on the field warehouse
were measured before being waived, and the measurement IS the reason. A waiver whose
justification is "looks fine" is how a real finding gets silenced, and the person who wrote it
will not be the person who has to trust it in six months.

**A waived finding never reaches the findings table at all.** Nothing counts it, no page shows it,
the ruled-on denominator does not include it. That is correct — it is not outstanding debt — and
it is exactly why the bar is a measurement rather than an opinion.

**Waive a finding, not a check.** `waivers` is keyed by model. If a check is wrong everywhere,
that is not a waiver, that is `questions: {the_check: {action: off}}` — and if a shipped check is
wrong on YOUR warehouse, the better fix is usually to replace its question rather than silence it.
See `assay guide questions`.

**When you are tempted to waive, check whether the finding is telling you the check is blind.**
The field case: `source_reaches_nothing` fired on a source a Python enricher reads. The finding was
right about the dbt graph and wrong about the warehouse. The fix was `meta: {read_by: path.py}` on
the source, which records the fact where a human will also read it, rather than a waiver that
records nothing.
"""

POLICY = """\
# questions: what each check DOES on a build

```yaml
questions:
  arbitrary_pick: {action: fail}      # red build
  hop_multiplies_rows: {action: queue, threshold: 0.7}
  description_contradicts_the_code: {action: annotate}
  ranks_by_degrees: {action: off}

gating:
  min_adjudications: 20
  min_agreement: 0.0
```

**The four actions.** `fail` fails the build. `queue` puts it in front of a person and passes.
`annotate` records it and says nothing. `off` does not run it.

**Keyed by the CHECK a finding carries, NOT by the question family.** This is the mistake assay's
own shipped example made: a family name where a check name belongs configures nothing at all, and
nothing tells you, because a config that parses but matches nothing looks exactly like a config
that is working. `assay check` warns about a key no check claims — do not ignore that line.

**A judged check cannot fail your build until it has been measured.** `min_adjudications` refuses
`fail` for a judged question with too few HUMAN verdicts and downgrades it to `queue`. Agent
rulings do not count, and that is the whole design: an agent is the only thing in the loop that
could write a hundred rulings, so the number that decides whether a check has earned authority has
to be one it cannot touch.

**`min_agreement` is the other half.** A count of wrong answers is still a count. A question people
read twenty-five times and disagreed with twelve times has earned nothing. Leave it at 0 until
`assay effectiveness` has printed real rates for your project, then pick a number from those
rather than from a default.

**Structural checks may gate immediately.** A parser decided them; there is no error rate to
measure. Only the judged tier waits.
"""

EXPLANATIONS = """\
# explanations: the options for adjudicating failed rows

```yaml
explanations:
  water_rights:
    - genuinely_wrong: "The value cannot be true of the real world."
    - upstream_late: "The source had not published when this ran."
    - test_too_strict: "The row is fine and the assertion is wrong."
```

**THE OPTIONS ARE THE DOMAIN KNOWLEDGE.** This is the part of the config worth maintaining. When
a dbt test fails, `assay adjudicate` samples the failing rows and asks which of YOUR options
applies. A generic set gives generic answers; the right set turns a red test into a routed one.

There is one set per mart because the answers differ per mart. A generic fallback is always
available, so start with two or three options on the mart whose tests actually fail, and add more
when an answer keeps coming back as the decline.

**A bounded probe walks the project.** `assay probe -n 8` takes the eight least-recently-observed
relations, so running it on a schedule covers everything rather than re-reading the front of the
list. A relation that could not be counted still records the attempt, so one unreadable relation
cannot starve the cycle.

**And the timeline is worth saying out loud.** A drift check needs TWO observations of a relation
before it can say anything, so on a 275-relation project at `-n 8` that is about 35 passes to
first coverage and about 70 before `key_stopped_holding` can fire anywhere. On an hourly build,
roughly a day and a half, then three. It is not broken in week one; it has not finished looking.

**Measured worth:** on the field warehouse this called wells `genuinely_wrong` at 0.50–0.70 on a
290-foot well with a water level of 26,018 feet. The `accepted_range` test that surfaced them
catches 12 rows; the real invariant, `water_level_ft <= well_depth_ft`, holds on 708. It found a
data defect AND an inadequate test, from a sample of six rows.
"""

RULING = """\
# ruling: reading a finding and recording what you concluded

`assay review -i` is a / d / u / s, least certain first. Or `rule(finding=..., verdict=..., why=...)`
through MCP, which records what an AGENT concluded.

**Agree, disagree and unclear mean different things and fix different problems.** Disagreement
means the criteria are wrong — reword an option. Unclear means the state does not carry what the
question asks — add a field. `assay effectiveness` reports them separately for exactly this
reason, and unclear is never in the agreement denominator.

**Reporting a false positive is the most useful answer you can give.** A false positive nobody
reports stays in the list forever, and every reader after you pays for it. `assay disagreements`
groups the rejections: twelve on one warehouse turned out to be three separate bugs.

**An agent ruling is evidence and never authority.** It cannot gate a build, satisfy the verdict
floor, anchor the regression check, or move the ruled-on number. What it CAN do is triage: sixty
models with a finding and none looked at is a wall; six an agent believes are real is a place to
start, and the person's keypress is still the one that counts.

**Rule on the FINDING, not the model.** One model can carry eight findings of one check.
Measured: a blanket disagree on `dim_business` covered six edges of which two were real, because
the API let one keypress answer all of them. Pass the finding id.
"""

LOOP = """\
# loop: findings -> verdicts -> fixes -> fewer findings

The whole point, and the only part that compounds. Four steps, and the last one is what says
whether the first three were worth doing.

**1. `assay check`** finds it. Structural checks are free; judged ones cost a call and are cached.

**2. `assay review --emit review.html -t target/`** writes a form: one card per (model, check),
twenty at a time, highest blast radius first, with the SQL and any reading an agent recorded.
Filled in whenever there is ten minutes, offline, no server. `--load verdicts.json` records the
lot. One turn per finding is one turn per finding, and a backlog of 159 is 159 turns nobody sits
through -- that is why the answering leaves the conversation.

**3. `assay plan -t target/`** turns the agreed ones into what to change. The fix SHAPE is a
lookup on the check name, so it is exact and costs nothing. The WORDS are not in it.

**4. `assay check` again**, which reports:

    of the 12 finding(s) a person agreed with, 5 are gone and 7 are still here.

**That is the only number here that measures the LOOP rather than the tool.** Every other figure
moves when assay gets better. This one moves when somebody reads SQL and then changes it, and
neither a release nor an agent can touch it.

## What a verdict does, which is not obvious

`disagree` DISMISSES the finding -- permanently, from `check` and everything reading it. It is
what stops reviewing being a tax: without it, ruling 115 findings wrong left you with 115
findings. It lapses by itself if the model is edited into a genuinely different defect, because
the dismissal is keyed to the finding and a finding's identity includes its evidence.

`agree` removes nothing, because the finding is real. It is what makes step 4 answerable, and it
needs the FINDING: a verdict filed against (model, check) cannot say which of that model's eight
findings was the real one.

`unclear` removes nothing and gates nothing. It is evidence the question could not be answered
from the state it was given.

Agreeing and then dismissing does not count as fixed. That is a retraction, and counting it would
make the one honest number gameable by the person it measures.

## Only a person's verdict does any of this

An agent ruling triages what to read first. It never dismisses, never counts toward an agreement
rate, and never authorizes a gate -- an agent that could dismiss could silence a project by
reading none of it carefully. `source = 'human'` is set by the code path rather than by anything
about who ran it, and `--by` is free text that nothing validates, so the honesty of whoever runs
it is the entire mechanism.
"""


_TEXT = {"start": START, "vocab": VOCAB, "questions": QUESTIONS, "waivers": WAIVERS,
         "policy": POLICY, "explanations": EXPLANATIONS, "ruling": RULING,
         "loop": LOOP}


def guide(topic: str = "") -> str:
    """The authoring guidance for one topic, or the index."""
    t = (topic or "").strip().lower()
    if t in _TEXT:
        body = _TEXT[t]
        if t == "questions":
            rules = _lint_rules()
            body += ("\n## What `assay banks --lint` will check\n\n"
                     + "\n".join(f"- `{r}`" for r in rules)
                     + f"\n\nAnd the {len(_families())} families already shipping are a worked "
                       "example each: `assay banks` prints where every one came from.\n")
        return body
    return index()


def index() -> str:
    keys = _config_keys()
    return (
        "# Setting assay up\n\n"
        "`assay guide <topic>`, and `assay guide start` if this project has never run it.\n\n"
        "| topic | what it covers |\n|---|---|\n"
        + "".join(f"| `{t}` | {b} |\n" for t, b in BLURBS.items()) + "\n"
        f"`audit.yml` is read for: {', '.join('`' + k + '`' for k in keys)}.\n"
        "`assay init` writes one with the defaults and enables no spend.\n")
