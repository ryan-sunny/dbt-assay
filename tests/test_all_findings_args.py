"""The single findings stream reaches every surface WITH the store.

*** FIVE CALLERS PASSED THE STORE AS THE THRESHOLD. ***
`plan`, `suggest`, `review --emit`, `read` and the resolved-cluster count called
`all_findings(..., entries, store, threshold)` against a signature of `(..., entries, threshold,
store)`. The key-change comparison raised on a float, a bare `except` took that for an old store,
and `key_stopped_holding` never reached any of them. Nothing said so, because the except was
written to be quiet about exactly the case it could not tell apart from this one.
"""
from __future__ import annotations

import time

import pytest
from typer.testing import CliRunner

from dbt_assay import live, probe
from dbt_assay.cli import _load, app
from dbt_assay.store import Store


def _key_that_stopped_holding(path) -> None:
    s = Store(str(path))
    probe.write(s, [probe.Observation("main.t", "id", 100, 100, 100, "unique")])
    time.sleep(0.005)
    probe.write(s, [probe.Observation("main.t", "id", 140, 140, 100, "has_duplicates")])
    s.close()


def test_threshold_and_store_cannot_be_passed_by_position(project_dir, tmp_path):
    project, digests, _f, schema, _s = _load(project_dir, None)
    with pytest.raises(TypeError):
        live.all_findings(project, digests, schema, None, 0.8)


def test_the_review_form_carries_a_key_that_stopped_holding(project_dir, tmp_path):
    """The form builds a card from every finding, so it is where the drop was visible."""
    store = tmp_path / "s.duckdb"
    _key_that_stopped_holding(store)
    form = tmp_path / "form.html"
    r = CliRunner().invoke(app, ["review", "--emit", str(form), "-t", str(project_dir),
                                 "--store", str(store), "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "key_stopped_holding" in form.read_text()
