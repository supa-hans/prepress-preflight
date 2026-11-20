"""Small 100% black that knocks out instead of overprinting. A note.

Black that knocks out leaves a gap in the colour beneath; a fraction of a
millimetre of misregistration shows it as a halo along fine type and rules.
Small black usually overprints (InDesign's [Black] swatch does by default), so
this lists the exceptions. A note, not a warning: on white paper, or over
nothing, knocking out does no harm, and some jobs want it.

The fix is overprint-black, which uses the same rules (shared from here).
"""
from __future__ import annotations

from collections import Counter

from ..content import CMYK
from ..model import NOTE
from .base import Check

MAX_TEXT_PT = 12.0
MAX_LINE_PT = 2.0
LABELS = {"path": "line(s)", "text": "text object(s)"}


def is_black(e) -> bool:
    """C0 M0 Y0 K100, or a separation named Black at 100%."""
    if not e.colour:
        return False
    if e.cs == CMYK:
        return len(e.colour) == 4 and max(e.colour[:3]) <= 0.001 and e.colour[3] >= 0.999
    return e.cs.kind == "sep" and e.cs.names == ("Black",) and e.colour[0] >= 0.999


def grey_black(e) -> bool:
    return e.cs.kind == "gray" and bool(e.colour) and e.colour[0] <= 0.001


def is_small(e, max_text=MAX_TEXT_PT, max_line=MAX_LINE_PT) -> bool:
    """Text up to max_text pt as placed, or a stroked line up to max_line pt."""
    if e.kind == "text":
        return e.size is not None and e.size <= max_text
    return e.kind == "path" and e.paint == "stroke" and e.width is not None and e.width <= max_line


def knocks_out(e) -> bool:
    """Not overprinting as intended: off, or on in CMYK with mode 0 (which overprints nothing)."""
    return not e.overprint or (e.cs == CMYK and e.opm != 1)


class BlackOverprintCheck(Check):
    """Small 100% black text and thin black lines that knock out. A note."""

    id = "black-overprint"
    name = "Black overprint"

    def page(self, page, content):
        counts: Counter = Counter()
        locations = []
        for e in content.events:
            if e.kind not in LABELS or not is_black(e) or not is_small(e) or not knocks_out(e):
                continue
            if e.bbox is not None and not page.media.intersects(e.bbox):
                continue
            counts[LABELS[e.kind]] += 1
            if e.bbox is not None:
                locations.append((page.number, list(e.bbox)))
        if counts:
            detail = ", ".join(f"{n} {label}" for label, n in counts.most_common())
            self.add(NOTE, f"Small 100% black knocks out instead of overprinting: {detail} "
                             f"(text up to {MAX_TEXT_PT:g} pt, lines up to {MAX_LINE_PT:g} pt)",
                     [page.number], locations)
