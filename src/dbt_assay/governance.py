"""What leaves the network, said once, and a mode that never sends a row value.

*** A CLIENT'S DATA TEAM ASKS ONE QUESTION FIRST: WHAT DO YOU SEND, AND TO WHOM. *** (Ryan,
2026-09-25) The judged tier sends a state per question to the model provider. Most states are
metadata: compiled SQL, names, descriptions, the project's own prose, and counts. Two carry row
values read out of the warehouse: a feed's sample (`assay feeds`) and a failing row dbt stored for
a test (`assay adjudicate`). `governance: {metadata_only: true}` in audit.yml refuses those two at
the one door every judged question goes through (`jev.decide` / `jev.prefetch`), before anything
is sent; the commands built on them say so and stop.

The failing-row samples on the review form are read through the project's own dbt and written
into the local HTML file. They are not sent anywhere.
"""
from __future__ import annotations

# The state builders whose states carry row values read from the warehouse.
ROW_BUILDERS = frozenset({"feed", "failing_row"})

POLICY: dict = {}                   # audit.yml `governance:`, set when a config loads


class RowValuesWithheld(RuntimeError):
    """`governance.metadata_only` is on and this question would send row values."""


def set_policy(block: dict | None) -> None:
    POLICY.clear()
    POLICY.update(block or {})


def metadata_only() -> bool:
    return bool(POLICY.get("metadata_only"))


def guard(builder: str) -> None:
    """Refuse a state carrying row values, before it is sent, in metadata-only mode."""
    if metadata_only() and builder in ROW_BUILDERS:
        raise RowValuesWithheld(
            f"not sent: a `{builder}` state carries row values read from the warehouse, and "
            f"`governance.metadata_only` is on in audit.yml. Nothing was sent to the model "
            f"provider. `assay onboard` lists what leaves your network in each mode.")


def what_leaves() -> list[tuple[str, str]]:
    """(what, where it goes) for everything that leaves this machine, in the current mode."""
    rows = [
        ("to your warehouse, through your own dbt (only after you allow it)",
         "read-only statements: counts, distinct counts, small samples; nothing is written"),
        ("to the model provider, per judged question",
         ("compiled SQL, model / column / test names, descriptions and claims your project "
          "wrote, and counts and ratios (row counts, distinct counts, fan-out)")),
    ]
    if metadata_only():
        rows.append(("row values", ("never: governance.metadata_only is on, so `feeds` and "
                                    "`adjudicate` refuse before sending")))
    else:
        rows.append(("row values, to the model provider",
                     ("only from `assay feeds` (a sample of rows per source) and `assay "
                      "adjudicate` (failing rows dbt stored). `governance: {metadata_only: "
                      "true}` stops both")))
    rows.append(("from GitHub, once", ("`assay prove --setup` downloads the pinned Lean "
                                        "toolchain; it sends nothing about your project")))
    rows.append(("nothing else", ("no telemetry; the store, the page and the form stay on this "
                                  "machine unless you publish them, and load nothing from the "
                                  "network when opened")))
    return rows
