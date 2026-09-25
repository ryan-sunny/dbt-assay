dbt-labs/jaffle_shop_duckdb at 36bde6c (Apache-2.0, LICENSE here), built with dbt 1.11 and
dbt-duckdb (`dbt build && dbt docs generate`): the compiled target only. It is what a new user runs
first, and `select * from final` is dbt's house style, so every test sits on a star.
