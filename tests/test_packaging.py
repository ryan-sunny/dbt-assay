"""The question banks are DATA, and data is what packaging silently drops."""
from pathlib import Path

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


def test_the_version_is_a_single_source_of_truth():
    import tomllib
    root = Path(dbt_assay.__file__).parent.parent.parent
    pj = root / "pyproject.toml"
    if not pj.exists():
        return                      # installed as a wheel; nothing to compare against
    with pj.open("rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    assert declared == dbt_assay.__version__


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
    from pathlib import Path

    import tomllib

    import dbt_assay
    root = Path(dbt_assay.__file__).parent.parent.parent
    pj = root / "pyproject.toml"
    if not pj.exists():
        return
    with pj.open("rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    floor = next(d for d in deps if d.startswith("sqlglot"))
    assert ">=28" in floor, floor


def test_an_absent_node_type_costs_one_check_not_every_parse():
    """Referencing a node type unconditionally makes assay fail on every model, which is far worse
    than losing one dialect check."""
    import inspect

    from dbt_assay import parse
    src = inspect.getsource(parse.digest)
    assert 'getattr(exp, "RegexpFullMatch", None)' in src
