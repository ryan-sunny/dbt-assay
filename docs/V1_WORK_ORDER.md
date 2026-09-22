# v1 work order

Everything in `FIELD_REPORT.md` closes before v1 is called. Ryan settled that, and three other
scope questions, on 2026-09-22. This is the ordered list, with what a second session verified
against the source and the store before any of it was planned.

Read `FIELD_REPORT.md` first — it is the evidence and the quotes. This is only the plan.

## The four decisions, recorded

| question | answer |
|---|---|
| what is v1 | **everything in the field report** |
| report page vs review form | **two artifacts that link.** Report read-only and shareable; the form owns every box you type into, including the rest of `audit.yml`. Not merged |
| warehouse cost accounting (§10) | **steps 1–2 first**, so the monitoring sweep records cost from its first run. Steps 3–5 also before v1 |
| Markdown panes (§3.3) | **render them.** A small inline renderer, no dependency, page stays one offline file |

## What verification changed

Four items in the field report are not what they look like. Checked against the source and the
production store before planning.

**§1.7 is already fixed.** Seed types are derived from the store (`export.py:_SEED_TYPE` and
`seed_type`). `available`, `carried` and `dropped` all resolve to `integer` today. Nothing to do;
the report was written from an earlier artifact.

**§1.3 is not a bug.** The `assay.0.20.0` rows are *agent* rulings. `mcp_server.py:356` stamps
`assay.{__version__}` — the version of the package the agent is running — while `runs` records the
version that ran the check. Both are true and they are different facts sharing a column. Fixed by
§1.5's label, not by touching data.

**§1.2 is confirmed and the line is exact.** `cli.py:4326` writes the `::finding::` row through
`store.adjudicate` with no `prompt_version`, so it defaults to `''`. 70 of 136 human rows on the
production store, all of them `::finding::` subjects. It bypasses `_record_one_verdict`, whose own
docstring warns that a second spelling of this write will drift. It did.

**§7.7 has a mechanism.** `data_monitoring_metrics` has 27 distinct `created_at` days on the box,
not 0. So "only 0 writes recorded" means the cadence query FAILED and `probe.run_sql` returned `[]`,
which is indistinguishable from "no rows". This is the absent-vs-failed defect one layer below
where `elementary.read` already handles it: the top-level reachability probe passes, then an
individual statement fails silently. Every `run_sql` caller has this.

## Order

Cost first, because the sweep is waiting on it and a sweep without a baseline has to be re-run.

### 1. Warehouse cost, steps 1–2 (§10.9)
- `warehouse_calls` table per §10.4, `NEVER_PRUNED`, in `SCHEMA.md` and the mermaid.
- Instrument `probe.run_via_dbt` and `probe.run_sql` — the only two places a statement reaches a
  warehouse. Timing, columns touched, rows, and the write.
- Bytes estimated from declared types × known row count. Rate card in `audit.yml`, never a constant.
- `bytes_measured` stays NULL unless an adapter gave a real number, and `estimate_basis` is not
  optional. A column mixing measured and guessed numbers is §1.7 again.

### 2. Bugs (§1)
- **1.1** Capture on first `pointermove` past a threshold, not on `pointerdown`. A tap stays a tap.
  Give the focus node an `onclick` too — the model the picture is about is the only unclickable box.
- **1.2** Pass the version on the `::finding::` write. Better: route it through
  `_record_one_verdict` so there is one spelling. Backfill the 70 existing rows from `runs`.
- **1.4** `' · '` separator, matching `explorer.py:1112`.
- **1.6** Render a day with runs and no calls as a zero, and say why (everything cached). Add the
  per-day series Ryan asked for.
- **7.7** Make a failed statement distinguishable from an empty result at every `run_sql` call site.

### 3. Shipped text (§2)
Delete assay's own release history from user-facing prose (`suggest.py:274,341,426`). Replace the
Colorado water examples with a generic domain — orders, customers, invoices
(`questions/semantics.yml:78`, `lint.py:671`, `reviewform.py:810`). Make the `min_adjudications`
paragraph conditional. Cut "thresholds and all". Rewrite the empty-vocab explainer and "severity
decides" to say what they resolve to.

### 4. The shared layout (§3) — the largest item
One two-pane shell: filter rail and scrollable list left, detail right, **both height-bound to the
window** so the page never scrolls as a whole. Every tab uses it: Findings, Answers, Claims, What
to configure, Questions. Group repeated cards by the reason they fired instead of stacking 44 of
equal weight. Rewrite the generated sentences in §3.4 so the useful part leads.

### 5. Markdown panes (§3.3)
~60 lines, inline, headings/lists/code/bold. Replaces hand-built DOM in the document-shaped panes.

### 6. The tabs (§4)
Overview reworked as the pitch surface — 47,491 claims read, 5,794 sentences classified, 18,079
answers, 356 models, $1.29, 264 defects no dbt test can express. The chain drawn as a wrapped grid
with real spacing. Answers showing the question text above its answers. Trace the prose appearing
in a SUBJECT cell (§4.5) — it is not in any sunny-data source file and not in assay's either.

### 7. The form (§5)
Header fixed beside the tab strip. Words tab states the task in the imperative with one filled
example. Prefill the why box from the agent's reading. `load_handback(path)` on the MCP server, and
the form skill drains `review_queue` and rules on everything before rendering, then offers to load
the handback the moment it is downloaded. The rest of `audit.yml` moves into the form.

### 8. Capabilities (§6)
Column descriptions into the column subject state. Vocab seeded from them. A column-level analogue
of `description_contradicts_the_code`. Flag missing descriptions, ingestion sources first.
Disagreement across models about what a column means.

### 9. Warehouse cost, steps 3–5 (§10)
`--dry-run`, then `--sample` with the label plumbed to every surface, then adapter-measured bytes
if `dbt show --log-format json` carries the response.

### 10. Operational (§7, §8)
Empty-store signal on every tool that reads the store — a new store must never read like a clean
warehouse. Lock-holder reporting and a timeout before taking the write lock. Lint a question that
references state its declared subject kind does not carry. `uvx --refresh` and the container env
recreate in the onboarding skill.

### 11. The mark (§11)
Inline SVG in both page headers, 23px, and the favicon. Spec and the exact string are in §11.3.

## Two things that must not be lost

**Release verification.** `node --check` on the extracted page script is already a test and a CI
job as of 0.47.2. Add the Playwright load asserting zero `pageerror` and non-empty panes (§8). File
size, exit code, determinism and the artifact round-trip all passed on a completely dead page.

**The edit pattern that caused three defects.** A script that rewrites string concatenations across
line boundaries does not know where a literal ends; it corrupted seven strings in one pass and
produced a well-formed Python file and a well-formed dead page. A multi-edit script that asserts
between replacements loses every earlier edit when one fails, silently — that shipped `elem.cadence`
into 0.46.0. One concern per edit, write immediately.
