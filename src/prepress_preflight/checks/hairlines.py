from __future__ import annotations

from ..edges import is_non_printing
from ..model import UNCHECKED, WARN
from .base import Check
from .white_overprint import is_white

MIN_PT = 0.25
EPS = 0.005  # a line set to exactly 0.25 pt is fine


class HairlineCheck(Check):
    """Stroked lines thinner than 0.25 pt, which may break up or vanish in print.

    The weight is the stroke width as placed on the page, after any scaling. A
    width of 0 means "thinnest the device can draw" and is always flagged.
    White strokes and strokes in non-printing spot colours (dielines) are left
    out, and so is anything wholly outside the printed area.

    Limits: only stroked lines are measured. A thin filled shape, or thin
    strokes inside a pattern fill, are not seen.
    """

    id = "hairlines"
    name = "Hairlines"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.thin: list[tuple] = []  # (page, width, bbox)

    def page(self, page, content):
        unlocated = 0
        for e in content.events:
            if e.paint != "stroke" or e.width is None or e.width * page.unit >= MIN_PT - EPS:
                continue
            if e.cs.indexed or is_white(e.cs, e.colour):
                continue
            names = [n for n in e.cs.names if n != "None"]
            if e.cs.kind == "sep" and names and all(is_non_printing(n) for n in names):
                continue
            if e.bbox is None:
                unlocated += 1
                continue
            if not page.print_area.intersects(e.bbox):
                continue
            self.thin.append((page.number, e.width * page.unit, list(e.bbox)))
        if unlocated:
            self.add(UNCHECKED, "Thin lines inside pattern fills could not be measured", [page.number])

    def finish(self):
        if self.thin:
            pages = sorted({p for p, _, _ in self.thin})
            page, width, _ = min(self.thin, key=lambda t: t[1])
            where = f" (page {page})" if len(pages) > 1 else ""
            self.add(WARN, f"{len(self.thin)} line(s) thinner than {MIN_PT:g} pt; thinnest is {width:.2f} pt{where}",
                     pages, [(p, b) for p, _, b in self.thin])
        return self.findings
