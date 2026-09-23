"""One mark, in four places, and a test so it stays one mark.

The page, the form, the favicon and `docs/assay-mark.svg` all show the same microtiter plate:
sixteen wells, eleven read and five not. Three hand-maintained copies of an SVG is three things
to update and two of them will be missed.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import dbt_assay
from dbt_assay.mark import FAVICON, MARK_SVG

ROOT = Path(dbt_assay.__file__).parent.parent.parent
WELL = re.compile(r'<circle cx="(\d+)" cy="(\d+)" r="5" fill="(#[0-9A-F]{6})"/>')


def _wells(svg: str) -> list:
    return sorted(WELL.findall(svg))


def test_the_plate_is_sixteen_wells_eleven_read_and_five_not():
    """*** THE FILLED-AND-CLEAR SPLIT IS NOT DECORATION. ***
    It says what the product says: some of this warehouse has been read and some has not."""
    wells = _wells(MARK_SVG)
    assert len(wells) == 16, wells
    clear = [w for w in wells if w[2] == "#FFFFFF"]
    assert len(clear) == 5, "the plate is either full or empty, and neither says anything"
    # The clear ones sit toward the bottom right, the way a plate reads part way through a run.
    assert all(int(x) >= 38 or int(y) >= 50 for x, y, _f in clear), clear


def test_three_colours_survive_being_small():
    """*** THE BLUE IS THE ONLY COOL COLOUR, WHICH IS WHY IT STILL READS AS THREE AT 20px. ***
    The alternative palette failed that test: its orange and red sit too close to separate."""
    fills = {f for _x, _y, f in _wells(MARK_SVG) if f != "#FFFFFF"}
    assert fills == {"#F3C337", "#EC6A7C", "#4A86C8"}


def test_the_file_on_disk_is_the_same_plate():
    """`docs/assay-mark.svg` is what the README shows. It is pretty-printed and carries a title,
    so the bytes differ on purpose -- the WELLS are what must not."""
    f = ROOT / "docs" / "assay-mark.svg"
    if not f.exists():
        import pytest
        pytest.skip("no docs in a wheel install")
    assert _wells(f.read_text()) == _wells(MARK_SVG), (
        "the mark in the docs and the mark in the pages have drifted apart")


def test_it_drops_into_an_f_string_without_escaping():
    """Both page shells are f-strings. A brace anywhere in here would be a format field."""
    assert "{" not in MARK_SVG and "}" not in MARK_SVG
    assert MARK_SVG.startswith("<svg") and MARK_SVG.endswith("</svg>")
    assert len(MARK_SVG) < 1200, "it rides inside every page; keep it under a kilobyte"


def test_the_favicon_is_the_marks_own_bytes():
    """Not a file beside the page: a linked asset is the thing that is missing the first time
    somebody emails the file or moves it between directories."""
    assert FAVICON.startswith("data:image/svg+xml;base64,")
    back = base64.b64decode(FAVICON.split(",", 1)[1]).decode("utf-8")
    assert back == MARK_SVG


def test_both_shipped_pages_carry_it():
    from dbt_assay import explorer, reviewform

    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": [],
            "unconfigured": [], "effectiveness": [], "moved": {}}
    for doc in (explorer.explorer_html(data, "<html></html>"),
                reviewform.form_html([], {}, "p", "x", "0")):
        assert MARK_SVG in doc, "a page ships without the mark"
        assert FAVICON in doc, "a page ships without the favicon"
