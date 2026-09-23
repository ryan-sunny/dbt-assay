"""The woodcuts, the type, and the mark. Everything the pages look like, in one place.

*** A SELF-CONTAINED PAGE MEANS THE PICTURES TRAVEL INSIDE IT. ***
Both artifacts are opened from `file://`, emailed, moved between directories and committed. A
linked asset is the thing that is missing the first time any of those happens, and on `file://` a
blocked request fails silently rather than loudly. So every cut and both fonts are base64 in the
document: fifteen cuts at about 95KB and two subset faces at 81KB, against a page that runs to
twelve megabytes on a real warehouse.

*** THE CUTS ARE 17th-CENTURY AND THE TYPE IS THE 17th-CENTURY TYPE. ***
The engravings are apparatus plates from the alchemical and assaying literature -- furnaces,
retorts, condensing trains, an assayer at his forge. They are 1-bit black on white already, which
is why they sit on a page that is otherwise rules and text without anything having to be toned
down to match.

IM Fell English is Igino Marini's digitisation of the types cut for the Oxford University Press in
the 1670s, under the Open Font Licence. Subset to the glyphs these pages actually use, which takes
190KB of TrueType to 41KB of woff2.

*** THE MARK IS DRAWN, NOT PHOTOGRAPHED. ***
A 288x181 engraving with hatching that fine turns to mush at 23px, which is the only size the
header mark is ever seen at. So the mark is an SVG alembic in the same ink weight: it survives a
favicon, and the real cuts are used where their detail is legible.
"""
from __future__ import annotations

import base64
from pathlib import Path

_DIR = Path(__file__).parent / "assets"


def _uri(name: str, mime: str) -> str:
    try:
        raw = (_DIR / name).read_bytes()
    except OSError:
        return ""
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _cut(stem: str) -> str:
    return _uri(f"{stem}.gif", "image/gif")


# *** NAMED FOR WHAT THEY SHOW, NOT FOR THE FILE THEY CAME IN. ***
# `jfren31` tells a reader nothing. A tab picks its plate by what the picture IS, so swapping one
# for a better scan later is a change in one dictionary rather than a grep across two page shells.
CUTS = {
    # The two serpentine condensers rising off a still, with the cooling barrel beside them.
    "condensers": _cut("jfren31"),
    # A small brick furnace, its fire visible, with a retort resting on a three-legged stool.
    "furnace": _cut("jfren23"),
    # An assayer working a cauldron under a hanging hood, tongs in hand.
    "assayer": _cut("jfren24"),
    # A rank of stills charged and working together.
    "rank": _cut("jfren16"),
    # Vessels joined mouth to neck in a descending chain, each feeding the next.
    "chain": _cut("jfren26"),
    # A man at the furnace drawing off a globe of collected vapour.
    "atwork": _cut("jfren27"),
    # A furnace in full draught, flames breaking over the top.
    "draught": _cut("jfren19"),
    # A tall labelled apparatus, its coil condenser lettered part by part.
    "apparatus": _cut("jfren28"),
    # Circulatory vessels under two suns: the same matter asked the same question repeatedly.
    "circulation": _cut("jfren20"),
    # Two tubs on stands, the plainest measure in the book.
    "tubs": _cut("jfren17"),
    # The instruments themselves, laid out and lettered.
    "instruments": _cut("jfren05"),
    # A tower furnace with its charge and its receiver.
    "tower": _cut("jfren34"),
    # Three bowls in cascade, each pouring into the one below.
    "cascade": _cut("jfren37"),
    # A retort on a tripod drawing into a tall column.
    "tripod": _cut("jfren39"),
    # *** THE MARK. ***
    # A double gourd with two loop handles and a side spout, standing on hatched ground.
    "gourd": _cut("jfren14"),
    # Two vessels slung in frames: the charge suspended rather than set down.
    "slung": _cut("jfren11"),
    # A cauldron over its fire with a retort drawing off the side.
    "cauldron": _cut("jfren12"),
    # A barrel, a brick furnace and a receiver: the whole arrangement, lettered A through E.
    "arrangement": _cut("jfren13"),
    # Four furnaces in a row, each charged, drawing into one train.
    "four": _cut("jfren15"),
    # A retort on a stand drawing into a flask, the plainest distillation in the book.
    "still": _cut("jfren18"),
    # Three compartments, lettered A, B and C: a flask over its fire, a retort drawing into a
    # receiver, and the torch. The one plate the review form carries.
    "workshop": _cut("jfren40"),
}

FELL_REGULAR = _uri("fell-regular.woff2", "font/woff2")
FELL_ITALIC = _uri("fell-italic.woff2", "font/woff2")

FONT_CSS = f"""
@font-face{{font-family:'Fell';font-style:normal;font-weight:400;
src:url({FELL_REGULAR}) format('woff2')}}
@font-face{{font-family:'Fell';font-style:italic;font-weight:400;
src:url({FELL_ITALIC}) format('woff2')}}
"""

# *** THE MARK IS THE CUT, NOT A DRAWING OF IT. ***
# The header mark is only ever seen at 23px, so the assumption was that an engraving with hatching
# that fine would turn to mush there and the mark had to be traced. Rendered both at 23, 46 and 96
# and the assumption was wrong: at 23px the cut reads as a small dense vessel and the trace reads
# as a modern line icon. The trace was cleaner and the cut was right, which is the argument for
# looking rather than reasoning about it.
#
# `mix-blend-mode: multiply` in the page CSS rather than a transparent GIF: these are 1-bit scans
# with an opaque white field, and blending drops that field onto the paper without editing the
# original file.
MARK_IMG = '<img class="mark" alt="assay" src="' + CUTS["gourd"] + '">'

# The name both page shells already use.
MARK_SVG = MARK_IMG


# The favicon is the same bytes: one vessel, one file, no second thing to keep in step.
FAVICON = CUTS["gourd"]
