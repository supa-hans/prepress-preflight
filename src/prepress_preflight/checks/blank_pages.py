"""Pages with nothing printing inside the trim.

A blank page in a booklet is often deliberate (an inside cover, a notes page),
so it is a note. A file in which every page is blank is a warning: something
has gone wrong in the export (artwork on a hidden layer, a missing link, or
everything set to white or to a non-printing spot).

"Blank" means no printing plate carries ink anywhere in the printed area. Crop
marks and slugs outside the trim, white objects, invisible text and
non-printing spots (dielines) do not count. Pages are first screened on the
objects themselves; only a page with no inked object is rendered, to confirm
on the separated plates that nothing prints. A page that cannot be rendered is
reported as could not check, never as blank or as fine.
"""
from __future__ import annotations

import numpy as np
import pymupdf

from ..document import device_matrix
from ..edges import is_non_printing
from ..model import NOTE, UNCHECKED, WARN
from ..plates import page_inks, render_channels
from .base import Check
from .white_overprint import is_white

DPI = 36
MIN_INK = 3  # of 255: anything above a rounding speck counts as ink


def _inked(e, page) -> bool:
    """True when the object puts ink inside the printed area (as far as its own colour says)."""
    if e.bbox is None or not page.print_area.intersects(e.bbox):
        return False
    if e.kind == "text" and e.loc is not None and e.loc[2] in (3, 7):
        return False
    if e.colour is not None and is_white(e.cs, e.colour):
        return False
    names = [n for n in e.cs.names if n != "None"] if e.cs is not None else []
    if e.cs is not None and e.cs.kind == "sep" and names and all(is_non_printing(n) for n in names):
        return False
    return True


class BlankPageCheck(Check):
    """Pages with nothing printing inside the trim."""

    id = "blank-pages"
    name = "Blank pages"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.blank: list[int] = []
        self.count = 0

    def page(self, page, content):
        self.count += 1
        if any(_inked(e, page) for e in content.events):
            return
        try:
            mu_page = self.doc.mu[page.number - 1]
            names, data = render_channels(mu_page, DPI / 72)
            printing = {ink.name for ink in page_inks(mu_page) if ink.printing}
            plates = [i for i, n in enumerate(names) if n in printing]
            area = pymupdf.Rect(*page.print_area.as_list()) * device_matrix(mu_page)
            area.normalize()
        except Exception as exc:  # never report a page as blank, or as fine, without having looked
            self.add(UNCHECKED, f"Could not tell whether the page is blank ({type(exc).__name__})", [page.number])
            return
        sx, sy = data.shape[1] / mu_page.rect.width, data.shape[0] / mu_page.rect.height
        block = data[int(area.y0 * sy):int(np.ceil(area.y1 * sy)), int(area.x0 * sx):int(np.ceil(area.x1 * sx))]
        if not plates or block.size == 0 or block[:, :, plates].max() < MIN_INK:
            self.blank.append(page.number)

    def finish(self):
        if not self.blank:
            return self.findings
        pages = self.blank
        listed = ", ".join(map(str, pages[:12])) + (" …" if len(pages) > 12 else "")
        if len(pages) == self.count:
            self.add(WARN, "Every page is blank inside the trim: nothing prints. Check for artwork on a hidden layer, "
                           "set to white or to a non-printing colour", pages)
        else:
            self.add(NOTE, f"{len(pages)} blank page(s) inside the trim: {listed}. Fine if deliberate (an inside "
                           "cover, a notes page); otherwise the artwork is missing", pages)
        return self.findings
