"""A confirmed judgment graduates into the project and is not asked again (Ryan: "bless my
warehouse"). A relationships test states a foreign key; the role question about that column is
then not asked, and the inventory reads the role as declared."""
from types import SimpleNamespace

from typer.testing import CliRunner

from dbt_assay import columns
from dbt_assay.cli import app


def test_a_relationships_test_states_the_role_and_a_unique_test_does_not():
    project = SimpleNamespace(tests=[
        SimpleNamespace(tests_model="model.p.orders", column="customer_id", kind="relationships"),
        SimpleNamespace(tests_model="model.p.orders", column="order_id", kind="unique")])
    assert columns.declared_roles(project) == {("model.p.orders", "customer_id"): "foreign_key"}


def test_the_columns_command_does_not_ask_what_the_project_states(project_dir, monkeypatch):
    from dbt_assay.manifest import Project
    uid = next(iter(Project.load(project_dir).models))

    class Stated(dict):
        def __contains__(self, k):
            return k[0] == uid
    monkeypatch.setattr(columns, "declared_roles", lambda project: Stated())
    r = CliRunner().invoke(app, ["columns", "--target", str(project_dir), "--print-state"],
                           env={"COLUMNS": "200"})
    assert "not asked: the project states their role" in r.output, r.output
