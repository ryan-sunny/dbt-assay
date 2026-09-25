"""schemapatch edits a model's entry in its own yml by lines, keeping every comment, and adds
only what is missing (patch.py: a second entry for a model is a dbt error)."""
import yaml

from dbt_assay.schemapatch import Edit, apply, new_entry

YML = """version: 2

models:
  - name: customers   # the customer dimension
    description: One row per customer.

    columns:
      - name: customer_id
        description: The key.
        tests:
          - unique

      - name: first_name

  - name: orders
    columns:
      - name: order_id
"""


def test_it_adds_what_is_missing_inside_the_right_entry_and_keeps_the_rest():
    out, refused = apply(YML, "customers", [
        Edit(column="customer_id", description="ignored: one is there", tests=["unique",
                                                                               "not_null"]),
        Edit(column="first_name", description="Given name, as the customer typed it."),
        Edit(column="email", description="Contact address: PII.", tests=["not_null"])])
    assert not refused
    assert "# the customer dimension" in out and out.count("- name: customers") == 1
    d = yaml.safe_load(out)
    cols = {c["name"]: c for c in d["models"][0]["columns"]}
    assert cols["customer_id"]["description"] == "The key."                # kept, not replaced
    assert cols["customer_id"]["tests"] == ["unique", "not_null"]
    assert cols["first_name"]["description"] == "Given name, as the customer typed it."
    assert cols["email"] == {"name": "email", "description": "Contact address: PII.",
                             "data_tests": ["not_null"]}
    assert d["models"][1] == {"name": "orders", "columns": [{"name": "order_id"}]}


def test_a_model_description_and_a_missing_entry():
    out, refused = apply(YML, "orders", [Edit(description="One row per order.")])
    assert yaml.safe_load(out)["models"][1]["description"] == "One row per order."
    _out, refused = apply(YML, "payments", [Edit(description="x")])
    assert refused and "no entry" in refused[0]
    new = yaml.safe_load(new_entry("payments", [Edit(description="One row per payment."),
                                                Edit(column="id", tests=["unique"])]))
    assert new["models"][0]["columns"][0]["data_tests"] == ["unique"]
