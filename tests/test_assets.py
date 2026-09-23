"""The woodcuts, the type and the mark: what the pages are made of.

*** A SELF-CONTAINED PAGE MEANS THE PICTURES AND THE TYPE TRAVEL INSIDE IT. ***
Both artifacts are opened from `file://`, emailed and committed. A linked asset is the thing that
is missing the first time any of that happens, and on `file://` a blocked request fails silently.
So the cuts and both faces are base64 in the document, and these assert that they still are.
"""
from __future__ import annotations

import base64

from dbt_assay import assets


def test_every_cut_is_embedded_and_is_a_gif():
    """A name that resolves to an empty string renders as a broken image, which on a page made of
    pictures is indistinguishable from a page that lost its pictures."""
    assert len(assets.CUTS) >= 12, "the plate book is nearly empty"
    for name, uri in sorted(assets.CUTS.items()):
        assert uri.startswith("data:image/gif;base64,"), f"{name} is not embedded"
        raw = base64.b64decode(uri.split(",", 1)[1])
        assert raw[:4] == b"GIF8", f"{name} is not a GIF"
        assert len(raw) > 800, f"{name} is suspiciously small"


def test_the_plates_stay_small_enough_to_ride_in_every_page():
    """They are 1-bit black on white, which is why fifteen of them cost less than one photograph.
    A colour scan slipped in here would quietly add megabytes to every report."""
    total = sum(len(u) for u in assets.CUTS.values())
    assert total < 600_000, f"the cuts come to {total // 1024}KB of base64; something is not 1-bit"


def test_both_faces_are_subset_woff2():
    """190KB of TrueType each becomes 41KB subset to the glyphs these pages use. Shipping the
    full face would be most of a megabyte for characters nothing renders."""
    for uri in (assets.FELL_REGULAR, assets.FELL_ITALIC):
        assert uri.startswith("data:font/woff2;base64,")
        raw = base64.b64decode(uri.split(",", 1)[1])
        assert raw[:4] == b"wOF2", "not woff2"
        assert len(raw) < 90_000, "the face was not subset"
    assert "@font-face" in assets.FONT_CSS and "Fell" in assets.FONT_CSS


def test_the_mark_is_the_cut_itself():
    """*** THE ASSUMPTION WAS THAT AN ENGRAVING COULD NOT SURVIVE 23px. IT WAS WRONG. ***

    The mark is only ever seen at header size, so the first version traced the vessel as an SVG.
    Rendered both at 23, 46 and 96: at 23px the cut reads as a small dense vessel and the trace
    reads as a modern line icon. The trace was cleaner and the cut was right.

    It is an `img`, and the page blends it onto the paper rather than shipping a second, edited
    copy of a scan.
    """
    m = assets.MARK_SVG
    assert m.startswith("<img ") and m.endswith(">")
    assert "{" not in m and "}" not in m, "it must drop into an f-string unescaped"
    assert 'class="mark"' in m and 'alt="assay"' in m
    assert assets.CUTS["gourd"] in m, "the mark is not one of the plates"
    assert "<svg" not in m, "the traced mark is back"


def test_the_favicon_is_the_same_vessel_as_the_mark():
    """One vessel, one file, no second thing to keep in step."""
    assert assets.FAVICON == assets.CUTS["gourd"]
    assert assets.FAVICON.startswith("data:image/gif;base64,")
    raw = base64.b64decode(assets.FAVICON.split(",", 1)[1])
    assert raw[:4] == b"GIF8"


def test_both_shells_size_the_mark_and_blend_it():
    """*** AN UNSIZED IMG IN A FLEX ROW RENDERS AT ITS NATURAL 160px. ***
    And an unblended 1-bit scan sits on a white field the paper shows around."""
    from dbt_assay import explorer, reviewform
    for css in (explorer.CSS, reviewform._CSS):
        rule = css[css.index("h1 > .mark{"):css.index("}", css.index("h1 > .mark{"))]
        assert "width:" in rule and "flex:none" in rule
        assert "mix-blend-mode:multiply" in rule


def test_both_shipped_pages_carry_the_mark_the_type_and_the_plates():
    from dbt_assay import explorer, reviewform

    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": [],
            "unconfigured": [], "effectiveness": [], "moved": {}}
    for doc in (explorer.explorer_html(data, "<html></html>"),
                reviewform.form_html([], {}, "p", "x", "0")):
        assert assets.MARK_SVG in doc, "a page ships without the mark"
        assert assets.FAVICON in doc, "a page ships without the favicon"
        assert "@font-face" in doc and "Fell" in doc, "a page ships without its type"
        assert assets.CUTS["gourd"] in doc, "a page ships without its plates"


def _explainer_cuts() -> list[str]:
    """The cut each `explainer(...)` call in the form passes, in source order.

    Read by walking the call to its matching paren rather than by regex: the arguments are
    concatenated JS strings running over several lines, and a line-wise pattern matches the
    example text as readily as the cut.
    """
    import re

    from dbt_assay import reviewform

    src = reviewform._JS
    out = []
    for m in re.finditer(r"(?<!function )\bexplainer\(", src):
        i, depth = m.end(), 1
        while depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        call = src[m.end():i - 1]
        last = re.findall(r"'([a-z_]+)'\s*$", call.strip())
        out.append(last[0] if last else "")
    return out


def test_no_two_tabs_of_the_form_carry_the_same_plate():
    """*** THE SAME CUT ON EVERY TAB IS WALLPAPER, NOT A LANDMARK. ***

    Shipped that way and it was caught on sight: "settings and words have the same fucking
    image". A reader arriving at Settings from Words saw the identical plate in the identical
    place, which reads as a page that did not change.
    """
    cuts = _explainer_cuts()
    assert len(cuts) >= 3, "the explainers stopped taking a cut at all"
    assert all(c in assets.CUTS for c in cuts), f"{cuts} names a plate that does not exist"
    assert len(set(cuts)) == len(cuts), f"two tabs share a plate: {cuts}"


def test_the_report_panes_do_not_share_a_plate_either():
    import re

    from dbt_assay import explorer

    used = re.findall(r"\(DATA\.cuts \|\| \{\}\)\.([a-z_]+)", explorer.JS + explorer._VIEWS)
    assert used, "the report stopped naming its plates"
    assert all(c in assets.CUTS for c in used), f"{used} names a plate that does not exist"
    assert len(set(used)) == len(used), f"two panes share a plate: {used}"
