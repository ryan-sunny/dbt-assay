"""The mark: a microtiter plate part way through a run.

*** THE FILLED-AND-CLEAR SPLIT IS NOT DECORATION. ***
Sixteen wells, eleven read and five not. It says what the product says, which is that a warehouse
is understood in the part somebody has actually looked at and not in the rest.

*** INLINE, BECAUSE BOTH PAGES ARE ONE FILE THAT OPENS OFF A DISK WITH NO NETWORK. ***
Not a file reference and not a raster data URI. Under a kilobyte against a page that runs to
megabytes, and it survives being emailed, committed and opened from `file://` -- which a linked
asset does not.

*** IT HOLDS UP AT 20px, WHICH IS THE ONLY SIZE THAT MATTERS HERE. ***
The wells are flat discs with no interior detail, so nothing turns to mush. And the blue is the
only cool colour, so the mark still reads as three colours rather than two when it is small --
the test the alternative palette failed, where the orange and the red sit too close together to
separate at header size.

*** ONE COPY. ***
The page, the form, the favicon and `docs/assay-mark.svg` are all the same sixteen wells. Three
hand-maintained copies of an SVG is three things to update and two of them will be missed, so
there is one string here and a test that the file on disk is still it.
"""
from __future__ import annotations

import base64

# Python single quotes throughout, so the SVG's own double quotes need no escaping -- and no
# braces anywhere, so it drops straight into an f-string.
_PLATE = ('<rect x="2.5" y="2.5" width="59" height="59" rx="9" '
          'fill="#F6F8F9" stroke="#C3CDD3" stroke-width="3"/>')

# Row by row, as the plate reads: filled where a well has been read, clear where it has not.
_WELLS = [
    ('#F3C337', '#EC6A7C', '#F3C337', '#FFFFFF'),
    ('#EC6A7C', '#F3C337', '#4A86C8', '#FFFFFF'),
    ('#F3C337', '#4A86C8', '#F3C337', '#FFFFFF'),
    ('#4A86C8', '#F3C337', '#FFFFFF', '#FFFFFF'),
]


def _wells() -> str:
    out = []
    for r, row in enumerate(_WELLS):
        for c, fill in enumerate(row):
            out.append(f'<circle cx="{14 + c * 12}" cy="{14 + r * 12}" r="5" fill="{fill}"/>')
    return "".join(out)


MARK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" '
            'aria-label="assay">' + _PLATE
            + '<g stroke="#BCC7CE" stroke-width="1.5">' + _wells() + '</g></svg>')

# The same bytes as a data URI, for `<link rel="icon">`. base64 rather than the raw markup: a
# URL-encoded SVG inside an attribute is a second escaping rule to get wrong, and this has none.
FAVICON = "data:image/svg+xml;base64," + base64.b64encode(MARK_SVG.encode("utf-8")).decode("ascii")

# `h1` becomes a flex row so the mark sits on the text's optical centre rather than its baseline.
MARK_CSS = """h1{display:flex;align-items:center;gap:9px}
.mark{width:23px;height:23px;flex:none}"""
