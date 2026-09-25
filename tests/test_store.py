"""The store: what every surface reads from."""
from dbt_assay.store import Store


def test_a_run_is_stamped_in_utc_whatever_the_machine_s_zone(tmp_path):
    """sunny-data feedback L7: the box wrote UTC and the laptop its local time, so a later laptop
    run sorted before the box's. Stored times are UTC on every machine now."""
    import os
    import time
    from datetime import datetime, timezone

    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/Denver"
    time.tzset()
    try:
        s = Store(str(tmp_path / "s.duckdb"))
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        s.con.execute("create table t (x timestamp)")
        s.con.execute("insert into t values (?), (now())", [datetime.now(timezone.utc)])
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        for (x,) in s.con.execute("select x from t").fetchall():
            assert before <= x <= after, (before, x, after)
        s.close()
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
