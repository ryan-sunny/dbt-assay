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
