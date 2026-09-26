"""Every `assay ...` the skills show an agent is a command that exists, with flags that exist.

*** THE SKILL TAUGHT A BLIND RUN, AND THE BLIND RUN WAS READ AS A FINDING. ***
`--project-dir` and `--dbt` appeared zero times in 42,001 characters of skill text, and every
warehouse-touching example omitted them. `practices` run from that pattern returned "23 of 23
standard check(s) were NOT LOOKED AT", and that was read as a fact about the project. `guide.py`
already reads its facts from the source rather than restating them; this does the same to the
examples.
"""

import typer.main

from dbt_assay import skilltext
from dbt_assay.cli import WAREHOUSE_COMMANDS, app

BY_NAME = {(c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")): c
           for c in app.registered_commands}


def _flags(cmd) -> set:
    params = typer.main.get_params_convertors_ctx_param_name_from_function(cmd.callback)[0]
    return {o for p in params for o in [*getattr(p, "opts", []), *getattr(p, "secondary_opts", [])]}


def _fenced(text: str) -> list[str]:
    """The body of every shell fence -- ```bash, ```sh or a bare ``` -- opened and closed in order.

    A regex over ```...``` also matches a CLOSING fence followed by a newline, and then reads the
    prose between two blocks as a block. That is how "assay does **not** reimplement..." came back
    as a command the first time this ran.
    """
    out, cur, lang = [], None, ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if cur is None:
                cur, lang = [], stripped[3:].strip().lower()
            else:
                if lang in ("", "bash", "sh", "shell", "console"):
                    out.append("\n".join(cur))
                cur = None
            continue
        if cur is not None:
            cur.append(line)
    return out


def examples(text: str) -> list[tuple[str, bool]]:
    """Every invocation, and whether it was inline: fenced lines (with continuations joined)
    and inline code."""
    import re as _re
    out = []
    for block in _fenced(text):
        joined = _re.sub(r"\s*\\\n\s*", " ", block)
        for raw in joined.splitlines():
            line = raw.split(" #")[0].strip()
            line = _re.sub(r"^uvx (--refresh )?--from '?dbt-assay(\[mcp\])?'? ", "", line)
            line = line.replace("uvx dbt-assay", "assay")
            if line.startswith("assay "):
                out.append((line, False))
    for inline in _re.findall(r"`(assay [^`\n]+)`", text):
        out.append((inline.strip(), True))
    return out


def problems(line: str, inline: bool = False) -> list[str]:
    """What is wrong with one invocation. An inline mention that is only the command's name --
    "`assay completeness` says ..." -- NAMES it rather than showing how to run it, so it must
    exist but need not carry a connection. Anything with arguments is an example, and is held to
    everything."""
    import shlex
    try:
        parts = shlex.split(line)             # a quoted step (`assay run "check --verify"`) is one
    except ValueError:                        # token, and its flags are the step's, not run's
        parts = line.split()
    name = parts[1] if len(parts) > 1 else ""
    if name.startswith(("<", "-")) or name in ("...",):
        return []
    cmd = BY_NAME.get(name)
    if cmd is None:
        return [f"`{line}`: no command `{name}`"]
    have = _flags(cmd)
    bad = [f"`{line}`: {f} is not a flag of `assay {name}`"
           for f in (p.split("=")[0] for p in parts[2:] if p.startswith("--")) if f not in have]
    only_placeholders = all(x.startswith("<") for x in parts[2:])
    if name in WAREHOUSE_COMMANDS and not (inline and only_placeholders):
        trigger, unless = WAREHOUSE_COMMANDS[name]
        if (trigger is None or trigger in parts) and not (unless and unless in parts):
            for need in ("--project-dir", "--dbt"):
                if need not in parts:
                    bad.append(f"`{line}` reaches the warehouse and omits {need}, so it runs "
                               f"blind and reports what it could not see")
    return bad


def test_the_warehouse_table_matches_the_real_options():
    for name, (trigger, unless) in WAREHOUSE_COMMANDS.items():
        have = _flags(BY_NAME[name])
        assert {"--project-dir", "--dbt"} <= have, name
        assert trigger is None or trigger in have, (name, trigger)
        assert unless is None or unless in have, (name, unless)


def test_every_example_in_the_skills_is_a_real_invocation():
    seen, bad = 0, []
    for text in (skilltext.SKILL_MD, skilltext.REVIEW_SKILL_MD):
        for line, inline in examples(text):
            seen += 1
            bad += problems(line, inline)
    assert seen > 40, f"the example reader found only {seen}; it is broken"
    assert not bad, "\n".join(bad)


def test_the_reader_catches_what_it_exists_to_catch():
    """A guard that cannot see passes wrongly. Point it at the defects it is for."""
    assert problems("assay practices --keys-only --model m")
    assert problems("assay volume --json")
    assert problems("assay check --nope")
    assert problems("assay nonesuch")
    assert not problems("assay check --target t")
    assert not problems("assay tests --target t")
    assert problems("assay tests --count-defaults --target t")
    assert not problems("assay volume --project-dir transform --dbt 'uv run dbt'")
    assert examples("```bash\nassay feeds \\\n  --project-dir x --dbt dbt\n```") == [
        ("assay feeds --project-dir x --dbt dbt", False)]
    assert not problems("assay completeness", inline=True), "naming it is not running it"
    assert problems("assay completeness")
    assert not problems("assay practices --keys-only --no-verify")


def test_the_docs_hold_their_examples_to_the_same_rules():
    """The README and the docs teach people the same way the skill teaches agents.

    A fenced block is what somebody copies, so it is held to everything. Inline prose that names
    a MODE -- "`assay check --verify` counts it" -- must name a real command and real flags, and is
    not asked to carry a connection string in the middle of a sentence.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    bad = []
    for name in ("README.md", "docs/OVERVIEW.md", "docs/PRODUCT.md"):
        p = root / name
        if not p.exists():
            continue
        for line, inline in examples(p.read_text()):
            if inline and len(line.split()) > 8 and "--" not in line:
                continue                                  # a sentence, not an invocation
            for problem in problems(line, inline):
                if "reaches the warehouse" in problem:
                    continue          # the skill is held to this; a doc menu names the command
                bad.append(f"{name}: {problem}")
    assert not bad, "\n".join(bad)
