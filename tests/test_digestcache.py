"""Each model's parse, kept between commands, keyed by exactly what produced it."""
import dataclasses

from dbt_assay import digestcache
from dbt_assay.parse import digest

SQL = "select a.id, count(*) as n from raw.orders a join raw.lines b on a.id = b.order_id group by 1"


def _cache(tmp_path, monkeypatch, project="p", dialect="duckdb"):
    monkeypatch.setattr(digestcache, "cache_dir", lambda: tmp_path / "assay" / "digests")
    monkeypatch.delenv("ASSAY_NO_DIGEST_CACHE", raising=False)
    return digestcache.DigestCache(project, dialect)


def test_a_second_load_reads_the_parse_and_it_is_the_same(tmp_path, monkeypatch):
    first = _cache(tmp_path, monkeypatch)
    made = first.get(SQL, "m", lambda: digest(SQL, "m"))
    first.save()
    assert first.hits == 0 and first.path.exists()

    second = _cache(tmp_path, monkeypatch)
    got = second.get(SQL, "m", lambda: (_ for _ in ()).throw(AssertionError("parsed again")))
    assert second.hits == 1
    assert dataclasses.asdict(got) == dataclasses.asdict(digest(SQL, "m")) == \
        dataclasses.asdict(made)


def test_changed_sql_a_new_name_or_new_code_is_parsed(tmp_path, monkeypatch):
    c = _cache(tmp_path, monkeypatch)
    c.get(SQL, "m", lambda: digest(SQL, "m"))
    c.save()
    again = _cache(tmp_path, monkeypatch)
    calls = []
    for sql, name in ((SQL + " ", "m"), (SQL, "m2")):
        again.get(sql, name, lambda sql=sql, name=name: calls.append(1) or digest(sql, name))
    assert len(calls) == 2 and again.hits == 0
    # another dialect is another file
    assert _cache(tmp_path, monkeypatch, dialect="snowflake").path != c.path
    # and the code that reads the SQL is in the key
    monkeypatch.setattr(digestcache, "code_hash", lambda: "changed")
    assert _cache(tmp_path, monkeypatch).path != c.path


def test_what_a_command_adds_to_a_digest_is_not_kept(tmp_path, monkeypatch):
    c = _cache(tmp_path, monkeypatch)
    d = c.get(SQL, "m", lambda: digest(SQL, "m"))
    d.output_columns.append("added_by_derive_columns")
    c.save()
    got = _cache(tmp_path, monkeypatch).get(SQL, "m", lambda: None)
    assert "added_by_derive_columns" not in got.output_columns
    # and two asks of one key are two objects
    c2 = _cache(tmp_path, monkeypatch)
    assert c2.get(SQL, "m", lambda: None) is not c2.get(SQL, "m", lambda: None)


def test_the_folder_is_beside_the_lean_toolchain(monkeypatch):
    monkeypatch.setenv("ASSAY_CACHE", "/x/.cache")
    from pathlib import Path
    assert digestcache._real_cache_dir() == Path("/x/.cache/assay/digests")


def test_an_edited_models_old_parse_leaves_and_the_switch_turns_it_off(tmp_path, monkeypatch):
    import pickle
    c = _cache(tmp_path, monkeypatch)
    c.get(SQL, "m", lambda: digest(SQL, "m"))
    c.save()
    c = _cache(tmp_path, monkeypatch)
    c.get(SQL + " where 1=1", "m", lambda: digest(SQL + " where 1=1", "m"))
    c.save()
    assert len(pickle.loads(c.path.read_bytes())) == 1
    monkeypatch.setenv("ASSAY_NO_DIGEST_CACHE", "1")
    off = digestcache.DigestCache("p", "duckdb")
    calls = []
    off.get(SQL + " where 1=1", "m", lambda: calls.append(1) or digest(SQL, "m"))
    assert calls == [1]


def test_an_unwritable_cache_costs_speed_not_a_result(tmp_path, monkeypatch):
    (tmp_path / "assay").write_text("a file where the folder would go")
    c = _cache(tmp_path, monkeypatch)
    assert c.get(SQL, "m", lambda: digest(SQL, "m")).ok
    c.save()                                   # no raise
