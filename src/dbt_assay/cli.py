"""`assay` -- the command. The package is dbt-assay so it is findable; the command is short to type."""
from __future__ import annotations

import json as _json
import time
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, relate
from .checks import run_all
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
    return project, digests, failures


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


@app.command()
def scan(target: str = typer.Option(None, "--target", "-t", help="path to dbt target/ directory")):
    """Read the project and report what can and cannot be audited."""
    tdir = _find_target(target)
    t0 = time.time()
    project, digests, failures = _load(tdir)
    _coverage_panel(project, digests, failures)

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
    project, digests, failures = _load(tdir)
    facts, edge_findings = relate.run_all(project, digests)
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
