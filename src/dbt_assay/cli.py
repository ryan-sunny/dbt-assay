"""`assay` -- the command. The package is dbt-assay so it is findable; the command is short to type."""
from __future__ import annotations

import json as _json
import time
import uuid
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, contracts, live, mcp_server, provenance, relate
from . import align as align_mod
from . import backtest as backtest_mod
from . import claims as claims_mod
from . import columns as columns_mod
from . import diff as diff_mod
from . import export as export_mod
from . import feeds as feeds_mod
from . import inventory as inv_mod
from . import judged as judged_mod
from . import live as live_mod
from . import patch as patch_mod
from . import practices as prac_mod
from . import probe as probe_mod
from . import render as render_mod
from . import rows as rows_mod
from . import semantics as sem_mod
from . import subjects as subjects_mod
from . import testing as testing_mod
from . import versioning as ver_mod
from .checks import run_all, unevaluable_tests
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


def _load(target: Path, dialect: str | None = None):
    project = Project.load(target)
    # An explicit --dialect always wins; otherwise the project says what it speaks. Recording it on
    # the project means every downstream default follows, rather than each call site remembering.
    project.dialect_override = dialect or ""
    dialect = project.dialect
    digests, failures = {}, []
    for uid, m in project.models.items():
        if not m.readable:
            continue
        d = digest(m.compiled, m.name, dialect)
        digests[uid] = d
        if not d.ok:
            failures.append((uid, m.name, m.path, d.error))
    # Columns are derived parents-first so a `select *` can be expanded with what the parents were
    # found to offer. Without this a starred model reports zero columns, which reads exactly like a
    # model that genuinely offers none.
    schema = Schema.load(project, target)
    schema_stats = derive_columns(project, digests, schema)
    return project, digests, failures, schema, schema_stats


def _review_coverage(findings, store_path: str) -> None:
    """How much of this has ever been looked at by a person.

    *** THE ONE NUMBER A RELEASE CANNOT IMPROVE, WHICH IS WHY IT BELONGS ON EVERY RUN. ***
    Every other figure assay prints responds to a release: a better check finds more, a better
    state raises a confidence, the DAG moves the blast radius. This moves only when somebody reads
    SQL. It is the only honest measure of whether a warehouse is being UNDERSTOOD rather than
    scanned -- and a good release makes it look WORSE, because finding more raises the denominator
    and no release raises the numerator.
    """
    if not findings or not Path(store_path or "").exists():
        return
    try:
        st = Store(store_path)
        ruled = st.ruled_subjects()
        agent = st.agent_rulings()
        st.close()
    except Exception:                                                   # noqa: BLE001
        return
    models = {f.subject for f in findings}
    seen = {m for m in models if m in ruled}
    pct = len(seen) / len(models) if models else 0
    by_agent = {a["subject"].split("::")[0] for a in agent} - seen
    colour = "green" if pct >= 0.5 else ("yellow" if seen else "red")
    console.print(f"\n[{colour}]{len(seen)} of {len(models)} model(s) with a finding have been "
                  f"ruled on by a person.[/]")
    if by_agent:
        console.print(f"[cyan]{len(by_agent)} more have an agent's reading[/] [dim]-- which is a "
                      f"place to start, not a substitute. `assay review -i` shows it beside the "
                      f"finding.[/]")
    if pct < 1:
        console.print("[dim]This is the only number here a release cannot improve. Every other "
                      "one moves when assay gets better, and an agent cannot raise it either; "
                      "this moves when you read SQL.[/]")


def _coverage_panel(project, digests, failures, show_errors: bool = True) -> None:
    cov = project.coverage()
    ok = sum(1 for d in digests.values() if d.ok)
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("project", f"[bold]{project.project_name}[/]  (dbt {project.dbt_version}, "
                         f"{project.adapter_type or 'adapter unknown'} "
                         f"\u2192 {project.dialect})")
    t.add_row("models", f"{cov['models']}   sources {cov['sources']}   tests {cov['tests']}   edges {cov['edges']}")
    # *** "1615 read (0 from disk, 0 from manifest)" IS THREE NUMBERS THAT DO NOT ADD UP. ***
    # A stranger cannot reconcile it, and the missing term is the one that matters: those 1615
    # came from STRIPPED Jinja, which is not a compile. Every source is named, always.
    srcs = [f"{cov['from_disk']} compiled, from disk" if cov["from_disk"] else "",
            f"{cov['from_manifest']} compiled, from the manifest" if cov["from_manifest"] else "",
            f"[yellow]{cov['from_stripped']} stripped, NOT compiled[/]"
            if cov.get("from_stripped") else ""]
    t.add_row("SQL assay read", f"{cov['readable']} of {cov['models']} model(s)  ("
                                + ", ".join(x for x in srcs if x) + ")")
    if cov.get("from_stripped"):
        t.add_row("", "[yellow]Stripped means the Jinja was removed and the rest parsed. It is "
                      "not a compile, and it is why some answers below are thinner.[/]")
    if cov.get("conflicting_copies"):
        t.add_row("[yellow]ambiguous[/]",
                  f"[yellow]{cov['conflicting_copies']} models have a DIFFERENT compiled body in "
                  f"another target dir. The canonical copy was audited.[/]")
    if cov["unreadable"]:
        t.add_row("[yellow]not read at all[/]",
                  f"[yellow]{cov['unreadable']} model(s) have no SQL assay could reach, so they "
                  f"are absent from everything below.[/]")
    t.add_row("parsed", f"{ok}/{len(digests)}" + (f"   [yellow]{len(failures)} failed[/]" if failures else ""))
    console.print(t)
    if not show_errors:
        # A first run should not open with five screens of someone else's SQL. The COUNT is the
        # signal; the bodies are a second command away.
        if failures:
            console.print(f"   [dim]`assay scan` prints why each of the {len(failures)} failed.[/]")
        return
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
def scan(
    target: str = typer.Option(None, "--target", "-t", help="path to dbt target/ directory"),
    dialect: str = typer.Option(None, "--dialect",
                                help="override the dialect; by default it is read from the "
                                     "manifest's own adapter_type"),
):
    """Read the project and report what can and cannot be audited."""
    tdir = _find_target(target)
    t0 = time.time()
    project, digests, failures, schema, sstats = _load(tdir, dialect)
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
    dialect: str = typer.Option(None, "--dialect",
                                help="override; read from the manifest by default"),
    verify: bool = typer.Option(False, "--verify",
                                help="count each flagged hop's join key through your own dbt. "
                                     "A join onto a key that is unique IN THE DATA cannot fan "
                                     "out, and dbt only knows which keys are DECLARED unique."),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin"),
):
    """Run the structural checks. No network, no API key, no spend."""
    tdir = _find_target(target)
    project, digests, failures, schema, sstats = _load(tdir, dialect)
    facts, _edge_findings = relate.run_all(project, digests, schema)
    findings: list = []
    # *** ONE STREAM. ***
    # Structural and judged findings were in separate worlds: `check` saw only the parser's, and
    # nothing from `infer` or `columns` ever reached the store. "What is wrong with this model"
    # needs a single answer.
    n_retired = 0
    # `_entries` shadows the module-level helper of the same name inside this function, and a
    # project with no store never enters the branch below. None means "no judged stream", which
    # is correct rather than a truncation.
    _entries = None
    if Path(store_path or "").exists():
        _s = Store(store_path)
        _obs = probe_mod.read(_s)
        _entries = inv_mod.build(project, digests, schema, _s, _obs, facts=facts)
        if verify:
            # *** THE COUNT IS THE ONLY THING THAT SETTLES THIS, AND IT IS ONE STATEMENT. ***
            # Two of twelve disagreements on a hand-ruled warehouse were a join onto a lookup
            # that IS unique and is not declared to be. Same batched arithmetic `practices`
            # already uses, pointed at the parent of a flagged hop.
            # *** COUNT WHAT DISAPPEARED, NOT WHAT MATCHED. ***
            # The first version reported parents marked unique and called them hops retired: 9
            # against 2 findings actually removed, because a parent can match a hop the union
            # rule already refused. A number that reads like a result has to be one.
            _before = len(judged_mod.hop_multiplies_rows(project, _entries))
            prac_mod.verify_join_keys(_entries, project, probe_mod, project_dir,
                                      profiles_dir, dbt_bin, schema=schema)
            n_retired = _before - len(judged_mod.hop_multiplies_rows(project, _entries))
            n_counted = prac_mod.verify_row_loss(_entries, project, probe_mod, project_dir,
                                                 profiles_dir, dbt_bin, schema=schema)
            if n_counted and not json_out:
                console.print(f"[dim]--verify: counted {n_counted} hop(s) for row loss. A hop "
                              f"that declares a filter, a group by or a union is not counted, "
                              f"because dropping rows there is the point.[/]")
            if n_retired and not json_out:
                # *** `--json` IS MACHINE-READABLE AND ONE LINE OF PROSE ENDS THAT. ***
                # This printed before the document and every parser downstream got
                # `Expecting value: line 1 column 1`. The same class as rich eating `[mcp]` out
                # of the instruction telling somebody to install it.
                console.print(f"[dim]--verify: {n_retired} hop(s) retired -- the join key is "
                              f"unique in the data, so the hop cannot fan out. Nothing in the "
                              f"project declared it.[/]")
        _s.close()
    # *** ONE PATH. *** `check` used to add the judged stream itself while `findings_for` did not,
    # so the CLI saw seven families and MCP saw two. Both call this.
    _cfg_pre = Config.load(config_path)
    findings = live.all_findings(project, digests, schema, _entries,
                                 _cfg_pre.row_loss_threshold)
    _verified = {"hops_retired_by_counting": n_retired} if verify else {}
    if check_name:
        findings = [f for f in findings if f.check == check_name]

    # *** audit.yml NOW DOES SOMETHING. ***
    # Thresholds, plain actions, waivers and scoping are applied here to BOTH streams. A config
    # that parses but is never consulted implies a control that does not exist, which is worse
    # than having none.
    cfg = Config.load(config_path)
    _warn_unknown_questions(cfg)
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
            "unevaluable_tests": [{"model": m, "test": t, "column": c, "why": w}
                                  for m, t, c, w in unevaluable_tests(project, digests)],
            **({"verified": _verified} if _verified else {}),
            # *** `--json` AND THE MCP TOOL ARE ONE DOCUMENT WITH TWO SPELLINGS OTHERWISE. ***
            # MCP returns `finding`; this did not, so anything reading the CLI's JSON could see a
            # finding and had no handle to rule on it.
            "findings": [{"finding": f.id,
                          "check": f.check, "model": f.subject_name, "file": f.file,
                          "summary": f.summary, "detail": f.detail, "weight": round(f.weight, 2),
                          "descendants": f.descendants, "marts": f.marts, "evidence": f.evidence}
                         for f in findings],
        }, indent=2))
        raise typer.Exit(0)

    _coverage_panel(project, digests, failures)
    _schema_panel(schema, sstats)

    blind = unevaluable_tests(project, digests)
    if blind:
        console.print(f"\n[yellow]{len(blind)} test(s) could not be evaluated[/] "
                      f"[dim]and are NOT a pass. Most common reason: "
                      f"{Counter(w for _m, _t, _c, w in blind).most_common(1)[0][0]}[/]")

    if not findings:
        console.print("\n[green]no structural findings[/]"
                      + ("  [dim](but see above: some tests could not be looked at)[/]"
                         if blind else ""))
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
        _review_coverage(findings, store_path)
    console.print(f"\n[dim]run {run_id} written to {store_path}[/]")

    if failing:
        raise typer.Exit(1)


def _project_dir_for(target: Path) -> Path | None:
    """The dbt project a target dir belongs to: the nearest parent holding dbt_project.yml.

    Walked rather than assumed, because `target/` is conventionally a sibling of the project file
    but a manifest copied somewhere for inspection has no project at all, and running dbt in the
    wrong directory is worse than not running it.
    """
    for d in [target, *target.resolve().parents][:5]:
        if (d / "dbt_project.yml").exists():
            return d
    return None


def _wrapper_hint(project_dir: Path) -> str:
    """The `uv run dbt` suggestion, found by walking UP to the repo root.

    *** IT LOOKED ONLY BESIDE `dbt_project.yml`, AND THAT IS THE WRONG DIRECTORY. ***
    Reported from the field on a repo whose layout is the normal one for a project that is not
    ONLY dbt:

        dbt_project.yml   ./transform/dbt_project.yml
        uv.lock           ./uv.lock            <- repo root, one level up

    So `onboard --compile` said "pass --dbt with the command you use" and named nothing, which is
    the wasted run the hint exists to prevent. `transform/`, `dbt/` and `warehouse/` are all common
    and the lockfile belongs to the repo, not to the dbt project inside it.

    It stops at the repo root rather than walking to `/`: a lockfile above the repo is somebody
    else's project, and suggesting its wrapper is worse than suggesting nothing.
    """
    here = Path(project_dir).resolve()
    for d in (here, *list(here.parents)[:8]):
        for lock, wrapper in (("uv.lock", "uv run dbt"), ("poetry.lock", "poetry run dbt"),
                              ("Pipfile.lock", "pipenv run dbt")):
            if (d / lock).exists():
                where = "" if d == here else f" ({d / lock}, above the dbt project)"
                return (f"  This looks like a {lock.split('.')[0]} project{where}: "
                        f'try --dbt "{wrapper}".')
        if (d / ".git").exists():
            break
    return ""


def _run_dbt_compile(target: Path, dbt_bin: str = "dbt", profiles_dir: str | None = None,
                     timeout: int = 900) -> tuple[bool, str]:
    """*** NEVER SILENTLY. ***

    `dbt compile` needs a warehouse connection on most adapters and can take minutes on a large
    project. Running it because assay felt like it, on someone else's first invocation, is how a
    tool gets uninstalled. It happens when asked for and the caller says what it bought.
    """
    import subprocess
    pd = _project_dir_for(target)
    if pd is None:
        return False, ("no dbt_project.yml above this target, so there is no project to compile. "
                       "Run `dbt compile` yourself in the project this manifest came from.")
    # *** dbt compile OVERWRITES run_results.json, AND THAT FILE HOLDS WHICH TESTS FAILED. ***
    # Reported from the field: after `assay onboard --compile`, run_results held one result and
    # nothing about test status, so anything downstream reading it saw a clean project. assay
    # caused a check to stop seeing and report a pass -- the exact defect this tool exists to
    # find. It is copied aside first and the caller is told where, because silently restoring it
    # would be its own lie: run_results is supposed to describe the LAST dbt invocation.
    rr = target / "run_results.json"
    kept = None
    if rr.exists():
        kept = target / "run_results.before-assay-compile.json"
        try:
            kept.write_bytes(rr.read_bytes())
        except OSError:
            kept = None

    cmd = [*dbt_bin.split(), "compile"]
    if profiles_dir:
        cmd += ["--profiles-dir", profiles_dir]
    try:
        r = subprocess.run(cmd, cwd=pd, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except FileNotFoundError:
        # *** A uv OR poetry PROJECT HAS NO BARE `dbt` ON PATH, AND THAT IS THE COMMON CASE. ***
        # Reported from the field: the compile failed with one line, onboard printed the rest of a
        # successful-looking run, and the models stayed unreadable. Guessing the wrapper from the
        # lockfile beside dbt_project.yml removes a whole wasted run.
        hint = _wrapper_hint(pd)
        return False, f"{dbt_bin!r} is not on PATH.{hint or ' Pass --dbt with the command you use.'}"
    except subprocess.TimeoutExpired:
        return False, f"`dbt compile` did not finish within {timeout}s."
    if r.returncode != 0:
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
        return False, "dbt compile failed: " + " / ".join(t.strip() for t in tail)
    where = str(pd)
    if kept is not None:
        where += (f"  [run_results.json now describes the compile, not your last test run. "
                  f"The previous one is at {kept.name}.]")
    return True, where


def _onboard_judge(project, digests, schema, findings, config_path: str, store_path: str,
                   judge: bool, judge_limit: int) -> bool | None:
    """The judged tier, on a first run, bounded.

    *** LEADS WITH THE DESCRIPTION FAMILY, AND ONLY THAT. ***
    It is one call per model, so the cost is legible and the latency is linear. It needs no probe,
    no catalog and no prior verdicts, so it works on a project assay has never seen. It does
    open a store, to cache what it asks. And
    its finding reads as English to someone who has never used this tool: your prose says one thing
    and your SQL does another. The column, predicate and grain families are all worth running and
    none of them opens as well, which is what `next` is for.

    *** WHICH MODELS: THE DOCUMENTED ONES, NEAREST THE MARTS. ***
    A model with no description cannot contradict one, so it is not a candidate at all. Among those
    that have prose, the ones with the most downstream are where a false description does the most
    damage, so a bounded pass spends its calls there rather than alphabetically.
    """
    console.print("\n[bold]4. what only judgment can see[/]")
    cfg = Config.load(config_path)

    store = Store(store_path) if Path(store_path).exists() else None
    entries = inv_mod.build(project, digests, schema, store,
                            probe_mod.read(store) if store else {})
    subs = [x for x in sem_mod.subjects(project, digests, schema, entries) if x.purpose]
    if store:
        store.close()

    if not subs:
        console.print("   [dim]no model in this project carries a description of its own, so "
                      "there is nothing for the description family to contradict. `assay columns` "
                      "and `assay semantics --families predicates` need no prose.[/]")
        return False

    subs.sort(key=lambda x: -len(project.descendants(x.uid)))
    picked, capped = subs[:judge_limit], len(subs) > judge_limit

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not judge or not client.available:
        # *** DO NOT TELL SOMEONE TO EXPORT A KEY THEY ALREADY HAVE. ***
        # `--no-judge` with a key present is a choice, not a missing capability, and saying
        # otherwise is the same defect this tool exists to find.
        has_key = client.available
        why = ("--no-judge was passed" if not judge else
               "no API key. Set TYPESAFE_API_KEY or OPENROUTER_API_KEY")
        console.print(f"   [yellow]not run:[/] {why}.")
        console.print(f"   [dim]{len(subs)} documented model(s) are waiting for it. This is the "
                      f"family a parser cannot do: it reads the description against the code and "
                      f"says whether they still agree.[/]")
        # *** SHOW THE QUESTION AND THE PRICE, NOT A WALL OF SOMEONE ELSE'S PROSE. ***
        # Someone deciding whether the tier is worth a key wants two things: what would be asked
        # about THEIR model, and what it costs. A 400-character dump of a description they already
        # wrote is neither.
        st = sem_mod.description_state(picked[0], cfg.vocab)
        from .contracts import QUESTIONS
        from .jev import USD_PER_INPUT_TOKEN
        est = (len(_json.dumps(st, default=str)) / 4 * USD_PER_INPUT_TOKEN) * len(picked)
        q = QUESTIONS["description_contradicts_the_code"]["instructions"]["question"]
        console.print(f'   [dim]the question: "{" ".join(q.split())[:150]}"[/]')
        per = est / max(len(picked), 1)
        console.print(f"   [dim]asked once per model. A first run covers {len(picked)} of them "
                      f"(--judge-limit) for about [bold]${est:.3f}[/bold]; all {len(subs)} would "
                      f"be about [bold]${per * len(subs):.2f}[/bold].[/]")
        console.print("   [dim]`assay onboard --judge-limit 20` tries twenty first.[/]")
        return None if has_key else False

    stale, asked = [], 0
    with console.status(f"judging {len(picked)} description(s)..."):
        st_store = Store(store_path)
        for sub in picked:
            st = sem_mod.description_state(sub, cfg.vocab)
            if not st:
                continue
            try:
                ans = decide(st_store, client, st, sem_mod.description_question(),
                             decision_key=f"{sub.uid}::desc",
                             prompt_version=sem_mod.DESC_VERSION, caller="assay.onboard")
            except BudgetExceeded as e:
                console.print(f"   [yellow]stopped at the spend cap: {e}[/]")
                break
            asked += 1
            a = ans.get("desc")
            if a and float(a["answer"]) >= 0.6:
                stale.append((sub.name, float(a["answer"]), len(project.descendants(sub.uid))))
        st_store.close()

    console.print(f"   [dim]{asked} description(s) judged"
                  + (f" of {len(subs)} (--judge-limit {judge_limit})" if capped else "")
                  + f" · {client.calls} calls, {client.input_tokens:,} tokens, "
                    f"${client.spent_usd:.4f}[/]")

    if not stale:
        console.print("   [green]every description judged still describes its code.[/]")
        return True
    console.print(f"   [bold]{len(stale)}[/] description(s) no longer describe their code:")
    t = Table(show_header=False, box=None, padding=(0, 2))
    for name, p_, desc in sorted(stale, key=lambda x: -x[1])[:10]:
        t.add_row(f"[bold]{name}[/]", f"[dim]{p_:.2f}[/]",
                  f"[dim]{desc} model(s) downstream[/]" if desc else "")
    console.print(t)
    # `assay trace` takes a COLUMN, not a model. Printing it here was a pointer at a command that
    # does something else, which is the same defect this family exists to find.
    console.print("   [dim]`assay semantics --families descriptions` re-reads any of them in full, "
                  "and `assay review -i` records whether you agree. Nothing here gates "
                  "anything.[/]")
    return True


@app.command()
def onboard(
    target: str = typer.Option(None, "--target", "-t", help="a dbt target dir, or a project root"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config", help="where audit.yml should live"),
    agent: bool = typer.Option(False, "--agent",
                               help="also write the agent skill file and print the MCP config"),
    compile_first: bool = typer.Option(False, "--compile",
                                       help="run `dbt compile` first when models have no "
                                            "compiled SQL. Needs your warehouse connection and "
                                            "can take minutes, so it is never automatic."),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin", help="the dbt command, e.g. 'uv run dbt'"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    judge: bool = typer.Option(True, "--judge/--no-judge",
                               help="run the judgment tier when a key is present"),
    judge_limit: int = typer.Option(120, "--judge-limit",
                                    help="how many models the first judged pass covers"),
    dialect: str = typer.Option(None, "--dialect",
                                help="override; read from the manifest by default"),
):
    """One command for a project assay has never seen. Reads, reports, writes nothing that gates.

    *** THE POINT IS THE HONEST PART, NOT THE WELCOME. ***
    A first run on someone else\'s warehouse is where assay is most likely to be quietly wrong: no
    compiled SQL, no catalog, a dialect it guessed. Every one of those degrades the answers without
    changing how confident the output looks, so onboard says which of them is true here BEFORE it
    shows a single finding, and it prints the command that fixes each one.

    *** AND IT JUDGES, BECAUSE THE STRUCTURAL TIER IS THE PART ANYONE COULD WRITE. ***
    A first run that only ran the parser sells assay as a linter. The description family is what it
    leads with: one call per model, nothing else in a warehouse can answer it, and the finding is
    legible without any assay vocabulary. The first one it found on the author\'s own warehouse was
    a staging model whose description claimed residential permits were filtered out. 14,150 rows
    survived that filter; 373 were non-residential and 157 were explicitly multifamily. Valid SQL,
    passing tests, false prose, and a lead product selling residential roofing jobs as commercial.
    """
    tdir = _find_target(target)
    project, digests, failures, schema, sstats = _load(tdir, dialect)
    cov = project.coverage()

    # *** COMPILED SQL IS THE SINGLE BIGGEST THING HOLDING assay BACK ON MOST PROJECTS. ***
    # Every check reads the compiled body. Without it a model is either skipped or read from
    # stripped Jinja, which is not a compile and says so. Offered, never assumed.
    missing = cov["unreadable"] + cov.get("from_stripped", 0)
    if compile_first and missing:
        console.print(f"[dim]compiling: {missing} model(s) have no compiled SQL...[/]")
        ok, why = _run_dbt_compile(tdir, dbt_bin, profiles_dir)
        if not ok:
            # *** LOUDER THAN ONE LINE ABOVE A SUCCESS SUMMARY. ***
            # It printed quietly, the run completed, and the models stayed unreadable while
            # everything below looked like it had worked.
            console.print(f"\n[bold red]  the compile did NOT run:[/] {why}")
            console.print(f"   [yellow]{missing} model(s) are still without compiled SQL, so "
                          f"everything below is the thinner answer.[/]\n")
        else:
            before = missing
            project, digests, failures, schema, sstats = _load(tdir, dialect)
            cov = project.coverage()
            now = cov["unreadable"] + cov.get("from_stripped", 0)
            console.print(f"   [green]compiled in {why}[/] "
                          f"[dim]{before - now} more model(s) readable "
                          f"({now} still without compiled SQL)[/]"
                          if now < before else
                          f"   [yellow]compiled, but {now} model(s) still have none.[/]")
    console.print("\n[bold]1. what assay found[/]")
    _coverage_panel(project, digests, failures, show_errors=False)
    if not project.adapter_type:
        console.print("   [yellow]this manifest names no adapter, so the dialect was assumed to be "
                      "duckdb. Pass --dialect if that is wrong.[/]")
    if missing and not compile_first:
        console.print("   [dim]`assay onboard --compile` runs `dbt compile` for you and re-reads "
                      "them. It needs your warehouse connection.[/]")
    console.print("\n[bold]2. what it can see[/]")
    _schema_panel(schema, sstats)

    console.print("\n[bold]3. what it found, with no key and no spend[/]")
    _facts, edge_findings = relate.run_all(project, digests, schema)
    findings = run_all(project, digests, schema) + edge_findings
    findings.sort(key=lambda f: -f.weight)
    blind = unevaluable_tests(project, digests)
    if blind:
        console.print(f"   [yellow]{len(blind)} test(s) cannot be evaluated[/] "
                      f"[dim]-- they are not a pass. `assay tests` lists them.[/]")
    if not findings:
        console.print("   [green]no structural findings.[/] "
                      "[dim]That is a real result on a small or careful project; on a large one it "
                      "usually means the compiled SQL is missing. Check the counts above.[/]")
    else:
        by = Counter(f.check for f in findings)
        t = Table(show_header=False, box=None, padding=(0, 2))
        for check_, n in by.most_common(8):
            ex = next(f for f in findings if f.check == check_)
            t.add_row(f"[bold]{n}[/]", check_, f"[dim]e.g. {ex.subject_name}[/]")
        console.print(t)
        console.print("   [dim]`assay check --check <name>` reads one of them in full.[/]")

    judged = _onboard_judge(project, digests, schema, findings, config_path, store_path,
                            judge, judge_limit)

    console.print("\n[bold]5. config[/]")
    p = Path(config_path) / "audit.yml"
    if p.exists():
        console.print(f"   [dim]{p} already exists; left alone.[/]")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_YML)
        console.print(f"   wrote [bold]{p}[/]. "
                      "[dim]Nothing in it fails a build, and assay refuses to gate on a question "
                      "with no recorded verdicts anyway.[/]")

    if agent:
        from .skilltext import SKILL_MD
        sp = Path(".claude/skills/dbt-assay/SKILL.md")
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(SKILL_MD)
        console.print(f"   wrote [bold]{sp}[/] [dim](the procedure an agent follows when it edits "
                      "a model)[/]")
        # markup=False, because rich reads `[mcp]` as a style tag and silently drops it -- which
        # would print an install line that installs no mcp.
        console.print(f"   MCP: claude mcp add assay -- uvx --from 'dbt-assay[mcp]' "
                      f"assay mcp --target {tdir}", style="dim", markup=False)

    console.print("\n[bold]6. next[/]")
    steps = []
    if cov["unreadable"] or cov.get("from_stripped"):
        n_raw = cov["unreadable"] + cov.get("from_stripped", 0)
        steps.append(("assay onboard --compile",
                      (f"{n_raw} model(s) were read without compiled SQL, which is the single "
                       f"biggest thing holding assay back here. This runs `dbt compile` for you.")))
    if not schema.catalog_present:
        steps.append(("dbt docs generate",
                      "gives assay real column lists for your sources instead of inferring them"))
    steps.append(("assay inventory --html inventory.html",
                  "one page per model: columns, provenance, and any description that drifted"))
    if findings:
        steps.append((f"assay check --check {by.most_common(1)[0][0]}",
                      "the finding there is most of"))
    if judged is None:
        steps.append(("assay onboard   (without --no-judge)",
                      "a key is present; section 4 was skipped because you asked it to be"))
    if judged:
        steps.append(("assay claims --extract",
                      "pull every claim out of this project's own prose, as data you can audit"))
        steps.append(("assay verify",
                      "check each of those claims against what the code actually does"))
        steps.append(("assay traverse",
                      "judge every hop in the graph for a fan-out nobody declared"))
        steps.append(("assay columns --limit 25",
                      "the same tier over every column: what each one MEANS, adjudicated"))
    elif judged is False:
        steps.append(("export OPENROUTER_API_KEY=...",
                      ("section 4 is what a key buys, and everything above ran without one. "
                       "openrouter.ai, or TYPESAFE_API_KEY from docs.typesafe.ai. A full judged "
                       "pass over a 265-model warehouse cost $0.0026.")))
    from .contracts import user_bank_dir as _ubd
    if _ubd():
        steps.append(("assay banks",
                      "you have an assay_questions/ directory; this lints what is in it"))
    if not agent:
        steps.append(("assay onboard --agent",
                      "writes the agent skill file and prints the MCP line"))
    t = Table(show_header=False, box=None, padding=(0, 2))
    for cmd, why in steps:
        t.add_row(f"[bold cyan]{cmd}[/]", f"[dim]{why}[/]")
    console.print(t)


def _warn_unknown_questions(cfg) -> None:
    """A `questions:` key matching no check configures nothing, and said so nowhere."""
    if not cfg.unknown_questions:
        return
    console.print(f"[yellow]audit.yml configures {len(cfg.unknown_questions)} question(s) that "
                  f"match no check:[/] {', '.join(cfg.unknown_questions)}")
    console.print("[dim]These do nothing. `questions:` is keyed by the CHECK a finding carries, "
                  "which is not the question family a verdict files under. "
                  "`assay check --json` lists every check name.[/]")


def _gate_progress(store_path: str, cfg) -> None:
    """How far each question is from being allowed to fail a build.

    *** "20 VERDICTS PER QUESTION" WAS UNREACHABLE WITHOUT A PROGRESS BAR. ***
    The floor is real and correct, and there was no surface anywhere that said how many you had,
    which question they counted for, or which questions counted for nothing at all. Ruling on a
    question that gates nothing is work nobody gets back, so it is named here rather than
    discovered afterwards.
    """
    from .contracts import load_all_banks
    from .judged import FAMILIES_WITHOUT_FINDINGS
    if not Path(store_path).exists():
        console.print(f"\n[dim]no store at {store_path}, so no verdicts are recorded yet. "
                      f"`assay columns` asks, `assay review -i` rules.[/]")
        return
    st = Store(store_path)
    human = st.adjudication_counts("human")
    labels = st.adjudication_counts("label")
    banks = sorted(load_all_banks())
    st.close()

    if not human and not labels:
        console.print("\n[dim]no verdicts recorded. `assay review -i` is one keypress each; "
                      f"a question may fail a build at {cfg.min_adjudications} of them.[/]")

    rows = [b for b in banks if human.get(b) or labels.get(b)] or banks
    t = Table(title="\nverdicts", header_style="bold", title_justify="left")
    t.add_column("question"); t.add_column("human", justify="right")
    t.add_column("labels", justify="right"); t.add_column("may gate")
    for b in rows:
        n = human.get(b, 0)
        if b in FAMILIES_WITHOUT_FINDINGS:
            gate = "[dim]no finding rests on this[/]"
        elif n >= cfg.min_adjudications:
            gate = "[green]yes[/]"
        else:
            gate = f"[yellow]{cfg.min_adjudications - n} more[/]"
        t.add_row(b, str(n), str(labels.get(b, 0)), gate)
    console.print(t)
    console.print("[dim]Labels come from your own tests and joins. They are evidence and never "
                  "permission: the label can be the thing that is wrong.[/]")


@app.command()
def config(
    config_path: str = typer.Option(".", "--config", help="directory holding audit.yml"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    check: bool = typer.Option(False, "--check",
                               help="also make one real call to prove the key works"),
):
    """What assay resolved: the config file, the provider, where the key came from, the cap.

    *** "NO API KEY FOUND" WAS WRONG FOR WEEKS AND NOTHING COULD SHOW IT. ***
    assay read only `os.environ`, so a key in a `.env` was invisible, and every judged command
    reported the tier as off. A capability check that can be wrong needs a way to see what it
    decided, otherwise the only way to debug it is to read the source.
    """
    from .jev import key_source, load_env
    load_env()
    cfg = Config.load(config_path)

    p = Path(config_path) / "audit.yml"
    t = Table(show_header=False, box=None, padding=(0, 2))
    # *** A PATH THAT GETS ELLIPSISED ANSWERS NOTHING. ***
    # The whole point of this command is to say WHERE the key came from, and rich truncates a long
    # cell by default, so the one fact worth printing is the first thing to disappear.
    t.add_column(no_wrap=True)
    t.add_column(overflow="fold")
    t.add_row("audit.yml", f"[bold]{p}[/]" if p.exists()
                           else f"[dim]{p} -- absent, so every default below is in force[/]")
    t.add_row("provider", cfg.provider)
    t.add_row("model", cfg.model)
    t.add_row("spend cap", f"${cfg.max_spend_usd:.2f} per invocation")
    t.add_row("gate floor", f"{cfg.min_adjudications} human verdicts before a question may fail "
                            f"a build")
    t.add_row("agreement floor",
              (f"{cfg.min_agreement:.0%} of those verdicts must AGREE, on the version shipping now"
               if cfg.min_agreement else
               "[dim]off. A count of wrong answers is still a count -- run `assay effectiveness` "
               "and set gating.min_agreement from the rates you have[/]"))

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if client.available:
        name, spec, _k = client._conn()
        t.add_row("key", f"[green]found[/] for [bold]{name}[/]  "
                         f"[dim]{spec['env']} from {key_source(spec['env'])}[/]")
    else:
        t.add_row("key", "[yellow]none.[/] [dim]Set TYPESAFE_API_KEY or OPENROUTER_API_KEY in "
                         "your environment, or put it in a .env here or above.[/]")
    console.print(t)

    if cfg.vocab:
        console.print(f"\n[dim]vocabulary: {len(cfg.vocab)} term(s), sent with every question[/]")
    if cfg.waivers:
        console.print(f"[dim]waivers: {sum(len(v) for v in cfg.waivers.values())}[/]")

    _warn_unknown_questions(cfg)
    _gate_progress(store_path, cfg)

    if not check:
        console.print("\n[dim]`assay config --check` makes one real call to prove the key "
                      "works.[/]")
        return
    if not client.available:
        raise typer.Exit(1)
    from .jev import noul
    try:
        client.ask({"sky": "blue"}, {"q": noul("Is the sky described as blue?")},
                   caller="assay.config")
    except Exception as e:
        console.print(f"\n[red]the key did not work:[/] {e}")
        raise typer.Exit(1) from e
    console.print(f"\n[green]the key works.[/] [dim]1 call, {client.input_tokens} tokens, "
                  f"${client.spent_usd:.5f}[/]")


@app.command()
def claims(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    extract: bool = typer.Option(False, "--extract",
                                 help="ask which sentences are claims, and store them"),
    select: str = typer.Option(None, "--select", "-s", help="scope it, e.g. \"path:models/water\""),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N models"),
    min_conf: float = typer.Option(0.7, "--min-confidence",
                                   help="how sure the extractor must be that a sentence IS a "
                                        "claim. Measured: above this every answer read correctly "
                                        "by hand; below it they are headers and fragments."),
    write: str = typer.Option(None, "--write", help="write claims.yml so you can edit and audit"),
    model: str = typer.Option(None, "--model", "-m", help="show one model's claims"),
):
    """What this project ASSERTS about its models, as data you can read, edit and rule on.

    *** A MODEL DESCRIPTION IS NOT ONE CLAIM, AND JUDGING IT AS ONE PRODUCES A COIN FLIP. ***
    Measured: "Boulder commercial building permits, residential filtered out" is two claims, the
    first supported and the second not. Put to a single choice it split 0.51/0.47 and flipped
    between runs. Split, the sharpest atomic claim read `contradicts` at 0.82.

    Extraction is SELECTION, never generation: code splits the prose, and a judgment says what job
    each sentence is doing. The model never writes a claim, so every one points at the file and
    line where a person wrote it.
    """
    from .selector import resolve
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, _schema, _s = _load(tdir)
    store = Store(store_path)

    if not extract:
        rows = store.claims(subject=model, checkable_only=True, min_conf=min_conf)
        if not rows:
            n = len(store.claims())
            store.close()
            console.print("[yellow]no claims stored yet.[/] "
                          f"[dim]{n} row(s) in the table. Run `assay claims --extract`.[/]"
                          if n else "[yellow]no claims stored.[/] "
                                    "[dim]Run `assay claims --extract` first.[/]")
            raise typer.Exit(0)
        if write:
            _write_claims_yaml(Path(write), rows)
            console.print(f"wrote [bold]{write}[/] with {len(rows)} claim(s). "
                          f"[dim]Edit it, then `assay claims --extract` keeps your edits: a "
                          f"suppressed claim stays suppressed.[/]")
            store.close()
            raise typer.Exit(0)
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("model"); t.add_column("kind"); t.add_column("claim", overflow="fold")
        for r in rows[:200]:
            t.add_row(r["subject_name"], r["kind"].replace("claim_about_", ""),
                      r["text"][:140] + ("  [cyan]" + r["citation"] + "[/]" if r["citation"] else ""))
        console.print(t)
        console.print(f"\n[dim]{len(rows)} claim(s) at confidence >= {min_conf}. "
                      f"`--write claims.yml` to audit them.[/]")
        store.close()
        raise typer.Exit(0)

    # ---- extract ----
    shared = sem_mod.boilerplate(project)
    cands = claims_mod.candidates(project, digests, shared)
    scope = resolve(project, select)
    if scope is not None:
        cands = [c for c in cands if c.subject in scope]
    seen, uniq = set(), []
    for c in cands:                       # the same sentence in a description AND a comment is one
        if c.claim_id not in seen:
            seen.add(c.claim_id)
            uniq.append(c)
    by_model: dict = {}
    for c in uniq:
        by_model.setdefault(c.subject, []).append(c)
    if limit:
        by_model = dict(list(by_model.items())[:limit])

    already = {r["claim_id"] for r in store.claims()}
    suppressed = {r["claim_id"] for r in store.claims() if r["status"] == "suppressed"}
    todo = {u: [c for c in cs if c.claim_id not in already]
            for u, cs in by_model.items()}
    todo = {u: cs for u, cs in todo.items() if cs}
    n_new = sum(len(v) for v in todo.values())
    console.print(f"[bold]{len(uniq)}[/] candidate sentence(s) across {len(by_model)} model(s) · "
                  f"[bold]{n_new}[/] not yet classified")
    if not n_new:
        store.close()
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key.[/] `assay config` shows what was resolved.")
        store.close()
        raise typer.Exit(1)

    rows, kinds = [], Counter()
    with console.status(f"classifying {n_new} sentence(s)..."):
        for uid, cs in todo.items():
            m = project.models[uid]
            for chunk in [cs[i:i + claims_mod.CHUNK]
                          for i in range(0, len(cs), claims_mod.CHUNK)]:
                st = claims_mod.kind_state(m.name, chunk, m.description or "", cfg.vocab)
                try:
                    ans = decide(store, client, st, claims_mod.kind_questions(chunk),
                                 contexts={f"claim__{i}": c.text[:120]
                                           for i, c in enumerate(chunk)},
                                 decision_key=f"{uid}::sentence::{chunk[0].claim_id}",
                                 prompt_version=claims_mod.KIND_VERSION, caller="assay.claims")
                except BudgetExceeded as e:
                    console.print(f"[yellow]stopped at the cap: {e}[/]")
                    break
                for i, c in enumerate(chunk):
                    a = ans.get(f"claim__{i}")
                    if not a:
                        continue
                    kinds[a["answer"]] += 1
                    rows.append((c.claim_id, c.subject, c.subject_name, c.text, c.source_kind,
                                 c.source_ref, a["answer"], a["confidence"], c.citation,
                                 "suppressed" if c.claim_id in suppressed else "active"))
    store.save_claims(rows)
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    t = Table(show_header=False, box=None, padding=(0, 2))
    for k, n in kinds.most_common():
        mark = "[bold]" if k in claims_mod.CHECKABLE else "[dim]"
        t.add_row(f"{mark}{n}[/]", f"{mark}{k}[/]")
    console.print(t)
    keep = sum(n for k, n in kinds.items() if k in claims_mod.CHECKABLE)
    console.print(f"\n[bold]{keep}[/] checkable claim(s). "
                  f"[dim]`assay claims` lists them, `--write claims.yml` audits them.[/]")
    store.close()


def _write_claims_yaml(path: Path, rows: list) -> None:
    """The audit surface. Ordered by model so a diff reads like a review."""
    import yaml
    out: dict = {}
    for r in rows:
        out.setdefault(r["subject_name"], []).append({
            "id": r["claim_id"], "claim": r["text"], "kind": r["kind"],
            "confidence": round(r["kind_conf"] or 0, 2), "from": r["source_ref"],
            **({"citation": r["citation"]} if r["citation"] else {}),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Claims assay extracted from this project's own prose. EDIT FREELY.\n"
        "# Delete a claim to suppress it; it stays suppressed across re-extraction.\n"
        "# Add one by hand with any id you like -- a claim nobody wrote down is still a claim.\n\n"
        + yaml.safe_dump(out, sort_keys=True, width=100, allow_unicode=True))


@app.command()
def verify(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    select: str = typer.Option(None, "--select", "-s"),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N claims"),
    min_conf: float = typer.Option(0.7, "--min-confidence"),
    model: str = typer.Option(None, "--model", "-m"),
):
    """Check every extracted claim against what the code actually does.

    One claim per call, with only the evidence that bears on it. A compound claim judged whole
    splits its probability and flips between runs; that is why `assay claims` breaks prose into
    atomic claims before anything is asked here.
    """
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    store = Store(store_path)
    rows = store.claims(subject=model, checkable_only=True, min_conf=min_conf)
    if select:
        from .selector import resolve
        scope = resolve(project, select)
        if scope is not None:
            rows = [r for r in rows if r["subject"] in scope]
    if limit:
        rows = rows[:limit]
    if not rows:
        store.close()
        console.print("[yellow]no claims to verify.[/] [dim]Run `assay claims --extract`.[/]")
        raise typer.Exit(0)

    observed = probe_mod.read(store)
    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        store.close()
        console.print("[yellow]no API key.[/] [dim]`assay config` shows what was resolved.[/]")
        raise typer.Exit(1)

    console.print(f"[bold]{len(rows)}[/] claim(s) to check")
    out, counts = [], Counter()
    with console.status(f"checking {len(rows)} claim(s)..."):
        for r in rows:
            ev = claims_mod.evidence_for(r["subject"], project, digests, schema, observed,
                                         claim_text=r["text"])
            if not ev:
                continue
            c = claims_mod.Claim(r["claim_id"], r["subject"], r["subject_name"], r["text"],
                                 r["source_kind"], r["source_ref"], citation=r["citation"] or "")
            try:
                ans = decide(store, client, claims_mod.align_state(c, ev, cfg.vocab),
                             claims_mod.align_question(),
                             contexts={"align": f"{c.subject_name}: {c.text[:120]}"},
                             decision_key=f"{c.subject}::claim::{c.claim_id}",
                             prompt_version=claims_mod.ALIGN_VERSION, caller="assay.verify")
            except BudgetExceeded as e:
                console.print(f"[yellow]stopped at the cap: {e}[/]")
                break
            a = ans.get("align")
            if not a:
                continue
            counts[a["answer"]] += 1
            if a["answer"] == "contradicts":
                out.append((c, a["confidence"], (a["probabilities"] or {}).get("contradicts", 0)))
    store.close()

    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    t = Table(show_header=False, box=None, padding=(0, 2))
    for k, n in counts.most_common():
        style = "[bold red]" if k == "contradicts" else "[dim]"
        t.add_row(f"{style}{n}[/]", f"{style}{k}[/]")
    console.print(t)
    if not out:
        console.print("\n[green]no claim is contradicted by its code.[/]")
        raise typer.Exit(0)
    console.print(f"\n[bold]{len(out)}[/] claim(s) the code contradicts:")
    for c, conf, p_ in sorted(out, key=lambda x: -x[2])[:20]:
        console.print(f"  [bold]{c.subject_name}[/]  [dim]p={p_:.2f}  {c.source_ref}[/]")
        console.print(f"    {c.text[:150]}")


@app.command()
def traverse(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    select: str = typer.Option(None, "--select", "-s"),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N edges"),
    model: str = typer.Option(None, "--model", "-m", help="only edges into this model"),
):
    """Judge every hop in the graph: does one row still mean the same thing on the other side?

    *** THIS IS THE ONE DEFECT CLASS NO SINGLE-MODEL CHECK CAN SEE. ***
    A fan-out introduced upstream and consumed downstream is invisible to every question that
    reads one model, and it is what a person only finds by chasing a number by hand. The graph
    facts are already free -- what each edge carries, what it drops, what it joins on -- so the
    only thing asked here is whether the hop changed what a row IS.
    """
    from .selector import resolve
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    facts, _edge_findings = relate.run_all(project, digests, schema)

    # *** CODE NARROWS FIRST. *** An edge that carries everything and joins on nothing cannot have
    # changed a grain, and asking is paying to be told so.
    cands = [f for f in facts if f.joined_on or f.dropped]
    if model:
        cands = [f for f in cands if f.child_name == model or f.parent_name == model]
    scope = resolve(project, select)
    if scope is not None:
        cands = [f for f in cands if f.child in scope]
    cands.sort(key=lambda f: -(len(f.dropped or []) + 10 * bool(f.joined_on)))
    if limit:
        cands = cands[:limit]
    console.print(f"[bold]{len(facts)}[/] edge(s), [bold]{len(cands)}[/] where something changes")
    if not cands:
        raise typer.Exit(0)

    store = Store(store_path)
    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        store.close()
        console.print("[yellow]no API key.[/] [dim]`assay config` shows what was resolved.[/]")
        raise typer.Exit(1)

    declared = relate.declared_keys(project)
    counts, bad = Counter(), []
    with console.status(f"judging {len(cands)} edge(s)..."):
        for f in cands:
            cd = digests.get(f.child)
            if cd is None or not cd.ok:
                continue
            st = {
                "parent": {"model": f.parent_name,
                           "declared_key": declared.get(f.parent) or None,
                           "columns": list(f.carried or [])[:25]},
                "child": {"model": f.child_name,
                          "declared_key": declared.get(f.child) or None,
                          "joins_on": list(f.joined_on or [])[:10],
                          "groups_by": list(cd.group_by or [])[:10] or None,
                          "uses_qualify": bool(getattr(cd, "has_qualify", False)) or None},
                "columns_the_child_drops": sorted(f.dropped or [])[:20] or None,
            }
            # *** WITHOUT THIS, EVERY WORD OF THE STATE IS TRUE AND THE CONCLUSION IS WRONG. ***
            # A parent collapsed inside a subquery before the join cannot fan the join out. 33% of
            # 543 hops read `silently_multiplied` on a real warehouse, and the top one was exactly
            # this shape.
            pre = (cd.pre_aggregated or {}).get(f.parent_name)
            if pre is not None:
                st["the_child_already_collapsed_the_parent_before_joining"] = {
                    "relation": f.parent_name,
                    "to_one_row_per": pre or "a distinct",
                }
            if f.parent_name in (cd.union_members or set()):
                st["the_child_reads_this_parent_as_one_arm_of_a_UNION"] = (
                    "so one row of the parent is one row of the child. The child having more "
                    "rows than this parent is the union, not a fan-out on this hop.")
            st = {k: v for k, v in st.items() if v}
            st["parent"] = {k: v for k, v in st["parent"].items() if v}
            st["child"] = {k: v for k, v in st["child"].items() if v}
            if cfg.vocab:
                st["vocabulary"] = cfg.vocab
            try:
                ans = decide(store, client, st,
                             {"edge": choice_q("edge_preserves_the_grain")},
                             contexts={"edge": f"{f.parent_name} -> {f.child_name}"},
                             decision_key=f"{f.child}::edge::{f.parent}",
                             prompt_version=_prompt_version("edge_preserves_the_grain"),
                             caller="assay.traverse")
            except BudgetExceeded as e:
                console.print(f"[yellow]stopped at the cap: {e}[/]")
                break
            a = ans.get("edge")
            if not a:
                continue
            counts[a["answer"]] += 1
            if a["answer"] == "silently_multiplied":
                bad.append((f, (a["probabilities"] or {}).get("silently_multiplied", 0)))
    store.close()

    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    t = Table(show_header=False, box=None, padding=(0, 2))
    for k, n in counts.most_common():
        style = "[bold red]" if k == "silently_multiplied" else "[dim]"
        t.add_row(f"{style}{n}[/]", f"{style}{k}[/]")
    console.print(t)
    if not bad:
        console.print("\n[green]no hop multiplies rows without saying so.[/]")
        raise typer.Exit(0)
    console.print(f"\n[bold]{len(bad)}[/] hop(s) that multiply rows without declaring it:")
    for f, p_ in sorted(bad, key=lambda x: -x[1])[:15]:
        console.print(f"  [bold]{f.parent_name}[/] -> [bold]{f.child_name}[/]  "
                      f"[dim]p={p_:.2f}  on {', '.join(sorted(f.joined_on or [])[:4]) or 'no key'}[/]")


@app.command()
def effectiveness(
    store_path: str = typer.Option("assay.duckdb", "--store"),
    source: str = typer.Option("human", "--source",
                               help="human | agent | label | all. Only human verdicts gate."),
    config_path: str = typer.Option(".", "--config"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Did the questions get BETTER? Agreement per family, per version of the question.

    *** THE TOOL COULD NOT MEASURE ITS OWN IMPROVEMENT, AND THE DATA WAS ALREADY THERE. ***
    Every question rewrite so far was found by a person reading output, and its effect was written
    into a markdown file by hand. `units_are_what_the_column_claims` went 2/4 to 8/8 across one
    rewrite. Those verdicts existed. The store threw the older ones away, because a re-ruling
    overwrote the row rather than joining it.

    Two axes, and they fail differently. `prompt_version` moves when YOU change a question, so the
    before and after is the measurement of your own work. `model_version` moves when Jev ships,
    under questions nobody touched, and it is the only way "our agreement fell and we changed
    nothing" is ever visible.
    """
    import json

    from .contracts import QUESTIONS

    if not Path(store_path).exists():
        console.print(f"[yellow]no store at {store_path}.[/] [dim]Run any judged command once.[/]")
        raise typer.Exit(1)
    store = Store(store_path)
    try:
        rows = store.effectiveness(source)
    finally:
        store.close()
    if not rows:
        console.print(f"[yellow]no {source} verdicts recorded yet.[/] [dim]`assay review -i` is "
                      f"where they come from, and nothing here can be computed without them.[/]")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(rows, default=str, indent=2))
        raise typer.Exit(0)

    shipping = {name: (q or {}).get("prompt_version", "") for name, q in QUESTIONS.items()}
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("family", overflow="fold"); t.add_column("question version")
    t.add_column("answered by"); t.add_column("ruled", justify="right")
    t.add_column("agreed", justify="right"); t.add_column("unclear", justify="right")
    t.add_column("open", justify="right")
    stale = 0
    for r in rows:
        pv = r["prompt_version"]
        # *** A VERDICT ABOUT v1 SAYS NOTHING ABOUT v4. ***
        # Marked rather than hidden: the rows are real evidence, about a question that no longer
        # exists in that wording.
        want = shipping.get(r["family"]) or (
            # A structural check's version is assay's own: it changed when the CHECK changed.
            f"assay.{_pkg_version()}" if pv.startswith("assay.") else "")
        old = bool(want) and pv not in ("(unversioned)", want)
        stale += bool(old)
        rate = r["agreement"]
        cell = "[dim]no verdict either way[/]" if rate is None else (
            f"{'[red]' if rate < 0.5 else '[yellow]' if rate < 0.8 else '[green]'}"
            f"{r['agree']}/{r['agree'] + r['disagree']}  ({rate:.0%})[/]")
        t.add_row(r["family"], f"[dim]{pv}[/]" if old else pv, r["model_version"],
                  str(r["n"]), cell,
                  str(r["unclear"]) if r["unclear"] else "[dim]0[/]",
                  f"[red]{r['open_disagreements']}[/]" if r["open_disagreements"] else "[dim]0[/]")
    console.print(t)

    total_open = sum(r["open_disagreements"] for r in rows)
    unclear = sum(r["unclear"] for r in rows)
    console.print(f"\n[bold]{total_open}[/] disagreement(s) still open. [dim]A disagreement closes "
                  f"when somebody agrees at a DIFFERENT version of the question, so this falls "
                  f"only when a question changed and a person re-read it. A release cannot lower "
                  f"it.[/]")
    if unclear:
        # *** `unclear` AND `disagree` NEED DIFFERENT REPAIRS. ***
        # Disagreement is wrong criteria. Unclear is a state that does not carry the answer, which
        # is what seventeen unclears on one warehouse turned out to be -- every one fixed by
        # putting something in the state, none by rewording an option.
        console.print(f"[bold]{unclear}[/] unclear, which is not disagreement. [dim]It is the "
                      f"subject state failing to carry what the question asks about. Reword an "
                      f"option to fix a disagreement; add a field to fix an unclear.[/]")
    if stale:
        console.print(f"[dim]{stale} row(s) greyed: recorded against a version of the question "
                      f"that is no longer shipping. They still count as evidence and they do not "
                      f"count toward the agreement floor.[/]")
    unver = sum(1 for r in rows if r["prompt_version"] == "(unversioned)")
    if unver:
        console.print(f"[dim]{unver} row(s) are (unversioned): recorded before the version was "
                      f"kept, or traceable to more than one. assay does not guess which.[/]")


@app.command()
def disagreements(
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    source: str = typer.Option("all", "--source", help="human | agent | all"),
    judge: bool = typer.Option(False, "--judge",
                               help="also ask whether two differently-worded reasons are the "
                                    "same defect. Costs a fraction of a cent; code groups the "
                                    "identical ones for free either way."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Group the open disagreements. N rejected findings, how many separate bugs?

    *** NINE RELEASES CAME OUT OF A PERSON RELAYING THIS OUT OF A TERMINAL. ***
    The verdicts and the reasons were in the store the whole time and nothing read them together.
    Ten of twelve disagreements on one warehouse were one sentence said ten ways, and collapsing
    them by hand is what produced 0.12.0's two fixes -- one of which was still open when the
    clustering was written, and this found it.

    It never closes anything. A disagreement closes when a question or a check CHANGED and a
    person re-read it, which is what `assay effectiveness` measures. This says which ones are
    probably one piece of work.
    """
    import json

    from .contracts import QUESTIONS
    from .subjects import candidate_pairs, normalise_reason, open_disagreements

    if not Path(store_path).exists():
        console.print(f"[yellow]no store at {store_path}.[/]")
        raise typer.Exit(1)
    store = Store(store_path)
    try:
        rows = open_disagreements(store, source)
        if not rows:
            console.print(f"[green]no open disagreement with a reason recorded[/] "
                          f"[dim](source={source}). A disagreement with no reason is not "
                          f"evidence, and `rule` refuses one.[/]")
            raise typer.Exit(0)

        # *** CODE CLUSTERS FIRST AND FOR FREE. ***
        # Identical first sentences are the same defect and no judgement is needed to say so.
        ids = [f"{r['subject']}#{r['question']}" for r in rows]
        pos = {k: i for i, k in enumerate(ids)}
        parent = list(range(len(rows)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> bool:
            ra, rb = find(a), find(b)
            if ra == rb:
                return False
            parent[rb] = ra
            return True

        seen: dict = {}
        for i, r in enumerate(rows):
            k = (r["family"], normalise_reason(r["note"]))
            if k in seen:
                union(seen[k], i)
            else:
                seen[k] = i
        free_clusters = len({find(i) for i in range(len(rows))})

        joined = 0
        if judge:
            cfg = Config.load(config_path)
            pairs = candidate_pairs(store)
            if not pairs:
                console.print("[dim]every differently-worded pair was already grouped by code; "
                              "nothing to ask.[/]")
            else:
                client = Client(provider=cfg.provider, model=cfg.model,
                                max_spend_usd=cfg.max_spend_usd)
                if not client.available:
                    console.print("[yellow]no API key.[/] [dim]`assay config` shows what was "
                                  "resolved. The free clustering above still ran.[/]")
                else:
                    q = QUESTIONS["same_defect"]
                    console.print(f"[dim]asking about {len(pairs)} differently-worded pair(s)...[/]")
                    for key, a, b in pairs:
                        ans = decide(
                            store, client,
                            {"check_or_question_both_rulings_are_about": a["family"],
                             "first_reason": (a["note"] or "")[:700],
                             "second_reason": (b["note"] or "")[:700]},
                            {q["id_prefix"]: noul_q("same_defect")},
                            contexts={q["id_prefix"]: f"{a['subject']} ~ {b['subject']}"},
                            decision_key=key, prompt_version=q["prompt_version"],
                            caller="assay.disagreements")
                        got = (ans or {}).get(q["id_prefix"]) or {}
                        try:
                            p_same = float(got.get("answer") or 0)
                        except (TypeError, ValueError):
                            continue
                        if p_same >= 0.6:
                            ka = pos.get(f"{a['subject']}#{a['question']}")
                            kb = pos.get(f"{b['subject']}#{b['question']}")
                            if ka is not None and kb is not None and union(ka, kb):
                                joined += 1
    finally:
        store.close()

    groups: dict = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    ordered = sorted(groups.values(), key=lambda g: -len(g))

    if as_json:
        print(json.dumps([{
            "size": len(g), "family": g[0]["family"],
            "reason": min((x["note"] or "" for x in g), key=len),
            "members": [x["subject"] for x in g],
            "every_ruling_is_an_agent_ruling": all(x["source"] == "agent" for x in g),
        } for g in ordered], indent=2, default=str))
        raise typer.Exit(0)

    console.print(f"\n[bold]{len(rows)}[/] open disagreement(s) -> "
                  f"[bold]{len(ordered)}[/] distinct defect(s)")
    for g in ordered:
        r = min(g, key=lambda x: len(x["note"] or ""))
        mark = "[red]" if len(g) >= 3 else ""
        console.print(f"\n{mark}{len(g)} ruling(s)[/]  [dim]{g[0]['family']}[/]"
                      if mark else f"\n{len(g)} ruling(s)  [dim]{g[0]['family']}[/]")
        console.print(f"  {(r['note'] or '')[:300]}")
        console.print(f"  [dim]{', '.join(sorted({str(x['subject']).split('::')[0].split('.')[-1] for x in g}))[:150]}[/]")
        if all(x["source"] == "agent" for x in g):
            # *** A CLUSTER OF AGENT RULINGS IS A HYPOTHESIS ABOUT A CHECK, NOT A VERDICT ON IT. ***
            console.print("  [dim]every ruling here is an agent's. That is a hypothesis about the "
                          "check, not a verdict on it: a person's keypress is still the only one "
                          "that counts.[/]")

    if judge:
        # *** THE DELTA IS THE MEASUREMENT OF WHETHER ASKING WAS WORTH ANYTHING. ***
        # Code grouped the identical first sentences for nothing. If judgement adds no groups on a
        # real corpus, that is a finding about this family and it belongs in VERIFICATION.md.
        console.print(f"\n[dim]code alone found {free_clusters} group(s); judgement merged "
                      f"{joined} more pair(s) that were worded differently.[/]")
    else:
        console.print("\n[dim]grouped by identical first sentence only, which costs nothing. "
                      "--judge also asks whether differently-worded reasons are one defect.[/]")
    console.print("[dim]Nothing here is closed. A disagreement closes when the check or the "
                  "question CHANGED and a person re-read it -- `assay effectiveness` counts "
                  "those.[/]")


@app.command()
def completeness(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin"),
    verify: bool = typer.Option(False, "--verify",
                                help="count through your own dbt: default shares, empty models, "
                                     "and row loss at a hop"),
    dialect: str = typer.Option(None, "--dialect"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Do we have all of it? Coverage of what this project itself declares.

    *** assay FOUND COMPLETENESS DEFECTS BY ACCIDENT BEFORE IT LOOKED FOR THEM. ***
    `test_cannot_fail` flagged a `not_null` on `coalesce(x, 'not looked up')` as unable to fire,
    which is a semantics finding. Reading it produced a completeness fact: 170,730 of 172,695 rows
    ARE that default, so the lookup has effectively never run. The numbers existed across three
    commands and nothing collected them.

    *** WHAT THIS DELIBERATELY IS NOT. ***
    No funnels, no conversion rates, no "row count fell 12% week over week". Every one of those
    needs somebody to say what the funnel IS and what a normal week looks like. That is intent,
    assay refuses to guess at intent, and the refusal is why its findings are worth reading.

    The line: assay can say a column is 99% its default. It cannot say whether that is bad. The
    first is a fact about code and rows; the second is a ruling, and that loop already exists.
    """
    import json

    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _failures, schema, _s = _load(tdir, dialect)
    store = Store(store_path) if Path(store_path).exists() else None
    facts, _ = relate.run_all(project, digests, schema)
    entries = inv_mod.build(project, digests, schema, store,
                            probe_mod.read(store) if store else {}, facts=facts)

    counted: dict = {}
    if verify:
        counted["hops"] = prac_mod.verify_row_loss(entries, project, probe_mod, project_dir,
                                                   profiles_dir, dbt_bin, schema=schema)
        patches = prac_mod.primary_key_patches(project, entries)
        held = prac_mod.verify_grains(patches, project, probe_mod, project_dir, profiles_dir,
                                      dbt_bin, schema=schema)
        counted["empty_models"] = sorted(n for n, c in held.items() if c[0] == 0)

    fs = live.all_findings(project, digests, schema, entries, cfg.row_loss_threshold)
    if store:
        store.close()
    buckets: dict = {}
    for f in fs:
        if f.check.startswith("source_") or f.check == "hop_drops_most_rows":
            buckets.setdefault(f.check, []).append(f)

    cov = project.coverage()
    doc = {
        "assay_can_read": {"models": cov["models"], "readable": cov["readable"],
                           "not_audited": cov["models"] - cov["readable"]},
        **{k: [{"subject": f.subject_name, "summary": f.summary, "evidence": f.evidence}
               for f in v] for k, v in sorted(buckets.items())},
        **({"empty_models": counted.get("empty_models", []),
            "hops_counted_for_row_loss": counted.get("hops", 0)} if verify else {}),
    }
    if as_json:
        print(json.dumps(doc, indent=2, default=str))
        raise typer.Exit(0)

    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("what"); t.add_column("n", justify="right"); t.add_column("meaning")
    t.add_row("models assay could not read", str(cov["models"] - cov["readable"]),
              "[dim]not audited, and not a pass[/]")
    for check, label in (
            ("source_reaches_nothing", "sources nothing reads"),
            ("source_only_a_test_reads", "sources only a test reads"),
            ("source_freshness_undeclared", "sources declaring no freshness"),
            ("source_freshness_stale", "sources behind their own freshness"),
            ("hop_drops_most_rows", "hops that lose most of the parent")):
        n = len(buckets.get(check, []))
        t.add_row(label, f"[red]{n}[/]" if n else "[dim]0[/]",
                  f"[dim]{_COMPLETENESS_MEANING[check]}[/]")
    if verify:
        t.add_row("models that are EMPTY", str(len(counted.get("empty_models", []))),
                  "[dim]a uniqueness test on one passes for the wrong reason[/]")
    console.print(t)

    for check in ("hop_drops_most_rows", "source_freshness_stale", "source_reaches_nothing"):
        got = buckets.get(check) or []
        if not got:
            continue
        console.print(f"\n[bold]{check}[/]")
        for f in got[:10]:
            console.print(f"  {f.summary}")

    if not verify:
        # *** AN ABSENT MEASUREMENT MUST NEVER READ AS A CLEAN ONE. ***
        console.print("\n[yellow]row loss, empty models and default shares were NOT counted.[/] "
                      "[dim]Re-run with --verify to count them through your own dbt. Their "
                      "absence here is not a pass.[/]")
    console.print("\n[dim]Every line above is coverage of what this project itself declares. "
                  "assay can say a column is 99% its default; it cannot say whether that is bad. "
                  "That second question is a ruling: `assay review -i`.[/]")


_COMPLETENESS_MEANING = {
    "source_reaches_nothing": "declared, loaded every run, and no model or test refers to it",
    "source_only_a_test_reads": "you are paying to test data nothing consumes",
    "source_freshness_undeclared": "nothing says how current it should be "
                                   "(silent when dbt_project_evaluator is installed)",
    "source_freshness_stale": "the project states how current it should be and it is not",
    "hop_drops_most_rows": "no filter, no group by, no collapse -- a join that is not matching",
}


@app.command()
def page(
    out: str = typer.Argument("assay.html", help="where to write it"),
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    dialect: str = typer.Option(None, "--dialect"),
    plain: bool = typer.Option(False, "--plain",
                               help="a sober report: no colour, no background, nothing to "
                                    "explain before somebody reads it"),
) -> None:
    """One page answering "is this warehouse understood, and by whom".

    *** NOT A DASHBOARD OF METRICS. *** The ruled-on number goes first and largest, because
    everything else on the page is downstream of whether anybody has read any of it, and because
    it is the only figure a release cannot improve.

    Self-contained, no network, no build step, and DETERMINISTIC: it carries the manifest's own
    `generated_at` and never a wall clock, so a rerun that changes nothing writes an identical
    file. That is the whole argument for a file over a server -- a file that diffs accrues, and
    one that churns on every run cannot be committed at all.
    """
    from . import render

    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir, dialect)
    store = Store(store_path) if Path(store_path).exists() else None
    facts, _ = relate.run_all(project, digests, schema)
    entries = inv_mod.build(project, digests, schema, store,
                            probe_mod.read(store) if store else {}, facts=facts)
    fs = live.all_findings(project, digests, schema, entries, cfg.row_loss_threshold)

    ruled_keys: set = set()
    agent_n, eff, moved = 0, [], {}
    if store is not None:
        ruled_keys = store.ruled_subjects()
        agent_n = len(store.agent_rulings())
        eff = store.effectiveness("human")
        run = store.con.execute(
            "select run_id from runs order by started_at desc limit 1").fetchone()
        if run:
            prev = store.previous_run(project.project_name, run[0])
            if prev:
                moved = store.diff(prev, run[0])

    def _is_ruled(f) -> bool:
        return f.subject in ruled_keys or f"{f.subject}::finding::{f.id}" in ruled_keys

    per: dict = {}
    for f in fs:
        d = per.setdefault(f.check, [0, 0, 0])
        d[0] += 1
        d[1] = max(d[1], f.marts)
        d[2] += _is_ruled(f)
    by_check = sorted(((c, n, m, r) for c, (n, m, r) in per.items()), key=lambda x: -x[1])

    counts = {c: sum(1 for f in fs if f.check == c) for c in (
        "source_reaches_nothing", "source_only_a_test_reads", "source_freshness_undeclared",
        "source_freshness_stale", "hop_drops_most_rows")}
    cov = project.coverage()
    completeness = [
        ("models assay could not read", cov["models"] - cov["readable"],
         "not audited, and an absent audit is not a pass"),
        ("sources nothing reads", counts["source_reaches_nothing"],
         "declared, loaded every run, no model and no test refers to it"),
        ("sources only a test reads", counts["source_only_a_test_reads"],
         "you are paying to test data nothing consumes"),
        ("sources declaring no freshness", counts["source_freshness_undeclared"],
         "silent when dbt_project_evaluator is installed"),
        ("sources behind their own freshness", counts["source_freshness_stale"],
         "the project states how current it should be and it is not"),
        ("hops that lose most of the parent", counts["hop_drops_most_rows"],
         "needs --verify; no filter, no group by, no collapse"),
    ]

    # *** WHAT IS ONE ROW OF THIS -- AND WHO SAID SO. ***
    # A grain a person declared and one a judgement reached at 0.53 are not the same fact, so the
    # page never adds them together.
    grain = {"declared": 0, "derived": 0, "judged": 0, "none": 0}
    for en in entries:
        if not en.grain:
            grain["none"] += 1
        else:
            grain[{"declared": "declared", "judged": "judged"}.get(
                en.grain.source, "derived")] += 1
    no_unique = len(prac_mod.primary_key_patches(project, entries))

    claims_summary: dict = {}
    if store is not None:
        rows = store.claims()
        if rows:
            conflicted = {c for en in entries for c, _p in (en.claim_conflicts or [])}
            claims_summary = {"total": len(rows),
                              "supported": sum(1 for r in rows if r["claim_id"] not in conflicted),
                              "contradicted": len(conflicted)}

    counted_row_loss = any(en.row_loss for en in entries)
    not_counted = "" if counted_row_loss else (
        '<p class="note"><b>Row loss and empty models were not counted.</b> Run '
        '<span class="mono">assay completeness --verify</span> to count them through this '
        "project's own dbt. Their absence above is not a pass.</p>")

    doc = render.page_html({
        "project": project.project_name or "this project",
        "models": len(project.models),
        # *** NEVER A WALL CLOCK. *** A page that churns cannot be committed.
        "generated_at": (project.raw.get("metadata") or {}).get("generated_at", "unknown"),
        "version": __version__,
        "ruled": sum(1 for f in fs if _is_ruled(f)), "findings_total": len(fs),
        "agent_rulings": agent_n, "effectiveness": eff,
        "by_check": by_check, "completeness": completeness, "moved": moved,
        "plain": plain, "grain": grain, "no_unique_test": no_unique,
        "claims": claims_summary, "not_counted_note": not_counted,
        "top_findings": [{"check": f.check, "model": f.subject_name, "summary": f.summary,
                          "marts": f.marts} for f in fs[:18]],
        "shown": min(18, len(fs)),
    })
    if store is not None:
        store.close()
    Path(out).write_text(doc)
    console.print(f"wrote [bold]{out}[/]  [dim]{len(doc):,} bytes, self-contained. "
                  f"Commit it: a rerun that changes nothing writes an identical file.[/]")


@app.command()
def banks(
    lint: bool = typer.Option(True, "--lint/--no-lint",
                              help="check every question against the rules Jev's shape imposes"),
    strict: bool = typer.Option(False, "--strict", help="exit non-zero on a warning too"),
    judge: bool = typer.Option(False, "--judge",
                               help="also ask whether any two options of a question could both be "
                                    "right about the same subject. Needs a key; about a cent."),
    config_path: str = typer.Option(".", "--config"),
    store_path: str = typer.Option("assay.duckdb", "--store",
                                   help="caches --judge, so an unchanged question keeps its "
                                        "answer and the check does not flap in CI"),
):
    """Every question assay will ask, where it came from, and whether its shape is sound.

    *** YOUR OWN QUESTIONS GO IN `assay_questions/` AND LOAD ON TOP. ***
    A directory here or in any parent, or wherever `ASSAY_QUESTIONS` points. A family with a new
    name is added; one with a shipped name REPLACES it, which is the point -- a warehouse whose
    `column_role` needs an extra option should not have to fork.

    The lint is every shape already measured to fail: arithmetic Jev cannot do, dates it reads as
    text, a choice with no way to decline, options described so alike there is nothing to cut on.
    It cannot tell you a question is GOOD. Only running it against cases you have already ruled on
    does that.
    """
    from .contracts import SHIPPED, load_all_banks, user_bank_dir
    from .lint import lint_all

    ud = user_bank_dir()
    all_banks = load_all_banks()
    added = sorted(n for n in all_banks if n not in SHIPPED)
    replaced = sorted(n for n in all_banks
                      if n in SHIPPED and all_banks[n].get("_source") != SHIPPED[n].get("_source"))
    where = (f"{len(added)} added, {len(replaced)} replaced, from {ud}" if ud
             else "no assay_questions/ directory found")
    console.print(f"[bold]{len(all_banks)}[/] question(s)  "
                  f"[dim]{len(SHIPPED)} shipped; {where}[/]")

    from .lint import caller_of
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("question"); t.add_column("type"); t.add_column("from")
    t.add_column("asked by"); t.add_column("about", overflow="fold")
    inert = []
    for name in sorted(all_banks):
        q = all_banks[name]
        own = name in added
        repl = name in replaced
        src = ("[green]yours[/]" if own else
               "[yellow]yours, replacing[/]" if repl else "[dim]shipped[/]")
        called = caller_of(name, q)
        if called is None:
            inert.append(name)
        t.add_row(f"[bold]{name}[/]" if own or repl else name,
                  q.get("type", "?"), src,
                  called[1] if called else "[red]nothing asks this[/]",
                  called[2] if called else "[dim]--[/]")
    console.print(t)
    if inert:
        # *** THE SAME RULE AS EVERYWHERE ELSE: A GUARD THAT MATCHES NOTHING PASSES WRONGLY. ***
        console.print(f"\n[red]{len(inert)} question(s) are never asked by anything:[/] "
                      f"{', '.join(inert)}")
        console.print("[dim]Add a [bold]subject:[/bold] line and `assay ask` runs it: one of "
                      "model, edge, column, predicate, expression, window. Add "
                      "[bold]finding_when:[/bold] naming the answers that are findings, and it "
                      "reaches `assay check` with everything else.[/]")

    if not lint:
        raise typer.Exit(0)
    from .lint import acknowledged_issues
    acks = acknowledged_issues(all_banks, SHIPPED)
    if acks:
        # Shown, never hidden: an acknowledgement is a decision someone made, and the next reader
        # deserves to see what was silenced and why.
        console.print(f"\n[bold]{len(acks)} rule(s) acknowledged[/]")
        for a in acks:
            console.print(f"  [cyan]ack[/]  [bold]{a.question}[/]  [dim]{a.rule}[/]")
            console.print(f"    {a.detail}")
    issues = lint_all(all_banks, SHIPPED)
    if judge:
        # *** THE LINTER USING THE TOOL'S OWN ARGUMENT ON ITSELF. ***
        # A parser settles what it can; whether two descriptions pick out the same case is a
        # question about meaning, and the static rule measured 0.25 on a real overlap.
        from .lint import judge_overlap
        cfg = Config.load(config_path)
        client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
        if not client.available:
            console.print("[yellow]--judge needs a key.[/] [dim]`assay config` shows what was "
                          "resolved. Everything above ran without one.[/]")
        else:
            st = Store(store_path)
            try:
                issues += judge_overlap(all_banks, client, st)
            finally:
                st.close()
            console.print(f"[dim]{client.calls} call(s), ${client.spent_usd:.4f}"
                          + ("  (cached answers cost nothing)" if client.calls < len(all_banks)
                             else "") + "[/]")
    if not issues:
        console.print("\n[green]every question has a shape Jev answers well.[/] "
                      "[dim]That is a check on the SHAPE. Only running it against cases you have "
                      "already ruled on says whether it is right.[/]")
        raise typer.Exit(0)
    errs = [i for i in issues if i.level == "error"]
    console.print(f"\n[bold]{len(errs)} error(s), {len(issues) - len(errs)} warning(s)[/]")
    for i in sorted(issues, key=lambda x: (x.level != "error", x.question)):
        colour = "red" if i.level == "error" else "yellow"
        console.print(f"\n  [{colour}]{i.level}[/]  [bold]{i.question}[/]  [dim]{i.rule}[/]")
        console.print(f"    {i.detail}")
    raise typer.Exit(1 if errs or (strict and issues) else 0)


def _estimate(subs, q: dict) -> float:
    """What asking every one of these would cost, from the real states rather than a guess.

    Sampled and extrapolated: serialising 3,540 states to count them exactly would be slower than
    the thing it is protecting you from.
    """
    from .jev import USD_PER_INPUT_TOKEN
    if not subs:
        return 0.0
    sample = subs[:25]
    overhead = len(_json.dumps({"questions": {q.get("id_prefix", "x"): {
        "type": q.get("type"), "instructions": q.get("instructions"),
        "criteria": q.get("criteria")}}}, default=str))
    per = sum(len(_json.dumps(x.state, default=str)) + overhead for x in sample) / len(sample)
    return per / 4 * USD_PER_INPUT_TOKEN * len(subs)


@app.command()
def ask(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    family: str = typer.Option(None, "--family", "-f", help="one family; default is all that "
                                                            "declare a subject"),
    select: str = typer.Option(None, "--select", "-s"),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N subjects per family"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="count the subjects and print one state; ask nothing"),
):
    """Run every question that declares a `subject:`, including your own.

    *** A FAMILY USED TO NEED A HAND-WRITTEN CALL SITE, SO HALF OF WRITING ONE WAS A CODE CHANGE. ***
    A family with a new name was loaded, linted, listed by `assay banks` and never asked by
    anything -- and it looked exactly like coverage. Three were written on a real warehouse before
    anyone noticed. Declare `subject: expression` and assay builds that state and asks it.

    Subjects: model, edge, column, predicate, expression, window. `expression` and `window` were
    asked for by name in the field and had no call site at all.
    """
    from .contracts import load_all_banks
    from .lint import caller_of
    from .selector import resolve
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    banks = load_all_banks()

    runnable = {n: q for n, q in banks.items() if q.get("subject")}
    if family:
        if family not in banks:
            console.print(f"[red]no question named {family!r}.[/] `assay banks` lists them.")
            raise typer.Exit(1)
        if not banks[family].get("subject"):
            called = caller_of(family)
            console.print(f"[yellow]{family} declares no `subject:`[/], so the generic runner "
                          f"cannot build a state for it."
                          + (f" It is asked by [bold]{called[1]}[/]." if called else ""))
            raise typer.Exit(1)
        runnable = {family: banks[family]}
    if not runnable:
        console.print("[yellow]no question declares a `subject:`.[/] "
                      "[dim]Add one to a question in assay_questions/ and it runs here. "
                      "The shipped families have their own commands; `assay banks` shows which.[/]")
        raise typer.Exit(0)

    scope = resolve(project, select)
    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    store = Store(store_path)
    total = Counter()

    for name, q in runnable.items():
        subs = subjects_mod.build(
            q["subject"],
            subjects_mod.SubjectSource(project, digests, schema, store),
            state=q.get("subject_state", "full"))
        if scope is not None:
            subs = [x for x in subs if x.uid in scope]
        if limit:
            subs = subs[:limit]
        # *** THE COUNT AND THE COST, BEFORE ANYTHING IS SPENT. ***
        # Reported from the field: `subject: expression` yields 3,540 subjects on a real project
        # against 82 for `window`. That is $0.35 to ask one question project-wide, and the number
        # worth printing is the one you see BEFORE running it without `--select`. A cap that fires
        # after the spend is not a cap, and neither is an estimate.
        est = _estimate(subs, q)
        console.print(f"\n[bold]{name}[/]  [dim]{q['subject']} · {len(subs)} subject(s) · "
                      f"~${est:.4f}[/]")
        if dry_run:
            if subs:
                console.print(f"  [dim]{_json.dumps(subs[0].state, default=str)[:400]}...[/]")
            continue
        if est > cfg.max_spend_usd:
            console.print(f"  [red]refused before spending anything:[/] ~${est:.2f} exceeds the "
                          f"${cfg.max_spend_usd:.2f} cap in audit.yml.")
            console.print("  [dim]Scope it with --select, cut it with --limit, or raise "
                          "jev.max_spend_usd. `--dry-run` shows the state and costs nothing.[/]")
            continue
        if not client.available:
            console.print("  [yellow]no API key.[/] [dim]`assay config` shows what was "
                          "resolved; --dry-run needs none.[/]")
            store.close()
            raise typer.Exit(1)

        want = q.get("finding_when")
        want = [want] if isinstance(want, str) else (want or [])
        counts, hits = Counter(), []
        with console.status(f"asking {name} about {len(subs)} subject(s)..."):
            for sub in subs:
                try:
                    ans = decide(store, client, {**sub.state, **({"vocabulary": cfg.vocab}
                                                                if cfg.vocab else {})},
                                 {q["id_prefix"]: choice_q(name)},
                                 contexts={q["id_prefix"]: f"{sub.name}"},
                                 decision_key=sub.key, prompt_version=q["prompt_version"],
                                 caller=f"assay.ask.{name}")
                except BudgetExceeded as e:
                    console.print(f"  [yellow]stopped at the cap: {e}[/]")
                    break
                a = ans.get(q["id_prefix"])
                if not a:
                    continue
                counts[a["answer"]] += 1
                if a["answer"] in want:
                    hits.append((sub, (a["probabilities"] or {}).get(a["answer"], 0)))
        total.update(counts)
        t = Table(show_header=False, box=None, padding=(0, 2))
        for k, n in counts.most_common():
            t.add_row(f"[bold red]{n}[/]" if k in want else f"[dim]{n}[/]",
                      f"[bold red]{k}[/]" if k in want else f"[dim]{k}[/]")
        console.print(t)
        for sub, p_ in sorted(hits, key=lambda x: -x[1])[:10]:
            console.print(f"    [bold]{sub.name}[/]  [dim]p={p_:.2f}  {sub.file}[/]")
        if not want and counts:
            console.print("    [dim]no `finding_when:`, so these are stored and produce no "
                          "finding.[/]")

    store.close()
    if not dry_run:
        console.print(f"\n[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                      f"${client.spent_usd:.4f}[/]")


@app.command()
def regress(
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
    family: str = typer.Option(None, "--family", "-f", help="only this question family"),
):
    """Re-ask every question a person already agreed with, and report what moved.

    *** VERDICTS ARE THE ONLY REGRESSION TEST assay HAS AGAINST A REAL BANK. ***
    Reported from the field, and it is the reason this command exists: an upgrade to the subject
    state moved two of eight verified answers on one family, and the answer DISTRIBUTION barely
    moved -- 79 of the same answer either side. The regression was invisible in every summary the
    tool prints and measurable only because eight rulings were on record.

    Run it after upgrading assay, after editing a question, and after changing `vocab`. An answer
    that moves away from something a person confirmed is the finding, whichever direction it went.
    """
    from .contracts import load_all_banks
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    store = Store(store_path)
    confirmed = store.confirmed(family)
    if not confirmed:
        store.close()
        console.print("[yellow]no confirmed answers to replay.[/] "
                      "[dim]`assay review -i` records them; this replays them. Eight is enough to "
                      "catch a regression that no summary shows.[/]")
        raise typer.Exit(0)

    banks = load_all_banks()
    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        store.close()
        console.print("[yellow]no API key.[/] [dim]`assay config` shows what was resolved.[/]")
        raise typer.Exit(1)

    console.print(f"[bold]{len(confirmed)}[/] confirmed answer(s) to replay")
    moved, held, skipped = [], 0, []
    by_family: dict = {}
    for row in confirmed:
        by_family.setdefault(row["family"], []).append(row)

    with console.status("replaying..."):
        for fam, rows in by_family.items():
            resolved = _resolve_family(fam, banks)
            q = banks.get(resolved) if resolved else None
            if q is None:
                skipped += [(r, f"no question named {fam!r} in any bank") for r in rows]
                continue
            fam = resolved
            if not q.get("subject"):
                skipped += [(r, "declares no subject; its own command replays it") for r in rows]
                continue
            subs = {x.key: x for x in subjects_mod.build(
                q["subject"], subjects_mod.SubjectSource(project, digests, schema, store),
                state=q.get("subject_state", "full"))}
            for r in rows:
                sub = subs.get(r["subject"])
                if sub is None:
                    skipped.append((r, "that subject no longer exists in this project"))
                    continue
                try:
                    got = decide(store, client,
                                 {**sub.state, **({"vocabulary": cfg.vocab} if cfg.vocab else {})},
                                 {q["id_prefix"]: choice_q(fam)},
                                 contexts={q["id_prefix"]: sub.name},
                                 decision_key=sub.key, prompt_version=q["prompt_version"],
                                 caller="assay.regress")
                except BudgetExceeded as e:
                    console.print(f"[yellow]stopped at the cap: {e}[/]")
                    break
                a = got.get(q["id_prefix"])
                if not a:
                    continue
                if a["answer"] == r["answered"]:
                    held += 1
                else:
                    moved.append((fam, sub, r["answered"], a["answer"], a.get("confidence") or 0))
    store.close()

    console.print(f"[dim]{client.calls} call(s), ${client.spent_usd:.4f}"
                  + ("  (unchanged states are cached and cost nothing)" if client.calls
                     < len(confirmed) else "") + "[/]")
    if skipped:
        console.print(f"[dim]{len(skipped)} not replayed: "
                      f"{skipped[0][1]}[/]")
    # *** A PASS COMPUTED OVER AN EMPTY SET IS THE WORST RESULT THIS TOOL CAN PRINT. ***
    # Reported from the field: every verdict was skipped on a name mismatch and this printed
    # "0/0 confirmed answers still hold" and exited 0. Green, over nothing. It is the same shape
    # as the guard that scans nothing and the scanner that matches nothing, in the one command
    # written to catch regressions.
    if held == 0 and not moved:
        console.print(f"\n[red]NOTHING WAS REPLAYED.[/] {len(skipped)} confirmed answer(s) were "
                      f"skipped, so this is not a pass -- it is a check that looked at nothing.")
        for r, why in skipped[:6]:
            console.print(f"  [dim]{r['family']} · {r['subject'][:60]}: {why}[/]")
        raise typer.Exit(1)
    if not moved:
        console.print(f"\n[green]{held}/{held} confirmed answers still hold.[/]"
                      + (f" [yellow]{len(skipped)} skipped.[/]" if skipped else ""))
        raise typer.Exit(0)

    console.print(f"\n[bold red]{len(moved)} of {held + len(moved)} confirmed answers MOVED[/]")
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("family"); t.add_column("subject", overflow="fold")
    t.add_column("you confirmed"); t.add_column("now says")
    for fam, sub, was, now, conf in moved:
        t.add_row(fam, sub.name, f"[green]{was}[/]", f"[red]{now}[/] [dim]@{conf:.2f}[/]")
    console.print(t)
    console.print("\n[dim]An answer moving away from one a person confirmed is the finding, "
                  "whichever direction it went. If the new answer is right, re-rule it; if the "
                  "old one was, the change that moved it is the defect.[/]")
    raise typer.Exit(1)


@app.command()
def patch(
    out_dir: str = typer.Argument("tests/assay", help="where to write, e.g. transform/tests/assay"),
    target: str = typer.Option(None, "--target", "-t"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin", help="the dbt command, e.g. 'uv run dbt'"),
    dry_run: bool = typer.Option(False, "--dry-run", help="print what it would write, write none"),
    dialect: str = typer.Option(None, "--dialect"),
):
    """Write the uniqueness tests assay can prove will pass.

    *** A GENERATED TEST THAT FAILS ON ITS FIRST RUN IS WORSE THAN NO TEST. ***
    Every grain is COUNTED through your own dbt before a file is written -- `count(*)` against
    `count(distinct <grain>)`, batched. A grain that was not counted, or was counted and did not
    hold, does not become a file and the reason is printed. That is the whole difference between
    a patch and a nag.

    Singular tests, not schema.yml entries: on a real project 344 of 358 models already had a
    schema yml entry, and a second entry for the same model is a dbt compilation error. A `.sql`
    file needs no entry anywhere, collides with nothing, and needs no dbt_utils.
    """
    import dbt_assay as _pkg
    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    project, _d, _sch, entries = _entries(tdir, store, dialect)
    patches = prac_mod.primary_key_patches(project, entries)
    if store:
        store.close()
    if not patches:
        console.print("[green]every model with a settled grain already has a uniqueness test.[/]")
        raise typer.Exit(0)

    console.print(f"[bold]{len(patches)}[/] model(s) with no uniqueness test. Counting each "
                  f"proposed grain before writing anything...")
    held = prac_mod.verify_grains(patches, project, probe_mod, project_dir, profiles_dir,
                                  dbt_bin, schema=_sch)
    if not held:
        console.print("[yellow]could not count a single grain[/] [dim]-- the models may not be "
                      "built, or --dbt / --project-dir may be wrong. Nothing will be written, "
                      "because an uncounted grain is not a passing one.[/]")
        raise typer.Exit(1)

    plans = patch_mod.plan(patches, held, _pkg.__version__, Path(out_dir))
    writable = [x for x in plans if x.sql]
    skipped = [x for x in plans if not x.sql]

    if writable:
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("model"); t.add_column("one row per", overflow="fold")
        t.add_column("counted", justify="right")
        for x in writable[:20]:
            t.add_row(x.model, ", ".join(x.columns), f"{x.rows:,} rows, {x.distinct:,} distinct")
        console.print(t)
    if skipped:
        console.print(f"\n[yellow]{len(skipped)} not written[/]")
        for x in skipped[:10]:
            console.print(f"  [dim]{x.model}: {x.skipped}[/]")

    if dry_run:
        console.print(f"\n[dim]--dry-run: {len(writable)} file(s) would be written to "
                      f"{out_dir}[/]")
        raise typer.Exit(0)
    if not writable:
        console.print("\n[yellow]nothing to write.[/] [dim]Every proposal was uncounted or did "
                      "not hold, and assay will not write a test it cannot prove passes.[/]")
        raise typer.Exit(0)

    n, blocked = patch_mod.write(plans, Path(out_dir))
    console.print(f"\n[green]wrote {n} test(s)[/] to [bold]{out_dir}[/]")
    for x in blocked:
        console.print(f"  [yellow]left alone:[/] {x.path.name} [dim]-- {x.skipped}[/]")
    console.print(f"[dim]Run them: dbt test --select path:{Path(out_dir).name}. Each one passes "
                  f"today; it is there to catch the day it stops.[/]")


@app.command()
def version():
    """Print the version."""
    console.print(f"assay {__version__}")


def main() -> None:
    """*** AN EXPECTED FAILURE MUST NOT LOOK LIKE A CRASH. ***

    A read-only working directory and a missing API key are ordinary situations, and both reached
    the terminal as a full traceback through assay's own internals, which reads as "the tool is
    broken" rather than "this path is wrong". The entry point names them and exits 1.
    """
    from .jev import NoProvider
    from .store import StoreUnwritable
    try:
        app()
    except (StoreUnwritable, NoProvider) as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()


@app.command()
def init(force: bool = typer.Option(False, "--force", help="overwrite an existing audit.yml")):
    """Write an audit.yml with the defaults. Asks nothing, enables no spend."""
    p = Path("audit.yml")
    if p.exists() and not force:
        console.print(f"[yellow]{p} already exists.[/] Pass --force to overwrite.")
        raise typer.Exit(1)
    p.write_text(DEFAULT_YML)
    console.print(f"wrote [bold]{p}[/]. Nothing in it enables spend; the judgment tier is opt-in.")


def _grain_setup(target: str | None, store_path: str | None = None,
                 dialect: str | None = None):
    tdir = _find_target(target)
    project, digests, _fail, schema, _sstats = _load(tdir, dialect)
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
    dialect: str = typer.Option(None, "--dialect"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin",
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
    dialect: str = typer.Option(None, "--dialect",
                                help="override; read from the manifest by default"),
    control: bool = typer.Option(False, "--control",
                                 help="ask only about columns the project already labels, and "
                                      "report agreement"),
):
    """Judge each column's role and what a NULL in it would mean."""
    cfg = Config.load(config_path)
    _tdir, project, digests, schema, declared, proposed = _grain_setup(target, store_path, dialect)
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
    interactive: bool = typer.Option(False, "--interactive", "-i",
                                     help="rule on them one keypress at a time"),
    from_labels: bool = typer.Option(False, "--from-labels",
                                     help="record verdicts from assertions already in the "
                                          "project: unique tests, declared keys, join conditions. "
                                          "Evidence, never a gate."),
    target: str = typer.Option(None, "--target", "-t", help="needed with --from-labels"),
    dialect: str = typer.Option(None, "--dialect"),
    limit: int = typer.Option(20, "--limit", "-n"),
    subject: str = typer.Option(None, "--subject", help="the decision key to rule on"),
    question: str = typer.Option(None, "--question"),
    verdict: str = typer.Option(None, "--verdict", help="agree | disagree | unclear"),
    correction: str = typer.Option("", "--correction", help="what it should have been"),
    note: str = typer.Option("", "--note"),
    who: str = typer.Option("", "--by"),
    repair: bool = typer.Option(False, "--repair",
                                help="re-point rulings written under a bare model name at the "
                                     "unique_id, so they join to findings again. Needs --target."),
):
    """List judgments nobody has ruled on, or record a verdict.

    *** THIS LOOP IS WHAT MANUFACTURES THE LABELLED SET. ***
    Until a question has verdicts, config refuses to let it fail a build. There is no way to skip
    this and still gate on anything honestly.
    """
    store = Store(store_path)
    if repair:
        _repair_subjects(store, target, dialect)
        store.close()
        raise typer.Exit(0)
    if from_labels:
        _record_from_labels(store, target, dialect)
        store.close()
        raise typer.Exit(0)
    if interactive:
        _review_loop(store, limit, target, dialect)
        store.close()
        raise typer.Exit(0)
    if verdict:
        if not (subject and question):
            console.print("[red]--verdict needs --subject and --question[/]")
            raise typer.Exit(1)
        row = store.con.execute(
            "select answer, prompt_version, model_version from model_decisions "
            "where decision_key = ? and question = ? order by decided_at desc limit 1",
            [subject, question]).fetchone()
        fam = question.split("__")[0]
        fam = {"role": "column_role", "null": "null_meaning",
               "key": "column_is_part_of_the_key"}.get(fam, fam)
        store.adjudicate(subject, question, fam, row[0] if row else "",
                         verdict, correction, note, who,
                         prompt_version=(row[1] if row else f"assay.{_pkg_version()}"),
                         model_version=row[2] if row else "")
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
    for key, q, ans, conf, pv, about, mv in rows:
        cf = f"  conf {conf:.2f}" if conf is not None else ""
        console.print(f"  [dim]{about or key.split('.')[-1]}[/]  {q} = [bold]{ans}[/]{cf}")
    console.print("\n[dim]assay review --subject <key> --question <q> --verdict agree|disagree[/]")
    store.close()


@app.command()
def inventory(
    target: str = typer.Option(None, "--target", "-t"),
    model: str = typer.Option(None, "--model", "-m", help="render one model as a document"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    json_out: bool = typer.Option(False, "--json"),
    html_out: str = typer.Option(None, "--html",
                                 help="write a self-contained page you can open, commit and diff"),
    write: str = typer.Option(None, "--write",
                              help="write contracts to a SEPARATE yaml file. Never touches "
                                   "your schema.yml."),
    everything: bool = typer.Option(False, "--include-unadjudicated",
                                    help="with --write, include entries nobody has ruled on"),
    limit: int = typer.Option(40, "--limit", "-n"),
    dialect: str = typer.Option(None, "--dialect",
                                help="override; read from the manifest by default"),
):
    """What every model in this project actually IS.

    Works with no API key: grain where code can settle it, provenance for every column, edges and
    blast radius. A judgment fills in role and sharpens grain; it is not the price of entry.
    """
    tdir = _find_target(target)
    project, digests, _failures, schema, _sstats = _load(tdir, dialect)
    store = Store(store_path) if Path(store_path).exists() else None
    observed = probe_mod.read(store) if store else {}
    entries = inv_mod.build(project, digests, schema, store, observed)

    if html_out:
        Path(html_out).write_text(
            render_mod.inventory_html(entries, project.project_name, inv_mod.describe))
        console.print(f"wrote [bold]{html_out}[/]  "
                      f"[dim]{len(entries)} models, "
                      f"{sum(len(e.columns) for e in entries):,} columns[/]")
        if store:
            store.close()
        raise typer.Exit(0)

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
    # *** `--select assay_*` MATCHES NOTHING, AND IT WAS THE LAST THING THE COMMAND SAID. ***
    # Reported from the field: `dbt list` shows all five nodes and the glob selects none of them.
    # A path selector does work, and the closing line of a command is the one instruction a
    # reader is most likely to run verbatim.
    # dbt resolves `path:` against the PROJECT root, not the working directory, so an absolute
    # path here would be another instruction that does not work.
    out_dir = Path(directory).resolve()
    proj = _project_dir_for(_find_target(None)) if Path("dbt_project.yml").exists() \
        or Path("transform/dbt_project.yml").exists() else None
    try:
        rel = out_dir.relative_to(proj.resolve()) if proj else Path(directory)
    except (ValueError, AttributeError):
        rel = Path(directory)
    console.print(f"\n[dim]now: [bold]dbt seed --select path:{rel}[/bold]  "
                  f"[/][dim](relative to your dbt project. An `{export_mod.PREFIX}*` glob "
                  f"matches nothing here, even though `dbt list` shows the nodes.)[/]")
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
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin", help='e.g. "uv run dbt"'),
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
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin", help='e.g. "uv run dbt"'),
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
    if not compile_on_save:
        # *** WITHOUT A RECOMPILE, SAVING A MODEL CHANGES NOTHING assay CAN SEE. ***
        # It watches source files and reads COMPILED SQL. Detecting a save and then re-reading a
        # stale artefact reports "nothing means anything different", which is the same sentence it
        # prints when a change really was harmless. Found by sitting in the loop.
        console.print("[yellow]--compile is off[/] [dim]so saving a model will not change what "
                      "assay reads, and it will report no change whether or not there was one. "
                      "Pass --compile, or keep your own `dbt compile` running.[/]")

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
                # Found by actually using it: a column change printed no column name at all.
                what = f"`{c.column}`" if c.column else ""
                console.print(f"[{colour}]{c.kind}[/] [bold]{c.model}[/] {what} "
                              f"{c.detail or ''}".rstrip())
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
    # *** CHECK BEFORE THE TRANSPORT OPENS, OR THE ERROR GOES NOWHERE. ***
    # `uvx dbt-assay mcp` skips the optional extra. The guard inside `serve` raised the right
    # message, but stdio already owned the channel, so the client saw CONNECTION_CLOSED and the
    # explanation was never printed anywhere a person could read it.
    try:
        mcp_server.server_class()
    except RuntimeError as e:
        # *** rich EATS `[mcp]` AS A STYLE TAG. ***
        # The message telling you to install the optional extra printed
        # `uvx --from 'dbt-assay'` -- without the extra. The instruction was broken by the exact
        # defect it was written to fix, and there is already a note about this in inventory.py.
        # `markup=False` prints the string, which is the only thing this line needs to do.
        console.print(str(e), style="red", markup=False)
        raise typer.Exit(1) from e
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


@app.command()
def semantics(
    target: str = typer.Option(None, "--target", "-t"),
    select: str = typer.Option(None, "--select", "-s",
                               help="scope it, e.g. \"path:models/water\""),
    families: str = typer.Option("both", "--families",
                                 help="predicates | descriptions | both"),
    print_state: bool = typer.Option(False, "--print-state"),
    limit: int = typer.Option(0, "--limit", "-n", help="stop after N models"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Why is that filter there, and does the description still describe the code?

    A `where` clause is either domain logic, a patch over a bad feed, or the thing that makes the
    model mean what it means. The SQL is identical for all three and nothing else tells them apart.
    """
    from .selector import resolve

    cfg = Config.load(config_path)
    tdir = _find_target(target)
    project, digests, _f, schema, _s = _load(tdir)
    store = Store(store_path) if Path(store_path).exists() else None
    entries = inv_mod.build(project, digests, schema, store,
                            probe_mod.read(store) if store else {})
    subs = sem_mod.subjects(project, digests, schema, entries)

    scope = resolve(project, select)
    if scope is not None:
        subs = [s for s in subs if s.uid in scope]
    if limit:
        subs = subs[:limit]

    do_pred = families in ("both", "predicates")
    do_desc = families in ("both", "descriptions")
    n_pred = sum(len(sem_mod.chunks(s.predicates)) for s in subs if s.predicates) if do_pred else 0
    n_desc = sum(1 for s in subs if s.purpose) if do_desc else 0
    console.print(f"[bold]{len(subs)}[/] models · "
                  f"{sum(len(s.predicates) for s in subs)} filters in {n_pred} calls · "
                  f"{n_desc} descriptions to check")

    if print_state:
        s0 = next((x for x in subs if x.predicates and x.purpose), None)
        if s0:
            console.print(_json.dumps(
                {"state": sem_mod.build_state(s0, s0.predicates[:2], cfg.vocab),
                 "questions": sem_mod.predicate_questions(s0.predicates[:2])},
                indent=1, default=str)[:2400])
        if store:
            store.close()
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key.[/] --print-state shows what would be sent.")
        raise typer.Exit(1)

    if store is None:
        store = Store(store_path)
    intents, stale = {}, []
    for s in subs:
        if do_pred and s.predicates:
            for chunk in sem_mod.chunks(s.predicates):
                st = sem_mod.build_state(s, chunk, cfg.vocab)
                try:
                    ans = decide(store, client, st, sem_mod.predicate_questions(chunk),
                             contexts={f"pred__{i}": f"{s.name}: {p}"
                                       for i, p in enumerate(chunk)},
                                 decision_key=f"{s.uid}::pred::{hash(tuple(chunk)) & 0xffff}",
                                 prompt_version=sem_mod.PRED_VERSION, caller="assay.semantics")
                except BudgetExceeded as e:
                    console.print(f"[yellow]stopped: {e}[/]")
                    do_pred = do_desc = False
                    break
                for i, p in enumerate(chunk):
                    a = ans.get(f"pred__{i}")
                    if a:
                        intents.setdefault(a["answer"], []).append(
                            (s.name, p, a.get("confidence")))
        if do_desc and s.purpose:
            st = sem_mod.description_state(s, cfg.vocab)
            try:
                ans = decide(store, client, st, sem_mod.description_question(),
                             decision_key=f"{s.uid}::desc",
                             prompt_version=sem_mod.DESC_VERSION, caller="assay.semantics")
            except BudgetExceeded as e:
                console.print(f"[yellow]stopped: {e}[/]")
                break
            a = ans.get("desc")
            if a and float(a["answer"]) >= 0.6:
                stale.append((s.name, float(a["answer"])))

    console.print(f"\n[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")

    if intents:
        t = Table(title="\nwhy the filters are there", header_style="bold")
        t.add_column("intent"); t.add_column("n", justify="right"); t.add_column("example")
        for k in sorted(intents, key=lambda k: -len(intents[k])):
            name, pred, conf = intents[k][0]
            t.add_row(k, str(len(intents[k])), f"{name}: {pred[:44]}")
        console.print(t)
        # A classification at 0.3 is the model declining to commit, not a finding. Listing it
        # as one is how a findings list earns its reputation.
        hacks = [h for h in intents.get("data_quality_workaround", []) if (h[2] or 0) >= 0.6]
        if hacks:
            console.print(f"\n[yellow]{len(hacks)} filter(s) read as a patch over a bad feed.[/] "
                          f"[dim]The real fix is upstream, and the filter goes stale when the "
                          f"feed improves.[/]")
            for name, pred, conf in sorted(hacks, key=lambda x: -(x[2] or 0))[:8]:
                console.print(f"  [bold]{name}[/]  {pred[:70]}  [dim]@{conf:.2f}[/]")

    if stale:
        console.print(f"\n[yellow]{len(stale)} description(s) contradict their code:[/]")
        for name, p in sorted(stale, key=lambda x: -x[1])[:10]:
            console.print(f"  [bold]{name}[/]  [dim]@{p:.2f}[/]")
    elif do_desc:
        console.print("\n[green]no description contradicts its code.[/]")
    store.close()


def _entries(tdir, store, dialect: str | None = None):
    project, digests, _f, schema, _s = _load(tdir, dialect)
    obs = probe_mod.read(store) if store else {}
    return project, digests, schema, inv_mod.build(project, digests, schema, store, obs)


@app.command()
def feeds(
    target: str = typer.Option(None, "--target", "-t"),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin", help='e.g. "uv run dbt"'),
    sample: int = typer.Option(20, "--sample", help="rows per relation; the defect is uniform "
                                                    "across a load, so twenty answer it"),
    limit: int = typer.Option(10, "--limit", "-n", help="how many sources to sample"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Has a feed changed its mind while its schema held still?

    A column whose name, type and row count all held steady while its CONTENT changed kind passes
    every schema test and volume monitor ever written.
    """
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    project, digests, schema, _entries_unused = _entries(tdir, store)
    declared = relate.declared_keys(project)
    known = {}
    tg = probe_mod.targets(project, digests, schema, declared, known)[:limit]
    console.print(f"[bold]{len(tg)}[/] source relations to sample")
    if not tg:
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if store is None:
        store = Store(store_path)
    sentinels, findings = [], []
    for t in tg:
        cols = feeds_mod.columns_to_sample(schema, t.uid, t.columns)
        prof_rows = probe_mod.run_sql(probe_mod.profile_sql(t.relation, cols, project.dialect),
                                      project_dir, profiles_dir, dbt_bin, limit=1)
        profile = prof_rows[0] if prof_rows else {}
        sent = probe_mod.sentinel_findings(t.relation, cols, profile)
        sentinels += [(t.relation, c, v, w) for c, v, w in sent]
        rows = probe_mod.run_sql(probe_mod.sample_sql(t.relation, cols, sample, project.dialect),
                                 project_dir, profiles_dir, dbt_bin, limit=sample)
        if not rows:
            continue
        subj = feeds_mod.FeedSubject(relation=t.relation, uid=t.uid, columns=cols,
                                     sample=rows, profile=profile, sentinels=sent)
        if not client.available:
            continue
        for chunk in feeds_mod.chunks(cols):
            st = feeds_mod.build_state(subj, chunk, cfg.vocab)
            try:
                ans = decide(store, client, st, feeds_mod.questions_for(chunk, subj),
                             decision_key=f"{t.uid}::feed",
                             prompt_version=feeds_mod.NAME_VERSION, caller="assay.feeds")
            except BudgetExceeded as e:
                console.print(f"[yellow]stopped: {e}[/]")
                break
            for c in chunk:
                a = ans.get(f"name__{c}")
                if a and a["answer"] in ("holds_something_else", "mixed") and \
                        (a.get("confidence") or 0) >= 0.6:
                    findings.append((t.relation, c, a["answer"], a.get("confidence")))
                # *** THE MODEL NAMES THE UNIT; CODE DECIDES IF THE NUMBERS FIT IT. ***
                # Asking the model whether a magnitude was plausible passed 218,235 "acres" at
                # 0.82. It now says only what the NAME claims, and `unit_range_conflict` compares
                # that against the range the probe counted.
                u = ans.get(f"unit__{c}")
                if u and u["answer"] not in ("no_unit_implied", "cannot_tell") \
                        and (u.get("confidence") or 0) >= 0.6:
                    why = feeds_mod.range_conflicts(u["answer"], profile, cols.index(c)
                                                    if c in cols else None)
                    if why:
                        findings.append((t.relation, c, f"unit_range_conflict: {why}",
                                         u.get("confidence")))

    if sentinels:
        console.print(f"\n[yellow]{len(sentinels)} placeholder value(s) found by counting, "
                      f"no judgment needed:[/]")
        for rel, c, v, w in sentinels[:10]:
            console.print(f"  [bold]{rel}.{c}[/] = {v}  [dim]{w}[/]")
    if findings:
        console.print(f"\n[yellow]{len(findings)} column(s) whose content does not match "
                      f"their name:[/]")
        for rel, c, what, conf in findings[:10]:
            console.print(f"  [bold]{rel}.{c}[/]  {what}  [dim]@{conf:.2f}[/]")
    elif client.available:
        console.print("\n[green]no column's content disagrees with its name.[/]")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    store.close()


@app.command()
def align(
    target: str = typer.Option(None, "--target", "-t"),
    select: str = typer.Option(None, "--select", "-s"),
    max_pairs: int = typer.Option(120, "--max-pairs"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Do two columns in different models mean the same thing?

    Every join in this project is somebody asserting two columns hold the same concept, so the
    labels are already written. Routed by rounding to the nearest level, with no threshold to tune.
    """
    from .selector import resolve

    cfg = Config.load(config_path)
    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    project, digests, schema, entries = _entries(tdir, store)
    scope = resolve(project, select)
    if scope is not None:
        entries = [e for e in entries if e.uid in scope]

    joined = align_mod.joined_pairs(project, digests, schema)
    pairs = align_mod.candidates(entries, joined, max_pairs=max_pairs)
    labelled = [p for p in pairs if p.label]
    console.print(f"[bold]{len(pairs)}[/] candidate pairs · "
                  f"[bold]{len(labelled)}[/] already asserted same by a join in this project")
    if not pairs:
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key.[/]")
        raise typer.Exit(1)
    if store is None:
        store = Store(store_path)

    routed, agree, checked = {}, 0, 0
    for chunk in [pairs[i:i + 10] for i in range(0, len(pairs), 10)]:
        st = align_mod.build_state(chunk, cfg.vocab)
        try:
            ans = decide(store, client, st, align_mod.questions_for(chunk),
                         decision_key=f"align::{hash(tuple(p.key for p in chunk)) & 0xffffff}",
                         prompt_version=align_mod.ALIGN_VERSION, caller="assay.align",
                         contexts={f"align__{i}":
                                   f"{x.model_a}.{x.column_a} ~ {x.model_b}.{x.column_b}"
                                   for i, x in enumerate(chunk)})
        except BudgetExceeded as e:
            console.print(f"[yellow]stopped: {e}[/]")
            break
        for i, p in enumerate(chunk):
            a = ans.get(f"align__{i}")
            if not a:
                continue
            r = align_mod.route(a)
            routed.setdefault(r, []).append((p, a.get("confidence")))
            if p.label == "same":
                checked += 1
                agree += (r == "same")

    t = Table(title="\nsame concept?", header_style="bold")
    t.add_column("route"); t.add_column("n", justify="right"); t.add_column("example")
    for k in ("same", "review", "different"):
        if routed.get(k):
            p, _c = routed[k][0]
            t.add_row(k, str(len(routed[k])), f"{p.column_a} ~ {p.column_b}")
    console.print(t)
    if checked:
        console.print(f"[dim]against pairs this project already joins: {agree}/{checked} agree "
                      f"({100 * agree // checked}%)[/]")
    for p, c in routed.get("same", [])[:10]:
        if not p.label:
            console.print(f"  [yellow]same concept, never joined[/] "
                          f"{p.model_a}.{p.column_a} ~ {p.model_b}.{p.column_b}")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    store.close()


def _count_defaults(project, digests, schema, probe, project_dir, profiles_dir, dbt_bin) -> bool:
    """How often each COALESCE default actually wins, for every test that cannot fail.

    *** "THIS TEST CANNOT FAIL" IS TRUE AND IS NOT THE ACTIONABLE SENTENCE. ***
    A defaulted column whose default is 99% of its rows is a lookup that never ran, and the test
    guarding it passes on every row while saying nothing about that. The finding says the guard is
    dead; this says the COLUMN is. Measured on a real warehouse: 170,730 of 172,695 rows of
    `dwr_analysis_status` are the string 'not looked up'.
    """
    from .checks.structural import default_literal, default_share_sql, tests_that_cannot_fail

    rows, seen = [], []
    for f in tests_that_cannot_fail(project, digests):
        ev = f.evidence or {}
        lit = ev.get("coalesce_default") or default_literal(ev.get("expression", ""))
        col = ev.get("column")
        if not lit or not col:
            continue
        rel = schema.relation.get(f.subject) or project.models[f.subject].name
        rows.append((rel, col, lit))
        seen.append((f.subject_name, rel, col, lit, f.marts))
    if not rows:
        console.print("[green]no test rests on a COALESCE default.[/]")
        return True

    console.print(f"[bold]{len(rows)}[/] defaulted column(s) to count, in one query")
    got = probe.run_sql(default_share_sql(rows), project_dir, profiles_dir, dbt_bin,
                        limit=len(rows) + 1)
    if not got:
        console.print("[yellow]could not count them.[/] [dim]The models may not be built, or "
                      "--dbt / --project-dir may be wrong. Nothing here is a pass.[/]")
        return False
    counts = {}
    for r in got:
        v = list(r.values())
        try:
            counts[str(r.get("c", v[0]))] = (int(r.get("n", v[1])), int(r.get("d", v[2])))
        except (TypeError, ValueError, IndexError):
            continue

    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("column", overflow="fold"); t.add_column("marts", justify="right")
    t.add_column("default"); t.add_column("share that ARE it", justify="right")
    hot = 0
    for name, rel, col, lit, marts in sorted(seen, key=lambda x: -x[4]):
        n, d = counts.get(f"{rel}.{col}", (0, 0))
        if not n:
            t.add_row(f"{name}.{col}", str(marts), lit, "[dim]not counted[/]")
            continue
        pct = d / n
        hot += pct >= 0.5
        style = "[red]" if pct >= 0.9 else ("[yellow]" if pct >= 0.5 else "[dim]")
        t.add_row(f"{name}.{col}", str(marts), lit, f"{style}{d:,} of {n:,}  ({pct:.0%})[/]")
    console.print(t)
    if hot:
        console.print(f"\n[bold]{hot}[/] column(s) are at least half default. "
                      f"[dim]The test passes on every row and says nothing about whether the "
                      f"lookup behind it ever ran: the coalesce conflates 'none' with 'not "
                      f"measured'.[/]")
    return True


@app.command("tests")
def tests_cmd(
    target: str = typer.Option(None, "--target", "-t"),
    count_defaults: bool = typer.Option(False, "--count-defaults",
                                        help="count how often each COALESCE default actually "
                                             "wins. Needs your dbt; one batched query."),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin"),
    gaps_only: bool = typer.Option(False, "--gaps-only",
                                   help="coverage only. Pure code, no API key, no spend."),
    dialect: str = typer.Option(None, "--dialect",
                                help="override; read from the manifest by default"),
    limit: int = typer.Option(80, "--limit", "-n", help="how many tests to judge"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Is each test's severity right, and what is a model exposed to that nothing asserts?"""
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    project, digests, _schema, entries = _entries(tdir, store, dialect)

    if count_defaults:
        # *** "THIS TEST CANNOT FAIL" IS TRUE AND IS NOT THE ACTIONABLE SENTENCE. ***
        # A `not_null` on `COALESCE(x, <literal>)` is a LIVE guard pointed at the wrong column.
        # The share that ARE the default is what someone acts on, and it is one batched query.
        _c = _count_defaults(project, digests, _schema, probe_mod, project_dir, profiles_dir,
                             dbt_bin)
        if store:
            store.close()
        raise typer.Exit(0 if _c else 1)

    gaps = testing_mod.coverage_gaps(project, digests, entries)
    console.print(f"[bold]{len(gaps)}[/] coverage gap(s) [dim]found by code alone[/]")
    t = Table(title="\nexposed, and nothing asserts it", header_style="bold")
    t.add_column("model"); t.add_column("marts", justify="right"); t.add_column("exposure")
    t.add_column("would be caught by")
    for g in gaps[:15]:
        t.add_row(g.model, str(g.marts), g.exposure[:44], ", ".join(g.would_catch)[:34])
    console.print(t)
    if gaps_only:
        if store:
            store.close()
        raise typer.Exit(0)

    subs = [s for s in testing_mod.subjects(project, entries) if s.marts][:limit]
    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("\n[yellow]no API key; severity needs one. --gaps-only needs none.[/]")
        raise typer.Exit(1)
    if store is None:
        store = Store(store_path)

    wrong = []
    for chunk in [subs[i:i + testing_mod.CHUNK] for i in range(0, len(subs), testing_mod.CHUNK)]:
        st = testing_mod.build_state(chunk, cfg.vocab)
        try:
            ans = decide(store, client, st, testing_mod.questions_for(chunk),
                         decision_key=f"sev::{hash(tuple(s.test_name for s in chunk)) & 0xffffff}",
                         prompt_version=testing_mod.SEV_VERSION, caller="assay.tests")
        except BudgetExceeded as e:
            console.print(f"[yellow]stopped: {e}[/]")
            break
        for i, s in enumerate(chunk):
            a = ans.get(f"sev__{i}")
            if not a:
                continue
            m = testing_mod.mismatch(s, a)
            if m:
                wrong.append((s, m, a.get("confidence")))

    if wrong:
        console.print(f"\n[yellow]{len(wrong)} test(s) whose severity looks wrong:[/]")
        for s, (lvl, why), conf in sorted(wrong, key=lambda x: -(x[2] or 0))[:12]:
            console.print(f"  [bold]{s.model}[/] {s.test_name[:46]}  [dim]{why} @{conf:.2f}[/]")
    else:
        console.print("\n[green]no test's severity disagrees with what it protects.[/]")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    store.close()


@app.command()
def adjudicate(
    target: str = typer.Option(None, "--target", "-t"),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin"),
    per_test: int = typer.Option(10, "--per-test", help="rows sampled per failing test"),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Rows a dbt test flagged: does the rest of the row explain it?

    dbt already built the candidate generator. `store_failures` writes every failing row, and a
    test returning 3,229 rows becomes a triage list instead of a reason to switch the test off.
    """
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    project, _digests, _schema, entries = _entries(tdir, store)

    rows, skipped = rows_mod.collect(project, entries, probe_mod, project_dir, profiles_dir,
                                     dbt_bin, per_test)
    console.print(f"[bold]{len(rows)}[/] failing row(s) to adjudicate · "
                  f"[dim]{len(skipped)} test(s) had nothing stored[/]")
    if not rows:
        console.print("[dim]dbt writes these only with store_failures on. "
                      "`dbt test --store-failures` then run this again.[/]")
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print("[yellow]no API key.[/]")
        raise typer.Exit(1)
    if store is None:
        store = Store(store_path)

    verdicts, incoherent = {}, []
    explanations = getattr(cfg, "explanations", None) or {}
    for i, fr in enumerate(rows):
        st = rows_mod.build_state(fr, cfg.vocab)
        try:
            ans = decide(store, client, st, rows_mod.questions_for(fr, explanations),
                         decision_key=f"{fr.model_uid}::row::{i}",
                         prompt_version=rows_mod.EXPL_VERSION, caller="assay.adjudicate")
        except BudgetExceeded as e:
            console.print(f"[yellow]stopped: {e}[/]")
            break
        a = ans.get("explanation")
        if a:
            verdicts.setdefault(a["answer"], []).append((fr, a.get("confidence")))
        c = ans.get("coherent")
        if c and float(c["answer"]) < 0.4:
            incoherent.append((fr, float(c["answer"])))

    t = Table(title="\nwhat the flagged rows actually are", header_style="bold")
    t.add_column("verdict"); t.add_column("n", justify="right"); t.add_column("example")
    for k in sorted(verdicts, key=lambda k: -len(verdicts[k])):
        fr, _c = verdicts[k][0]
        t.add_row(k, str(len(verdicts[k])), f"{fr.model}: {fr.rule[:36]}")
    console.print(t)
    real = verdicts.get("genuinely_wrong", [])
    if real:
        console.print(f"\n[red]{len(real)} row(s) nothing explains:[/]")
        for fr, conf in sorted(real, key=lambda x: -(x[1] or 0))[:8]:
            console.print(f"  [bold]{fr.model}[/] {fr.rule}  [dim]@{conf:.2f}[/]")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    store.close()


@app.command()
def practices(
    target: str = typer.Option(None, "--target", "-t"),
    project_dir: str = typer.Option(".", "--project-dir"),
    profiles_dir: str = typer.Option(None, "--profiles-dir"),
    dbt_bin: str = typer.Option("dbt", "--dbt", "--dbt-bin"),
    schema_name: str = typer.Option(None, "--evaluator-schema",
                                    help="where dbt-project-evaluator built its fct_ tables"),
    dialect: str = typer.Option(None, "--dialect",
                                help="override; read from the manifest by default"),
    verify: bool = typer.Option(True, "--verify/--no-verify",
                                help="count each proposed grain through your dbt before "
                                     "recommending it. A test that fails on its first run is "
                                     "not a patch."),
    keys_only: bool = typer.Option(False, "--keys-only",
                                   help="just the primary-key patches. Pure code, no key, no "
                                        "warehouse."),
    store_path: str = typer.Option("assay.duckdb", "--store"),
    config_path: str = typer.Option(".", "--config"),
):
    """Standard dbt practice: deferred to where it exists, adjudicated where it is noisy.

    assay does not reimplement dbt-project-evaluator. It reads that package's own fct_ tables and
    adds what they lack: consequence, adjudication for the checks with real exceptions, and one
    stream with the gate discipline.
    """
    cfg = Config.load(config_path)
    tdir = _find_target(target)
    store = Store(store_path) if Path(store_path).exists() else None
    project, _d, _sch, entries = _entries(tdir, store, dialect)

    patches = prac_mod.primary_key_patches(project, entries)
    # *** "CAN BE WRITTEN" IS NOT "WOULD PASS", AND THE DIFFERENCE WAS 0 OF 7. ***
    # Counted, in one statement per batch, wherever the models are built. A proposal nobody can
    # count stays absent from `held` and is reported as unchecked, never as holding.
    held: dict = {}
    if verify and patches:
        held = prac_mod.verify_grains(patches, project, probe_mod, project_dir, profiles_dir,
                                      dbt_bin, schema=_sch)
        if not held:
            console.print("[yellow]could not count any proposed grain[/] [dim]-- the models may "
                          "not be built, or `--dbt`/`--project-dir` may be wrong. Nothing below "
                          "has been verified.[/]")
    if patches:
        t = Table(title="no uniqueness test, and here is what it should cover",
                  header_style="bold")
        t.add_column("model"); t.add_column("marts", justify="right")
        t.add_column("the grain a test should assert"); t.add_column("from")
        inexpressible, would_fail, empty = [], [], []
        writable = 0
        for name, cols, src, marts, dropped in patches[:15]:
            if not cols:
                inexpressible.append((name, dropped, marts))
                continue
            counted = held.get(name)
            verdict, why = prac_mod.grain_verdict(counted)
            if verdict == "fails":
                would_fail.append((name, cols, marts, counted))
                continue                  # a test that fails on its first run is not a patch
            if verdict == "empty":
                # *** THIS COMMAND CALLED IT `holds` AND `patch` CALLED IT EMPTY. ***
                # `0 distinct < 0 rows` is false, so an empty table fell through to the success
                # branch of a condition that never considered it, and the word `holds` printed in
                # the column a reader scans for green. Same two integers, opposite readings.
                empty.append((name, cols, marts))
                continue
            note = (f"  [yellow](and {', '.join(map(str, dropped))}, which it does not emit)[/]"
                    if dropped else "")
            if verify and verdict == "uncounted":
                note += "  [dim](not counted)[/]"
            elif verdict == "holds":
                note += f"  [green]({why})[/]"
                writable += 1
            t.add_row(name, str(marts), ", ".join(cols)[:44] + note, src)
        console.print(t)
        n_ok = writable if verify else sum(1 for p_ in patches if p_[1])
        console.print(f"[dim]{n_ok} model(s) where a test can be written as-is. The standard check "
                      f"says 'no primary key test'; this says which columns it should cover, and "
                      f"only ever names columns the model actually emits.[/]")
        if would_fail:
            # *** THE STRONGER FINDING, AND IT WAS INVISIBLE. ***
            # No uniqueness test AND nobody knows what one row is. Worse than a missing test, and
            # printing it as a recommendation would hand someone a test that fails immediately.
            console.print(f"\n[red]{len(would_fail)} model(s) where nobody knows what one row "
                          f"is[/] [dim]-- no uniqueness test, and the inferred grain does not "
                          f"hold when counted:[/]")
            for name, cols, marts, (n, d) in sorted(would_fail, key=lambda x: -(x[3][0] / max(x[3][1], 1))):
                console.print(f"  [bold]{name}[/]  [dim]{marts} marts · {', '.join(cols)[:40]} "
                              f"gives {d:,} distinct over {n:,} rows "
                              f"([bold]{prac_mod.fanout(n, d)}[/bold])[/]")
        if empty:
            console.print(f"\n[yellow]{len(empty)} model(s) whose table is EMPTY[/] [dim]-- a "
                          f"uniqueness test on an unbuilt model passes for the wrong reason, and "
                          f"in the repo it is indistinguishable from a verified one. Build these "
                          f"and run this again:[/]")
            for name, cols, marts in empty[:8]:
                console.print(f"  [bold]{name}[/]  [dim]{marts} marts · {', '.join(cols)[:40]}[/]")
        if inexpressible:
            # *** THE GRAIN IS NOT IN THE OUTPUT, SO NOTHING CAN ASSERT IT. ***
            # A model that dedups on a column and then drops it cannot have its own uniqueness
            # tested by anything downstream. Verification found this on a model documented as
            # "one row per company + city" that does `partition by name_key, city` and then
            # `select * exclude (name_key)`.
            console.print(f"\n[yellow]{len(inexpressible)} model(s) whose grain is NOT in their "
                          f"own output[/], so no test downstream can assert it:")
            for name, cols, marts in inexpressible[:8]:
                console.print(f"  [bold]{name}[/]  [dim]{marts} marts · groups or dedups on "
                              f"{', '.join(map(str, cols))[:60]}, none of which it emits[/]")
    if keys_only:
        if store:
            store.close()
        raise typer.Exit(0)

    cats = prac_mod.categories(cfg.practices)
    flags, not_checked = prac_mod.collect(project, entries, probe_mod, project_dir, profiles_dir,
                                          dbt_bin, cats, schema_name)
    # *** A CHECK WHOSE TABLE IS NOT THERE IS NOT A CHECK THAT PASSED. ***
    # Reported from the field: a partially built evaluator -- five fct_ models of many -- reported
    # one category and said nothing about the rest, so a partial build read as a clean project.
    # This is the same defect as a guard that scans nothing, and it is now impossible to miss.
    if not_checked:
        console.print(f"\n[yellow]{len(not_checked)} of {len(cats)} standard check(s) were NOT "
                      f"LOOKED AT[/] [dim]-- their table is absent or empty, and assay cannot "
                      f"tell those apart from here. This is not a pass.[/]")
        console.print(f"  [dim]{', '.join(sorted(not_checked)[:8])}"
                      + (f" and {len(not_checked) - 8} more" if len(not_checked) > 8 else "")
                      + "[/]")
        console.print("  [dim]`dbt build --select package:dbt_project_evaluator` builds them "
                      "all.[/]")
    if not flags:
        console.print("\n[yellow]no dbt-project-evaluator findings.[/] [dim]"
                      + ("Given the above, that is because most of it was not built, not because "
                         "the project is clean." if not_checked else
                         "Every check assay could reach came back empty.") + "[/]")
        if store:
            store.close()
        raise typer.Exit(0)

    by_cat = {}
    for f in flags:
        by_cat.setdefault(f.category, []).append(f)
    t = Table(title="\nstandard practice", header_style="bold")
    t.add_column("category"); t.add_column("n", justify="right"); t.add_column("checks")
    for cat in ("enforce", "adjudicate", "recommend"):
        fs = by_cat.get(cat) or []
        if fs:
            t.add_row(cat, str(len(fs)),
                      ", ".join(sorted({x.check.replace("fct_", "") for x in fs}))[:52])
    console.print(t)

    for f in sorted(by_cat.get("enforce", []), key=lambda f: -f.marts)[:10]:
        console.print(f"  [red]enforce[/] [bold]{f.model}[/] {f.check.replace('fct_','')}  "
                      f"[dim]{f.why} · {f.marts} marts[/]")

    todo = by_cat.get("adjudicate", [])
    if not todo:
        if store:
            store.close()
        raise typer.Exit(0)

    client = Client(provider=cfg.provider, model=cfg.model, max_spend_usd=cfg.max_spend_usd)
    if not client.available:
        console.print(f"\n[dim]{len(todo)} flag(s) need a judgment; no API key, so they are "
                      f"listed unadjudicated.[/]")
        if store:
            store.close()
        raise typer.Exit(0)
    if store is None:
        store = Store(store_path)

    verdicts = {}
    for f in todo:
        st = prac_mod.build_state(f, cfg.vocab)
        try:
            ans = decide(store, client, st, prac_mod.question_for(f),
                         decision_key=f"practice::{f.check}::{f.model}",
                         prompt_version=prac_mod.PRACTICE_VERSION, caller="assay.practices")
        except BudgetExceeded as e:
            console.print(f"[yellow]stopped: {e}[/]")
            break
        a = ans.get("exception")
        if a:
            verdicts.setdefault(a["answer"], []).append((f, a.get("confidence")))

    t2 = Table(title="\nadjudicated", header_style="bold")
    t2.add_column("verdict"); t2.add_column("n", justify="right"); t2.add_column("example")
    for k in sorted(verdicts, key=lambda k: -len(verdicts[k])):
        f, _c = verdicts[k][0]
        t2.add_row(k, str(len(verdicts[k])), f"{f.model}: {f.check.replace('fct_','')}")
    console.print(t2)
    for kind in ("a_real_problem", "a_missing_layer"):
        for f, conf in sorted(verdicts.get(kind, []), key=lambda x: -(x[1] or 0))[:6]:
            console.print(f"  [yellow]{kind}[/] [bold]{f.model}[/] "
                          f"{f.check.replace('fct_','')} [dim]@{conf:.2f} · {f.marts} marts[/]")
    console.print(f"[dim]{client.calls} calls, {client.input_tokens:,} tokens, "
                  f"${client.spent_usd:.4f}[/]")
    store.close()


@app.command()
def skill(
    out: str = typer.Option(None, "--write", help="write to a path, e.g. "
                                                  ".claude/skills/dbt-assay/SKILL.md"),
):
    """Emit the agent procedure: what to call before and after editing a dbt model.

    The MCP server gives an agent the ABILITY to check itself. This gives it the OBLIGATION.
    Without it an agent checks when it remembers; with it, checking is the procedure.
    """
    from .skilltext import SKILL_MD
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SKILL_MD)
        console.print(f"wrote [bold]{p}[/]")
    else:
        print(SKILL_MD)


# *** THE GATE DISCIPLINE IS THEORETICAL UNTIL THIS IS FAST. ***
# `assay review --subject X --question Y --verdict agree` is too much typing to do a hundred times,
# and a hundred is roughly what a question needs before it may fail a build. One keypress each
# turns "well calibrated in my reading" into a measured number, which is the only thing that ever
# earns a check authority.
# *** THIS MAP WAS HAND-MAINTAINED AND HAD DRIFTED, TWICE. ***
# A question id is `<prefix>__<subject>`, and a verdict is filed under the family the prefix names.
# The map lived here, the prefixes lived in the code that builds the questions, and the family
# names lived in the YAML banks: three copies of one fact, and `name__` and `unit__` were missing
# from this one, so every verdict on the two feed families was filed under a family that does not
# exist. It counted toward nothing and appeared in no report.
#
# Now each bank declares its own `id_prefix` and this is derived. One copy, and a test asserts the
# round trip for every question that exists.
def _family_map() -> dict:
    from .contracts import load_all_banks
    return {q["id_prefix"]: name for name, q in load_all_banks().items() if q.get("id_prefix")}


_FAMILY = _family_map()

def choice_q(name: str) -> dict:
    """A bank entry as a live question. One place, so a rename cannot half-apply."""
    from .contracts import QUESTIONS
    from .jev import choice as _c
    q = QUESTIONS[name]
    return _c(q["instructions"], q["criteria"])


def noul_q(name: str) -> dict:
    """A noul bank entry as a live question. Same single place as `choice_q`, same reason."""
    from .contracts import QUESTIONS
    from .jev import noul as _n
    q = QUESTIONS[name]
    crit = q.get("criteria") or {}
    return _n(q["instructions"],
              (crit.get("true") or {}).get("what") if isinstance(crit.get("true"), dict)
              else crit.get("true"),
              (crit.get("false") or {}).get("what") if isinstance(crit.get("false"), dict)
              else crit.get("false"))


def _pkg_version() -> str:
    """assay's own version, which IS the version of a structural check being ruled on."""
    import dbt_assay
    return dbt_assay.__version__


def _prompt_version(name: str) -> str:
    from .contracts import QUESTIONS
    return QUESTIONS[name]["prompt_version"]


def _family_of(question: str) -> str:
    return _FAMILY.get(question.split("__")[0], question.split("__")[0])


def _resolve_family(recorded: str, banks: dict) -> str | None:
    """A family name, from whatever `review` happened to write down.

    *** `review` RECORDED A PREFIX AND `regress` LOOKED UP A NAME. ***
    `_family_of` falls back to the prefix when the bank is not loaded, so a verdict on a custom
    family was filed under `water.prio` while `regress` asked `banks.get("water.prio")` against a
    dict keyed by `seniority_ordered_by_the_wrong_date`. Every verdict was skipped, and the
    command written to catch exactly that class of regression reported a pass.

    Resolved on BOTH spellings here rather than migrating the store, because an old store must
    keep working and the two spellings will coexist in every store that already exists.
    """
    if recorded in banks:
        return recorded
    for name, q in banks.items():
        if q.get("id_prefix") == recorded:
            return name
    return None


def _keypress() -> str:
    """One keypress on a terminal, one line anywhere else.

    `click.getchar()` reads /dev/tty directly and raises when there is not one, so piping input --
    a script, a test, CI -- killed the loop outright. A tool people drive by hand should still be
    drivable by a pipe.
    """
    import sys

    # *** typer 0.27 STOPPED DEPENDING ON click, AND THIS IMPORT WAS UNDECLARED. ***
    # `import click` sat inside the one function that records verdicts, so the whole review loop --
    # the only way a question ever reaches its gate -- died with ModuleNotFoundError for anyone on
    # a current typer. It survived this long only because click used to arrive for free.
    #
    # click is declared now, and this still degrades rather than dies: a terminal read is a
    # convenience, and losing it must not lose the ability to rule.
    if sys.stdin.isatty():
        try:
            import click
            return click.getchar().lower()
        except ImportError:
            pass                      # fall through to line input; one Enter per verdict
        except (OSError, KeyboardInterrupt, EOFError):
            return "q"
    line = sys.stdin.readline()
    return (line.strip()[:1] or "q").lower()


def _review_context(target, dialect: str):
    """Everything needed to rule on a judgment WITHOUT going to open the model.

    *** A VERDICT NOBODY CAN REACH IN FIVE SECONDS DOES NOT GET GIVEN. ***
    The loop showed `role__address = dimension @0.43` and nothing else, so ruling on it meant
    finding the model and reading it. Four verdicts existed. The evidence has to be in front of
    the person.
    """
    try:
        tdir = _find_target(target)
        project, digests, _f, schema, _s = _load(tdir, dialect)
    except Exception:                                                   # noqa: BLE001
        return None
    from . import provenance as prov
    return {"project": project, "digests": digests, "schema": schema,
            "prov": {uid: prov.classify(uid, project, digests, schema) for uid in project.models}}


def _show_evidence(ctx, key: str, question: str) -> None:
    if not ctx or key not in ctx["project"].models:
        return
    m = ctx["project"].models[key]
    d = ctx["digests"].get(key)
    b = ctx["project"].blast_radius(key)
    col = question.split("__", 1)[1] if "__" in question else None
    console.print(f"  [dim]{m.path} · {b['descendants']} downstream, {b['marts']} marts[/]")
    if col and d:
        expr = (d.output_exprs.get(col.lower()) or "").strip()
        p = (ctx["prov"].get(key) or {}).get(col.lower())
        if expr:
            console.print(f"  [dim]{col} =[/] {expr[:110]}")
        if p:
            console.print(f"  [dim]comes from: {p.kind} — {p.evidence[:70]}[/]")
    elif d and d.predicates_atomic:
        console.print(f"  [dim]filters: {', '.join(d.predicates_atomic[:2])[:110]}[/]")


def _repair_subjects(store, target, dialect) -> None:
    """Re-point rulings written under a bare model name at the unique_id.

    *** NINETY-NINE ROWS SAID `recorded: true` AND JOINED TO NOTHING. ***
    `findings.subject` is `model.sunny_data.int_azcc_owners` and the MCP write path accepted the
    bare `int_azcc_owners`. Both spellings name the same model and nothing checked that they
    matched, so the queue that was supposed to show what an agent had already read showed twenty
    items and no readings. `rule` refuses such a write now; this fixes the ones already there.

    It resolves and never guesses. A name that matches no model, or more than one, is reported and
    left exactly as it is: a ruling moved to the wrong model would be worse than an orphaned one,
    because it would look attached.
    """
    tdir = _find_target(target)
    project, _d, _f, _sch, _s = _load(tdir, dialect)
    by_name: dict = {}
    for uid, m in project.models.items():
        by_name.setdefault(m.name, []).append(uid)
    rows = store.con.execute(
        "select distinct subject from adjudications").fetchall()
    fixed, ambiguous, unknown = [], [], []
    for (subj,) in rows:
        head = str(subj).split("::")[0]
        if head in project.models:
            continue
        hits = by_name.get(head) or []
        if len(hits) == 1:
            fixed.append((subj, str(subj).replace(head, hits[0], 1)))
        elif hits:
            ambiguous.append(subj)
        else:
            unknown.append(subj)
    if not fixed:
        console.print("[green]nothing to repair[/] [dim]-- every ruling's subject is a model "
                      "this project knows.[/]" if not (ambiguous or unknown) else
                      "[yellow]nothing could be repaired safely.[/]")
    for old_s, new_s in fixed:
        # The key is (subject, question, prompt_version); an update can collide with a row already
        # written under the right spelling, so the older orphan gives way rather than raising.
        store.con.execute(
            "delete from adjudications where subject = ? and (question, prompt_version) in "
            "(select question, prompt_version from adjudications where subject = ?)",
            [new_s, old_s])
        store.con.execute("update adjudications set subject = ? where subject = ?",
                          [new_s, old_s])
    if fixed:
        console.print(f"[bold]{len(fixed)}[/] subject(s) re-pointed at their unique_id. "
                      f"[dim]They join to findings again.[/]")
    for label, group, why in (("ambiguous", ambiguous, "names more than one model"),
                              ("unknown", unknown, "names no model in this project")):
        if group:
            console.print(f"[yellow]{len(group)} left alone[/] [dim]({why}): "
                          f"{', '.join(str(x) for x in group[:6])}[/]")
            console.print("[dim]A ruling moved to the wrong model is worse than an orphaned one, "
                          "because it would look attached.[/]")


def _review_loop(store, limit: int, target=None, dialect: str | None = None) -> None:
    rows = store.pending(limit)
    if not rows:
        console.print("[green]nothing is waiting for a verdict.[/]")
        return
    # *** MOST UNCERTAIN FIRST. ***
    # A verdict on an answer the model already gave at 0.99 teaches almost nothing. One on a 0.45
    # is where the question is actually being decided, so that is what a person should spend their
    # attention on.
    def informativeness(r):
        conf = r[3]
        return abs((conf if conf is not None else 0.5) - 0.5)
    rows = sorted(rows, key=informativeness)

    ctx = _review_context(target, dialect)
    if ctx is None and target:
        console.print("[yellow]could not read the project, so no evidence will be shown.[/]")
    # *** SAY WHICH OF THESE WILL EVER AUTHORISE ANYTHING, BEFORE THE KEYPRESSES START. ***
    # Nine of ten families have no finding resting on them. Their answers are still worth having,
    # and someone sitting down to move a GATE should know which rows are not going to move it.
    from .judged import FAMILIES_WITHOUT_FINDINGS
    fams = Counter(_family_of(r[1]) for r in rows)
    idle = sorted(f for f in fams if f in FAMILIES_WITHOUT_FINDINGS)
    if idle:
        n = sum(fams[f] for f in idle)
        console.print(f"[dim]{n} of these are {', '.join(idle)}, which no finding rests on yet: "
                      f"ruling on them records evidence and moves no gate.[/]")
    agent_said: dict = {}
    for a in store.agent_rulings():
        agent_said.setdefault((a["subject"], a["question"]), []).append(a)
    if agent_said:
        console.print(f"[cyan]{len(agent_said)} of these already have an agent's reading[/] "
                      f"[dim]-- shown beside the finding. It is context, not the answer: only a "
                      f"person's keypress counts toward anything.[/]")
    console.print(f"[bold]{len(rows)}[/] to rule on, least certain first.  "
                  "[dim]a agree · d disagree · u unclear · s skip · q quit[/]\n")
    done = 0
    for key, q, ans, conf, pv, about, mv in rows:
        fam = _family_of(q)
        subject = about or key.split(".")[-1]
        cf = f"  [dim]confidence {conf:.2f}[/]" if conf is not None else ""
        console.print(f"[dim]{fam}[/]  [bold]{subject}[/]")
        for a in (agent_said.get((subject, q)) or [])[:1]:
            # *** AN AGENT'S READING IS WORTH SEEING, AND IS NOT THE ANSWER. ***
            # It triages: this row is in front of you because something already read the SQL and
            # had a view. The keypress is still yours and it is still the only one that counts.
            console.print(f"  [cyan]an agent read this and said {a['verdict']}[/]"
                          f"  [dim]{(a['note'] or '')[:96]}[/]")
        console.print(f"  {q.split('__')[0]}  =  [bold]{ans}[/]{cf}")
        _show_evidence(ctx, key, q)
        ch = _keypress()
        if ch == "q":
            break
        if ch == "s":
            console.print("  [dim]skipped[/]\n")
            continue
        v = {"a": "agree", "d": "disagree", "u": "unclear"}.get(ch)
        if not v:
            console.print("  [dim]not a verdict; skipped[/]\n")
            continue
        store.adjudicate(key, q, fam, str(ans), v, who="review",
                         prompt_version=pv or "", model_version=mv or "")
        done += 1
        colour = {"agree": "green", "disagree": "red", "unclear": "yellow"}[v]
        console.print(f"  [{colour}]{v}[/]\n")

    if not done:
        return
    t = Table(title="verdicts", header_style="bold")
    t.add_column("family"); t.add_column("n", justify="right"); t.add_column("agreement",
                                                                            justify="right")
    for fam, n in sorted(store.adjudication_counts().items()):
        a = store.accuracy(fam)
        t.add_row(fam, str(n),
                  f"{100 * a['agreement']:.0f}%" if a["agreement"] is not None else "-")
    console.print(t)
    console.print(f"[dim]{done} recorded this round. `assay config` shows how far each question "
                  f"is from its floor. A question may fail a build once it has "
                  f"enough of these.[/]")


def _record_from_labels(store, target, dialect: str) -> None:
    """*** THE PROJECT ALREADY MADE THESE JUDGMENTS. THEY WERE NEVER WRITTEN DOWN AS VERDICTS. ***

    A `unique` test says a column is an identifier. A declared key says what one row is. A join
    condition says two columns hold the same concept. Every one is a human decision, made earlier,
    about something slightly narrower than the question assay asked -- which is exactly why they
    are recorded as `label` and never count toward a gate. Measured: reading four role
    disagreements by hand showed three were the LABEL being wrong.
    """
    from . import columns as cm
    from . import relate

    tdir = _find_target(target)
    project, _digests, _f, _schema, _s = _load(tdir, dialect)
    declared = relate.declared_keys(project)
    labels = cm.free_labels(project)

    rows = store.con.execute(
        "select decision_key, question, answer, prompt_version, model_version "
        "from model_decisions").fetchall()
    tally = {"agree": 0, "disagree": 0}
    per_family: dict = {}

    for key, q, ans, pv, mv in rows:
        fam = _family_of(q)
        want = None
        if q.startswith("role__"):
            want = labels.role.get((key, q[len("role__"):]))
        elif q.startswith("key__"):
            col = q[len("key__"):]
            if key in declared:
                want = "in" if col in declared[key] else "out"
                ans = "in" if float(ans) >= 0.5 else "out"
        elif q.startswith("align__"):
            continue          # the pair behind the id is not recoverable from the store alone
        if want is None:
            continue
        verdict = "agree" if str(ans) == str(want) else "disagree"
        store.adjudicate(key, q, fam, str(ans), verdict,
                         note=f"the project asserts {want}", who="project", source="label",
                         prompt_version=pv or "", model_version=mv or "")
        tally[verdict] += 1
        d = per_family.setdefault(fam, {"agree": 0, "disagree": 0})
        d[verdict] += 1

    if not tally["agree"] and not tally["disagree"]:
        console.print("[yellow]nothing in the store lines up with an assertion in the project.[/] "
                      "[dim]Run `assay infer` or `assay columns` first.[/]")
        return

    t = Table(title="recorded from the project's own assertions", header_style="bold")
    t.add_column("family"); t.add_column("n", justify="right")
    t.add_column("agree", justify="right"); t.add_column("rate", justify="right")
    for fam, d in sorted(per_family.items()):
        n = d["agree"] + d["disagree"]
        t.add_row(fam, str(n), str(d["agree"]), f"{100 * d['agree'] // n}%")
    console.print(t)
    human = sum(store.adjudication_counts("human").values())
    console.print(f"[dim]These are recorded as `label`, not `human`. They are evidence about a "
                  f"question and never permission for it to fail a build: a label can itself be "
                  f"wrong, and on a real project three of four disagreements were exactly that. "
                  f"Human verdicts so far: {human}. Add more with `assay review -i`.[/]")
