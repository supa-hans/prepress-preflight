"""Small text printed in several inks.

Each ink is a separate plate, and on press the plates land a fraction of a
millimetre apart. On large type nobody sees it; on fine type the letters
blur and grow coloured fringes, and thin strokes can fill in. So small type is
best printed in one ink.

  * warn: small text in rich black (K 85% or more with any C, M or Y). It is
    meant to read as black, and K alone does that cleanly; the rich-black-text
    fix makes it K100.
  * note: other small text in three or more inks (a brand colour built from
    C, M and Y, say). It may be deliberate, so it is only listed.

Small: up to 8 pt as placed (font size times any scaling). An ink counts at
10% or more. Invisible text (render mode 3 or 7, as OCR layers use) is ignored.
Only CMYK colours are measured.
"""
from __future__ import annotations

from collections import Counter

from ..content import CMYK
from ..model import NOTE, WARN
from .base import Check

SMALL_TEXT_PT = 8.0
INK_MIN = 0.10
RICH_K = 0.85


def inks(colour) -> int:
    return sum(v >= INK_MIN for v in colour)


def is_rich_black(e) -> bool:
    return (e.cs == CMYK and bool(e.colour) and len(e.colour) == 4 and e.colour[3] >= RICH_K
            and max(e.colour[:3]) >= INK_MIN)


def is_small_text(e) -> bool:
    return (e.kind == "text" and e.size is not None and e.size <= SMALL_TEXT_PT
            and not (e.loc is not None and e.loc[2] in (3, 7)))


def _pct(colour) -> str:
    return " ".join(f"{ink}{round(v * 100)}" for ink, v in zip("CMYK", colour))


class SmallTextInksCheck(Check):
    """Small text in rich black (warn) or in three or more inks (note)."""

    id = "small-text-inks"
    name = "Small text inks"

    def page(self, page, content):
        rich, other = Counter(), Counter()
        rich_at, other_at = [], []
        for e in content.events:
            if not is_small_text(e) or e.cs != CMYK or not e.colour or len(e.colour) != 4:
                continue
            if e.bbox is not None and not page.media.intersects(e.bbox):
                continue
            where = [(page.number, list(e.bbox))] if e.bbox is not None else []
            if is_rich_black(e):
                rich[_pct(e.colour)] += 1
                rich_at += where
            elif inks(e.colour) >= 3:
                other[_pct(e.colour)] += 1
                other_at += where
        if rich:
            self.add(WARN, f"Small text (up to {SMALL_TEXT_PT:g} pt) in rich black: {sum(rich.values())} text object(s), "
                           f"{_colours(rich)}. Black text this size prints cleanest in K only",
                     [page.number], rich_at)
        if other:
            self.add(NOTE, f"Small text (up to {SMALL_TEXT_PT:g} pt) in three or more inks: {sum(other.values())} "
                           f"text object(s), {_colours(other)}", [page.number], other_at)


def _colours(c: Counter) -> str:
    top = [f"{n} in {colour}" for colour, n in c.most_common(3)]
    more = len(c) - 3
    return ", ".join(top) + (f" and {more} other colour(s)" if more > 0 else "")
