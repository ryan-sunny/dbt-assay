"""`config_comment_contradicts_the_store`: audit.yml's own numbers, read against the store."""
from dbt_assay.cli import _record_one_verdict
from dbt_assay.selfaudit import claims, config_findings
from dbt_assay.store import Store

YML = """\
questions:
  # 38 of 38 agreed. The most reliable check here.
  test_cannot_fail:
    action: queue

  # 24 ruled, all agreed.
  arbitrary_pick:
    action: queue

  # 0 of 10 agreed before 0.15.0 and 0.21.1.
  hop_multiplies_rows:
    action: queue

  # A parser decided it, so it may gate immediately -- except nothing gates until a question
  # clears min_adjudications, which none do.
  duckdb_full_match:
    action: queue
"""


def _store(tmp_path, n_tcf: int, n_pick: int):
    s = Store(str(tmp_path / "s.duckdb"))
    for i in range(n_tcf):
        _record_one_verdict(s, f"model.p.m{i}", "test_cannot_fail", "agree", "", "r", "me")
    for i in range(n_pick):
        _record_one_verdict(s, f"model.p.p{i}", "arbitrary_pick", "agree", "", "r", "me")
    return s


def test_the_claims_are_read_with_the_question_they_sit_above():
    got = {(c.question, c.kind, c.numbers) for c in claims(YML)}
    assert ("test_cannot_fail", "of", (38, 38)) in got
    assert ("arbitrary_pick", "all", (24,)) in got
    assert ("duckdb_full_match", "none_clear", ()) in got
    assert not any(c.question == "hop_multiplies_rows" for c in claims(YML)), \
        "a claim dated to a version is history, not a claim about now"


def test_a_true_comment_is_not_a_finding(tmp_path):
    (tmp_path / "audit.yml").write_text(
        YML.replace("38 of 38", "3 of 3").replace("24 ruled", "2 ruled"))
    s = _store(tmp_path, 3, 2)
    assert config_findings(str(tmp_path), s) == []
    s.close()


def test_a_drifted_count_and_a_cleared_floor_are_findings(tmp_path):
    (tmp_path / "audit.yml").write_text(YML)
    s = _store(tmp_path, 90, 24)
    fs = config_findings(str(tmp_path), s)
    says = {f.evidence["says"]: f for f in fs}
    assert "38 of 38 agreed" in says
    assert "90 of 90 agreed" in says["38 of 38 agreed"].detail
    assert any("none do" in k for k in says), "test_cannot_fail has cleared the floor of 20"
    assert all(f.file.endswith("audit.yml") and f.evidence["line"] for f in fs)
    ids = {f.id for f in fs}
    _record_one_verdict(s, "model.p.extra", "test_cannot_fail", "agree", "", "r", "me")
    assert {f.id for f in config_findings(str(tmp_path), s)} == ids, \
        "the same stale comment stays the same finding as the store moves"
    s.close()


def test_check_reports_it(project_dir, tmp_path):
    import json

    from typer.testing import CliRunner

    from dbt_assay.cli import app
    (tmp_path / "audit.yml").write_text(YML)
    s = _store(tmp_path, 90, 24)
    s.close()
    r = CliRunner().invoke(app, ["check", "-t", str(project_dir), "--config", str(tmp_path),
                                 "--store", str(tmp_path / "s.duckdb"), "--json"])
    assert any(f["check"] == "config_comment_contradicts_the_store"
               for f in json.loads(r.output)["findings"])
