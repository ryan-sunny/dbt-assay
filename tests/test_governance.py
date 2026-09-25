"""*** A CLIENT'S DATA TEAM ASKS ONE QUESTION FIRST: WHAT DO YOU SEND, AND TO WHOM. ***"""
import pytest
from fakejev import FakeJev

from dbt_assay import governance, jev
from dbt_assay.config import Config
from dbt_assay.states import Recipe
from dbt_assay.store import Store

Q = {"desc": {"type": "choice", "criteria": {"a": {"what": "x"}, "b": {"what": "y"}}}}


@pytest.fixture(autouse=True)
def _reset():
    governance.set_policy({})
    yield
    governance.set_policy({})


def test_metadata_only_refuses_row_values_before_anything_is_sent(tmp_path):
    Config.from_dict({"governance": {"metadata_only": True}})
    s = Store(str(tmp_path / "s.duckdb"))
    client = FakeJev()
    rows = Recipe("failing_row", "model.p.m::row::0", {"i": 0}, {"row": {"email": "a@b.c"}})
    with pytest.raises(governance.RowValuesWithheld):
        jev.decide(s, client, rows, Q, prompt_version="v1")
    with pytest.raises(governance.RowValuesWithheld):
        jev.prefetch(s, client, [(rows, Q, "v1", None)] * 2, workers=4)
    assert client.calls == 0
    # metadata is still asked about
    meta = Recipe("subject", "model.p.m", {"uid": "m"}, {"sql": "select 1"})
    jev.decide(s, client, meta, Q, prompt_version="v1")
    assert client.calls == 1
    s.close()


def test_what_leaves_says_so_in_each_mode():
    said = " ".join(w + " " + h for w, h in governance.what_leaves())
    assert "feeds" in said and "adjudicate" in said and "metadata_only" in said
    governance.set_policy({"metadata_only": True})
    said = " ".join(w + " " + h for w, h in governance.what_leaves())
    assert "never" in said
