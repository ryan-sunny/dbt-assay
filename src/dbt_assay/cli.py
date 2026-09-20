"""`assay` -- the command. The package is dbt-assay so it is findable; the command is short to type."""
from __future__ import annotations

import json as _json
import time
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, contracts, relate
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
    store_path: str = typer.Option(None, "--store", help="persist to a DuckDB file"),
    limit: int = typer.Option(25, "--limit", "-n", help="how many findings to print"),
    check_name: str = typer.Option(None, "--check", help="only this check"),
):
    """Run the structural checks. No network, no API key, no spend."""
    tdir = _find_target(target)
    project, digests, failures, schema, sstats = _load(tdir)
    facts, edge_findings = relate.run_all(project, digests, schema)
    findings = run_all(project, digests) + edge_findings
    findings.sort(key=lambda f: -f.weight)
    if check_name:
        findings = [f for f in findings if f.check == check_name]

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
            console.print(f"[bold]{f.subject_name}[/]  [dim]{f.file}[/]")
            console.print(f"  {f.check}: {f.summary}  [dim]({reach})[/]")
            console.print(f"  [dim]{f.detail}[/]\n")
        if len(findings) > limit:
            console.print(f"[dim]... {len(findings) - limit} more. --limit to see them, "
                          f"--json for all.[/]")

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


def _grain_setup(target: str | None):
    tdir = _find_target(target)
    project, digests, _fail, schema, _sstats = _load(tdir)
    declared = relate.declared_keys(project)
    proposed = contracts.propose_all(project, digests, schema, declared)
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
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target)

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
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target)
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
    exact = over = under = wrong = 0
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
        verdict = ("exact" if got == want else
                   "superset" if want < got else
                   "subset" if got < want else "wrong")
        if verdict == "exact":
            exact += 1
        elif verdict == "superset":
            over += 1
        elif verdict == "subset":
            under += 1
        else:
            wrong += 1
        rows.append((project.models[uid].name, verdict, sorted(got), sorted(want)))

    n = max(exact + over + under + wrong, 1)
    t = Table(title="\ngrain judgment vs the project's own declared keys", header_style="bold")
    t.add_column("outcome"); t.add_column("n", justify="right"); t.add_column("%", justify="right")
    for label, v in (("exact", exact), ("kept too many", over),
                     ("dropped too many", under), ("disagrees", wrong)):
        t.add_row(label, str(v), f"{100 * v // n}%")
    console.print(t)
    console.print(f"[dim]code alone was exact on {code_exact}/{len(work)}; "
                  f"with judgment {exact}/{n}[/]")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    for name, v, got, want in [r for r in rows if r[1] != "exact"][:10]:
        console.print(f"  [yellow]{v}[/] {name}: got {got}  declared {want}")
    store.close()
