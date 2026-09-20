"""*** THE DIALECT WAS A FLAG NOBODY KNEW TO PASS, AND FORGETTING IT LOST REAL FINDINGS. ***

Measured on three cloned public projects, correct dialect vs the old `duckdb` default:

| project             | parse failures | findings lost |
|---------------------|----------------|---------------|
| basedosdados (BQ)   | 12 -> 128      | 2, incl. a `test_cannot_fail` |
| snowflake_monitoring| 2 -> 4         | 1 |
| elementary (pg)     | 12 -> 12       | 0 |

Nothing spurious appeared; the failure was pure silent UNDER-reporting, which is worse than a false
positive because it looks exactly like a clean project.
"""
import sqlglot

from dbt_assay.manifest import ADAPTER_DIALECT, Project


def test_every_mapped_dialect_is_one_sqlglot_actually_has():
    """A typo here reads as 'unknown dialect' and sqlglot falls back, silently, to generic SQL."""
    for dialect in set(ADAPTER_DIALECT.values()):
        sqlglot.parse_one("select 1", dialect=dialect)      # raises on an unknown dialect


def test_the_dialect_comes_from_the_manifest_not_from_a_default(tmp_path):
    import json
    for adapter, expected in [("snowflake", "snowflake"), ("bigquery", "bigquery"),
                              ("fabric", "tsql"), ("duckdb", "duckdb")]:
        d = tmp_path / adapter
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "metadata": {"project_name": "p", "dbt_version": "1.0.0", "adapter_type": adapter},
            "nodes": {}, "sources": {}, "parent_map": {}, "child_map": {}}))
        assert Project.load(d).dialect == expected, adapter


def test_a_manifest_with_no_adapter_still_parses_as_something(tmp_path):
    import json
    d = tmp_path / "bare"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p"}, "nodes": {}, "sources": {},
        "parent_map": {}, "child_map": {}}))
    p = Project.load(d)
    assert p.adapter_type == ""
    assert p.dialect == "duckdb"        # and `onboard` says out loud that it assumed this


def test_an_explicit_dialect_beats_the_manifest(tmp_path):
    import json
    d = tmp_path / "over"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "adapter_type": "bigquery"},
        "nodes": {}, "sources": {}, "parent_map": {}, "child_map": {}}))
    p = Project.load(d)
    assert p.dialect == "bigquery"
    p.dialect_override = "snowflake"
    assert p.dialect == "snowflake"


def test_no_command_declares_duckdb_as_its_dialect_default():
    """*** THIS IS THE REGRESSION, AND IT IS THE WHOLE TEST FILE'S REASON. ***

    Any command that defaults `--dialect` to a literal parses every other warehouse as DuckDB for
    anyone who does not know the flag exists. The default must be None so the manifest decides.
    """
    import typer.main

    from dbt_assay import cli
    bad = []
    for command in cli.app.registered_commands:
        for param in typer.main.get_params_convertors_ctx_param_name_from_function(
                command.callback)[0]:
            if param.name == "dialect" and param.default is not None:
                bad.append(f"{command.callback.__name__}={param.default!r}")
    assert not bad, bad


def test_a_library_default_cannot_disagree_with_the_project():
    """`derive_columns`, `joined_pairs` and `classify` take a project, so they can read its dialect
    rather than assume one. Three CLI call sites dropped the flag before this."""
    import inspect

    from dbt_assay import align, infer, provenance
    for fn in (infer.derive_columns, align.joined_pairs, provenance.classify):
        src = inspect.getsource(fn)
        assert 'dialect = dialect or getattr(project, "dialect", "duckdb")' in src, fn.__name__
        assert inspect.signature(fn).parameters["dialect"].default is None, fn.__name__
