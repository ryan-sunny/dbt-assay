"""`onboard` and `check` count the same checks the same way.

*** THE ONBOARDING PATH OPENED BY CONTRADICTING ITSELF. ***
Reported from the field (25.13): on a fresh store with no config, `onboard` said 208
`column_has_no_description` and 37 `arbitrary_pick`; `check` said 129 and 33. Two causes, one in
each direction: `check`'s stream dropped the schema, so a column known only from the SQL was
invisible to it, and `onboard` built its own list and skipped the dedupe. Both now read one stream.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from typer.testing import CliRunner

from dbt_assay import live
from dbt_assay.cli import _load, app

runner = CliRunner()


def test_a_column_known_only_from_the_sql_is_in_the_stream(project_dir):
    """The fixture declares no columns anywhere, so every column is DERIVED."""
    project, digests, _f, schema, _s = _load(project_dir, None)
    fs = [f for f in live.all_findings(project, digests, schema)
          if f.check == "column_has_no_description"]
    assert fs, "a model whose columns come from its SQL still has undocumented columns"


def test_onboard_and_check_print_the_same_counts(project_dir, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ob = runner.invoke(app, ["onboard", "-t", str(project_dir), "--store",
                             str(tmp_path / "ob.duckdb"), "--config", str(cfg), "--no-judge"])
    assert ob.exit_code == 0, ob.output
    section = ob.output.split("3. what it found")[1].split("4. what only judgment")[0]
    row = re.compile(r"^\s*(\d+)\s+([a-z_]+)\b", re.MULTILINE)
    onboard_counts = {m.group(2): int(m.group(1)) for m in row.finditer(section)}
    ck = runner.invoke(app, ["check", "-t", str(project_dir), "--store",
                             str(tmp_path / "ck.duckdb"), "--config", str(tmp_path / "empty"),
                             "--json"])
    check_counts = Counter(f["check"] for f in json.loads(ck.stdout)["findings"])
    assert onboard_counts, section
    for name, n in onboard_counts.items():
        assert check_counts[name] == n, (name, n, check_counts[name])
