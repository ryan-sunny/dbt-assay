"""The question banks are DATA, and data is what packaging silently drops."""
from pathlib import Path

import pytest

import dbt_assay
from dbt_assay.contracts import load_all_banks


def test_the_banks_load_from_wherever_the_package_is_installed():
    """A packaging mistake ships a tool whose bank is empty, and that fails at import rather
    than at install."""
    banks = load_all_banks()
    assert len(banks) >= 12
    for name, q in banks.items():
        assert q.get("type") in ("choice", "noul", "score"), name
        assert q.get("prompt_version"), name
        assert q.get("instructions"), name


def test_every_bank_file_lives_inside_the_package():
    d = Path(dbt_assay.__file__).parent / "questions"
    assert d.is_dir()
    ymls = sorted(p.name for p in d.glob("*.yml"))
    assert len(ymls) >= 6, ymls


def _pyproject() -> dict | None:
    """*** tomllib IS STDLIB ONLY FROM 3.11, AND assay SUPPORTS 3.10. ***

    The first CI run failed on 3.10 for exactly this: the PACKAGE works there, my tests did not.
    Skipping is right; raising the floor would be dropping real support to spare a test.
    """
    import pytest
    tomllib = pytest.importorskip("tomllib",
                                  reason="stdlib from 3.11; the package itself does not need it")
    from pathlib import Path

    import dbt_assay
    pj = Path(dbt_assay.__file__).parent.parent.parent / "pyproject.toml"
    if not pj.exists():
        return None                 # installed as a wheel; nothing to compare against
    with pj.open("rb") as fh:
        return tomllib.load(fh)


def test_the_version_is_a_single_source_of_truth():
    data = _pyproject()
    if data is None:
        return
    assert data["project"]["version"] == dbt_assay.__version__


def test_every_choice_and_score_can_be_built_without_a_key():
    """The bank is inspectable with no provider configured at all."""
    from dbt_assay.jev import choice, noul, score
    for name, q in load_all_banks().items():
        if q["type"] == "choice":
            assert choice(q["instructions"], q["criteria"])["type"] == "choice", name
        elif q["type"] == "score":
            assert len(q["criteria"]) >= 2, name
            assert score(q["instructions"], q["criteria"])["type"] == "score", name
        else:
            assert noul(q["instructions"])["type"] == "noul", name


def test_the_declared_sqlglot_floor_is_not_a_claim_assay_cannot_keep():
    """`>=25` was a lie: `exp.RegexpFullMatch` does not exist before 28, so assay failed to parse
    ANY model there. CI pins the floor and the latest on every push."""
    data = _pyproject()
    if data is None:
        return
    floor = next(d for d in data["project"]["dependencies"] if d.startswith("sqlglot"))
    assert ">=28" in floor, floor


def test_an_absent_node_type_costs_one_check_not_every_parse():
    """Referencing a node type unconditionally makes assay fail on every model, which is far worse
    than losing one dialect check."""
    import inspect

    from dbt_assay import parse
    src = inspect.getsource(parse)
    assert 'getattr(exp, "RegexpFullMatch", None)' in src


def test_the_action_does_not_gate_by_default():
    """Nothing should fail a build until its question has recorded verdicts, and assay refuses to
    anyway. An action that gates out of the box would be muted within a week."""
    from pathlib import Path

    import yaml

    import dbt_assay
    root = Path(dbt_assay.__file__).parent.parent.parent
    p = root / "action.yml"
    if not p.exists():
        return
    a = yaml.safe_load(p.read_text())
    assert a["inputs"]["fail-on-findings"]["default"] == "false"
    assert "comment" in a["inputs"]


def test_the_action_captures_an_exit_code_rather_than_reading_it_through_a_pipe():
    """`cmd | head` reports head's status, so a gate written that way passes whatever happened."""
    from pathlib import Path

    import dbt_assay
    root = Path(dbt_assay.__file__).parent.parent.parent
    p = root / "action.yml"
    if not p.exists():
        return
    body = p.read_text()
    assert 'echo "code=$?" >> "$GITHUB_OUTPUT"' in body


def test_an_unwritable_store_is_an_error_message_not_a_traceback():
    """*** A READ-ONLY WORKING DIRECTORY IS AN ORDINARY CI SETUP. ***

    It surfaced as a full traceback through the store's own internals, which reads as "assay is
    broken" rather than "this path is wrong".
    """
    import pytest

    from dbt_assay.store import Store, StoreUnwritable
    with pytest.raises(StoreUnwritable) as e:
        Store("/nope/definitely/not/writable.duckdb")
    assert "--store" in str(e.value)


def test_the_console_script_points_at_the_handler_and_not_at_the_app():
    """Pointing at `app` directly means nothing catches the expected failures."""
    data = _pyproject()
    if data is None:
        return
    assert data["project"]["scripts"]["assay"] == "dbt_assay.cli:main"


def test_the_readme_python_snippet_is_the_question_assay_actually_ships():
    """*** A README THAT DRIFTS FROM THE CODE IS THE DEFECT THIS TOOL EXISTS TO FIND. ***

    The snippet is presented as "the real question assay ships", so it is executed and compared
    rather than trusted. Three option lists in the overview had already drifted before anyone
    checked them once.
    """
    import re
    from pathlib import Path

    import dbt_assay
    from dbt_assay.contracts import QUESTIONS
    from dbt_assay.jev import noul  # noqa: F401  (used by eval)

    root = Path(dbt_assay.__file__).parent.parent.parent
    readme = root / "README.md"
    if not readme.exists():
        return                                          # installed as a wheel
    m = re.search(r"```python\n(.*?)```", readme.read_text(), re.DOTALL)
    assert m, "the README no longer carries the worked example"
    q = eval(m.group(1).split("\n", 1)[1].strip())
    real = QUESTIONS["description_contradicts_the_code"]
    assert q["instructions"] == real["instructions"]["question"].strip()
    assert q["criteria"]["true"] == real["criteria"]["true"]["what"]
    assert q["criteria"]["false"] == real["criteria"]["false"]["what"]


def test_the_diagram_the_readme_points_at_exists_and_has_no_external_refs():
    """GitHub sanitizes SVG. A diagram with a script or a remote href renders as nothing."""
    import xml.etree.ElementTree as ET
    from pathlib import Path

    import dbt_assay
    root = Path(dbt_assay.__file__).parent.parent.parent
    svg = root / "docs" / "how-jev-fits.svg"
    if not (root / "README.md").exists():
        return
    assert svg.exists(), "README points at a diagram that is not in the repo"
    ET.parse(svg)                                       # raises on malformed XML
    body = svg.read_text()
    for forbidden in ("<script", "xlink:href", "<foreignObject", "<image"):
        assert forbidden not in body, forbidden
    assert 'src="docs/how-jev-fits.svg"' in (root / "README.md").read_text()


def test_the_release_script_and_the_ci_guard_read_the_same_version():
    """*** EIGHT VERSIONS WERE BUMPED, PUSHED, AND NEVER TAGGED. ***

    0.25 through 0.45 exist as commits and as nothing else. The release is tag-driven, so bumping
    `__version__` and pushing felt like releasing and published nothing -- and PyPI only shows the
    LAST successful upload, so the gap was invisible from the outside until somebody looked. A
    thing that stopped happening, reported the same way as a thing that is fine, which is the
    defect this project exists to find.

    Two spellings of "what version is this" is how they drift apart again, so both read it from
    the package.
    """
    from pathlib import Path

    import dbt_assay
    root = Path(dbt_assay.__file__).parent.parent.parent
    script = root / "scripts" / "release.sh"
    ci = root / ".github" / "workflows" / "ci.yml"
    if not script.exists():
        pytest.skip("no repo checkout here")
    read = 'import dbt_assay; print(dbt_assay.__version__)'
    assert read in script.read_text(), "the release script invents its own version"
    assert read in ci.read_text(), "the CI guard invents its own version"


def test_the_release_script_refuses_the_ways_a_release_goes_wrong():
    """It refuses rather than guesses. Each of these was a real way to publish something wrong."""
    from pathlib import Path

    import dbt_assay
    script = Path(dbt_assay.__file__).parent.parent.parent / "scripts" / "release.sh"
    if not script.exists():
        pytest.skip("no repo checkout here")
    body = script.read_text()
    assert "set -euo pipefail" in body
    for guard, why in (
        ("git status --porcelain", "a dirty tree would publish uncommitted work"),
        ("already tagged", "PyPI is append-only; a reused version cannot be corrected"),
        ("uv run pytest -q", "the suite is a gate, not a suggestion"),
        ("uv run ruff check", "lint is a gate too"),
        ("pyproject says", "two files carry the version and they can disagree"),
    ):
        assert guard in body, f"the release script does not refuse: {why}"
    # the commit and the tag leave together or not at all
    assert 'git push origin HEAD "v$VER"' in body, (
        "pushing the commit separately from the tag is how main ends up carrying a version "
        "nothing published")


def test_ci_fails_on_main_when_the_version_has_no_tag():
    from pathlib import Path

    import dbt_assay
    ci = Path(dbt_assay.__file__).parent.parent.parent / ".github" / "workflows" / "ci.yml"
    if not ci.exists():
        pytest.skip("no repo checkout here")
    body = ci.read_text()
    assert "released:" in body, "the tag guard is gone"
    assert "github.ref == 'refs/heads/main'" in body, "the guard would fire on every branch"
    assert "there is no v$VER tag" in body
    assert "scripts/release.sh" in body, "the failure does not say how to fix it"
