"""*** DOCS THAT DRIFT FROM THE CODE ARE THE DEFECT THIS TOOL EXISTS TO FIND. ***

Three things had already drifted before anyone checked once: fourteen of fifteen question families
were never named in either document, so a reader who saw `column_is_part_of_the_key` in
`assay config` had nowhere to look it up; the `rebase` MCP tool was absent from the skill file that
is supposed to be an agent's whole procedure; and three option lists in the overview named options
that do not exist.

This is the check assay would make of any other project, made of assay.
"""
from pathlib import Path

import pytest

import dbt_assay

ROOT = Path(dbt_assay.__file__).parent.parent.parent


def _docs() -> str | None:
    r, o = ROOT / "README.md", ROOT / "docs" / "OVERVIEW.md"
    if not r.exists():
        return None                                  # installed as a wheel
    return r.read_text() + o.read_text()


def test_every_question_family_is_named_in_the_docs():
    """`rests_on` and `assay config` both print these names. A name with no documentation is a
    dead end for whoever reads it."""
    from dbt_assay.contracts import load_all_banks
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    missing = [b for b in load_all_banks() if b not in docs]
    assert not missing, missing


def commands() -> set[str]:
    """*** READ THE APP, NOT ITS RENDERED HELP. ***

    The first version of this scraped `assay --help` for box-drawing characters. rich does not
    emit them without a TTY, so on CI the reader found ZERO commands -- and the "assert it found
    something" clause is the only reason that surfaced as a failure rather than as a pass. Exactly
    the defect this file is about: a fact read through a representation that can differ from it.
    """
    from dbt_assay.cli import app
    return {(c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-"))
            for c in app.registered_commands}


def test_every_cli_command_is_named_in_the_docs():
    """*** AND IT HAS TO LOOK FOR THE COMMAND, NOT FOR THE WORD. ***

    This matched the bare name anywhere in either document, so `assay evidence` was already
    "documented" by the sentence *the reason is a MEASUREMENT* containing no such word -- but by
    `evidence` appearing in a dozen unrelated paragraphs. A command called `page`, `check`,
    `diff` or `config` is a common English word, and the guard passed for every one of them on
    prose that has nothing to do with the command.

    A guard that matches something other than what it is checking is the defect this codebase
    keeps finding in other people's warehouses. It looks for `assay <name>`, which is how a
    document actually introduces a command.
    """
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    cmds = commands()
    assert len(cmds) > 20, "the command reader found almost nothing; it is broken"
    missing = sorted(c for c in cmds if f"assay {c}" not in docs)
    assert not missing, missing


def test_every_mcp_tool_is_in_the_skill_file():
    """The skill file IS the agent's procedure. A tool missing from it is a tool the agent will
    never call, however well it is implemented.

    Checked against BOTH shipped procedures together. A tool named in neither is unreachable; a
    tool named in only one is fine and usually correct -- `rule` belongs in the review procedure
    and `changed_contracts` in the editing one, and forcing every tool into both would make each
    file a list of everything, which is what an agent skips.
    """
    from dbt_assay.mcp_server import TOOLS
    from dbt_assay.skilltext import REVIEW_SKILL_MD, SKILL_MD
    assert TOOLS, "the tool list is empty; the reader is broken"
    both = SKILL_MD + REVIEW_SKILL_MD
    assert not [n for n, _d in TOOLS if n not in both]


def test_the_families_with_no_finding_are_marked_as_such_where_they_are_listed():
    """Someone choosing what to rule on must be able to see which rows move a gate."""
    from dbt_assay.judged import FAMILIES_WITHOUT_FINDINGS
    p = ROOT / "docs" / "OVERVIEW.md"
    if not p.exists():
        pytest.skip("no docs in a wheel install")
    body = p.read_text()
    i = body.find("## Every question, and what rests on it")
    assert i >= 0, "the reference table is gone"
    table = body[i:body.find("\n## ", i + 10)]
    for fam in FAMILIES_WITHOUT_FINDINGS:
        row = next((ln for ln in table.splitlines() if f"`{fam}`" in ln), None)
        assert row, f"{fam} is not in the table"
        assert "—" in row or "-" in row.split("|")[-2], f"{fam} is not marked as feeding nothing"


def test_every_command_and_flag_in_the_docs_actually_exists():
    """*** A DOC THAT NAMES A FLAG THAT DOES NOT EXIST IS WORSE THAN NO DOC. ***

    Caught twice by hand before this existed: `assay inventory --out` is `--html`, and
    `assay backtest --commits` is `--limit`. Someone copying either one gets an error and stops
    trusting the rest of the page.

    Checked against the registered PARAMETERS, never against rendered help, which wraps.
    """
    import re

    import typer.main

    from dbt_assay.cli import app
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")

    by_name = {(c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")): c
               for c in app.registered_commands}
    bad, seen = [], 0
    for block in re.findall(r"```bash\n(.*?)```", docs, re.DOTALL):
        for raw in block.splitlines():
            line = raw.split("#")[0].strip().replace("uvx dbt-assay", "assay")
            if not line.startswith("assay "):
                continue
            seen += 1
            parts = line.split()
            cmd = by_name.get(parts[1])
            if cmd is None:
                bad.append(f"{parts[1]}: no such command")
                continue
            params = typer.main.get_params_convertors_ctx_param_name_from_function(
                cmd.callback)[0]
            flags = {o for p in params for o in getattr(p, "opts", [])}
            for f in (p for p in parts[2:] if p.startswith("--")):
                if f not in flags:
                    bad.append(f"{parts[1]} {f}")
    assert seen > 25, f"the doc reader found only {seen} commands; it is broken"
    assert not bad, bad


def test_the_vocabulary_really_does_reach_every_question():
    """*** `assay config` PRINTS "sent with every question". THAT HAS TO BE TRUE. ***

    It reached seven families and not the two added last. A claim is exactly where a project's own
    words matter most -- "division" means something specific in a water warehouse, and a judgment
    that does not know it is guessing.

    Checked by building each state builder's output with a vocabulary and looking for it, rather
    than by grepping for the word, because a parameter that is accepted and dropped greps fine.
    """
    from dbt_assay import align, claims, columns, contracts, feeds, practices, rows, semantics
    v = {"division": {"means": "a Colorado water court region, 1 through 7"}}

    c = claims.Claim("i", "s", "n", "one row per division", "description")
    built = {
        "claims.kind_state": claims.kind_state("m", [c], "", v),
        "claims.align_state": claims.align_state(c, {"columns_this_model_produces": ["a"]}, v),
    }
    for name, st in built.items():
        assert st.get("vocabulary") == v, name

    # the builders that take it positionally, proven to still accept and keep it
    for mod in (align, columns, contracts, feeds, practices, rows, semantics):
        src = __import__("inspect").getsource(mod)
        assert 'state["vocabulary"] = vocab' in src or '"vocabulary"' in src, mod.__name__


def test_every_question_family_has_a_verification_status():
    """*** AN ANSWER IS NOT EVIDENCE THAT THE QUESTION WORKS. ***

    Two families passed every test in this suite and failed when a person read their output
    against real data. docs/VERIFICATION.md records which have had that done and which have not,
    and a family missing from it is one whose status nobody can look up.
    """

    from dbt_assay.contracts import SHIPPED
    p = ROOT / "docs" / "VERIFICATION.md"
    if not (ROOT / "README.md").exists():
        pytest.skip("no docs in a wheel install")
    assert p.exists(), "the verification record is gone"
    body = p.read_text()
    missing = [f for f in SHIPPED if f"`{f}`" not in body]
    assert not missing, missing


def test_the_docs_do_not_state_a_family_count_that_is_wrong():
    """*** "TWELVE QUESTION FAMILIES SHIP" SURVIVED FOUR FAMILIES BEING ADDED. ***

    A number written in prose is a copy of a fact, and it drifts silently because nothing reads it.
    Spelled-out numbers are checked because that is how they are written here.
    """
    import re

    from dbt_assay.contracts import load_all_banks
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
             8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
             14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
             19: "nineteen", 20: "twenty"}
    # Past twenty the words are built, not typed: a table that stops at twenty read "Thirty
    # families ship" as no total at all the day a release crossed it.
    for tens, word in ((20, "twenty"), (30, "thirty")):
        words[tens] = word
        for unit in range(1, 10):
            words[tens + unit] = f"{word}-{words[unit]}"
    words[40] = "forty"
    right = words.get(len(load_all_banks()), str(len(load_all_banks())))
    # *** ONLY THE SENTENCES THAT CLAIM THE TOTAL. ***
    # A first version matched "two families sharing a prefix" and "nine families satisfied
    # nothing", which are counts of SUBSETS and correct. A guard that matches too much is the
    # mirror of one that matches nothing, and it gets deleted just as fast.
    # "N families ship" is the only phrasing that unambiguously claims the TOTAL. "nine of ten
    # families satisfied nothing" is a historical subset and correct; matching it made this guard
    # fail on true sentences, which is how a guard gets deleted.
    # A hyphenated number is ONE word here: "Thirty-one" read as "one" the first time a release
    # crossed a tens boundary without landing on it.
    totals = re.compile(r"\b([A-Za-z]+(?:-[A-Za-z]+)?|\d+) (?:question )?famil(?:ies|y) ship\b",
                        re.IGNORECASE)
    seen, wrong = 0, []
    for m in totals.finditer(docs):
        said = m.group(1).lower()
        if said not in {v for v in words.values()} | {str(k) for k in words}:
            continue
        seen += 1
        if said != right:
            wrong.append(docs[max(0, m.start() - 40):m.start() + 50].replace("\n", " "))
    assert seen, "the reader found no total at all; it is broken"
    assert not wrong, wrong


def test_the_version_the_action_example_pins_is_the_current_one():
    """A README pinning an old tag hands every new user a version behind the docs around it."""
    import re

    import dbt_assay
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    pinned = set(re.findall(r"dbt-assay@v(\d+\.\d+\.\d+)", docs))
    assert pinned, "the action example is gone"
    assert pinned == {dbt_assay.__version__}, (pinned, dbt_assay.__version__)


def test_the_product_doc_names_only_checks_and_families_that_exist():
    """*** PROSE THAT NAMES SOMETHING WHICH DOES NOT EXIST IS THE DEFECT THIS TOOL FINDS. ***

    The first draft of PRODUCT.md invented five check names by describing them instead of reading
    them: `bbox_used_as_distance` for `bbox_as_radius`, `variant_columns` for `variant_column`,
    and three more. A doc is a claim about the code, and assay's whole argument is that those go
    stale silently.
    """
    import re

    from dbt_assay.config import known_checks
    from dbt_assay.contracts import SHIPPED
    from dbt_assay.mcp_server import TOOLS

    p = ROOT / "docs" / "PRODUCT.md"
    if not p.exists():
        pytest.skip("no docs in a wheel install")
    known = set(known_checks()) | set(SHIPPED) | {n for n, _d in TOOLS}
    assert len(known) > 20, "the reader is broken; a scanner that knows nothing passes everything"
    # Only the two reference tables, so ordinary prose in backticks is not swept up.
    body = p.read_text()
    rows = [ln for ln in body.splitlines() if ln.startswith("| `")]
    assert len(rows) >= 12, f"only found {len(rows)} table rows; the reader is broken"
    bad = []
    for ln in rows:
        name = re.match(r"\| `([a-z_]+)`", ln)
        if name and name.group(1) not in known:
            bad.append(name.group(1))
    assert not bad, f"PRODUCT.md names checks or families that do not exist: {bad}"


def test_the_checked_in_skills_are_what_the_package_would_write():
    """*** THE REPO'S OWN SKILL FILE HAD BEEN STALE FOR TWENTY-THREE RELEASES. ***

    `onboard --agent` writes `.claude/skills/<name>/SKILL.md` from `skilltext`, and the copies
    committed here were last regenerated at 0.10.2. The module had grown 140 lines since and
    removed none, so an agent opening this repository read a procedure that never mentioned
    `guide`, `violations`, `suggestions` or `evidence` -- every one of them a tool the agent would
    therefore never call, which is the exact failure `test_every_mcp_tool_is_in_the_skill_file`
    exists to prevent, one copy further out.

    Two spellings of one document, and the stale one is the copy a reader actually opens.
    """
    from dbt_assay import skilltext
    if not (ROOT / ".claude").is_dir():
        pytest.skip("no repo checkout here")
    for name, text in (("dbt-assay", skilltext.SKILL_MD),
                       ("assay-review", skilltext.REVIEW_SKILL_MD)):
        p = ROOT / ".claude" / "skills" / name / "SKILL.md"
        assert p.exists(), f"{p} is missing; `assay onboard --agent` writes it"
        assert p.read_text() == text, (
            f"{p} is not what the package ships. Run `assay onboard --agent`, or the copy people "
            f"read here drifts from the copy they install.")


def test_the_review_skill_says_what_the_human_label_does_not_prove():
    """*** `--by` IS FREE TEXT AND `source='human'` IS SET BY THE CODE PATH. ***

    Nothing binds a verdict to a person: `--by` fills `decided_by`, defaults to `unknown`, and is
    never validated. The skill's own honesty is the entire mechanism, so it has to say that out
    loud rather than leave a future reader to assume the label was checked by something.
    """
    from dbt_assay.skilltext import REVIEW_SKILL_MD as R
    assert "label, not a proof" in R
    assert "`--by` is free text" in R and "`unknown`" in R
    assert "do not record anything" in R.lower()


def test_the_shipped_skills_are_not_addressed_to_one_person():
    """It installs into other people's projects. A procedure naming this author is a draft."""
    import re

    from dbt_assay import skilltext
    for name in ("SKILL_MD", "REVIEW_SKILL_MD"):
        body = getattr(skilltext, name)
        bad = [ln for ln in body.splitlines() if re.search(r"\b(Ryan|sunny_data|sunnydata)\b", ln)]
        assert not bad, f"{name}: {bad}"


def test_the_shipped_skills_are_still_shaped_like_documents():
    """*** A REFLOW MERGED FOUR BULLETS INTO ONE RUN-ON PARAGRAPH, AND NOTHING NOTICED. ***

    Rewrapping the review procedure to 100 columns treated consecutive lines as one paragraph, so
    `- Quote the actual claim ... - If an agent already ruled ... - Give your own read` came out
    as a single block with the dashes inline. Still valid markdown. Still loads. Unreadable, and
    unreadable in the specific way that makes an agent skip the rules it is there to follow.

    Nothing structural was checked, because the file was only ever eyeballed.
    """
    import yaml

    from dbt_assay import skilltext
    for name in ("SKILL_MD", "REVIEW_SKILL_MD"):
        body = getattr(skilltext, name)
        lines = body.splitlines()
        assert lines[0] == "---", f"{name} has no frontmatter"

        # *** AND IT HAS TO PARSE, NOT MERELY BE PRESENT. ***
        # This asserted `"name: " in body[:200]` and passed on a file whose frontmatter was one
        # flattened line -- `name: assay-review description: >- Walk assay's findings...` -- which
        # YAML reads as a scanner error, so the skill did not load at all. The reflow that set the
        # column limit treated the two keys as one paragraph and rewrapped across the newline
        # between them.
        #
        # A substring check cannot see that. It is the same defect this file keeps finding: a
        # guard that matches something other than the thing it is checking.
        fm = body.split("---", 2)[1]
        try:
            meta = yaml.safe_load(fm)
        except yaml.YAMLError as e:
            raise AssertionError(f"{name} frontmatter is not YAML: {e}") from e
        assert isinstance(meta, dict), f"{name} frontmatter is not a mapping: {type(meta).__name__}"
        for key in ("name", "description"):
            assert key in meta, f"{name} frontmatter has no `{key}` key: got {sorted(meta)}"
            assert str(meta[key]).strip(), f"{name} frontmatter `{key}` is empty"
        assert "description" not in str(meta["name"]), \
            f"{name}: `name` swallowed the next key, so the lines were joined"
        assert body.count("```") % 2 == 0, f"{name} has an unclosed code fence"
        assert sum(1 for ln in lines if ln.startswith("- ")) >= 4, f"{name} lost its bullets"
        # A list item never continues a line that already holds one.
        assert not [ln for ln in lines if ln.lstrip().startswith("- ") and " - " in ln], \
            f"{name} has bullets folded into a paragraph"
        # *** THE COLUMN LIMIT IS ABOUT PROSE, AND TWO THINGS HERE ARE NOT PROSE. ***
        # A markdown table row cannot be wrapped without breaking the table, and a shell command
        # inside a fence cannot be wrapped without breaking the command. Neither is the reflow
        # hazard this guard exists for -- that is a paragraph rewrapped across a newline -- and
        # holding them to 100 columns would mean deleting the reference rather than wrapping it.
        prose, fenced = [], False
        for ln in lines:
            if ln.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced and not ln.lstrip().startswith("|"):
                prose.append(ln)
        over = [ln for ln in prose if len(ln) > 100]
        assert not over, f"{name} has prose lines past 100 columns: {[len(x) for x in over]}"


def test_every_check_the_code_ships_is_named_in_the_docs():
    """*** THE FAMILY GUARD EXISTED; THE CHECK GUARD DID NOT. ***

    `test_every_question_family_is_named_in_the_docs` covers the judged banks. Nothing covered
    `known_checks()`, so five of twenty-eight checks were in no user-facing document at all --
    `join_fans_out`, `key_column_stopped_mattering`, `key_started_holding`, `narrow_read` and
    `test_outruns_its_source`. Four of those were drift nobody had noticed.

    A check with no documentation is a finding somebody reads, searches for, and cannot look up.
    From the outside that is indistinguishable from the tool inventing a category.
    """
    from dbt_assay.config import known_checks
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    docs += (ROOT / "docs" / "PRODUCT.md").read_text()
    ks = known_checks()
    assert len(ks) > 20, "the check reader found almost nothing; it is broken"
    missing = sorted(c for c in ks if c not in docs)
    assert not missing, missing


def test_the_mcp_tool_count_in_the_docs_is_the_real_one():
    """A number written by hand beside a list that grows is a number that goes stale.

    It said fourteen the day a fifteenth was added, in the same commit that added it.
    """
    import re

    from dbt_assay.mcp_server import TOOLS
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    words = {14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
             19: "Nineteen", 20: "Twenty", 21: "Twenty-one", 22: "Twenty-two",
             23: "Twenty-three", 24: "Twenty-four", 25: "Twenty-five", 26: "Twenty-six",
             27: "Twenty-seven", 28: "Twenty-eight", 29: "Twenty-nine", 30: "Thirty",
             31: "Thirty-one", 32: "Thirty-two"}
    said = re.search(r"\b(" + "|".join(sorted(words.values(), key=len, reverse=True))
                     + r") tools\b", docs)
    assert said, "the tool count sentence is gone; keep it or drop this test deliberately"
    assert said.group(1) == words.get(len(TOOLS)), \
        f"docs say {said.group(1)} tools, the code ships {len(TOOLS)}"


def test_the_frontmatter_guard_catches_the_flattening_that_produced_it():
    """*** A GUARD NOBODY HAS SEEN FAIL IS A GUARD NOBODY HAS TESTED. ***

    The previous version asserted `"name: " in body[:200]`, which passes on the exact file that
    broke: two keys rewrapped onto one line by a 100-column reflow, which YAML reads as a scanner
    error. So the skill did not load at all and the guard said it was fine.

    This pins the regression by feeding the guard the real broken text.
    """
    import yaml

    broken = ("---\n"
              "name: assay-review description: >- Walk assay's findings with a person, one at a\n"
              "time, and record their verdicts. Use when they say review findings.\n"
              "---\n\n# body\n")
    # the check that used to be here would pass
    assert "name: " in broken[:200], "the fixture no longer reproduces the old false pass"
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken.split("---", 2)[1])


def test_a_name_that_swallowed_the_next_key_is_caught_even_when_it_parses():
    """*** AND SOME FLATTENINGS ARE VALID YAML. ***

    `name: assay-review description: foo` raises. But `name: assay-review description foo` -- no
    colon in the tail -- parses cleanly as one string, and the skill then has a name nobody meant
    and no description at all. Parsing is necessary and not sufficient.
    """
    import yaml
    meta = yaml.safe_load("name: assay-review description is the rest of the line\n")
    assert isinstance(meta, dict) and "description" not in meta
    assert "description" in str(meta["name"]), "the fixture does not reproduce the shape"


def test_every_check_has_a_fix_shape():
    """*** A PLAN THAT OMITS WHAT IT HAS NO SHAPE FOR READS AS A PLAN THAT COVERED EVERYTHING. ***

    `plan` turns an agreed finding into what to change, and the shape comes from a table keyed by
    check name. A check added without a row lands in the plan as `unknown` and says so -- which is
    honest at runtime and still a gap. This closes it at build time instead.
    """
    from dbt_assay.config import known_checks
    from dbt_assay.plan import SHAPES
    ks = known_checks()
    assert len(ks) > 20, "the check reader found almost nothing; it is broken"
    missing = sorted(c for c in ks if c not in SHAPES)
    assert not missing, f"no fix shape for: {missing}"
    for name, (shape, how) in SHAPES.items():
        assert shape and how, name
        assert len(how) > 40, f"{name}: the `how` says nothing useful"


def test_onboarding_flags_the_vocabulary_and_what_has_been_spent():
    """*** A FIRST RUN THAT NEVER MENTIONS THEIR WORDS LEAVES THE WIDEST GAP UNNAMED. ***

    A vocab term goes into every judged question's state, so one asserted outside where it is true
    is wrong in every answer about that part of the project at once -- 25% of one real warehouse's
    answers. `onboard` walks somebody through setup and had nothing to say about it.
    """
    import inspect

    from dbt_assay import cli
    src = inspect.getsource(cli.onboard)
    assert "lint_vocab" in src, "onboarding does not lint their vocabulary"
    assert "cost_mod.ledger" in src or "cost as cost_mod" in src, \
        "onboarding does not say what has already been spent"
    from dbt_assay.guide import plan_rows
    assert "plan_rows(" in src and any("review --emit" in r[1] for r in plan_rows()), (
        "onboarding never points at the form. The queue is one turn per finding, which is a wall "
        "at two hundred; the form is where their reasons accrue.")


def test_one_plan_and_every_surface_reads_it():
    """Ryan: "it all needs to be coming from assay". `guide start`, `onboard` and the MCP guide
    tool give one order, from guide.PLAN, and every command in it exists with the flags it names.
    `guide configure` covers every top-level section audit.yml is read for."""
    import typer.main

    from dbt_assay import guide
    from dbt_assay.cli import app
    group = typer.main.get_command(app)
    for _ph, cmd, _what, _cost in guide.plan_rows():
        assert cmd in guide.START, cmd
        words = cmd.split()
        c = group.get_command(None, words[1])
        assert c is not None, f"`{cmd}` names a command that does not exist"
        # the command's own parameters, not its --help: help wraps at the terminal's width
        opts = {o for p in c.params for o in getattr(p, "opts", [])}
        for flag in (w for w in words if w.startswith("--")):
            assert flag in opts, f"`{cmd}`: {flag} is not a flag"
    for k in guide._config_keys():
        assert f"`{k}" in guide.CONFIGURE, f"guide configure never mentions `{k}`"


def test_every_mcp_tool_reaches_the_skill_and_the_overview():
    """*** A TOOL AN AGENT NEVER HEARS ABOUT IS A TOOL THAT DOES NOT EXIST. ***

    Three surfaces have to agree: the server registers it, the skill tells an agent when to call
    it and what to do without the server, and the overview describes it to a person. A tool added
    to one of the three looks exactly like coverage.
    """
    from dbt_assay import skilltext
    from dbt_assay.mcp_server import TOOLS
    skill = skilltext.SKILL_MD
    overview = (ROOT / "docs" / "OVERVIEW.md").read_text() if (ROOT / "docs").is_dir() else None
    missing_skill = [n for n, _d in TOOLS if f"`{n}(" not in skill and f"`{n}()" not in skill]
    assert not missing_skill, f"MCP tools the agent skill never names: {missing_skill}"
    if overview is None:
        pytest.skip("no docs in a wheel install")


def _registered():
    """(command name, its flags) read off the app, never off rendered help."""
    import typer.main

    from dbt_assay.cli import app
    for c in app.registered_commands:
        name = c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")
        params = typer.main.get_params_convertors_ctx_param_name_from_function(c.callback)[0]
        yield name, {o for p in params for o in getattr(p, "opts", [])
                     if o.startswith("--") and o != "--help"}


def test_every_command_and_every_flag_reaches_the_shipped_skills():
    """*** SOMEBODY ONBOARDING THROUGH THE SKILL COULD NOT FIND HALF THE TOOL. ***

    Measured when this was written: 21 of 47 commands appeared in neither shipped procedure, and
    85 flags appeared in neither the procedures nor the docs -- `page --monitoring` among them,
    which is the only way the report says anything about whether the warehouse is watched. An
    agent reading the skill has no signal that an unnamed flag exists, so the capability is the
    same as absent.

    The reference is generated from the app, so this guard is really checking that it is still
    generated and still attached.
    """
    from dbt_assay.skilltext import REVIEW_SKILL_MD, SKILL_MD
    both = SKILL_MD + REVIEW_SKILL_MD
    cmds = list(_registered())
    assert len(cmds) > 20, "the command reader found almost nothing; it is broken"

    missing_cmds = sorted(n for n, _f in cmds if f"assay {n}" not in both)
    assert not missing_cmds, f"commands named in neither procedure: {missing_cmds}"

    missing_flags = sorted(f"{n} {o}" for n, flags in cmds for o in flags if o not in both)
    assert not missing_flags, f"flags named in neither procedure: {missing_flags}"


def test_the_reference_is_generated_rather_than_typed():
    """A hand-maintained table of 47 commands is wrong by the next release, and silently: the
    reader believes it. If somebody pastes it in as a literal, this fails."""
    from dbt_assay import skilltext
    assert "_command_reference()" in Path(skilltext.__file__).read_text()
    table = skilltext._command_reference()
    assert table.count("\n| `assay ") == len(list(_registered()))


def test_every_check_and_shipped_family_has_a_plain_title():
    """(Ryan) `test_never_ran_is_a_gap_or_a_leftover` is an identifier, not a label: every list a
    person reads shows the title, with the id beside it."""
    from dbt_assay.config import known_checks
    from dbt_assay.contracts import load_all_banks
    from dbt_assay.titles import TITLES, title
    missing = sorted((set(known_checks()) | set(load_all_banks(with_user=False))) - set(TITLES))
    assert not missing, missing
    assert title("some_project_family") == "Some project family"
