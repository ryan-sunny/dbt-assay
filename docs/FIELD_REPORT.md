# Field report: the first production run, and everything wrong with what it produced

Written 2026-09-22 by the session that runs assay against `sunny_data`, for the session that builds assay.

What produced this: `assay check` and `assay page` and `assay form` run on the Contabo box against the real
warehouse (356 models, 210 sources, 33GB DuckDB), on 0.47.2, with Jev resolving. Store: 70MB, 23 runs, 264
findings, 136 human rulings, 105 agent rulings. The artifacts reviewed are `assay-prod.html` (11.9MB) and
`review-prod.html`.

Ryan read both end to end and reacted to every screen. His words are quoted where the wording carries the
signal. I verified each claim against the source or the store before writing it down, and said so where I
could not. Bands: BUG is wrong behavior, TEXT is shipped prose that should not exist, LAYOUT is presentation,
GAP is a missing capability.

---

## 1. Bugs, with the evidence

### 1.1 BUG. Pan-to-drag ate every node click on the chain tab

Ryan: "those fuckin nodes were clickable once upon a time and now they arent and that makes me MAD"

He is right and this is a real regression, not a layout illusion. `explorer.py:730`:

```js
s.addEventListener('pointerdown', ev => {
  from = {x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y};
  s.classList.add('drag');
  s.setPointerCapture(ev.pointerId);
});
```

The capture is taken on the SVG root on every press, unconditionally. Once the root captures the pointer,
every later pointer event for that pointer retargets to `s`, so the `click` lands on the canvas and never on
the node `<g>`. The handlers are still attached and still correct: `box()` at `explorer.py:584` takes an
`onclick`, adds class `clk` at line 600, and both the parent boxes (line ~459) and child boxes (line ~467)
pass `nodeCard`. They simply never fire.

Fix: do not capture on `pointerdown`. Capture on the first `pointermove` that exceeds a few pixels, or leave
capture off and suppress the pan on `pointerup` when total movement was under threshold. A tap has to stay a
tap.

Second, smaller: the focus node at `explorer.py:472` is the one box drawn with no `onclick` at all. The model
the neighborhood is about is the only thing you cannot click.

### 1.2 BUG. Every finding-scoped human ruling is stamped `(unversioned)`, permanently

This came out of chasing Ryan's question about old version numbers. One keypress in the review form writes two
adjudication rows, 1.5ms apart, and only one of them gets a version:

```
model.sunny_data.stg_blm_plss_sections                         assay.0.37.1   agree
model.sunny_data.stg_blm_plss_sections::finding::126f98596f47  ''             agree
```

Measured on the production store: 136 human rows, all distinct subjects, zero duplicates. 57 carry
`assay.0.37.1`, 70 carry `''`, 8 carry `water.prio.v4`, 1 carries `assay.0.33.2`. The 70 empty ones are
exactly the `::finding::` subjects.

The backfill at `store.py:495-517` resolves a version by looking up `model_decisions.prompt_version` and
falling back to `(select 'assay.' || r.assay_version from runs r where r.started_at <= a.decided_at ...)`.
That lookup is sound and the comment explaining it is sound. Something on the finding-scoped write path is
not reaching it.

Consequence: `prompt_version` exists so a release can be compared before and after. 70 of 136 human verdicts,
the ones attached to a specific finding rather than a model, can never take part in that comparison. They
will read `(unversioned)` forever.

### 1.3 BUG or unexplained. A version tag exists that `runs` cannot produce

5 agent rulings on 2026-09-20 at 19:48 are stamped `assay.0.20.0`. There is no 0.20.0 row in `runs`. The
latest run at or before that moment was 0.11.0 at 16:24. Under the documented lookup that tag is impossible.
Worth one query from your side. Either a second write path stamps versions, or `runs` lost rows.

### 1.4 BUG. `114 6 read` reads as `1146 read`

`explorer.py:1619`:

```js
return {label: c, n: n, note: read ? read + ' read' : '', ...}
```

`rankedBars` prints `n` then `note` with a space. `114` and `6 read` become `114 6 read`. Line 1112 already
does this correctly in the check dropdown with `' · '`. Use the same separator. Ryan: "it should have
like dot between those OBVIOUSLY."

### 1.5 The old version numbers on the verdict charts are correct data with a label that lies

Ryan: "whya re these such an old version of assay or am i misreading?"

He is misreading, and the label is why. The tag is the assay that was running when the verdict was made,
which the store resolves by lookup. His agent rulings genuinely were made on 2026-09-20 when 0.11.0 was live,
and `runs` confirms it. But the chart renders `arbitrary_pick assay.0.11.0` with nothing saying *ruled under*,
so it reads as "assay is running something old". Label it, or move the version out of the bar label and into
the tooltip.

### 1.6 The Spend tab has nothing from today and cannot say why

Ryan: "spend is fine but did it not include the run we JJUST did?"

Measured: 7 runs on 2026-09-22, zero `model_calls` rows on 2026-09-22. `max(called_at)` is 2026-09-21
22:06:32. The most likely truth is that today's runs cost nothing because the judged states were cached and
nothing needed asking. That is a good story and the tab does not tell it. It silently omits the day instead,
which is indistinguishable from broken recording.

Show the day with a zero. Say why it is zero. Ryan also wants "spend per day graph or something or spend per
run kinda graph idk just a little visuali spice", which is the same fix with a series behind it.

### 1.7 `assay.yml` declares three columns as varchar that the store holds as INTEGER

Carried from earlier in the run. `available`, `carried` and `dropped` are declared varchar. The store has
INTEGER. This made me report that `water_section_fingerprint` drops 65 columns when the real figure is 794,
because `len()` on a varchar returns string length. It also breaks the shipped `example_assay_join_surface`
query, which does `sum(VARCHAR)`.

---

## 2. Shipped text that has to go

All four of these are in the package, not in Ryan's data. I grepped to be sure.

### 2.1 TEXT. assay's own release history is printed into the user's report

`suggest.py:341`:

```
"waived one reason, and the fix was structural (0.15.0, 0.21.1), not a third "
```

Ryan: "like what the FUCK is this sentence? is this abobout assay itself like WHAT? why the FUCK would shit
like that be ever fucking mentined on this shit?"

Also `suggest.py:274` and `suggest.py:426`, same idea. A user reading their own warehouse report has no way to
know what 0.15.0 and 0.21.1 are, and no reason to care. Delete the version references. If the underlying point
is worth making, make it about their project.

### 2.2 TEXT. Colorado water law is a shipped example in a general-purpose tool

`questions/semantics.yml:78`:

```
"per C.R.S. 37-92-302(1)(c), objections close two months after filing"
```

`lint.py:671` uses `nontributary` as its worked example. `reviewform.py:810` uses
`'e.g. conditional_right: a claim on water not yet diverted'` as a placeholder, repeated on every card on the
page.

Ryan: "this C.R.S shit AGAIN is very water tabele related and dbtp-assay is a fucking genreal tool with
fucking NOTHING to do with water table its just being used by it."

This leaked out of developing against this warehouse. Replace with examples from a generic domain. Orders,
customers, invoices.

### 2.3 TEXT. The min_adjudications paragraph prints on cards with zero verdicts

`explorer.py:1274`, unconditional:

> Only a human verdict counts toward min_adjudications. An agent ruling is evidence and never authority: it
> cannot gate a build, satisfy the verdict floor, anchor the regression check, or move the ruled-on number.

Ryan: "like what is this fucking sentence? why is this shit here?"

Screenshot context: `column_role`, 5,252 subjects asked, human verdicts 0, all verdicts 0. The paragraph
explains a distinction between two things that are both zero. Show it once in the tab header, or only when the
card actually has both kinds.

### 2.4 TEXT. "thresholds and all"

`suggest.py:396`:

```python
draft += "\n  # this is what `assay init` ships for it, thresholds and all."
```

Ryan: "thresholds and all :B SHUT THE FUCK UP WITH THIS SHIT"

It is twee, it repeats on every card, and it says nothing the reader can act on.

### 2.5 TEXT. The empty-vocab explainer

`explorer.py:1496`:

> Candidates drawn from what the checks actually found, each with the measurement behind it. assay proposes
> the candidate and the measurement. It never proposes the meaning, so every `means:` and `implies:` below is
> empty on purpose.

Ryan: "vonfusing shitty sentence. lots of those shitty confusing sentences in here tbh."

The idea is right. The sentence takes three clauses to reach it and leads with the abstraction. Something
closer to: "assay measured these and cannot know what they mean. Fill in `means:` and `implies:` yourself."

### 2.6 TEXT. "severity decides"

`explorer.py:1649` and `cli.py:5944`. Ryan: "tf is severity decides?"

The note reads `assay suggests annotate` when there is a shipped opinion and `severity decides` when there is
not. The second names a mechanism the reader cannot see. Say what it resolves to for this check: "not
configured, falls back to warn, cannot fail a build."

---

## 3. The presentation problem, which is the largest item here

Ryan, repeatedly, across nearly every tab: "the actual PRESENTATION of information needs to be much much
better. the layout here is just abysmal and looks buggy." And: "i just mean like coherent and providing ONLY
THE NEEDED CONTEXT FOR THE USER TO ACCOMPLISH THE TASK."

He is not asking for visual polish. He said so directly: "im not even talking antything fancy like EVER".

### 3.1 LAYOUT. One shared two-pane layout, used by every tab

Findings already has the right shape: a filter rail and scrollable list on the left, a detail pane on the
right. The bug is that the left column, including its filters, dropdowns and checkboxes, is not height-bound
to the right pane, so the page scrolls as a whole when it should not.

Ryan: "the left area INCLDUING filters and dropdowns and checkboxes and the scorllable list need to be the
same size as the window on the right. so that the screen doesnt jankily scroll down when it shouldtn."

Then make every other tab use it. He named them: Answers, Claims, What to configure, Questions. "everything
just sharing a perfectly designed fleixle layout is def the ideal state."

What to configure is the worst offender because it does not use it: 113 items in one vertical stack. "i gotta
scroll for 100 fucking years because of how badlyl made that UI is man jesus CHRIST. no reason this shit
doesnt fit into the same layout as like the findings tab and consolidate so well."

### 3.2 LAYOUT. Repetition is fine. Undifferentiated repetition is not

This came up on three separate screens and it is one complaint.

Vocabulary, 44 items, all rendering as "`X` is 99.68% unique in Y and is named like a key" with an identical
empty YAML block underneath. Per-check policy, 21 items, all rendering as "`X` is firing and audit.yml says
nothing about it" with an identical YAML block. Vocab candidates, all rendering as "N models join on `X` (M
hops) and the vocab does not define it".

Ryan: "repeitive and unclear should be like better. split by reason it was flagged or something idk just needs
to be so much better presentation wise. and actually make fucking sense." And on the next: "this part is so
repetitive which is fine but it needs to be presented so much better."

The content is correct. The rendering gives 44 cards equal weight and equal height when what the reader needs
is the grouping, the differences, and a way to act on a batch. This is a table, or a grouped list keyed by the
reason, not 44 stacked cards.

### 3.3 LAYOUT. Markdown that does not render

Ryan: "dont even get me started on all the attempted markdown that doeesnt render lol i mean can these be MD
files instead maybe it would be better and cuter and easier and look better idk big understaking though but
whatever like im sick of this shit like i want fuckin shit to be nice."

Worth taking seriously as a question rather than a directive. Some of these panes are documents, not
interfaces. If a pane is prose plus code blocks, rendering it as Markdown is less code than hand-building the
DOM and it will look better. He flagged it as possibly a big undertaking himself.

### 3.4 LAYOUT. Reading quality of generated sentences

Ryan on the waivers pane: "like it reads SO BAD SO SO BAD its so confusing and unclear and ppoorly presented."
On the agreement cards: "this just reads so fucking badly man." On the same-reason cards: "1 i think these are
just wrong 2 i think like... it reads so bad and looks like fucking shit."

Sample of what he is reading:

```
`hop_multiplies_rows` was ruled wrong on az_address_sections, and nothing waives it
`code_contradicts_a_claim` agrees 2/6 (33%), and has no action set
```

Both are compressed to the point of needing decoding. The second in particular buries the useful part, which
is that 6 rulings is under the floor of 20 and the number should not be trusted yet. That fact is present, as
a bullet, below the headline that states the untrustworthy number.

---

## 4. Report page, tab by tab

### 4.1 Overview. Complete rework

Ryan: "overview (which i fucking hate and need sa complete and total rework and overhaul to present the real
relevant pertitent info the IMMEDIATELY show the demonstrable impact of assay and how it can do shit that
otherwise jsut wasnt happening at this scale with this level of intelligence."

This is the pitch surface. He is about to share assay on forums. Right now the overview leads with a
findings-by-check bar chart, which is an inventory. What it should lead with is the thing nothing else could
have done: 47,491 claims read, 5,794 sentences classified, 18,079 answers, across 356 models, for a few cents,
finding 264 defects that no dbt test can express.

### 4.2 The chain. The drawing does not survive a real DAG

20 parents drawn in a single row, fit-zoomed until the node text is unreadable. Ryan: "20 findings lol and its
just abig fuckin mess it needs to be like a 5x4 grid or something with enough space or something."

There is already a fallback at `explorer.py` for `!drawIn`, printing "N parents, too many to draw." The
threshold is too high, or the layout needs to wrap rather than spread. A grid with a per-row cap and real
spacing, plus the click fix from 1.1.

### 4.3 What to configure. Ryan's harshest reaction, and a scope decision

"all of these seem retarded and wrong? overall this what to configure page needs a FUCKLOAD of work a full
overhaul basically itmkaes no fucking sense at all and is formatted like shit and doesnt even offer the
ability to configure anything? it just says what to configure? i understand you can take it to the agent but
like jesus christ what? this page feels like it turns to absolute shit."

Then he settled the scope himself: "disregard the what to cofnigure should be including configuration and shit
on the report html but it needs to be on the form html and idk man they need ot like link to each other to
open each other or omsehitng so you can reference it or even combine them idk."

So: the report page stays read-only and keeps the *what*. The form gets the *doing*. The two link to each
other. Combining them is on the table.

### 4.4 Answers. Needs a title and a shape

Ryan: "the answers tab liek i ahve no clue what its even talking about i cant even see the questions? i can
see thefamily and click in sure but like... what the FUCK am i even looking at? this shit needs like an
overview and a title and shit and needs to be not this dogshit table that is confusing as fuck... it should be
like by question showing the question and showing the shit below it or something."

What he sees after clicking a family: a 10-column table, 5,794 rows showing 2,000, columns QUESTION / SUBJECT
/ ABOUT / ANSWERED / CONF / NEXT BEST / VERSION, sorted ascending by confidence. The question text itself is
not on the screen. `sentence__7` is not a question.

Show the question. Then the answers under it.

### 4.5 One row is displaying prose where a model name belongs

On the Answers table, the SUBJECT cell of one row contains:

> Adjudications key to a judged decision and a structural finding has none behind it, so this is the honest
> weaker claim:

Ryan: "is that about an assay column with adjudicatinos as the fuckin word or is that water table shit?"

I could not find that string in any sunny-data source file. It reads like assay's own prose. Either the
sentence extractor is picking up text it should not, or a subject label is falling through to the sentence
body. Worth tracing on your side.

### 4.6 Questions and Claims. Fold into the shared layout, per 3.1

---

## 5. The review form

Overall: "def better than other page bc simpler but still struggles big time with the lack of fuckin proper
presentation and enough information and ocntext on how to actually fill out the page and shit."

### 5.1 LAYOUT. The header moves when you change tabs

"the download handback and the name should be up top by the tabs to the right of those so it doesnt get moved
around by the UI when swithcing tabs."

The name field, the download button and the answered counter sit in a per-tab toolbar. Move them next to the
tab strip so they hold position.

### 5.2 The Words tab does not say what to do

"readpeating that example is annoying. and its SO UNCLEAR Waht you should actually be doing here."

Every card on the tab shows the same placeholder, `e.g. conditional_right: a claim on water not yet diverted`
(`reviewform.py:810`, and see 2.2). The header says "Options for the failing-row family, per mart. These ARE
the domain knowledge: the generic set is always available and these are added to it." The cards say
`(NEW OPTION NAME)` above an empty box. Nothing states the task in the imperative or shows one filled example.

### 5.3 GAP. Prefill the why box with the agent's reading

"if optional is empty i feel like taking the agents output should be an option as the why... if thats not
stored already."

It is stored. The card already renders "AN AGENT READ THIS" with the full reason above the radio buttons. When
the human picks agree, offer that text as the why with one click. Cheap, and it turns a blank box into a
confirmation.

### 5.4 GAP. The form should not be showing unread findings at all

"why is sometimes saying an agent hasnt read this isnt that YOUR job when YOURE using the skills to make the
report and shit? like you read the unread shit and put in your shit? i thought it was idk."

He is right and this is a skill defect, not a form defect. 167 cards to rule on, 91 carry a reading, so 76 say
"nothing on this question". The skill that generates the form should drain `review_queue` and `rule` on
everything first, then render. The human should never open a form where a quarter of the cards have no agent
reading.

### 5.5 GAP. Downloading handback.json does not close the loop

"clicking that download verdcits needs to like trigger the session ro something like update it so its aware it
was made and the agent and review it and integrate or we need to like automate that process or someting idk
just outputting that file is cool and all but we need to actually like perform the stpes to make that file
actually do something."

The CLI path exists: `assay review --load handback.json --apply` (`cli.py:4106`, instructions at 4247). The
MCP server does not expose it. I listed the tools: contract, lineage, blast_radius, findings, changed_contracts,
practices, rule, review_queue, violations, claims, traversal, rebase, plan, spend, stale, vocabulary,
suggestions, evidence. There is no load.

Add a `load_handback(path)` tool, and have the skill check the downloads directory or ask for the path
immediately after handing over the form. Right now the human does the most valuable work in the system and
then the file sits in ~/Downloads.

### 5.6 GAP. Put the rest of the config in the form

"i feel like theres other configs and shit that shuld accessible here. i mean like i know you can edit through
claude interface or whatever and like the dbt files and shit too but sitll? like might as well have it all be
in the form area too right? and its all version control trackable properly anyway so its like the same thing
anyway i reckon."

Vocab, waivers, explanations, gating floors, per-check actions. It all ends up in `audit.yml` under version
control either way, so a form that writes it is the same artifact by a friendlier route. This is the other
half of the 4.3 decision.

---

## 6. Capabilities Ryan asked for

### 6.1 Seed vocab from column descriptions, and flag descriptions that drift

"for vocab yeah pulling from column descriptions first would be ideal obvi... so like ya i guess its not just
like define the vocab sso its HERE but like define in that warehouses model output like col descriptions and
we can pull those for vocab. and then track changse and shit that way."

Directly connected to his other question, which I can now answer. He asked whether the description feeds the
probabilistic role judgments he was looking at (`allows_domestic` judged @0.99, `decreed_use_codes` judged
@0.52). It does not. `subjects.py:148-164`, the column subject state is:

```python
state=_prune({"model": m.name, "column": c,
              "expression": (expr or "")[:300] or None,
              "derived_from": roots.get(c.lower()),
              "other_columns": [...][:25]})
```

No description. The role is judged from the column name, its expression, its roots and its sibling names. The
schema.yml description is not consulted at column level anywhere I could find. Note also the silent caps:
80 columns per model, 25 siblings.

So his proposal is not redundant with anything that exists. Three pieces:

1. Seed vocab candidates from column descriptions rather than leaving `means:` and `implies:` empty.
2. Feed the description into the column subject state so role judgments can use it, which likely moves that
   0.52 on `decreed_use_codes`.
3. A column-level analogue of `description_contradicts_the_code`, flagging a description that has drifted from
   how the column is actually produced and used. He asked for exactly this: "be albe to kinda flag like if it
   seems like the comment or description is falling out of line with the meaning or usage of that column in
   the warehouse basically. dope shit like that."

He also wants missing descriptions flagged, with ingestion sources called out: "column descriptions are those
required or flagged that they should be included and like especially for like the ingestion sources and shit".

And a stretch: "ideally we could like provide some level of descrition based on the outputs of the various
questions so if there isnt an existing description we could like offer it at some level lol... idk... i mean
other shit can do that better presumably." He is ambivalent on that last one.

### 6.2 Disagreement across models on what a column means

"being able to trace like a dag of queries like if the models disagree on the column description or something
shomehow ya know?"

Same column name, described differently in two places, or described one way and used another downstream. This
is the `section_id` problem generalized, and `section_id` is the one that cost this project real money.

---

## 7. What it took to get this running on production

Answering the original question. These are the things that went wrong between "assay works on my laptop" and
"assay ran against the real warehouse", in the order they bit.

### 7.1 The store was invisible to the container and assay reported success

I staged the store at `/home/ryan/sunny-data/.assay-run.duckdb`. `/app` in the container is not the repo
mount. Only `warehouse`, `dagster_home`, `transform`, `ingestion`, `enrichment`, `orchestration`, `delivery`,
`discovery`, `command_center` and `assets` are bound.

assay created an empty store at the path it could not find and reported `0 of 76 model(s) ruled`. No error. No
warning. An empty store and a clean warehouse produce the same output.

This is the single most dangerous behavior I hit. A store that was just created should not be silently
equivalent to a store with nothing wrong in it. Say "this store is new and has no history" on the first run
against an empty store, and say it in the page too.

Fixed by staging at `transform/.assay-run.duckdb`, which is bound.

### 7.2 `docker compose restart` does not pick up a new env var

Added `TYPESAFE_API_KEY` to the box `.env`, restarted, no key. Environment is baked at container create.
`docker compose up -d` recreates and works. Not an assay problem, but it is the kind of thing an onboarding
doc should say, because the failure looks like "assay cannot see my key".

### 7.3 DuckDB's single-writer lock versus a scheduled pipeline

`check --verify` hung for 11 minutes. It was blocked on the write lock held by `water_reports_nightly`, which
had been running 37 minutes with two more jobs queued behind it. Killing my run left a stale lock held by PID
70034 that needed `kill -9`.

assay could handle this much better. Before taking the lock, look at who holds it and say so. "Waiting on
PID 70034, holding the write lock since 19:42" is a completely different experience from a cursor that does
not move. A `--timeout` would help too.

### 7.4 uvx served stale code while stamping the new version number

This is the one worth carrying furthest. The production page rendered completely blank while the local page
was fine, on the same version pin. `node --check` on the extracted script gave
`SyntaxError: Unexpected identifier 'check'` at line 1251: two string literals concatenated with the quotes
eaten, which killed the entire 81KB script. The static HTML tabs rendered, so the page looked structurally
present and was entirely dead.

After the 0.47.2 fix it was still blank on the box and still fine locally. I settled it by reading
`explorer.py` out of the installed wheel inside the container. The published wheel was correct. uvx was
serving a cached 0.47.1 environment while the process reported itself as 0.47.2.

`uvx --refresh --from dbt-assay==0.47.2` produced a correct page from the identical pin.

Two takeaways. Operationally, always `--refresh` on the box. For assay, the version stamp on the page is an
assertion the page makes about itself, and in this case it was false. Embed a content hash of the generating
module next to the version so the stamp is evidence. It is the same argument that `state_hash` already makes
for judged answers.

Related: `uvx --from dbt-assay==0.47.2` reported the version unsatisfiable while the PyPI JSON API already
listed it. The `/simple/` index had not propagated and uvx had a cached index listing. `--refresh` fixed both.

### 7.5 Custom questions: the kind is not discoverable and a wrong one lints clean

I wrote two custom questions for water. The first drafts lint-passed and never fired, because a question can
only ask what its subject kind's state can answer. `filters` is only carried by `model`. A question about an
absent predicate cannot be asked one predicate at a time, and `edge` does not carry filters at all.

Nothing told me this. It cost an hour and I wrote the reason into a comment block at the top of
`assay_questions/water_sections.yml` so the next person does not repeat it. The lint should catch it: if a
question's `instructions` or `criteria` reference a state field the declared `subject` does not carry, that is
a lint error, and it is mechanically checkable.

Separately, my first draft of `call_exposure_includes_nontributary` failed lint on `not_a_calculator`. The
lint was right and the message was clear. That one worked well.

### 7.6 Vocab scoping was the highest-value config change of the whole run

Adding `applies_to: {select:, exclude:}` to 12 of 16 terms took the lint from 13 warnings to 1. Vocab is
injected into every judged state, so an unscoped water term was steering judgments on business licences.
Worth saying loudly in the onboarding path, because the default of writing an unscoped term is the wrong
default at this warehouse's size.

### 7.7 Things still open at the end of the run

- 63 findings a person agreed with, 0 fixed. The page says so plainly, which is good, and it is the number
  that matters most.
- 15 checks fire that `audit.yml` does not name.
- `dbt source freshness` has been dead since 2026-07-08 on both the box and the laptop, 105 rows with an
  identical timestamp. assay cannot see that it is stale, and assay is the thing that is supposed to know.
- `data_monitoring_metrics` reports `live` with 6,042 rows and "only 0 writes recorded" at the same time. The
  cadence query and the state query disagree.
- `assay_plan.jsonl` and the loop metric are not exported to the warehouse.
- No import path from the `assay_adjudications.csv` seed back into a store.

---

## 8. Server or skill

Splitting the above by where the fix belongs.

Belongs in the MCP server:

- `load_handback(path)`. The largest single gap. 5.5.
- An empty-store signal on every tool that reads the store, so an agent cannot mistake a new store for a clean
  warehouse. 7.1.
- Lock holder reporting and a timeout on anything that writes. 7.3.
- Column descriptions in the column subject state. 6.1.

Belongs in the skills:

- The form skill must drain `review_queue` and `rule` on everything before rendering the form. 5.4.
- The form skill must offer to load the handback the moment the human downloads it, rather than ending the
  turn on "open this file". 5.5.
- The onboarding skill should scope vocab with `applies_to` by default and explain why. 7.6.
- The onboarding skill should state the `uvx --refresh` requirement and the container env recreate. 7.2, 7.4.

Belongs in lint:

- A question referencing state its declared subject kind does not carry. 7.5.

Belongs in release verification:

- `node --check` on the extracted page script. It caught the blank page in ten seconds, twice. File size,
  exit code, determinism and round-trip tests all passed on a completely dead page.
- A Playwright load asserting zero `pageerror` and non-empty content in each tab pane.

Both are seconds, need no warehouse, and are the difference between shipping a deterministic valid-HTML file
and shipping one that runs.

---

## 9. What is working

Worth recording, because the list above is all complaint.

The findings themselves are good. 264 findings on a warehouse this size, and Ryan read and ruled on 136 of
them without disputing the substance of any. His complaints are about how they are displayed, never about
whether they are real.

The two-tier design pays. The structural tier found `arbitrary_pick` 33 times, and this project has shipped
that exact bug three times before by hand. Total spend to judge 356 models, 47,491 claims and 5,794 sentences
was about $1.29 over two days.

`assay plan` produces real work items. The deferral mechanism works. The lint caught a bad custom question
with a clear message. And the one number the system refuses to let a release improve, the count a person
actually ruled on, is the right number to have built the whole thing around.

---

## 10. Requested build: warehouse query cost accounting

Ryan asked for this directly and wants it in before the monitoring sweep runs, so that the first sweep is a
baseline with cost recorded rather than a sweep we have to repeat. His framing:

> "i wanna track (if we can) like bytes processed or consomued all that metrics like around how much those
> queries 'cost' bc duckdb its free ofc but snowflake or bq and shit not so mcuh so the queries need to be
> like beru anylistic and quick and optimized andl ike not doing big calcs or anything mustoly just aggs and
> shit"

Settled with him: exact stays the default, sampling is an opt-in for people with big warehouses.

### 10.1 Why this matters more than it looks

assay is about to be shown to people who do not run DuckDB. For them every probe statement is a line item.
The first question a BigQuery user asks is "what will this cost me to run", and right now assay cannot answer
it at all. Being able to answer it before execution is a better opening argument than anything on the overview
tab.

### 10.2 The existing probe design is DuckDB-shaped, and that is not portable

`probe.py:154-162` builds one statement carrying `count(*)`, `count(col)` and `count(distinct col)` for every
candidate column, so twenty columns cost one scan instead of twenty. The docstring at `probe.py:16` reasons
about this explicitly and concludes that an approximate first pass would cut compute inside the scan but not
the I/O that dominates.

That is correct for DuckDB and correct for Snowflake. It inverts on BigQuery.

| engine | billed on | batching many columns into one scan |
|---|---|---|
| DuckDB | nothing | correct |
| Snowflake | warehouse seconds, micro-partitions pruned | correct |
| BigQuery | bytes of the columns read | worst case, bills every column |

On BigQuery the bill is a function of which columns are touched, not how many statements touch them.
Batching twenty `count(distinct)` into one statement bills for all twenty columns and saves nothing.
`APPROX_COUNT_DISTINCT` does not help either, because it still reads the column. The only levers that reduce
a BigQuery bill are touching fewer columns and sampling.

This does not mean the current design is wrong. It means the cost model is an engine property and assay should
know which one it is under, and say so.

### 10.3 The architectural constraint

`probe.py:376`: "assay never holds a credential." Every warehouse read goes out through
`dbt show --inline` (`run_via_dbt` at `probe.py:206`, `run_sql` at `probe.py:375`). Those two functions are
the only places a query reaches a warehouse, which makes them the only two places to instrument, and it is a
small surface.

It also means assay cannot call BigQuery's `jobs.query` dry-run API itself to get an exact
`totalBytesProcessed`. Any real engine number has to come back through dbt. Worth verifying on your side
whether `dbt show --log-format json` surfaces the adapter response, since the BigQuery adapter does carry
bytes processed on its `AdapterResponse`. If it does, v2 gets exact numbers for free. If it does not, the
estimate below is what there is, and it must be labeled as an estimate.

### 10.4 Schema

Mirror `model_calls`, which already records what assay spent on Jev per call. The symmetry is the point: one
table for what assay spent on thinking, one for what it spent on the warehouse, and the same tab renders both.

```
warehouse_calls(
  call_id        varchar,     -- content hash of the statement, so a rerun is identifiable
  run_id         varchar,     -- joins to runs
  caller         varchar,     -- 'assay.probe.keys', 'assay.probe.profile', 'assay.volume', ...
  relation       varchar,
  statement_kind varchar,     -- 'key_scan' | 'profile' | 'sample' | 'metadata'
  dialect        varchar,
  columns_touched integer,
  column_names   varchar,     -- json array, so a cost can be attributed to a column later
  rows_returned  bigint,
  rows_scanned   bigint,      -- from the relation's known row_count, not measured
  bytes_estimated bigint,
  bytes_measured bigint,      -- null unless the adapter gave a real number
  estimate_basis varchar,     -- 'declared_types' | 'adapter' | 'unknown'
  sampled        boolean,
  sample_rows    bigint,
  wall_ms        integer,
  usd_estimated  double,
  rate_card      varchar,     -- which rate produced usd_estimated
  called_at      timestamp
)
```

Two rules on this table. `bytes_measured` stays null rather than being filled with the estimate, because a
column that silently mixes measured and guessed numbers is the same failure as the varchar/INTEGER mismatch in
1.7. And `estimate_basis` is not optional, for the same reason.

### 10.5 What can honestly be computed without a credential

Per statement, from what assay already has: the relation, the exact column list (it builds the SQL), the
relation's row count from `observed_keys`, and the declared column types from the manifest. Bytes estimated is
then `rows × sum(width(type))` over the columns touched, using per-dialect nominal widths, which is exactly
how BigQuery prices a scan. For a variable-width type the nominal width is a guess and should be conservative.

Wall time comes from wrapping the `subprocess.run` calls. On Snowflake, wall time is the thing being billed,
so it is the measurement rather than a proxy.

Pricing is a rate card in `audit.yml`, not a constant in the code, because published rates move and a number
assay cannot justify is worse than no number:

```yaml
cost:
  engine: bigquery          # or duckdb, snowflake. default: read from the dbt profile
  rate_card: bigquery.on_demand.2026
  usd_per_tb_scanned: 6.25
  # snowflake: usd_per_credit + warehouse size
```

### 10.6 `--dry-run`

Prints every statement the sweep would issue, the columns each touches, the estimated bytes and the estimated
total, and executes nothing. This is the feature that answers "what will this cost me" before a stranger runs
assay against a warehouse they pay for. It needs no credential and no warehouse, which means it also works as
a test.

### 10.7 `--sample`, opt-in, and it must label what it did

Exact stays the default. `probe.py:13` treats exactness as the whole point, "settles it exactly, once, and
caches forever", and that is worth protecting. But a uniqueness check on a billion-row BigQuery table is a
real bill, and Ryan's call is to offer the escape hatch: "exact and offering sample as an opt-in in case they
got BIG OLE DBS."

Requirements:

- Dialect-correct sampling. `using sample n%` on DuckDB, `TABLESAMPLE SYSTEM (n PERCENT)` on BigQuery,
  `sample (n)` on Snowflake.
- The `Observation` carries `sampled` and the sample size, and every surface that prints the finding prints
  that it was sampled. A sampled uniqueness result is evidence, not a settled fact, and it must not be cached
  as though it were one.
- A sampled result must never satisfy the same claim an exact one does. `probe.py:26` already draws the line
  between "unique in today's data" and a constraint. Sampled sits one step weaker again, and the language
  should say so.
- This is the same class of problem as 1.2: a weaker record that looks like a stronger one because a field
  did not get set. Set the field at the point of creation, not at the point of display.

### 10.8 Where it surfaces

- `assay cost`, or a second section under `assay spend`, showing warehouse spend next to Jev spend.
- The Spend tab gets both series, which also fixes 1.6, since a day with runs and no spend renders as a zero
  rather than as an absent row.
- `assay probe --dry-run` prints the estimate and the statement list.
- The MCP server gets the estimate on `stale` and on whatever triggers a sweep, so an agent can see the cost
  of the thing it is about to do before doing it.

### 10.9 Order of work

1. Wrap `run_via_dbt` and `run_sql` with timing and a `warehouse_calls` write. Nothing else changes.
2. Estimate bytes from declared types and the known row count. Rate card in `audit.yml`.
3. `--dry-run`.
4. `--sample`, with the label plumbed all the way to every surface.
5. Verify whether `dbt show --log-format json` carries the adapter response. If it does, fill `bytes_measured`
   and set `estimate_basis` to `adapter`.

Steps 1 and 2 are what the baseline sweep needs. The rest can land after.

---

## 11. The mark, and removing the Foghorn Leghorn references

Done on the README side already. The page and form side is left here as a spec because both files are in your
active tree.

### 11.1 Removed from README.md

- `<img src="docs/foghorn.jpg" align="right" width="210" alt="">` on line 3
- `*I say, I say -- assay.*` on line 5, which is the catchphrase

`docs/foghorn.jpg` is deleted. Nothing else in the repo referenced it, and no other line in the README carried
the bit. `docs/FIELD_NOTES.md:2724` still records the history of trying it in the header and reverting, which
is a log entry and should stay.

The README now opens with the mark right-aligned at 116px, then straight into the tagline that was always
doing the real work:

```markdown
# assay

<img src="docs/assay-mark.svg" align="right" width="116" alt="">

**Recover the semantics your warehouse never wrote down.**
```

### 11.2 The mark

`docs/assay-mark.svg`. A microtiter plate: rounded square, 4x4 grid of 16 wells, 11 filled and 5 clear toward
the bottom right, the way a plate reads part way through a run.

Palette is the real-plate one Ryan picked over the Suns alternative: yellow `#F3C337`, pink `#EC6A7C`, blue
`#4A86C8`, clear wells white with a `#BCC7CE` ring, plate `#F6F8F9` with a `#C3CDD3` edge.

Two reasons it holds up at 20px, which is the size that matters for the header. The wells are flat discs with
no interior detail, so nothing turns to mush. And the blue is the only cool colour, so the mark still reads as
three colours rather than two when it is small. The Suns palette failed that second test, since the orange and
the red sit too close together to separate at header size.

The filled-and-clear split is not decoration. It says what the product says: some wells have been read, some
have not yet.

### 11.3 Where it goes in the page and the form

Both pages are self-contained and deterministic, so this has to be inline SVG. Not a file reference, not a
raster data URI. It is 977 bytes against an 11.9MB page.

`explorer.py:388`, the page header:

```python
<h1>{e(meta['project'])}<span>everything assay knows</span></h1>
```

`reviewform.py:952`, the form header:

```python
<h1>{e(project)}<span>{len(card_list)} to rule on &middot; ...
```

Put the mark before the project name in both, at 22 to 24px, vertically centred against the `h1` baseline.
Suggested markup, with the `svg` first inside a flex `h1`:

```html
<svg class="mark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="assay">...</svg>
```

```css
h1 { display: flex; align-items: center; gap: 9px; }
.mark { width: 23px; height: 23px; flex: none; }
```

The full inline string, ready to paste. It contains no braces, so it drops into an f-string without escaping:

```
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="assay"><rect x="2.5" y="2.5" width="59" height="59" rx="9" fill="#F6F8F9" stroke="#C3CDD3" stroke-width="3"/><g stroke="#BCC7CE" stroke-width="1.5"><circle cx="14" cy="14" r="5" fill="#F3C337"/><circle cx="26" cy="14" r="5" fill="#EC6A7C"/><circle cx="38" cy="14" r="5" fill="#F3C337"/><circle cx="50" cy="14" r="5" fill="#FFFFFF"/><circle cx="14" cy="26" r="5" fill="#EC6A7C"/><circle cx="26" cy="26" r="5" fill="#F3C337"/><circle cx="38" cy="26" r="5" fill="#4A86C8"/><circle cx="50" cy="26" r="5" fill="#FFFFFF"/><circle cx="14" cy="38" r="5" fill="#F3C337"/><circle cx="26" cy="38" r="5" fill="#4A86C8"/><circle cx="38" cy="38" r="5" fill="#F3C337"/><circle cx="50" cy="38" r="5" fill="#FFFFFF"/><circle cx="14" cy="50" r="5" fill="#4A86C8"/><circle cx="26" cy="50" r="5" fill="#F3C337"/><circle cx="38" cy="50" r="5" fill="#FFFFFF"/><circle cx="50" cy="50" r="5" fill="#FFFFFF"/></g></svg>
```

Neither page has a `prefers-color-scheme` block today, so no dark variant is needed. If dark mode ever lands,
the plate fill and the clear wells are the two values that have to change, and the three well colours can stay
as they are.

### 11.4 Also worth putting it on

- The favicon, from the same SVG.
- `docs/PRODUCT.md`, which is the page the README sends new readers to.

---

## 12. 0.48.0 on production: verified fixed, and one new bug in `assay volume`

Run 2026-09-22 on the box, 0.48.0, against the real warehouse. Page `build 43b9e520adef`, which matches the
checkout, so the stamp is evidence now rather than an assertion. That closes 7.4.

### 12.1 Verified fixed against the real artifacts

| item | evidence |
|---|---|
| 1.1 chain node clicks | 6 nodes drawn, 6 carry `clk`, a click opens a card. `setPointerCapture` no longer eats it |
| 2.6 "severity decides" | now reads `not configured; warns, cannot fail a build` |
| 3.1 document scroll | 0px overflow on all ten tabs, measured in a browser at 1440x900 |
| 4.3 page and form link | `assay review --report` wires them |
| 5.1 form header moves | download button holds `top: 50` across panes |
| 10.7 `--sample` | shipped, exact is the default, help text carries the language |
| monitoring deferral | names `assay volume` and ends "Nothing here says the monitoring is fine" |

Both files pass `node --check` and drive with zero page errors.

Findings went 264 to 468. `column_has_no_description` 129, `models_disagree_about_a_column` 73,
`description_promises_what_the_code_does` 2. Loop metric unmoved: 63 agreed, 0 fixed, 56 of 253 models ruled
on by a person.

### 12.2 BUG. `assay volume` leads with a false outage

The volume report's headline table, highest blast radius first:

```
table                     change    rows now    marts
buyer_leads_enriched     -100.0%           0        6
```

`buyer_leads_enriched` has 281,286 rows right now. I checked the warehouse directly.

Two separate faults produce that line, and both matter on their own.

**Elementary's `row_count` is a per-bucket count, not a table row count.** `bucket_duration_hours` is 24, and
the 126 buckets recorded for this table sum to 13,579 against a table holding 281,286 rows. So the metric
counts rows landing in a day, not rows in the relation. A quiet day records 0. Comparing one bucket to the
previous one and calling the result a percentage change in table size treats an ordinary quiet day as a table
being emptied.

**The observation is 80 days old and nothing checks that.** The newest `row_count` bucket for this table
starts 2026-07-04. The column is labelled `rows now`. It is not now, it is July.

The recency guard would have caught exactly this one: of 114 relations with `row_count` history, this is the
only one whose newest observation is more than 30 days old. A single `max(bucket_start)` predicate removes the
false alarm and keeps every true one.

What makes this worth fixing rather than tuning: `volume` already knows to be careful about staleness
elsewhere in the same report. It prints "63 monitors last FAILED and have not run since. A stale failure looks
exactly like a live one." That is the correct instinct, applied to monitor results and not applied to the
row-count observations directly above it.

It is also the `arbitrary_pick` and `test_cannot_fail` bug class, which assay ships checks for. A number was
read from a log without constraining which row it came from or how old it was.

Suggested shape of the fix:

1. Constrain to the latest bucket per relation and carry its age.
2. Drop, or visibly mark as stale, anything whose newest observation is older than a threshold. Not silence:
   an 80-day-old monitor is itself a finding, and a more honest one than a fake outage.
3. Rename the column. It is `rows in the last observed bucket`, not `rows now`. If a real table row count is
   wanted, that is a `count(*)`, which `probe` already knows how to cost.
4. Say what the metric measures, since a reader who has not read Elementary's source will assume a table
   count.

### 12.3 The true findings underneath, which are good

Stripping the false line, the rest of the report is real and useful:

- 63 monitors last failed and have not run since, oldest 73 days.
- 459 of 1,317 declared tests have never produced a result.
- 1,587 test results are SKIPPED, which is not a pass. Related to the outage fixed in `b0577278`, where a
  broken build skipped 108 models.
- 233 models with a mart downstream have no row-count history at all, against 114 that do.

The last one is the most useful sentence in the report, and it is phrased exactly right: "Nothing here covers
those."

### 12.4 BUG-adjacent. The cost estimate cannot price a cold store

`probe --dry-run` on production:

```
271 statements, one scan each. Nothing was run.
631.8 MB scanned, $0.0000 (duckdb, duckdb.local). 233 statement(s) could not be
estimated and are not in this total.
```

Saying so rather than quietly totalling a partial number is correct and is what `estimate_basis` was for. But
86% unestimated is a lot, and the cause is structural: the estimate needs a row count, and row counts only
exist for relations a previous probe already scanned. The store holds 39 relations with a `row_count` and
exactly 38 statements were estimated.

So the feature cannot price a cold store, which is the exact moment a paying user wants the number. A new
BigQuery user runs `--dry-run` first, and gets nothing.

The fix needs no scan on any engine, because every engine publishes row counts as catalog metadata:

- DuckDB: `duckdb_tables().estimated_size`
- BigQuery: `INFORMATION_SCHEMA.TABLE_STORAGE.total_rows`, and `total_logical_bytes` is a better basis than
  rows times type widths since it is what the scan actually reads
- Snowflake: `INFORMATION_SCHEMA.TABLES.ROW_COUNT`

All three are free metadata reads. `estimate_basis` then becomes `catalog` rather than `unknown`.

### 12.5 Still open from the earlier sections

- The form did not get the flex shell the page got. Document overflow per pane: words 6705px, findings 6059,
  waivers 2834, explanations 2773, settings 1196, monitoring 0. Some of that is honest pagination, but the
  words pane is 40 near-identical cards, which is 3.2 rather than pagination.
- 197 of 339 form cards are a cold start. The mechanism shipped as `--reads <json>`; nothing generates the
  file yet. This is 5.4 and it is a skill job, not a form job.

---

## 13. The probe sweep is 99.7% dbt startup, and what that does to the cost layer

Found by measuring the production sweep rather than by reading code. 271 relations, launched on the box.

### 13.1 The measurement

Rate, sampled over 90 seconds of a live sweep: 5 dbt invocations, so 18 seconds per statement. The dbt debug
log for those same statements:

```
SQL status: OK in 0.027 second
SQL status: OK in 0.096 second
SQL status: OK in 0.012 second
SQL status: OK in 0.059 second
```

The warehouse work is 12 to 96 milliseconds. The wall time is 18,000. So 99.7% of the sweep is dbt process
startup and manifest parse, and 271 statements take about 80 minutes to perform roughly 20 seconds of
querying.

### 13.2 Why it is built this way, which is not an accident

Two hard constraints, both good, both documented in the code.

`probe.py:376`: "Any read-only statement, through the project's own dbt. assay never holds a credential."
That is why anyone can point assay at a production warehouse without handing it a secret. It should not be
given up.

`pyproject.toml`: assay does not depend on dbt-core, deliberately. The comment says why: "dbt-core pins
adapters, Python versions and dependencies aggressively; depending on it means fighting every user's
environment and breaking on every dbt release." Confirmed, nothing under `src/dbt_assay/` imports dbt.

Those two together rule out the obvious fix. `dbt.cli.main.dbtRunner` would parse once and invoke many times
in a single process, which is exactly what is wanted, and it requires importing dbt-core. So that door is
closed on purpose.

Given both constraints, shelling out to the user's dbt binary is the correct mechanism. What is not required
by either constraint is **one shell-out per statement**. That is the part to fix.

### 13.3 The fix that keeps both constraints

The per-relation statements are independent aggregates with no joins. They can be combined into far fewer
`dbt show` invocations:

```sql
select 'sunny.raw.co_addresses' as rel, count(*) as row_count, count(addr_id) as nn_0, ...
union all
select 'sunny.raw.elpaso_parcels' as rel, count(*) as row_count, ...
```

`interpret()` already parses a single result row per target, so it needs a relation label to split a batched
result back out. Cutting 271 invocations to 30 takes the sweep from 80 minutes to under 10, changes nothing
about what is measured, holds no credential and imports no dbt.

Two cautions. Batch width has to be bounded, because a statement too wide will hit a parser or planner limit
on some engine and the failure mode today is an `unknown` row that looks like a shrug. And on BigQuery a
batched statement still scans every column in it, so batching is a wall-clock optimisation and never a bill
optimisation. Which is the same split as 10.2: batching helps where time is billed, and does nothing where
bytes are.

There is already an escape hatch for people who want to run the SQL themselves: `probe --emit` writes the
statements out and `--load` reads the results back. That path is manual today and is the right thing to point
a large-warehouse user at until batching lands.

### 13.4 The consequence for the cost layer, which is the serious part

`wall_ms` recorded around `subprocess.run` reads about 18,000ms for a statement the warehouse ran in 30ms.

Snowflake is billed on warehouse seconds. If `wall_ms` is the basis for a Snowflake estimate, the number is
wrong by roughly 600x, and it is wrong in the direction that looks plausible rather than absurd, so nobody
catches it.

The fix is the one already built for bytes. `dbt show --log-format json --log-level debug` carries
`run_result.adapter_response`, and the same debug log carries `SQL status: OK in N second`. Take execution
time from the engine, never from the subprocess.

Concretely, in the `warehouse_calls` schema from 10.4:

- `wall_ms` keeps meaning what it says, the subprocess round trip, and is never a cost basis.
- Add `exec_ms`, from the adapter response or the debug log, null when the adapter gives nothing.
- `estimate_basis` governs `exec_ms` the same way it governs bytes. A Snowflake estimate built on `wall_ms`
  must not be emitted at all; absent is better than 600x wrong.

This is the same rule three times now: 1.7 (varchar declared over INTEGER), 12.2 (an 80-day-old bucket
labelled `rows now`), and here. A column that mixes a measured value with a stand-in produces a number nobody
can audit, and it always looks fine.

---

## 14. Baseline sweep results: the cost ledger works, and 26% of the sweep fails

`assay probe` against the real warehouse, 0.48.0, 271 relations, about 88 minutes wall clock.

```
271 statement(s), 631.8 MB, $0.0000, 5256.0s of warehouse time.
observed 548 columns, 242 unknown
```

### 14.1 What the cost layer got right

`assay cost` now prints a warehouse section beside the Jev section, which is the symmetry from 10.4. Three
lines in that output are doing exactly the job they were specced for:

```
70 statement(s) FAILED and are not in the money. A failed statement and an empty result are different facts here.
247 statement(s) could not be estimated and are not in the bytes. `dbt docs generate` gives assay the column
types; `assay probe` gives it the row counts.
every byte figure here is an ESTIMATE from declared types and a known row count, never a number an adapter
returned. `estimate_basis` on each row says which it is.
```

Failures excluded from the money, unestimated statements excluded from the bytes, and the basis stated rather
than implied. That is the rule from 10.4 held three times in one screen.

### 14.2 BUG. The same rule was not applied to time, and time is the Snowflake bill

`5256.0s of warehouse time`, in a column headed `time`, with no qualifier anywhere.

The warehouse did not spend 5,256 seconds. Per 13.1 the actual statements ran in 12 to 96 milliseconds, so
271 of them is roughly 20 seconds. The recorded figure is subprocess wall clock, which is 99.7% dbt startup.

On DuckDB nobody notices, because the money column reads $0.0000 either way. Set `cost.engine: snowflake` and
a rate, which is what the last line of that output invites, and this becomes the bill. 5,256 seconds against
20 actual is wrong by 263x, in the direction that looks plausible.

The honesty rule was applied to bytes and not to time. Apply it to both:

- `wall_ms` keeps meaning the subprocess round trip and is never a cost basis.
- `exec_ms` comes from `run_result.adapter_response` or the `SQL status: OK in N second` line, null when the
  adapter gives nothing.
- A Snowflake estimate with no `exec_ms` is not emitted. Absent beats 263x wrong.

### 14.3 BUG. 70 of 271 statements fail on columns that do not exist

Every one of the 70 failures is the same error:

```
Binder Error: Referenced column ... not found in FROM clause!
```

Traced to a concrete case. `sunny.raw.nhdplus_flowline` holds exactly these columns:

```
comid, fcode, ftype, geom, gnis_id, gnis_name, length_km, reachcode, vpu
```

assay generated `count(basin_name)` against it. There is no `basin_name`. The dry-run output explains why in
its own words: for a relation with no structural key hint, "columns inferred from what its children
reference". A child model invents `basin_name` in its own SELECT list, and that name was attributed back to
the parent.

Two defects, and the second is the expensive one.

**The candidate list is never validated.** `probe.py:248` already runs
`select column_name from information_schema.columns` for another purpose. The inferred-from-children path
does not use it. One predicate removes every one of these 70.

**One bad column discards the whole relation.** The batching at `probe.py:156-162` is the design's central
efficiency: one scan, every candidate column. The cost is that a single unbindable name fails the statement,
so every good candidate for that relation is recorded `unknown` too. That is how 548 columns produce 242
unknowns off 70 failed statements.

A failed batch should drop the column named in the error and retry once. On DuckDB a bind error is free, but
70 failures at 18 seconds each is 21 minutes of the 88, spent to learn nothing.

This is the batching failure mode predicted in 13.3, arriving from a different direction than expected. It is
also worth noting that `unknown` is the correct thing to record, and `probe.py` says so: "a failure is
recorded as unknown, never as 'not unique'". The record is honest. The statement should not have failed.

### 14.4 The real results, which are good

179 columns settled as `unique` and 117 as `has_nulls`, on relations where nothing in the project declared a
key:

```
unique sunny.raw.co_addresses.addr_id        2,779,592 rows, all non-null and distinct
unique sunny.raw.business_entities._dlt_id   3,090,339 rows, all non-null and distinct
unique sunny.raw.co_parcels_geom.pk            359,456 rows, all non-null and distinct
```

Those are facts about the data that no dbt test asserted and nothing in the repo wrote down, which is the
point of the tier. Fixing 14.3 would add roughly 242 more.

### 14.5 The monitoring pane fills correctly, and shows a derived cadence of 0.0 days

`assay volume --json > volume.json` then `assay review --emit ... --monitoring volume.json` works end to end.
The pane goes from "Nothing measured yet" to the real report, and the content is good:

```
monitor ran then stopped
`dbt_source_freshness_results` has not been written to for 77 days, and a stopped monitor reads exactly
like one that finds nothing

volume is not being watched
233 models with a mart downstream have no row-count history

test skipped rather than passed
1,587 test results are SKIPPED, which is not a pass
```

The 77-day figure independently confirms the source-freshness gap recorded in 7.7.

One line in it is wrong:

```
assay measured: this project runs dbt every 0.0 day(s), across 44 run(s).
Three missed runs is 5 day(s); nothing is configured, so the derived number is what is used.
```

A cadence of 0.0 days is not a cadence. This project runs dbt several times a day, so the median gap is
sub-daily and rounds away. The second sentence then does not follow from the first: three missed runs at 0.0
days apart is 0 days, not 5, so a floor is clearly being applied and is not being disclosed.

Two fixes, both small:

- Render a sub-daily cadence in hours. "every 7 hours, across 44 runs" is a true sentence; "every 0.0 days"
  is not.
- Say the floor out loud. "Three missed runs is 0.9 days, floored to the 5-day minimum" is honest and keeps
  the derived number auditable, which is the same rule as `estimate_basis`.

This matters more than it looks because the derived number is what is used when nothing is configured, and
the pane says so. A user reading `0.0` has no way to judge whether the 5 is reasonable for their project.

---

## 15. `test_cannot_fail` is correct and too blunt to rank. Three signals assay already has would fix it

> **CORRECTED BY SECTION 21.** The measurement proposed in 15.2 already ships as `assay tests
> --count-defaults`. This section was written without it and its triage of the findings is wrong.
> Read 21 first.

From using 0.49.1 on water table rather than from testing it. 63 agreed findings, 50 of them
`test_cannot_fail`, 37 on water models. Ryan: "flagging 37 i wouldnt expect them all to be like a big deal".
He is right, and the split is not visible from the finding text.

### 15.1 What the 37 actually are, measured

| kind | n | measured against production | worth |
|---|---|---|---|
| `accepted_values`, CASE with no ELSE | 12 | 11 of 12 have **zero** NULLs | vacuous, nothing hiding |
| `not_null` on a COALESCE'd column | 14 | see below | the interesting ones |
| `unique` on a column the SQL groups by | 11 | true by construction | keep as a regression guard |

The `not_null` group, measured:

```
water_rights.dwr_analysis_status          173,480 rows   97.0% are 'not looked up'
water_parcels.irrigated_acres_on_parcel 2,732,262 rows   96.0% are 0
water_section_summary.wells_drilled        63,349 rows   33.9% are 0
```

The finding says the test cannot fail and suggests deleting it. The true statement is stronger and different:
**a `not_null` test reads as coverage on a column that is 3% real data.** That is a fact about the warehouse,
not about test hygiene, and it is the one a person would act on.

The one live defect in all 37 came from the same place: `water_outreach_agents.contact_role`, 571 of 573
NULL, which traced to the broker list resolving contacts against a lead-gen enrichment table that was never
fed brokerage websites. 2 of 270 sites match. Found by following a `test_cannot_fail` finding to the data.

### 15.2 Three signals, all already collected, none joined to the check

**Null share, for `accepted_values`.** `observed_keys` already carries `relation, column_name, row_count,
non_null, distinct_ct`. That answers "does the CASE actually leave anything unmatched" exactly. The gap is
coverage, not capability: `probe` targets relations with no settled grain, and every mart in this group has
one, so it is never measured. Probe should also cover columns named in a `test_cannot_fail` finding. That is
affordable now that 271 statements batch into 23.

**Default share, for `not_null` on a COALESCE.** Null counts cannot answer this, because `non_null` equals
`row_count` by construction. It needs `sum(case when col = <default> then 1 else 0 end)`, and assay can write
it without help because it already parsed the COALESCE to raise the finding at all. One more aggregate in the
same batched statement. `audit.yml` is already recording "99% default" for `dwr_analysis_status` somewhere,
so the number exists in the system and does not reach the finding.

**Run history, from `volume.json`.** It already reports 459 declared tests that never produced a result and
1,587 results SKIPPED. A test that cannot fail and has passed 47 times is test-hygiene. A test that cannot
fail and has never run is two problems, and today they read identically.

### 15.3 What the finding should say

Same check, three rankings, no new tier:

```
test_cannot_fail  water_rights.dwr_analysis_status          13 marts
  not_null on coalesce(ca.dwr_analysis_status, 'not looked up') cannot fail.
  MEASURED: 97.0% of 173,480 rows are the default, so the column is 3% real
  and this test reads as coverage on the other 97%.

test_cannot_fail  water_section_hazards.flood_status         1 mart
  accepted_values on a CASE with no ELSE cannot fail.
  MEASURED: 0 NULL in 108,917 rows. The branches cover the data. Low priority.
```

The second one is a finding a person closes in five seconds. Today it costs the same attention as the first.

### 15.4 The broader point, which is Ryan's

> "mostly just like filling out the vocab and the context and the waivers and definitions and all that shit
> i think is how we can really build out the warehouse quality"

This session is evidence for that. Scoping 12 of 16 vocab terms with `applies_to` took the lint from 13
warnings to 1, and vocab is injected into every judged state, so one unscoped water term was steering
judgments about business licences. That is the highest-leverage surface in the config and it is the one with
the least support: the vocab candidates pane proposes the term and the measurement and leaves `means:` and
`implies:` empty on purpose, which is correct, and then offers nothing to help fill them.

6.1 already proposes seeding `means:` from column descriptions. The stronger version, given 15.2, is to seed
`implies:` from the measurement. `dwr_analysis_status` does not need a human to discover that it is 97%
placeholder; it needs a human to decide whether that is acceptable.

---

## 16. Running list, from using 0.49.1 rather than testing it

Small things. None of these blocks anything.

### 16.1 The cost preview is on the free commands and not on the paid ones

> **PARTLY WRONG, see 21.3.** `assay ask --dry-run` prices any family that declares a subject, and
> `jev.max_spend_usd` is a hard cap checked before the call. The per-command wrappers lack a dry run;
> the generic path has one.

`probe --dry-run` and `volume --judge --dry-run` both price the work and send nothing. Both are tiers that
cost nothing on DuckDB.

`feeds`, `align`, `semantics` and `tests` all call Jev and none of them has `--dry-run`. They can be bounded
(`feeds -n`, `tests -n`, `semantics -n` and `--select`, `align --select`) but you cannot ask what a run will
cost before starting it.

That inverts the guardrail. `assay cost` already describes itself as "the number to put in front of somebody
BEFORE proposing a judged run", and the commands that need it most are the ones that cannot produce it.

### 16.2 `assay cost` totals across runs, so a fixed defect still reads as live

After the 0.49.0 batched sweep the ledger read:

```
assay.probe.…  294 statements  631.9 MB  $0.0000  5808.2s  70 failed
```

294 is 271 from the old unbatched run plus 23 from the new one, and all 70 failures belong to the old one.
The batched run had zero. Someone reading that screen on a version that fixed the bug sees 70 failures.

The per-run split is only visible by querying `warehouse_calls` directly, which already carries `run_id`. A
run filter, or a newest-run default with a total beneath it, resolves it.

### 16.3 `assay stale` reports a zero it cannot stand behind

```
0 of 18,079 judged answer(s) are about SQL that has since changed. 0 current, 18,079 cannot be checked.
  17,812: decided before assay recorded a checksum
```

The output is honest about this and says so plainly, which is right. The problem is the headline: "0 are
about SQL that has since changed" is the first line and reads as a clean bill, when the real state is that
98.5% cannot be assessed either way. Lead with the coverage, not with the zero. Same class as 12.2, where a
number that could not be checked was presented as a measurement.

`--exact` rebuilds each answer's state and would give a real answer here. It is free. It is worth saying so
at the point the checksum path comes up empty, rather than in the flag's help text.

### 16.4 `volume --judge` narrows to nothing, and says so well

Worth recording as a thing done right. The dry run ended:

> no movement lines up with a claim, so there is nothing to ask. That is code narrowing before anything is
> spent, not a clean bill.

Free, correct, and it refuses to let the absence of a question read as an absence of a problem. This is the
sentence the three items above are each missing.

### 16.5 12.2 is fixed

The stale-observation guard shipped. The same table that produced the false outage now reads:

```
5 table(s) moved, in observations more than 30 days old, so they describe the past rather than now:
  buyer_leads_enriched  -100.0%  newest observation 80d ago. Nothing has written a row-count bucket for it since.
```

The number is unchanged and the sentence around it is now true.

---

## 17. The batching fix went in at the call site, so every other command still pays the full tax

### 17.1 Measured

`assay feeds -n 5` against production: **3m53s for 5 sources**, 47 seconds each, 1 Jev call, 2,261 tokens,
$0.0001. The money is nothing. All 210 sources would cost under half a cent and take roughly **2.7 hours**,
essentially all of it dbt booting.

This is section 13 again, in a command the 0.49.0 fix did not reach.

### 17.2 Why it did not reach it

`run_sql` and `run_via_dbt` in `probe.py` are the only two places assay touches a warehouse. Callers of them
in 0.49.1:

```
cli.py          run_sql=5
practices.py    run_sql=5
probe.py        run_sql=3   run_via_dbt=1
rows.py         run_sql=2
```

The batching landed **inside `probe.py`**, at the call site. Every other caller still issues one statement per
`dbt show`, and every one of them pays a fresh process and a full manifest parse per statement.

So `probe` went from 271 invocations to 23 and from 88 minutes to 10, and `feeds`, `practices`, `rows` and
the five `cli.py` call sites got nothing.

A fair share of this is the report's fault, and it is worth recording as a lesson about the report rather
than about the code. Section 13 measured `probe`, named `probe`, and proposed a fix for `probe`. The fix
followed the measurement exactly. A precise report on one command produced a precise fix to one command.

### 17.3 The fix belongs one level down

`run_sql` should take a list of statements and coalesce them into as few `dbt show` invocations as the
dialect allows, returning results keyed back to the caller. Then:

- every current caller gets the speedup with no change to its own code
- every future caller gets it by default rather than by remembering
- the batch-width bound, the per-statement failure isolation and the relation labelling from 13.3 are written
  once instead of per command
- `warehouse_calls` keeps one row per logical statement, so the cost ledger does not change shape

`probe.py` already contains a working implementation of all of it. The work is moving it, not inventing it.

### 17.4 What it is worth

Rough, from measured per-statement times:

| command | today | batched at the chokepoint |
|---|---|---|
| `probe` (271 relations) | 10 min, already fixed | 10 min |
| `feeds` (210 sources) | ~2.7 hours | ~12 min |
| `practices`, `rows`, cli paths | unmeasured, same shape | same shape |

A tool whose pitch is "ask it instead of reading SQL" cannot have a two-and-a-half hour command in it, and
the reason it does is a fix applied one level too high.

---

## 18. The enforcement gap: assay is advisory in a repo that knows how to be mandatory

This is the most important section in this document and it took a day of using the tool to see it.

### 18.1 The correction that prompted it

I proposed gating `test_cannot_fail` on the evidence that it had 90 human rulings at 100% agreement, well
over the `min_adjudications: 20` floor. Ryan: *"well those 90 human rulings are all from you lol. i jsut said
sure to get it poppin."*

So the number is an agent agreeing with itself, recorded through a form and labelled `human`. The review
skill states this plainly -- `source = 'human'` is a label any process can write, and the only thing keeping
it meaningful is the procedure being followed. It was not, and then the number was used as evidence for a
build gate.

Two consequences, and the second is the bigger one.

The gating argument is void. Nothing on this project has earned the right to fail a build, because no check
has verdicts that were not produced by an agent.

And the floor does not protect against this. `min_adjudications: 20` counts rows where `source = 'human'`.
Ninety agent rulings laundered through a form clear it four times over. The floor measures volume, and the
thing it is standing in for is independence.

### 18.2 The irony, stated exactly

`audit.yml` on this project, in the `questions:` block:

```yaml
  # 38 of 38 agreed. The most reliable check here, ...
  test_cannot_fail:
    action: queue
```

The store says 90. And further down:

```yaml
  # A parser decided it, so it may gate immediately -- except nothing gates until a question
  # clears min_adjudications, which none do.
```

One now does. Both comments assert a number about the store and both have drifted from it.

That is `description_contradicts_the_code`, which assay ships a check for, occurring in assay's own
configuration file. Assay reads a project's `schema.yml` prose against its SQL and never reads its own
config's prose against its own store. The same is true of the skill text and this document: 17.2 records the
report producing a fix narrower than the problem, and this records the config making claims nothing verifies.

A `config_comment_contradicts_the_store` check is mechanically possible and would have caught both lines. A
comment in `audit.yml` that asserts a count is checkable against the store the same way a description is
checkable against SQL.

### 18.3 What the tool is actually for, and why it is not doing it

Ryan: *"this thing should be evaluating the sql makiung sure it doesnt fucking suck... the point is so that
when YOU mr fucking retard claude write code that goes into this warehouse its ENFORCED that it uses these
best practices ive defined."*

That is a different surface from the one this whole document has been exercising. Everything above is a
report read after the fact. What is wanted is a gate that fires while the code is being written.

sunny-data already knows how to do this. It has exactly one hook:

```
PreToolUse / Bash -> blocks hand-rendering a water report to Desktop
```

That hook does not care whether the agent read CLAUDE.md. It stops it. It works.

**Assay is not wired into that surface at all.** No `PreToolUse` on Edit, no `PostToolUse` on Write, nothing
matching `transform/models/**.sql`. Every piece of assay's enforcement lives in skill prose instructing an
agent to remember, and agents do not: this session skipped the review skill's "drain the queue before you
emit" and had to be caught by the user.

### 18.4 The missing flag, which is the whole blocker

`assay check` takes no `--select` and no `--model`. `claims`, `traverse`, `semantics` and `align` all take
`--select`; `check` has only `--limit`, which caps what is *printed*. So `check` always runs the entire
project, and on sunny-data that is about ten minutes.

A ten-minute command cannot sit in a hook that fires on a model edit.

So the enforcement gap is not a config problem and not a skill problem. It is one missing flag:

```bash
assay check --select <model>     # structural only, one model, sub-second
```

With that, the hook is four lines of JSON and assay stops being a thing you read:

```
PostToolUse / Edit|Write on transform/models/**.sql
  -> assay check --select <the edited model> --store ...
  -> non-zero exits with the finding text as the reason, and the edit does not stand
```

That is what turns "these are the practices I defined" into practices that hold.

### 18.5 Order of work

1. `assay check --select <model>`, structural tier only, fast enough for a hook.
2. Ship a hook recipe in the skill, or better, have `assay init` offer to write it, so the enforcement is
   installed rather than described.
3. Only then does `action: fail` mean anything, and only once a check has verdicts a person actually gave.
4. `config_comment_contradicts_the_store`, so `audit.yml` cannot drift from the numbers it cites.

Items 1 and 2 are the product. Everything in sections 1 through 17 is a report; this is the tool doing the
job the report keeps describing.

---

## 19. Where Jev is not used and should be, and how to make the banks pluggable

Two asks from Ryan: put the missing question families in the bank, and make adding families, vocab, claims
and the rest genuinely configurable. Plus: find the places in the workflow where the judged tier is cheap and
absent.

### 19.1 The map as it stands

18 shipped families across 10 banks, plus 4 custom water families this project added.

```
bank       family                            pv               opts   asked here
align      edge_preserves_the_grain          edge.v2             5      1,629
align      same_concept                      align.v1            3        120
columns    column_role                       role.v3            11      5,252
columns    null_meaning                      null.v3             5    bundled into role.v3+null.v3
feeds      field_matches_its_name            feed.name.v2        4          3
feeds      units_are_what_the_column_claims  feed.units.v4      13          0
grain      column_is_part_of_the_key         key.v1              2        120
practices  practice_exception                practice.v1         5        150
rows       row_explanation                   row.v2              5         48
rows       row_is_internally_coherent        row.coh.v1          2          0
rulings    same_defect                       rule.same.v1        2        179
semantics  claim_alignment                   claim.v2            4      1,781
semantics  description_contradicts_the_code  desc.v1             2        304
semantics  predicate_intent                  pred.v2             5        406
semantics  sentence_is_a_claim               sentence.v2         6      5,794
testing    options_overlap                   overlap.v1          5         11
testing    severity_fit                      sev.v1              3         80
volume     volume_contradicts_a_claim        volume.v1           4          0
```

Two things this makes visible that the family list alone does not.

**Prompts compose, and the version string records the composition.** The store holds `role.v3+null.v3`,
`desc.v1+comments+scoped`, `pred.v2+scoped`, `edge.water.v2`. So `null_meaning` is not unasked; it rides
inside the `column_role` call. Scoping a question mints a new version automatically. This is good design and
it is undocumented anywhere a person adding a family would look.

**`subject:` is declared on 2 of 18 shipped families.** Only `same_defect` (`ruling_pair`) and
`volume_contradicts_a_claim` (`model`). The other 16 inherit it from their bank. A custom family in
`assay_questions/` must declare it explicitly, and choosing wrong produces a family that lints clean and
never fires. That cost an hour on this project (7.5). The asymmetry is the single largest barrier to adding
a suite: the shipped banks do not have to say the thing the lint makes everyone else say.

### 19.2 The largest unused judged tier: nothing writes the reads file

`assay review --reads <json>` takes `{'<subject>::<check>': {verdict, why}}` and pre-fills MY READ on every
card. **Nothing in assay produces that file.** The skill says "an agent writes that file once, offline",
which in practice means an agent in a conversation, one finding at a time, at conversation cost.

Priced from this project's own ledger: `practices` judged 171 findings for $0.0057, so $0.000033 per finding.
The 212 cold cards on the current form would cost about **$0.007**.

So the most expensive human-facing step in the loop, the one the review skill is built around and the one
this session was caught skipping, is the only step handed to a conversation rather than to the tier that
costs a third of a hundredth of a cent per item.

`assay read --out reads.json` closes it. Same evidence the form shows, same `rule()` verdict vocabulary, one
batched pass, and the output is a file a person reviews rather than a write to the store, so the
agent-is-not-authority rule is preserved exactly.

### 19.3 Other places the judged tier is absent and cheap

| where | today | what a judgment would add |
|---|---|---|
| vocab `means:`/`implies:` | left empty on purpose, no draft offered | draft from the measurement and the column descriptions; a person edits rather than composes (6.1) |
| `column_has_no_description`, 129 here | reports the absence | draft the description from expression, role and lineage |
| waiver `reason:` | required, hand-written | draft from the finding's own evidence, which is what a waiver is restating |
| `assay plan` `fix_shape` | names the shape, refuses the words | propose the replacement sentence as a proposal. The refusal is right; offering nothing is not the only alternative |
| `audit.yml` comments vs the store | nothing checks them | 18.2: a comment asserting a count is checkable the way a description is |
| 63 stale monitors, 459 never-run tests | counted | judge which matter given what reads them |

### 19.4 The missing families

The gap Ryan's `not_null` observation points at, first, because it is the one with 15 live cases:

**`default_is_a_measurement_or_an_absence`.** `COALESCE(wells_drilled, 0)` defaults to a real count; a
section with no wells has zero wells. `COALESCE(dwr_analysis_status, 'not looked up')` defaults to a marker
meaning nobody checked. Today both raise the same `test_cannot_fail` finding and the difference is invisible.
One question separates 15 findings into 13 deletions and 2 coverage problems. The 15 cases already exist and
can be used as the calibration set the day it is written.

Five more with no family at all:

- **`filter_is_complete`.** `predicate_intent` asks why a filter exists. Nothing asks whether it covers
  everything it should. The nontributary exclusion is a hand-written list of ten aquifer names, which is
  exactly this question.
- **`units_agree_across_models`.** `units_are_what_the_column_claims` is per column. Nothing asks whether
  `af` in one model is the `af` in another.
- **`time_grain`.** Daily, monthly, point-in-time, as-of. Unasked, and a silent mismatch is a wrong number
  that looks right.
- **`tie_break_is_total`.** `arbitrary_pick` is structural only. A judged counterpart would say whether the
  ordering is actually total, which is the bug this project has shipped three times.
- **`sentinel_is_not_a_value`.** FEMA writes -9999, CO liquor writes a future April 1. The project's own
  gotcha list carries this and no question asks it.

### 19.5 What "configurable and friendly" needs

From adding four families to this project:

1. **Make `subject:` inferable, or make the shipped banks declare it too.** The asymmetry is the trap.
2. **Lint a question against the state its subject actually carries.** A family referencing `filters` under
   `subject: edge` is mechanically detectable and is the failure that lints clean and never fires.
3. **`assay question new <name>`** writing a scaffold with the subject's available state fields in comments.
4. **A try-before-you-run loop.** `assay ask <family> --select <3 models> --show-prompt` prints the composed
   prompt and the answers without writing. Today a new family is authored blind and validated by running it
   across 356 models.
5. **Document prompt composition.** `role.v3+null.v3` is a real and useful mechanism that nothing explains.
6. **The same treatment for vocab, claims and waivers**, which are authored by hand into `audit.yml` with no
   scaffold, no preview and, for waivers, no selector (18.4).

---

## 20. Context the tools are not getting, and the monitoring bank

### 20.1 `contract(model)` omits everything about the model's health

`live.contract_of` returns:

```
model, path, materialized, grain, grain_source, grain_confidence,
reads, descendants, marts_downstream, description,
columns[{name, role, comes_from, in_key}]
```

Its own docstring calls it "fifteen lines instead of two hundred. This is the whole argument for the MCP
server", and CLAUDE.md on this project tells every agent to call it before touching a model.

It carries no findings, no claims, no human verdicts, no waivers in force, no measurements and no test
coverage. It is an anatomy chart with no chart notes. `findings(model)` exists as a separate call and nothing
instructs an agent to make it.

This is the enforcement gap of section 18 in miniature. Before editing `int_azcc_owners` the useful sentence
is not "the grain is `owner_key`". It is "the grain is `owner_key`, 19 marts read it, three findings are
open, one of which a person agreed with, and a waiver on `hop_multiplies_rows` expires in March." The first
tells an agent what the model is. The second changes what it writes.

Three additions, all already in the store, none requiring a new measurement:

- **findings**, carrying `ruled_by`, so an agreed finding reads differently from an unread one
- **waivers in force**, because a waived finding is a decision already made and an agent will otherwise
  re-litigate it
- **the settled grain's measurement** where `observed_keys` has one. "`owner_key`, measured unique over
  3.09M rows today" is a different claim from "`owner_key`"

### 20.2 Measurements in judged state: targeted, not global

The obvious generalisation is to inject the counted tier into every judged state the way vocab is injected.
The evidence on this project says do not.

`assay effectiveness` on the current store reports **14 open disagreements and 1 unclear**, and states the
rule that separates them:

> Reword an option to fix a disagreement; add a field to fix an unclear.

An `unclear` is the subject state failing to carry what the question asks about. There is one. Fourteen
problems are wrong options, which more state does not fix and may obscure. Ryan also recalls an earlier
attempt at feeding measurements to Jev performing worse than without, which is consistent with this: extra
state that the question does not need is noise that has to be reasoned past.

So the recommendation is narrow and stays narrow:

- Add a measurement to a question **only** where that question's answers show `unclear`, or where a check
  demonstrably cannot rank without it. Section 15's `test_cannot_fail` is the clear case: null share for the
  `accepted_values` variety, default share for the `not_null` variety. That is two fields on one check, not a
  global change.
- Do not inject `observed_keys`, `volume.json` or `warehouse_calls` into every judged state.
- `unclear` is the signal that says when to. It is already measured per family. Let it drive the decision
  rather than a judgment call at authoring time.

The separate and unconditional improvement is 20.1: measurements belong in what the MCP hands an **agent**,
which is a reader that benefits from context, rather than in what a prompt hands **Jev**, which is a judge
answering one narrow question.

### 20.3 A monitoring bank, four families

Monitoring today is one family, `volume_contradicts_a_claim`, which has never fired here because it narrows
to nothing before spending. Everything else in that tier is structural counting. Each family below has live
cases in this project's current `volume.json`.

**`movement_is_expected_for_this_kind_of_table`.** `mesa_code` moved +2061%, `tempe_code` +23%. Whether
either is alarming depends on what the table is: a backfilled code-enforcement feed doubling is routine, a
reference table doubling is not. assay holds the DAG position and the description and never asks.

**`monitor_covers_what_matters`.** 233 models with a mart downstream and no row-count history. Nobody is
going to write 233 monitors, so the useful answer is which of the 233 are worth watching. Both inputs, blast
radius and how the model is built, are already in the state.

**`stale_monitor_still_matters`.** 63 monitors last failed and have not run since, oldest 73 days. Some are
on tables that no longer exist or no longer feed anything. This separates a real alert backlog from
archaeology.

**`test_never_ran_is_a_gap_or_a_leftover`.** 459 declared tests have never produced a result. Some are
coverage holes, some are on models that stopped building. Those want opposite responses and are currently one
number.

All four judge **the monitoring**, never the data, which preserves the line the tier already draws: assay
asserts a monitor exists, is current and covers what matters, and never measures volume itself.

### 20.4 One thing the release already moved

Worth recording because it is the only number in `effectiveness` a release can move, and it moved:

```
2 reason(s) stopped recurring.
  8 subject(s), now 0 (hop_multiplies_rows)  -- "A UNION MEMBER EDGE CANNOT MULTIPLY"
  2 subject(s), now 0 (hop_multiplies_rows)  -- "FALSE POSITIVE, MEASURED. LEFT JOIN to a unique lookup"
```

Ten false positives that a person wrote a reason for, fixed structurally rather than waived, and now firing
on nothing.

---

## 21. Audit of this document against what actually ships

Written last, after reading `src/dbt_assay/cli.py` rather than `--help` output. Several proposals above are
for things that already exist. They are corrected here rather than silently, because the value of this
document is that somebody can act on it, and a phantom feature wastes their time the way it wasted Ryan's.

### 21.1 Root cause: the setup path exists, is good, and nothing puts an agent on it

`assay guide start` prints the six-step order for a project that has never run assay. `assay onboard` runs
the first pass and, with `--agent`, **writes the agent skill file and prints the MCP config**. On this
project both were hand-assembled from `--help` instead.

The `onboard` docstring states exactly what skipping it costs:

> A first run on someone else's warehouse is where assay is most likely to be quietly wrong: no compiled
> SQL, no catalog, a dialect it guessed. Every one of those degrades the answers without changing how
> confident the output looks.

That is the real finding. Both skills tell an agent to call `contract()` before an edit. Neither tells it to
run `guide start` before configuring anything, and the CLI does not either. The good path is there and is
not the path taken. Every correction below is downstream of that single miss.

### 21.2 Section 15 is wrong. The measurement ships as `assay tests --count-defaults`

15.2 proposes measuring the default share behind a `not_null` on a COALESCE. That is step 3 of
`assay guide start`:

> `assay tests --count-defaults` -- tests that cannot fail, and how often each COALESCE default actually
> wins. "This test cannot fail" is true; "this default is 99% of your rows" is the sentence somebody acts on.

Run on this project, in one batched query:

```
13 defaulted column(s) to count, in one query
  water_rights.dwr_analysis_status            'not looked up'  168,341/173,480  (97%)
  water_parcels.irrigated_acres_on_parcel                   0  2,623,680/2,732,262 (96%)
  water_section_summary.wells_household_only                0     54,528/63,349  (86%)
  az_section_summary.n_water_level                          0     96,624/114,305 (85%)
  az_section_summary.n_well_depth                           0     95,650/114,305 (84%)
  az_section_summary.wells_production                       0     93,572/114,305 (82%)
  water_eco_reach.species_on_reach                          0      1,872/2,427   (77%)
  az_section_summary.parcel_count                           0     85,264/114,305 (75%)
  water_section_summary.rights_late_adjudicated             0     44,974/63,349  (71%)
  water_eco_reach.rights_senior_to_isf                      0      1,280/2,427   (53%)
  water_section_fingerprint.section_hash                    0          0/108,917  (0%)
  water_eco_basin.recorded_species                          0          0/172      (0%)

10 column(s) are at least half default. The test passes on every row and says nothing about whether the
lookup behind it ever ran: the coalesce conflates 'none' with 'not measured'.
```

Two corrections to section 15 follow.

**The triage in 15.1 is wrong.** It called 11 of the `not_null` findings redundant counts where zero is a
real measured value, safe to delete. Ten of thirteen are at least half default. `wells_household_only` at
86% and the four `az_section_summary` columns at 75-85% are not mostly-real-counts with some zeros. This is
a live water-table data-quality problem, not test hygiene: a section with no wells and a section whose well
lookup never ran are the same value in the warehouse today.

**One flag is cleared.** `water_section_fingerprint.section_hash` defaults 0 of 108,917 times. The
coalesce-to-zero never fires, so there is no fingerprint collision. Section 14.3's concern is resolved.

What section 15 still gets right is the *judged* half: `tests --count-defaults` measures the share and does
not decide whether the default means "none" or "not measured". That distinction is
`default_is_a_measurement_or_an_absence` from 19.4, and the thirteen rows above are its calibration set.

### 21.3 Section 16 corrections

**16.1 is partly wrong.** `assay ask --dry-run` counts the subjects and prints one state without asking, for
any family that declares a subject, with `--family`, `--select` and `--limit`. `jev.max_spend_usd` is a hard
cap checked before the call. What is true is narrower: the per-command wrappers (`feeds`, `align`,
`semantics`, `tests`) have no `--dry-run` of their own, so the preview exists on the generic path and not on
the paths a person actually reaches for.

**16.2 is wrong.** `assay cost --since` scopes the ledger by time. The claim that a per-run split is only
available by querying `warehouse_calls` is false. The presentation point survives in weaker form: the
default view totals across runs, so a fixed defect still reads as live unless you know to pass `--since`.

### 21.4 Section 19.5 corrections

The question-bank system is already more modular than 19.5 assumes. From `assay banks`:

> Your own questions go in `assay_questions/`. A directory here or in any parent, or wherever
> `ASSAY_QUESTIONS` points. A family with a new name is added; one with a shipped name REPLACES it, which is
> the point -- a warehouse whose `column_role` needs an extra option should not have to fork.

So overriding a shipped family without forking works today, and `ASSAY_QUESTIONS` makes bank location
configurable. The lint is described honestly too: it catches shapes already measured to fail and "cannot
tell you a question is GOOD. Only running it against cases you have already ruled on does that."

That last sentence is 19.5 item 4, already answered: `assay regress --family` replays existing rulings
against a changed question. Combined with `ask --dry-run --limit`, the try-before-you-run loop exists.

What survives from 19.5: `subject:` being required for custom families and implicit for 16 of 18 shipped
ones (the trap that cost an hour), no lint of a question against the state its subject carries, and no
scaffold command. Three items, not six.

### 21.5 What survives the audit

Verified absent from `cli.py` as of 0.49.1:

| finding | evidence |
|---|---|
| 19.2 nothing writes a `--reads` file | `reads_path` is only ever `json.loads`ed; no writer anywhere |
| 18.4 `check` cannot be scoped to a model | flags are `--check --config --dbt --dialect --json --limit --profiles-dir --project-dir --store --target --verify`. `--check` scopes by check, never by model |
| 20.1 `contract()` omits findings, waivers, measurements | `live.contract_of` returns shape only |
| 17 batching sits in `probe.py`, not in `run_sql` | `cli.py` 5 callers, `practices.py` 5, `rows.py` 2, all unbatched |
| 18.2 nothing checks `audit.yml` comments against the store | no such check in any bank |
| 20.3 monitoring has one judged family | `volume.yml` holds `volume_contradicts_a_claim` alone |
| 16.3 `stale` leads with a zero it cannot stand behind | presentation, `--exact` exists and is free |

19.2 is the strongest of these and the most valuable: the most expensive human-facing step in the loop is the
only one with no judged path, at a measured $0.000033 per finding.

### 21.6 The rule this document should have followed

Read the source before proposing the feature. `cli.py` gives all 47 commands and every flag in one pass and
is faster than shelling out to `--help` per command. Six of this document's proposals would not have been
written.

---

## 22. MCP parity, what the skills omit, and what a waiver actually is

Read from `mcp_server.py`, `cli.py`, `skilltext.py` and `config.py`, not from `--help`.

### 22.1 MCP is a read surface. 37 of 47 commands have no tool

```
MCP tools (21):
  blast_radius changed_contracts claims contract evidence findings guide lineage
  load_handback monitoring plan practices rebase review_queue rule spend stale
  suggestions traversal violations vocabulary

No MCP tool (37):
  check page review probe volume feeds columns semantics align tests ask banks
  config onboard scan cost effectiveness calibration disagreements regress
  adjudicate backtest calibrate completeness diff export infer inventory patch
  prune suggest trace traverse verify version_check version_stamps watch
```

Of the 21 tools, 19 read and 2 write (`rule`, `load_handback`). Everything that *runs* something -- any
structural check, any judged tier, any counted tier, either artifact, any config inspection, onboarding, or
question validation -- has no tool at all.

So MCP and the skills are not two routes to the same place. MCP is a strict subset, and the skill exists to
cover the other 37 commands with bash. Consequences worth naming:

- An agent without shell access cannot run assay. It can read what a previous run stored and rule on it,
  and nothing else.
- Ryan has asked for CLI/MCP/skill parity repeatedly. The gap is not a few missing tools, it is that the
  whole verb surface is absent.
- The highest-value additions are the ones an agent needs mid-task and cannot get: `check` (scoped, per
  18.4), `ask`, `banks`, `config`, `cost`. Those five turn MCP from a library into a working surface.

### 22.2 The skills omit the two flags that make half the commands work

`--project-dir` and `--dbt` appear **zero times** in 42,001 characters of `skilltext.py`. Every command that
reaches a warehouse needs them: `probe`, `volume`, `feeds`, `practices`, `adjudicate`, `completeness`,
`patch`, `tests --count-defaults`.

Every warehouse-touching example in the skill omits them:

```
assay practices --keys-only --model <model>
assay volume --json > volume.json          # "their connection, free, no judgment"
assay completeness
assay patch tests/assay
assay volume
```

The `volume` line acknowledges it needs a connection in the same comment that omits the flags supplying one.

Measured cost on this project: `practices` was run from that pattern and returned
"23 of 23 standard check(s) were NOT LOOKED AT". That output was read as a finding about the project. It was
a finding about the invocation. The tool does warn -- "could not count any proposed grain -- the models may
not be built, or `--dbt`/`--project-dir` may be wrong" -- but the warning is buried in output the skill
taught the agent to expect as normal.

Fix: every example in the skill that reaches a warehouse carries both flags, and the skill states once that a
counted-tier command without them is blind rather than clean.

### 22.3 What the skills get right, and the one thing that fails anyway

The setup block is present and correct:

```bash
uvx --refresh --from 'dbt-assay[mcp]' assay onboard --target target/
claude mcp add assay --scope project -- uvx --refresh --from 'dbt-assay[mcp]' assay mcp --target target
uvx --refresh --from 'dbt-assay[mcp]' assay skill all --write .
```

`guide("start")` is named as the order for a new project, with "do not skip to the judged tier". The box
guidance from 7.x is in there. None of it was followed on this project, and the reason is structural rather
than textual: the setup block sits inside a 42,000-character document that an agent reads when it decides to,
and nothing gates on having read it. Compare the one hook in sunny-data, which stops the agent whether or not
it read CLAUDE.md.

This is 18.3 restated from the other side. The skill is advisory, and advisory instructions are followed at
the rate an agent chooses, which this session measured at well under one.

`ASSAY_QUESTIONS` appears zero times in the skill, so the mechanism that makes bank location configurable is
undiscoverable from the documented path.

### 22.4 What a waiver is, exactly

`config.py`:

```python
class Waiver:
    question: str       # which check
    reason: str         # REQUIRED; config raises without it
    until: str | None   # optional expiry

def waived(self, model, question):
    for w in self.waivers.get(model, []):         # exact model name
        if w.question != question: continue
        if w.until and str(w.until) < today: continue   # an expired waiver is not a waiver
        return w
```

A waiver is **(model, check) -> "this is fine, here is why, until this date"**. One model, one check, exact
name match. With `until` set it stops silencing on that date and the finding returns on its own.

The distinction that matters, because the two are easy to confuse and mean opposite things:

| | claim | effect | counts as |
|---|---|---|---|
| `disagree` verdict | the check is WRONG here | removes the finding permanently | evidence about the check, feeds agreement rates |
| waiver in `audit.yml` | the check is RIGHT and I accept it | silences it, expires | a decision with a name and a date on it |

Assay already draws this line in the "Decide first" panel: if the reason names a *thing this warehouse has
that the checker has no word for*, the fix is vocab; only if it names a *case the checker gets wrong* is it
the check. Getting it backwards turns a real defect permanently invisible, which is why `reason` is required
and why an expiry is recommended.

Two gaps:

- **No selector.** `self.waivers.get(model, [])` is an exact-name dict while vocab terms take
  `applies_to: {select:, exclude:}`. Fourteen near-identical waivers cannot be written as one, and a new
  model with the same shape is uncovered. This is 18.4.
- **Nothing drafts the reason.** It is required, hand-written, and restates evidence assay already holds.
  19.3 covers this.

---

## 23. The three config surfaces: one is well designed, two are not

`vocab`, `waivers` and `explanations` are the three places a project teaches assay about itself. They are
built to three different standards.

### 23.1 When a waiver rather than a disagree, with live examples

Ryan: *"when would a finding be true and im like eh stfu basically?"*

The test is whether the check's **factual claim** is true.

| the claim is | do |
|---|---|
| false, it misread the SQL | **disagree**. Removes the finding permanently; counts as evidence about the check |
| true, and points at something real | **fix it** |
| true, but the action it implies is wrong here | **waiver**, with the reason and an expiry |
| true, and the reason names a THING the checker has no word for | **vocab**, because that fixes it everywhere |

A waiver is for *correct observation, wrong prescription*. It is never "eh, shut up" -- `reason` is required
in order to stop it being that, and this project's own config comment says why: "A waiver whose
justification is 'looks fine' is how a real finding gets silenced."

The four waivers in force here are all the same shape. `bbox_as_radius` on `stg_blm_plss_sections` is
factually correct: the SQL does build `ST_MakeEnvelope` and intersect with it. Disagreeing would be a lie.
But the envelope is a grid cell rather than an approximated circle, and the waiver says exactly that: "The
check's 'a box is not a circle' argument holds only where an envelope approximates a radius."

The sharpest is `int_water_structure_comid`, which counted the tie-break before waiving it and then concluded:

> assay flags it because comid is not DECLARED unique, which is true and is the honest state of the project
> rather than a defect in this model.

The check is right about the project and wrong about the model. That is the waiver case exactly.

All four carry `until: 2027-01-01`. That matters: "wrong prescription" can become right later, and an expired
waiver stops silencing, so the finding returns rather than disappearing forever.

### 23.2 `vocab` is the best-designed surface in the tool

It takes `applies_to` as a selector, string or `{select:, exclude:}`, validated by the same validator that
`when.select` uses on a question, and the validation **refuses syntax it does not understand rather than
matching everything** -- because a silently ignored selector scopes nothing while appearing to scope
something. `exclude` exists because `models/water/az` sits under `models/water`, so a Colorado term scoped to
the parent still reaches all 70 Arizona models. `exclude` with no `select` is an error.

The reason it is built this well is recorded in the source, from this project:

> 19,707 judged answers -- 25% -- were about Arizona models, and every one of them was sent sixteen
> assertions of Colorado water law as universal fact, including a statute citation with no force there. A
> term asserted outside where it is true steers every answer wrong at once.

### 23.3 `explanations` and `waivers` get none of that

```python
cfg.explanations = data.get("explanations") or {}        # config.py:367
self.waivers.get(model, [])                              # config.py:439
```

`explanations` are the per-model answer options added to the generic set for the row-adjudication family,
consumed at `cli.py:5852` by `assay adjudicate`. Neither surface has a selector, and `explanations` has no
validation whatsoever.

Consequences seen directly on this project:

- **40 marts, each needing its own options**, with no way to write one set for "every water section mart".
  The form renders 40 near-identical cards, which is the repetition complaint in 3.2 with a config cause
  rather than a rendering one.
- **Four near-identical waivers** where two of them say, in prose, that they are the same shape as each
  other: "Same shape as stg_blm_plss_sections: a grid cell from stored bounds, not a radius."
- A new model matching either pattern is covered by neither.

The fix is uniformity, and the good implementation already exists: give `waivers` and `explanations` the
same validated `applies_to` that `vocab` has. The selector, the validator and the refuse-unknown-syntax
behaviour are written and tested.

### 23.4 Nothing drafts any of the three

All three are hand-authored into `audit.yml`. For all three, assay already holds the evidence the human is
being asked to restate:

- a **waiver reason** restates a finding's own evidence
- a **vocab term** restates a measurement the vocab-candidates pane already computed and prints with
  `means:` and `implies:` deliberately empty
- an **explanation option** restates what the failing rows of a test have in common, which is what
  `adjudicate` reads anyway

This is 19.3 applied to config rather than to findings. The rule that keeps it honest is the one the vocab
pane already follows: propose the candidate and the measurement, never the meaning. A draft a person edits
is different from a decision made for them.

### 23.5 One thing worth copying elsewhere: the guide is generated

`guide.py` does not hand-maintain its content. `_lint_rules()` reads the lint source for its own rule names,
`_families()` loads every bank, `_config_keys()` parses `DEFAULT_YML`. So a new lint rule, a new question
family or a new config key cannot go unmentioned.

That is the correct answer to the drift problem in 18.2, already implemented in one place. The stale
`audit.yml` comments ("38 of 38 agreed" against a store holding 90) are the same class of bug in a file
where the same technique has not been applied.

---

## 24. The missing verdict, and self-auditing

### 24.1 There is no verdict for "correct, and I accept it"

`store.py:762`:

```python
if verdict not in ("agree", "disagree", "unclear"):
```

Section 23.1 establishes that the honest response to a true finding with a wrong prescription is a waiver.
**The form and the CLI cannot record that.** From a card the options are:

| verdict | what it says | what it costs when it is the wrong one |
|---|---|---|
| `agree` | the finding is right | it stays open forever and lands in "63 agreed, 0 fixed" |
| `disagree` | the check is WRONG | a lie; permanently removes a true finding |
| `unclear` | the finding does not carry enough to decide | also a lie; counts as evidence the state is thin |

This is not a UI nicety. `disagree` feeds the agreement rates, and the agreement rate is the single number
that decides whether a check may ever gate a build. `code_contradicts_a_claim` sits at 33% and
`identifier_outside_grain` at 44% on this project. If any of those disagreements were really
correct-but-accepted, the check is being told it was wrong when it was right, and the one signal meant to be
independent is being polluted by the absence of an option.

It also explains the stuck loop metric. "63 agreed, 0 fixed" almost certainly contains findings that were
read, judged correct, and accepted, with no way to say so.

Proposed fourth verdict, `accept`:

- does **not** claim the check erred, so it does not count against the agreement rate
- suppresses the finding from the open list, the way a waiver does
- writes a waiver proposal into `audit.yml` carrying the reason and an expiry
- is excluded from "agreed and still here", because an accepted finding is not outstanding work

### 24.2 The waivers tab proposes waivers from disagreements, which is the opposite claim

Correction to an earlier reading of this pane: it does **not** merely list existing waivers. It proposes
candidates, and the restraint behind it is right (`reviewform.py:530`):

> A waiver written from a ruling is the one kind assay can propose honestly: the reason is not generated, it
> is the sentence the person typed when they disagreed.

The problem is the source. It selects rulings where `verdict == "disagree"`. A disagreement says the check
was wrong; a waiver says the check was right and is accepted. Turning one into the other records the
opposite of what the person said.

With 24.1 in place this resolves itself: waiver candidates come from `accept` verdicts, which is exactly what
an `accept` means, and the pane stops converting a claim into its negation.

Two smaller form notes:

- **Tab order.** Current: words, explanations, waivers, monitoring, settings, findings. Settings should be
  last; it is currently fifth of six.
- **A waiver candidate should be visible from the finding**, not only in a separate pane. The card is where
  the evidence is and where the decision is made.

### 24.3 What `claims` and `verify` get right, and why it matters here

The strongest design principle in the tool, from `assay claims`:

> Extraction is SELECTION, never generation: code splits the prose, and a judgment says what job each
> sentence is doing. The model never writes a claim, so every one points at the file and line where a person
> wrote it.

And the measurement that forced it: "Boulder commercial building permits, residential filtered out" judged as
one claim split 0.51/0.47 and flipped between runs. Atomised, the sharpest claim read `contradicts` at 0.82.

This is the rule that should govern every drafting proposal in this document. 19.3 and 23.4 propose drafting
vocab meanings, column descriptions and waiver reasons. Each must be a **proposal a person edits**, keyed to
the evidence it came from, never a value written into config unattended. `claims` already shows the shape:
select, point at the source, ask one narrow question, never generate.

### 24.4 Self-auditing: more exists than expected, and one class is missing

Already shipped:

| command | audits |
|---|---|
| `config --check --strict` | what assay resolved: config file, provider, where the key came from, the cap |
| `banks --strict` | every question's shape, against lint rules measured to fail |
| `effectiveness` | agreement per family per version; disagreements still open; unclears |
| `calibration` | whether confidence predicts correctness |
| `backtest` | replays the repo's own git history and measures whether the checks catch what it already fixed |
| `version-check`, `version-stamps` | whether a version bump is owed; whether each row records the logic that produced it |
| `guide.py` | generated from source: lint rules, families and config keys are read, never typed |

`config`'s docstring records exactly why self-inspection earns its place: "NO API KEY FOUND was wrong for
weeks and nothing could show it... A capability check that can be wrong needs a way to see what it decided."

**The missing class is prose drift in assay's own artifacts.** Three instances found in this session:

1. `audit.yml` says "38 of 38 agreed" against a store holding 90, and "nothing gates until a question clears
   min_adjudications, which none do" when one now does (18.2).
2. `skilltext.py` contains seven example invocations of warehouse-touching commands and **none** carries
   `--project-dir` or `--dbt` (22.2). This produced a blind `practices` run that was read as a finding.
3. This document proposed six features that already ship (21).

All three are mechanically checkable, and `guide.py` already demonstrates the technique:

- **Validate every example command in the skill against the real CLI signature.** Parse `assay <cmd> <flags>`
  out of `skilltext.py`, check each flag exists and that warehouse-touching commands carry a connection.
  Catches 22.2 exactly, in CI, forever.
- **`config_comment_contradicts_the_store`.** A comment in `audit.yml` asserting a count is checkable against
  the store the same way a `schema.yml` description is checkable against SQL. It is the tool's own flagship
  check pointed at its own config.
- **Run assay on assay.** The repo is not a dbt project, so the dbt-specific tiers do not apply, but the
  prose-versus-code family is exactly what `description_contradicts_the_code` does and it has never been
  pointed inward.

The first of these is the cheapest and would have prevented the most expensive mistake in this session.
