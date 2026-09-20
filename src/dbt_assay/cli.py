"""`assay` -- the command. The package is dbt-assay so it is findable; the command is short to type."""
from __future__ import annotations

import json as _json
import time
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, contracts, mcp_server, provenance, relate
from . import backtest as backtest_mod
from . import columns as columns_mod
from . import diff as diff_mod
from . import export as export_mod
from . import inventory as inv_mod
from . import judged as judged_mod
from . import live as live_mod
from . import probe as probe_mod
from . import versioning as ver_mod
from .checks import run_all
from .config import DEFAULT_YML, Config
from .infer import Schema, derive_columns
from .jev import BudgetExceeded, Client, NoProvider, decide
from .manifest import Project
from .parse import digest
from .store import Store

app = typer.Typer(add_completion=False, help="Recover the semantics your warehouse never wrote down.")
console = Console()


def _find_target(given: str | None) -> Path:
    if given:
        p = Path(given)
        if (p / "manifest.json").exists():
            return p
        if (p / "target" / "manifest.json").exists():
            return p / "target"
        raise typer.BadParameter(f"no manifest.json under {p}")
    for c in (Path("target"), Path("transform/target"), Path("dbt/target")):
        if (c / "manifest.json").exists():
            return c
    raise typer.BadParameter(
        "could not find target/manifest.json. Pass --target, or run `dbt parse` in your project.")


def _load(target: Path):
    project = Project.load(target)
    digests, failures = {}, []
    for uid, m in project.models.items():
        if not m.readable:
            continue
        d = digest(m.compiled, m.name)
        digests[uid] = d
        if not d.ok:
            failures.append((uid, m.name, m.path, d.error))
    # Columns are derived parents-first so a `select *` can be expanded with what the parents were
    # found to offer. Without this a starred model reports zero columns, which reads exactly like a
    # model that genuinely offers none.
    schema = Schema.load(project, target)
    schema_stats = derive_columns(project, digests, schema)
    return project, digests, failures, schema, schema_stats


def _coverage_panel(project, digests, failures) -> None:
    cov = project.coverage()
    ok = sum(1 for d in digests.values() if d.ok)
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("project", f"[bold]{project.project_name}[/]  (dbt {project.dbt_version})")
    t.add_row("models", f"{cov['models']}   sources {cov['sources']}   tests {cov['tests']}   edges {cov['edges']}")
    t.add_row("compiled SQL", f"{cov['readable']} read  ({cov['from_disk']} from disk, "
                              f"{cov['from_manifest']} from manifest)")
    if cov.get("conflicting_copies"):
        t.add_row("[yellow]ambiguous[/]",
                  f"[yellow]{cov['conflicting_copies']} models have a DIFFERENT compiled body in "
                  f"another target dir. The canonical copy was audited.[/]")
    if cov["unreadable"]:
        t.add_row("[yellow]not audited[/]",
                  f"[yellow]{cov['unreadable']} models have no compiled SQL. "
                  f"Run `dbt compile` to include them.[/]")
    t.add_row("parsed", f"{ok}/{len(digests)}" + (f"   [yellow]{len(failures)} failed[/]" if failures else ""))
    console.print(t)
    for _, name, _, err in failures[:5]:
        console.print(f"   [yellow]parse failed[/] {name}: {err}")


def _schema_panel(schema, stats: dict) -> None:
    from collections import Counter
    prov = Counter(schema.columns(u).source_of for u in schema.project.models)
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("columns known", f"{sum(v for k, v in prov.items() if k != 'unknown')}"
                               f"/{len(schema.project.models)} models"
                               f"   [dim](derived {prov['derived']}, catalog {prov['catalog']}, "
                               f"declared {prov['declared']})[/]")
    if stats["expanded"]:
        t.add_row("star expansion", f"{stats['expanded']} models had `select *` expanded from their parents")
    if not schema.catalog_present:
        t.add_row("[dim]catalog.json[/]",
                  "[dim]absent. `dbt docs generate` adds real column lists for sources.[/]")
    console.print(t)


@app.command()
def scan(target: str = typer.Option(None, "--target", "-t", help="path to dbt target/ directory")):
    """Read the project and report what can and cannot be audited."""
    tdir = _find_target(target)
    t0 = time.time()
    project, digests, failures, schema, sstats = _load(tdir)
    _coverage_panel(project, digests, failures)
    _schema_panel(schema, sstats)

    feats = Table(title="\nstructure found", show_header=True, header_style="bold")
    feats.add_column("feature"); feats.add_column("count", justify="right")
    counts = {
        "CTEs": sum(len(d.ctes) for d in digests.values()),
        "joins": sum(len(d.joins) for d in digests.values()),
        "cross / lateral joins": sum(1 for d in digests.values() for j in d.joins
                                     if j.kind == "CROSS" or j.lateral),
        "window functions": sum(len(d.windows) for d in digests.values()),
        "models using QUALIFY": sum(1 for d in digests.values() if d.has_qualify),
        "models with a GROUP BY": sum(1 for d in digests.values() if d.group_by),
        "distinct join keys": len({k for d in digests.values() for k in d.join_keys()}),
        "edges with column facts": len(relate.edge_facts(project, digests)),
        "models declaring a key": len(relate.declared_keys(project)),
    }
    for k, v in counts.items():
        feats.add_row(k, str(v))
    console.print(feats)
    console.print(f"\n[dim]{time.time() - t0:.1f}s, no network, no API key[/]")


@app.command()
def check(
    target: str = typer.Option(None, "--target", "-t"),
    json_out: bool = typer.Option(False, "--json", help="emit findings as JSON"),
    store_path: str = typer.Option("assay.duckdb", "--store",
                                   help="the DuckDB file to read judgments from and write to"),
    limit: int = typer.Option(25, "--limit", "-n", help="how many findings to print"),
    check_name: str = typer.Option(None, "--check", help="only this check"),
    config_path: str = typer.Option(".", "--config", help="directory holding audit.yml"),
):
    """Run the structural checks. No network, no API key, no spend."""
    tdir = _find_target(target)
    project, digests, failures, schema, sstats = _load(tdir)
    facts, edge_findings = relate.run_all(project, digests, schema)
    findings = run_all(project, digests) + edge_findings
    # *** ONE STREAM. ***
    # Structural and judged findings were in separate worlds: `check` saw only the parser's, and
    # nothing from `infer` or `columns` ever reached the store. "What is wrong with this model"
    # needs a single answer.
    if Path(store_path or "").exists():
        _s = Store(store_path)
        _obs = probe_mod.read(_s)
        _entries = inv_mod.build(project, digests, schema, _s, _obs)
        findings += judged_mod.run_all(project, _entries, relate.declared_keys(project))
        _s.close()
    findings.sort(key=lambda f: -f.weight)
    if check_name:
        findings = [f for f in findings if f.check == check_name]

    # *** audit.yml NOW DOES SOMETHING. ***
    # Thresholds, plain actions, waivers and scoping are applied here to BOTH streams. A config
    # that parses but is never consulted implies a control that does not exist, which is worse
    # than having none.
    cfg = Config.load(config_path)
    _st = Store(store_path) if Path(store_path or "").exists() else None
    policed, waived = judged_mod.apply_policy(findings, cfg, _st, project)
    if _st:
        _st.close()
    findings = [f for f, _a, _w in policed]
    actions = {(f.check, f.subject): a for f, a, _w in policed}

    if json_out:
        print(_json.dumps({
            "coverage": project.coverage(),
            "parse_failures": [{"model": n, "error": e} for _, n, _, e in failures],
            "findings": [{"check": f.check, "model": f.subject_name, "file": f.file,
                          "summary": f.summary, "detail": f.detail, "weight": round(f.weight, 2),
                          "descendants": f.descendants, "marts": f.marts, "evidence": f.evidence}
                         for f in findings],
        }, indent=2))
        raise typer.Exit(0)

    _coverage_panel(project, digests, failures)
    _schema_panel(schema, sstats)

    if not findings:
        console.print("\n[green]no structural findings[/]")
    else:
        by = {}
        for f in findings:
            by.setdefault(f.check, []).append(f)
        summary = Table(title="\nfindings", show_header=True, header_style="bold")
        summary.add_column("check"); summary.add_column("n", justify="right")
        for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
            summary.add_row(k, str(len(v)))
        console.print(summary)

        console.print()
        for f in findings[:limit]:
            reach = f"{f.descendants} downstream, {f.marts} marts" if f.descendants else "leaf"
            act = actions.get((f.check, f.subject), "annotate")
            colour = {"fail": "red", "queue": "yellow"}.get(act, "dim")
            console.print(f"[bold]{f.subject_name}[/]  [dim]{f.file}[/]")
            console.print(f"  [{colour}]{act}[/]  {f.check}: {f.summary}  [dim]({reach})[/]")
            console.print(f"  [dim]{f.detail}[/]\n")
        if len(findings) > limit:
            console.print(f"[dim]... {len(findings) - limit} more. --limit to see them, "
                          f"--json for all.[/]")

    if waived:
        console.print(f"[dim]{len(waived)} finding(s) suppressed by audit.yml: "
                      + ", ".join(sorted({w for _f, w in waived}))[:160] + "[/]")

    failing = [f for f, a, _w in policed if a == "fail"]
    if failing:
        console.print(f"\n[red]{len(failing)} finding(s) configured to fail.[/]")

    if store_path:
        run_id = uuid.uuid4().hex[:12]
        s = Store(store_path)
        ok = sum(1 for d in digests.values() if d.ok)
        s.write_run(run_id, project, project.coverage(), ok, len(failures), str(tdir), __version__)
        s.write_findings(run_id, findings)
        s.write_edge_facts(run_id, facts)
        s.write_unreadable(run_id, [(uid, m.name, m.path, "no compiled SQL")
                                    for uid, m in project.models.items() if not m.readable]
                           + [(uid, n, p, e) for uid, n, p, e in failures])
        prev = s.previous_run(project.project_name, run_id)
        if prev:
            d = s.diff(prev, run_id)
            console.print(f"\n[bold]vs previous run:[/] {len(d['new'])} new, "
                          f"{len(d['gone'])} resolved, {d['same']} unchanged")
            for c, n, sm in d["new"][:5]:
                console.print(f"  [red]+[/] {n}: {sm}")
            for c, n, sm in d["gone"][:5]:
                console.print(f"  [green]-[/] {n}: {sm}")
        s.close()
        console.print(f"\n[dim]run {run_id} written to {store_path}[/]")

    if failing:
        raise typer.Exit(1)


@app.command()
def version():
    """Print the version."""
    console.print(f"assay {__version__}")


if __name__ == "__main__":
    app()


@app.command()
def init(force: bool = typer.Option(False, "--force", help="overwrite an existing audit.yml")):
    """Write an audit.yml with the defaults. Asks nothing, enables no spend."""
    p = Path("audit.yml")
    if p.exists() and not force:
        console.print(f"[yellow]{p} already exists.[/] Pass --force to overwrite.")
        raise typer.Exit(1)
    p.write_text(DEFAULT_YML)
    console.print(f"wrote [bold]{p}[/]. Nothing in it enables spend; the judgment tier is opt-in.")


def _grain_setup(target: str | None, store_path: str | None = None):
    tdir = _find_target(target)
    project, digests, _fail, schema, _sstats = _load(tdir)
    declared = relate.declared_keys(project)
    observed = {}
    if store_path and Path(store_path).exists():
        s = Store(store_path)
        observed = probe_mod.read(s)
        s.close()
    proposed = contracts.propose_all(project, digests, schema, declared, observed)
    return tdir, project, digests, schema, declared, proposed


@app.command()
def infer(
    target: str = typer.Option(None, "--target", "-t"),
    print_state: bool = typer.Option(False, "--print-state",
                                     help="render exactly what would be sent, and send nothing"),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N models (0 = all)"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config", help="directory holding audit.yml"),
):
    """Infer each model's grain. Code proposes the candidates; a judgment picks the key."""
    cfg = Config.load(config_path)
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target, store_path)

    # Only models with SURPLUS candidates need a judgment at all. A single-column candidate set has
    # nothing to eliminate, so asking about it spends tokens to learn what code already knew.
    work = [(uid, c) for uid, c in proposed.items() if len(c.columns) > 1]
    if limit:
        work = work[:limit]
    console.print(f"[bold]{len(proposed)}[/] models have code-proposed candidates; "
                  f"[bold]{len(work)}[/] have more than one column and need a judgment.")

    if print_state:
        for uid, cand in work[:3]:
            st = contracts.build_state(uid, project, digests, schema, cand, declared, cfg.vocab)
            qs = contracts.key_questions(cand)
            console.print(f"\n[bold]{project.models[uid].name}[/]  via {cand.route}")
            console.print(_json.dumps({"state": st, "questions": qs}, indent=1, default=str))
        est = sum(len(_json.dumps(contracts.build_state(uid, project, digests, schema, c,
                                                        declared, cfg.vocab), default=str))
                  for uid, c in work) / 4
        console.print(f"\n[dim]{len(work)} calls, ~{int(est):,} input tokens, "
                      f"about ${est * 0.042 / 1_000_000:.4f}. Nothing was sent.[/]")
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key.[/] The structural checks need none: `assay check`.")
        console.print("[dim]Set TYPESAFE_API_KEY (preferred) or OPENROUTER_API_KEY, or use "
                      "--print-state to see exactly what would be sent.[/]")
        raise typer.Exit(1)

    store = Store(store_path)
    judged, asked = {}, 0
    for uid, cand in work:
        state = contracts.build_state(uid, project, digests, schema, cand, declared, cfg.vocab)
        try:
            answers = decide(store, client, state, contracts.key_questions(cand),
                             decision_key=uid, prompt_version=contracts.PROMPT_VERSION,
                             caller="assay.infer")
        except BudgetExceeded as e:
            console.print(f"[yellow]stopped: {e}[/]")
            break
        except NoProvider as e:
            console.print(f"[red]{e}[/]")
            raise typer.Exit(1) from None
        judged[uid] = contracts.key_from_answers(cand, answers)
        asked += 1

    console.print(f"\njudged [bold]{asked}[/] models   "
                  f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    narrowed = [(u, g) for u, g in judged.items() if g.dropped]
    for uid, g in narrowed[:12]:
        console.print(f"  [bold]{project.models[uid].name}[/]: key {g.columns}  "
                      f"[dim]dropped {g.dropped}[/]")
    store.close()


@app.command()
def calibrate(
    target: str = typer.Option(None, "--target", "-t"),
    limit: int = typer.Option(0, "--limit", "-n"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Measure the grain judgment against the keys this project already declares.

    *** THE LABELLED SET IS FREE AND ALREADY IN THE REPO. ***
    Every `unique` and `unique_combination_of_columns` test is a human statement of a model's key.
    So the first thing this tier produces is a confusion matrix, not an impression -- which is the
    only thing that ever earns a question the right to fail a build.
    """
    cfg = Config.load(config_path)
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target, store_path)
    work = [(uid, c) for uid, c in proposed.items()
            if uid in declared and len(c.columns) > 1]
    if limit:
        work = work[:limit]

    code_exact = sum(1 for uid, c in work
                     if sorted(x.lower() for x in c.columns) == sorted(declared[uid]))
    console.print(f"[bold]{len(work)}[/] labelled models with surplus candidates "
                  f"(code alone is exactly right on {code_exact})")

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key; cannot calibrate.[/] `assay infer --print-state` shows "
                      "what would be sent.")
        raise typer.Exit(1)

    store = Store(store_path)
    exact = over = under = wrong = unsure = 0
    rows = []
    for uid, cand in work:
        state = contracts.build_state(uid, project, digests, schema, cand, declared, cfg.vocab)
        try:
            answers = decide(store, client, state, contracts.key_questions(cand),
                             decision_key=uid, prompt_version=contracts.PROMPT_VERSION,
                             caller="assay.calibrate")
        except BudgetExceeded as e:
            console.print(f"[yellow]stopped: {e}[/]")
            break
        g = contracts.key_from_answers(cand, answers)
        got, want = {x.lower() for x in g.columns}, set(declared[uid])
        # A disagreement only on columns the judgment FLAGGED as unresolved is not a wrong answer,
        # it is an unanswered one. Counting it as wrong hides that the model told you so.
        verdict = ("exact" if got == want else
                   "uncertain" if (got - want) and (got - want) <= set(g.uncertain) else
                   "superset" if want < got else
                   "subset" if got < want else "wrong")
        if verdict == "exact":
            exact += 1
        elif verdict == "uncertain":
            unsure += 1
        elif verdict == "superset":
            over += 1
        elif verdict == "subset":
            under += 1
        else:
            wrong += 1
        rows.append((project.models[uid].name, verdict, sorted(got), sorted(want)))

    n = max(exact + over + under + wrong + unsure, 1)
    t = Table(title="\ngrain judgment vs the project's own declared keys", header_style="bold")
    t.add_column("outcome"); t.add_column("n", justify="right"); t.add_column("%", justify="right")
    for label, v in (("exact", exact), ("flagged uncertain", unsure),
                     ("kept too many", over), ("dropped too many", under),
                     ("disagrees", wrong)):
        t.add_row(label, str(v), f"{100 * v // n}%")
    console.print(t)
    console.print(f"[dim]code alone was exact on {code_exact}/{len(work)}; "
                  f"with judgment {exact}/{n}[/]")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    for name, v, got, want in [r for r in rows if r[1] != "exact"][:10]:
        console.print(f"  [yellow]{v}[/] {name}: got {got}  declared {want}")
    store.close()


@app.command()
def probe(
    target: str = typer.Option(None, "--target", "-t"),
    project_dir: str = typer.Option(".", "--project-dir",
                                    help="the dbt project to run `dbt show` from"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dialect: str = typer.Option("duckdb", "--dialect"),
    dbt_bin: str = typer.Option("dbt", "--dbt-bin",
                                help='how to invoke dbt, e.g. "uv run dbt"'),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="print the SQL that would run, and run nothing"),
    emit: bool = typer.Option(False, "--emit",
                              help="write the SQL to stdout for you to run yourself"),
    load: str = typer.Option(None, "--load", help="a JSON file of results from --emit"),
    limit: int = typer.Option(0, "--limit", "-n"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
):
    """Count what the SQL cannot settle. Runs through YOUR dbt; assay never sees a credential."""
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target, store_path)
    known = {uid: [c.lower() for c in c_.columns] for uid, c_ in proposed.items()}
    tg = probe_mod.targets(project, digests, schema, declared, known)
    if limit:
        tg = tg[:limit]

    console.print(f"[bold]{len(tg)}[/] relations have no settled grain and are read by a model.")
    if not tg:
        raise typer.Exit(0)

    if emit:
        print(probe_mod.emit(tg, dialect))
        raise typer.Exit(0)

    if dry_run:
        for t_ in tg[:8]:
            console.print(f"\n[bold]{t_.relation}[/]  [dim]{t_.why}[/]")
            console.print(f"  [dim]{probe_mod.build_sql(t_, dialect)[:220]}[/]")
        console.print(f"\n[dim]{len(tg)} statements, one scan each. Nothing was run.[/]")
        raise typer.Exit(0)

    store = Store(store_path)
    if load:
        payload = _json.loads(Path(load).read_text())
        obs = []
        for t_ in tg:
            row = payload.get(t_.relation)
            if row:
                obs += probe_mod.interpret(t_, row)
        probe_mod.write(store, obs, via="loaded")
        console.print(f"loaded {len(obs)} observations")
        store.close()
        raise typer.Exit(0)

    ok = unknown = 0
    found = []
    for t_ in tg:
        obs, _sql = probe_mod.run_via_dbt(t_, project_dir, profiles_dir, dialect,
                                          dbt_bin=dbt_bin)
        probe_mod.write(store, obs)
        for o in obs:
            if o.status == "unknown":
                unknown += 1
            else:
                ok += 1
            if o.status == "unique":
                found.append(o)
    console.print(f"observed [bold]{ok}[/] columns, [yellow]{unknown} unknown[/] "
                  f"(a failure is recorded as unknown, never as 'not unique')")
    for o in found[:15]:
        console.print(f"  [green]unique[/] {o.relation}.{o.column}  [dim]{o.detail}[/]")
    store.close()


@app.command()
def columns(
    target: str = typer.Option(None, "--target", "-t"),
    print_state: bool = typer.Option(False, "--print-state",
                                     help="render exactly what would be sent, and send nothing"),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N models"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    with_null: bool = typer.Option(False, "--with-null",
                                   help="also ask what a NULL means. Off by default: without a "
                                        "null rate from `assay probe` it answers at ~0.49 "
                                        "confidence over six options."),
    control: bool = typer.Option(False, "--control",
                                 help="ask only about columns the project already labels, and "
                                      "report agreement"),
):
    """Judge each column's role and what a NULL in it would mean."""
    cfg = Config.load(config_path)
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target, store_path)
    labels = columns_mod.free_labels(project)

    work = []
    for uid in project.models:
        if uid not in digests or not digests[uid].ok:
            continue
        facts = columns_mod.facts_for(uid, project, digests, schema, declared)
        cols = [c for c in facts]
        if control:
            # *** ASK ABOUT WHAT IS ALREADY KNOWN. ***
            # A not_null test declares NULL impossible; a unique test declares an identifier.
            # Agreement on those is measurable today, with no human labelling at all.
            # *** ONLY THE ROLE FAMILY HAS USABLE FREE LABELS. ***
            # A not_null test says a column cannot be NULL, which code now decides, so it is not
            # evidence about what a NULL would MEAN. Using it as such measured 0/25 and measured
            # the wrong thing.
            cols = [c for c in cols if (uid, c) in labels.role]
        if cols:
            grain = [x.lower() for x in (proposed[uid].columns if uid in proposed else
                                         declared.get(uid) or [])]
            work.append((uid, facts, cols, grain))
    if limit:
        work = work[:limit]

    calls = sum(len(columns_mod.chunks(c)) for _u, _f, c, _g in work)
    ncols = sum(len(c) for _u, _f, c, _g in work)
    console.print(f"[bold]{ncols}[/] columns across [bold]{len(work)}[/] models, "
                  f"{calls} calls at {columns_mod.CHUNK} columns each")

    if print_state:
        for uid, facts, cols, grain in work[:2]:
            chunk = columns_mod.chunks(cols)[0]
            st = columns_mod.build_state(uid, project, schema, facts, chunk, grain, cfg.vocab)
            console.print(f"\n[bold]{project.models[uid].name}[/]")
            console.print(_json.dumps({"state": st,
                                       "questions": columns_mod.questions_for(chunk, facts, with_null)},
                                      indent=1, default=str)[:2600])
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key.[/] --print-state shows exactly what would be sent.")
        raise typer.Exit(1)

    store = Store(store_path)
    agree = disagree = 0
    disagreements = []
    for uid, facts, cols, grain in work:
        for chunk in columns_mod.chunks(cols):
            st = columns_mod.build_state(uid, project, schema, facts, chunk, grain, cfg.vocab)
            try:
                answers = decide(store, client, st, columns_mod.questions_for(chunk, facts, with_null),
                                 decision_key=uid,
                                 prompt_version=f"{columns_mod.ROLE_VERSION}+{columns_mod.NULL_VERSION}",
                                 caller="assay.columns")
            except BudgetExceeded as e:
                console.print(f"[yellow]stopped: {e}[/]")
                store.close()
                raise typer.Exit(0) from None
            for c in chunk:
                for fam, qid, lab in (("column_role", f"role__{c}", labels.role.get((uid, c))),
                                      ("null_meaning", f"null__{c}",
                                       labels.null_meaning.get((uid, c)))):
                    a = answers.get(qid)
                    if not a or lab is None:
                        continue
                    if a["answer"] == lab:
                        agree += 1
                    else:
                        disagree += 1
                        disagreements.append(
                            (project.models[uid].name, c, fam, a["answer"], lab,
                             a.get("confidence")))

    n = agree + disagree
    console.print(f"\n[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    if n:
        console.print(f"[bold]against the project's own tests:[/] {agree}/{n} agree "
                      f"({100 * agree // n}%)")
        for name, c, fam, got, want, conf in disagreements[:12]:
            cf = f" @{conf:.2f}" if conf is not None else ""
            console.print(f"  [yellow]{fam}[/] {name}.{c}: said [bold]{got}[/]{cf}, "
                          f"the project's test says [bold]{want}[/]")
    store.close()


@app.command()
def review(
    store_path: str = typer.Option("assay.duckdb", "--store"),
    limit: int = typer.Option(20, "--limit", "-n"),
    subject: str = typer.Option(None, "--subject", help="the decision key to rule on"),
    question: str = typer.Option(None, "--question"),
    verdict: str = typer.Option(None, "--verdict", help="agree | disagree | unclear"),
    correction: str = typer.Option("", "--correction", help="what it should have been"),
    note: str = typer.Option("", "--note"),
    who: str = typer.Option("", "--by"),
):
    """List judgments nobody has ruled on, or record a verdict.

    *** THIS LOOP IS WHAT MANUFACTURES THE LABELLED SET. ***
    Until a question has verdicts, config refuses to let it fail a build. There is no way to skip
    this and still gate on anything honestly.
    """
    store = Store(store_path)
    if verdict:
        if not (subject and question):
            console.print("[red]--verdict needs --subject and --question[/]")
            raise typer.Exit(1)
        row = store.con.execute(
            "select answer from model_decisions where decision_key = ? and question = ?"
            " order by decided_at desc limit 1", [subject, question]).fetchone()
        fam = question.split("__")[0]
        fam = {"role": "column_role", "null": "null_meaning",
               "key": "column_is_part_of_the_key"}.get(fam, fam)
        store.adjudicate(subject, question, fam, row[0] if row else "",
                         verdict, correction, note, who)
        acc = store.accuracy(fam)
        console.print(f"recorded. [bold]{fam}[/] now has {acc['n']} verdicts, "
                      f"{acc['agree']} agreeing.")
        store.close()
        raise typer.Exit(0)

    counts = store.adjudication_counts()
    if counts:
        t = Table(title="verdicts recorded", header_style="bold")
        t.add_column("family"); t.add_column("n", justify="right")
        t.add_column("agreement", justify="right")
        for fam, n in sorted(counts.items()):
            a = store.accuracy(fam)
            t.add_row(fam, str(n),
                      f"{100 * a['agreement']:.0f}%" if a["agreement"] is not None else "-")
        console.print(t)
    else:
        console.print("[dim]no verdicts yet. Nothing can gate a build until there are.[/]")

    rows = store.pending(limit)
    console.print(f"\n[bold]{len(rows)}[/] judgments awaiting a verdict:")
    for key, q, ans, conf, _pv in rows:
        cf = f"  conf {conf:.2f}" if conf is not None else ""
        console.print(f"  [dim]{key.split('.')[-1]}[/]  {q} = [bold]{ans}[/]{cf}")
    console.print("\n[dim]assay review --subject <key> --question <q> --verdict agree|disagree[/]")
    store.close()


@app.command()
def inventory(
    target: str = typer.Option(None, "--target", "-t"),
    model: str = typer.Option(None, "--model", "-m", help="render one model as a document"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    json_out: bool = typer.Option(False, "--json"),
    write: str = typer.Option(None, "--write",
                              help="write contracts to a SEPARATE yaml file. Never touches "
                                   "your schema.yml."),
    everything: bool = typer.Option(False, "--include-unadjudicated",
                                    help="with --write, include entries nobody has ruled on"),
    limit: int = typer.Option(40, "--limit", "-n"),
):
    """What every model in this project actually IS.

    Works with no API key: grain where code can settle it, provenance for every column, edges and
    blast radius. A judgment fills in role and sharpens grain; it is not the price of entry.
    """
    tdir = _find_target(target)
    project, digests, _failures, schema, _sstats = _load(tdir)
    store = Store(store_path) if Path(store_path).exists() else None
    observed = probe_mod.read(store) if store else {}
    entries = inv_mod.build(project, digests, schema, store, observed)

    if model:
        e = next((x for x in entries if x.name == model), None)
        if not e:
            console.print(f"[red]no model named {model}[/]")
            raise typer.Exit(1)
        console.print(f"\n[bold]{e.name}[/]  [dim]{e.path}[/]")
        console.print(f"[dim]{e.materialized} · reads {', '.join(e.reads[:6]) or 'nothing'} · "
                      f"{e.descendants} downstream, {e.marts} marts[/]\n")
        console.print(inv_mod.describe(e))
        console.print(f"\n  grain  {e.grain.render() if e.grain else '[yellow]unsettled[/]'}\n")
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("column"); t.add_column("role"); t.add_column("value comes from")
        t.add_column("null means")
        for c in e.columns[:60]:
            t.add_row(("[bold]" + c.name + "[/]") if c.in_key else c.name,
                      c.role.render() if c.role else "[dim]-[/]",
                      c.provenance.render(),
                      c.null_meaning.render() if c.null_meaning else "[dim]-[/]")
        console.print(t)
        if store:
            store.close()
        raise typer.Exit(0)

    if json_out:
        print(_json.dumps({
            "models": [{"name": e.name, "layer": e.layer, "grain": e.grain.value if e.grain else None,
                        "grain_source": e.grain.source if e.grain else None,
                        "grain_confidence": e.grain.confidence if e.grain else None,
                        "columns": [{"name": c.name, "role": c.role.value if c.role else None,
                                     "provenance": c.provenance.value,
                                     "in_key": c.in_key} for c in e.columns],
                        "descendants": e.descendants, "marts": e.marts} for e in entries],
        }, indent=2, default=str))
        raise typer.Exit(0)

    settled = sum(1 for e in entries if e.grain)
    by_source = {}
    for e in entries:
        if e.grain:
            by_source[e.grain.source] = by_source.get(e.grain.source, 0) + 1
    ncols = sum(len(e.columns) for e in entries)
    roles = sum(1 for e in entries for c in e.columns if c.role)

    console.print(f"[bold]{len(entries)}[/] models, [bold]{ncols:,}[/] columns")
    console.print(f"grain settled for [bold]{settled}[/]  "
                  + "  ".join(f"[dim]{k}[/] {v}" for k, v in sorted(by_source.items())))
    console.print(f"column roles judged for [bold]{roles:,}[/]"
                  + ("" if roles else "  [dim](run `assay columns`)[/]"))

    t = Table(title="\ninventory", header_style="bold")
    t.add_column("model"); t.add_column("layer"); t.add_column("grain")
    t.add_column("from"); t.add_column("cols", justify="right"); t.add_column("reach", justify="right")
    shown = [e for e in entries if not e.unreadable][:limit]
    for e in shown:
        g = ", ".join(e.grain.value) if e.grain and isinstance(e.grain.value, list) else (
            str(e.grain.value) if e.grain else "[yellow]unsettled[/]")
        src = e.grain.source if e.grain else "-"
        if e.grain and e.grain.confidence is not None:
            src += f" {e.grain.confidence:.2f}"
        t.add_row(e.name, e.layer, g[:46], src, str(len(e.columns)),
                  f"{e.descendants}/{e.marts}")
    console.print(t)
    if len(entries) > limit:
        console.print(f"[dim]... {len(entries) - limit} more. --limit, --json, "
                      f"or --model <name> for one in full.[/]")

    if write:
        adjudicated = set()
        if store:
            for subj, q in store.con.execute(
                    "select subject, question from adjudications").fetchall():
                adjudicated.add((subj, q))
                adjudicated.add((subj, "grain"))
        import yaml as _yaml
        data = inv_mod.to_yaml_dict(entries, not everything, adjudicated)
        Path(write).write_text(_yaml.safe_dump(data, sort_keys=False, width=100))
        console.print(f"\nwrote [bold]{write}[/] with {len(data['models'])} models. "
                      f"[dim]Separate file; your schema.yml is untouched.[/]")

    if store:
        run_id = uuid.uuid4().hex[:12]
        inv_mod.write_store(store, run_id, entries)
        store.close()


@app.command()
def export(
    directory: str = typer.Argument(..., help="where to write, e.g. transform/seeds/assay"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    fmt: str = typer.Option("seeds", "--format", help="seeds | parquet"),
    no_docs: bool = typer.Option(False, "--no-docs", help="skip the generated schema.yml"),
):
    """Put assay's tables in your warehouse, as data your own models can join to.

    Seeds work on every adapter with no external-table setup: write them, run `dbt seed`, and the
    inventory, the findings, every stored judgment and every human verdict are real relations.
    """
    if not Path(store_path).exists():
        console.print(f"[yellow]no store at {store_path}.[/] Run `assay check --store` first.")
        raise typer.Exit(1)
    store = Store(store_path)
    out = (export_mod.to_parquet(store, directory) if fmt == "parquet"
           else export_mod.to_seeds(store, directory))
    if not out:
        console.print("[yellow]nothing to export yet.[/]")
        store.close()
        raise typer.Exit(0)

    t = Table(title="exported", header_style="bold")
    t.add_column("table"); t.add_column("rows", justify="right"); t.add_column("file")
    for e in out:
        t.add_row(e.table, f"{e.rows:,}", e.path.name)
    console.print(t)

    if not no_docs and fmt == "seeds":
        p = Path(directory) / "assay.yml"
        p.write_text(export_mod.schema_yml(store, out))
        ex = Path(directory) / "example_assay_defect_classes.sql"
        ex.write_text(export_mod.EXAMPLE_SQL)
        console.print(f"wrote [bold]{p.name}[/] documenting every column, "
                      f"and an example query in {ex.name}")
    console.print(f"\n[dim]now: dbt seed --select {export_mod.PREFIX}*[/]")
    store.close()


@app.command()
def trace(
    column: str = typer.Argument(..., help="model.column, e.g. water_rights.decreed_af"),
    target: str = typer.Option(None, "--target", "-t"),
):
    """Where did this number come from?

    Follows a column back through the DAG until the first hop that actually did something to the
    value, and stops there rather than guessing further. The trail ends honestly at a source: what
    produced a column outside dbt is not knowable from a manifest.
    """
    if "." not in column:
        console.print("[red]pass model.column[/]")
        raise typer.Exit(1)
    model_name, col = column.rsplit(".", 1)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    uid = next((u for u, m in project.models.items() if m.name == model_name), None)
    if not uid:
        console.print(f"[red]no model named {model_name}[/]")
        raise typer.Exit(1)

    hops = provenance.trace(col, uid, project, digests, schema)
    if not hops:
        console.print(f"[yellow]{col} is not an output column of {model_name}[/]")
        raise typer.Exit(1)

    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("model"); t.add_column("column"); t.add_column("what happened there")
    for name, c, kind, why in hops:
        t.add_row(name, c, f"{kind}  [dim]{why}[/]")
    console.print(t)
    last = hops[-1][2]
    if last == "from_source":
        console.print("\n[dim]The trail ends at a source. What produced this value happened "
                      "outside this project, and assay will not guess at it.[/]")


@app.command("diff")
def diff_cmd(
    baseline: str = typer.Option(..., "--baseline", "-b",
                                 help="a target/ directory to compare AGAINST, e.g. a checkout "
                                      "of main"),
    target: str = typer.Option(None, "--target", "-t", help="the current target/ directory"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    markdown: bool = typer.Option(False, "--markdown", help="the paragraph, for a PR comment"),
    limit: int = typer.Option(30, "--limit", "-n"),
):
    """What changed about what your models MEAN.

    A grain change is invisible in a SQL diff -- it looks like somebody edited a GROUP BY. This
    says one row stopped being one row per (section, case), how many models consume it, and how
    many of those aggregate over it and are now inflated.
    """
    tdir = _find_target(target)
    bdir = _find_target(baseline)
    store = Store(store_path) if Path(store_path).exists() else None
    obs = probe_mod.read(store) if store else {}

    proj_a, dig_a, _f, sch_a, _s = _load(tdir)
    after = inv_mod.build(proj_a, dig_a, sch_a, store, obs)

    proj_b, dig_b, _f2, sch_b, _s2 = _load(bdir)
    before = inv_mod.build(proj_b, dig_b, sch_b, store, obs)
    if store:
        store.close()

    changes = diff_mod.compare(before, after, proj_a, dig_a)
    if not changes:
        console.print("[green]No model changed meaning.[/] "
                      "[dim]A rewrite whose contract is unchanged needs no semantic review.[/]")
        raise typer.Exit(0)

    if markdown:
        print(diff_mod.summarise(changes[:limit]))
        raise typer.Exit(0)

    grain = [c for c in changes if c.kind in ("grain", "grain_in_sql")]
    if grain:
        console.print(f"[bold red]{len(grain)} model(s) changed grain[/]\n")
        for c in grain[:limit]:
            console.print(f"[bold]{c.model}[/]  {c.detail}")
            if c.consumers:
                console.print(f"  [dim]{len(c.consumers)} consumers[/]", end="")
                if c.aggregating_consumers:
                    console.print(f"  [red]{len(c.aggregating_consumers)} aggregate over it: "
                                  f"{', '.join(c.aggregating_consumers[:4])}[/]", end="")
                console.print(f"  [dim]{c.marts} marts downstream[/]")
            console.print("  [dim]Nothing in the SQL diff says this.[/]\n")

    rest = [c for c in changes if c.kind not in ("grain", "grain_in_sql")]
    if rest:
        t = Table(title="other contract changes", header_style="bold")
        t.add_column("model"); t.add_column("what"); t.add_column("column")
        t.add_column("before"); t.add_column("after")
        for c in rest[:limit]:
            t.add_row(c.model, c.kind, c.column or "-",
                      str(c.before or "-")[:28], str(c.after or "-")[:28])
        console.print(t)
    if len(changes) > limit:
        console.print(f"[dim]... {len(changes) - limit} more[/]")


@app.command()
def backtest(
    repo: str = typer.Option(".", "--repo", "-r", help="the git repo holding your dbt models"),
    limit: int = typer.Option(60, "--limit", "-n", help="how many commits to replay"),
    fix_like_only: bool = typer.Option(False, "--fix-like-only",
                                       help="only commits whose message says fix/bug/broken. Off "
                                            "by default: on a real repo 3 of 4 commits that "
                                            "removed a known defect did not say so."),
    since: str = typer.Option(None, "--since", help="e.g. 2026-01-01"),
    show: int = typer.Option(10, "--show", help="how many replays to print"),
    compile_fallback: bool = typer.Option(
        False, "--compile",
        help="where the Jinja strip fails, really compile that commit in a detached worktree. "
             "Slower (a project parse per commit) and far more faithful."),
    project_dir: str = typer.Option(".", "--project-dir",
                                    help="the dbt project inside the repo, e.g. transform"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt-bin", help='e.g. "uv run dbt"'),
):
    """Replay this repo's own history and measure whether the checks catch what it already fixed.

    A commit whose message says it fixed something is a defect and its repair, already labelled by
    whoever wrote it. Did the check fire before the fix and go quiet after?

    Reads blobs out of the object store with `git show`. No checkout, no stash, nothing that could
    collide with other work in the same clone.
    """
    compiler = None
    if compile_fallback:
        compiler = backtest_mod.Compiler(repo, project_dir, dbt_bin, profiles_dir)
        if not compiler.ok:
            console.print(f"[red]could not create a worktree: {compiler.reason}[/]")
            raise typer.Exit(1)
        console.print("[dim]compiling where the strip fails, in a detached worktree. "
                      "Your working tree is untouched.[/]")
    try:
        with console.status("replaying...") as st:
            replays = backtest_mod.run(
                repo, limit, since, fix_like_only, compiler,
                on_commit=lambda i, n, subj: st.update(f"replaying {i + 1}/{n}  {subj[:60]}"))
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None

    if not replays:
        console.print("[yellow]no commits touching model SQL were found.[/] "
                      "[dim]Try --since, or a larger --limit.[/]")
        raise typer.Exit(0)

    if compiler:
        compiler.close()
    counts = backtest_mod.tally(replays)
    rate = counts.pop("_silenced_rate", None)
    had = counts.pop("_had_something", 0)
    t = Table(title="replayed history", header_style="bold")
    t.add_column("outcome"); t.add_column("n", justify="right"); t.add_column("means")
    MEANS = {
        "caught": "fired before, quiet after: the check works",
        "introduced": "quiet before, fires after: this commit added something",
        "still_firing": "fires at both: the commit did not address what assay sees",
        "silent": "assay saw nothing either side",
        "no_pair": "the model was added or removed here, so there is nothing to compare",
        "unparseable": "a blob assay could not read; NOT counted as clean",
    }
    counts.pop("skipped", None)
    for k in ("caught", "introduced", "still_firing", "silent", "no_pair", "unparseable"):
        if counts.get(k):
            t.add_row(k, str(counts[k]), MEANS[k])
    console.print(t)
    n_compiled = sum(1 for r in replays if r.via == "compiled")
    if n_compiled:
        console.print(f"[dim]{n_compiled} replay(s) were recovered by a real compile.[/]")
    if rate is not None:
        console.print(f"[dim]a check was firing in {had} replay(s); a later commit silenced it in "
                      f"{rate:.0%} of them. The denominator is deliberately not every commit: most "
                      f"touch models that never had the defect.[/]")

    caught = [r for r in replays if r.verdict == "caught"]
    for r in caught[:show]:
        label = "" if r.message_says_fix else "  [dim](the message never says 'fix')[/]"
        console.print(f"\n[green]caught[/] [bold]{r.model}[/]  [dim]{r.sha}[/]{label}")
        console.print(f"  [dim]{r.subject}[/]")
        console.print(f"  fired before, quiet after: {', '.join(r.checks_that_caught)}")

    unread = [r for r in replays if r.verdict == "unparseable"]
    if unread:
        pairs = [r for r in replays if r.verdict != "no_pair"]
        console.print(f"\n[yellow]{len(unread)} of {len(pairs)} comparable replays could not be "
                      f"read[/] [dim]({len(unread) / max(len(pairs), 1):.0%}), and are not counted "
                      f"as clean. A Jinja strip is not a compile.[/]")


@app.command()
def watch(
    target: str = typer.Option(None, "--target", "-t"),
    project_dir: str = typer.Option(".", "--project-dir", help="the dbt project to compile in"),
    compile_on_save: bool = typer.Option(False, "--compile",
                                         help="run `dbt compile --select <changed>+` on a save"),
    dbt_bin: str = typer.Option("dbt", "--dbt-bin", help='e.g. "uv run dbt"'),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    interval: float = typer.Option(1.0, "--interval", help="seconds between checks"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
):
    """Stay quiet until something in your working tree MEANS something different.

    The baseline is a snapshot taken at start, not your previous save, so breaking something and
    fixing it produces no alarm at all -- which is correct, because nothing ended up different.
    A file that does not parse is "still typing", never a finding.
    """
    import subprocess
    import time as _time

    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    obs = probe_mod.read(store) if store else {}

    state = live_mod.read(tdir, store, obs)
    baseline = live_mod.Snapshot.of(state.entries)
    console.print(f"[bold]watching[/] {len(baseline.entries)} models. "
                  f"[dim]baseline taken now; ctrl-c to stop.[/]")

    seen = live_mod.sql_files(tdir, project_dir)
    try:
        while True:
            _time.sleep(interval)
            now = live_mod.sql_files(tdir, project_dir)
            moved = [p for p, m in now.items() if seen.get(p) != m]
            if not moved:
                continue
            seen = now
            names = sorted({Path(p).stem for p in moved})
            if compile_on_save:
                sel = " ".join(f"{n}+" for n in names[:8])
                cmd = [*dbt_bin.split(), "compile", "--select", *sel.split()]
                if profiles_dir:
                    cmd += ["--profiles-dir", profiles_dir]
                subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, check=False)

            state = live_mod.read(tdir, store, obs)
            if state.unparsed:
                console.print(f"[dim]still typing: {', '.join(state.unparsed[:4])}[/]")
                continue
            changes = live_mod.changes_since(baseline, state)
            if not changes:
                console.print(f"[dim]{', '.join(names[:4])} saved · nothing means anything "
                              f"different[/]")
                continue
            console.print()
            for c in changes[:8]:
                colour = "red" if c.severity >= 3 else "yellow"
                console.print(f"[{colour}]{c.kind}[/] [bold]{c.model}[/]  {c.detail or ''}")
                if c.aggregating_consumers:
                    console.print(f"  [red]{len(c.aggregating_consumers)} downstream models "
                                  f"aggregate over it: "
                                  f"{', '.join(c.aggregating_consumers[:4])}[/]")
                elif c.consumers:
                    console.print(f"  [dim]{len(c.consumers)} consumers, {c.marts} marts[/]")
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/]")
    finally:
        if store:
            store.close()


@app.command()
def mcp(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
):
    """Serve assay as tools an agent can call instead of reading your SQL.

    contract, lineage, blast_radius, findings, changed_contracts, rebase. A contract is fifteen
    lines where the SQL is two hundred, so an agent can hold a whole project's meaning in about
    what reading four models costs it today.
    """
    tdir = _find_target(target)
    try:
        mcp_server.serve(str(tdir), store_path if Path(store_path).exists() else None)
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None


@app.command("version-check")
def version_check(
    baseline: str = typer.Option(..., "--baseline", "-b",
                                 help="a target/ directory to compare against, e.g. main"),
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    bump: bool = typer.Option(False, "--bump", help="print the exact edit for each model that "
                                                    "owes one"),
    write: bool = typer.Option(False, "--write",
                               help="with --bump, apply it. A targeted text edit; your comments "
                                    "are not round-tripped through a YAML dumper."),
    project_root: str = typer.Option(".", "--project-root",
                                     help="where the patch paths in the manifest are relative to"),
):
    """A version bump is owed when the MEANING changed, and never when it did not.

    A reformat, a renamed CTE or a join rewritten as a subquery owes nothing. That is the whole
    reason this rule is bearable: every other version-bump check fires on whitespace and gets
    switched off within a fortnight.
    """
    tdir, bdir = _find_target(target), _find_target(baseline)
    store = Store(store_path) if Path(store_path).exists() else None
    obs = probe_mod.read(store) if store else {}

    pa, da, _f, sa, _s = _load(tdir)
    after = inv_mod.build(pa, da, sa, store, obs)
    pb, db, _f2, sb, _s2 = _load(bdir)
    before = inv_mod.build(pb, db, sb, store, obs)
    if store:
        store.close()

    changes = diff_mod.compare(before, after, pa, da)
    states = ver_mod.assess(changes, pb, pa)
    owing = [s for s in states if s.owes]

    if not states:
        console.print("[green]nothing changed meaning, so nothing owes a bump.[/]")
        raise typer.Exit(0)

    t = Table(title="version", header_style="bold")
    t.add_column("model"); t.add_column("needs"); t.add_column("version")
    t.add_column("status"); t.add_column("why")
    for s in states:
        v = f"{s.before if s.before is not None else '-'} -> {s.after if s.after is not None else '-'}"
        status = "[green]bumped[/]" if s.bumped else "[red]owes a bump[/]"
        t.add_row(s.model, s.required, v, status, (s.reasons[0] if s.reasons else "")[:54])
    console.print(t)

    if bump and owing:
        console.print()
        for s in owing:
            if write and s.patch_path:
                p = str(Path(project_root) / s.patch_path)
                ok = ver_mod.write_bump(p, s.model, s.suggested)
                console.print(f"{'[green]wrote[/]' if ok else '[yellow]could not locate[/]'} "
                              f"{s.model} version {s.suggested} in {s.patch_path}")
            else:
                console.print(f"[bold]{s.model}[/]\n{ver_mod.patch_text(s)}\n")

    if owing:
        console.print(f"\n[red]{len(owing)} model(s) owe a version bump.[/]")
        raise typer.Exit(1)
    console.print("\n[green]every model whose meaning changed was bumped.[/]")


@app.command("version-stamps")
def version_stamps(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    recommend: bool = typer.Option(False, "--recommend",
                                   help="also list marts carrying no version stamp"),
):
    """Does each row say which version of the logic produced it?

    A constant column named like a version is a stamp. A model that declares itself v3 and stamps
    its rows with 2 makes every row produced since the bump untraceable, which is the one job the
    column had.
    """
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    store = Store(store_path) if Path(store_path).exists() else None
    entries = inv_mod.build(project, digests, schema, store, probe_mod.read(store) if store else {})
    if store:
        store.close()

    drift = ver_mod.column_drift(project, entries, digests)
    if drift:
        t = Table(title="version stamp drift", header_style="bold")
        t.add_column("model"); t.add_column("column"); t.add_column("stamps")
        t.add_column("declares")
        for name, col, got, want in drift:
            t.add_row(name, col, str(got), str(want))
        console.print(t)
    else:
        console.print("[green]no version stamp disagrees with its declaration.[/]")

    if recommend:
        missing = ver_mod.unstamped_marts(project, entries, digests)
        if missing:
            console.print(f"\n[dim]{len(missing)} mart(s) carry no version stamp. A constant "
                          f"column lets you tell later which logic produced a row:[/]")
            for n in missing[:12]:
                console.print(f"  [dim]{n}[/]")
    if drift:
        raise typer.Exit(1)
