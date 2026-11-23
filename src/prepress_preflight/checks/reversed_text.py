"""Small white text knocked out of a background printed in several inks.

White text is not printed: it is the paper showing through a gap left in
every plate behind it. When that background is built from two or more inks,
the gap has to line up on each plate. On press the plates land a fraction of a
millimetre apart, so one ink creeps into the letters and fine strokes fill in
or grow a coloured edge. On a single ink there is nothing to misregister.

  * warn: white text up to 7 pt as placed, out of two or more inks;
  * note: the same from 7 up to 10 pt, where most sans-serif type holds up but
    light or serif faces may not.

"Two or more inks" means two or more printing plates (process or spot) at 30%
or more behind the text, measured on the separated plates, so overprint and
spot inks count as they print. The background is taken as the 90th percentile
of each plate inside the text's box: the letters themselves are bare paper,
and they cover well under half of it. Font weight is not judged. Invisible
text (render mode 3 or 7) and text set to overprint (which is not reversed)
are ignored.
"""
from __future__ import annotations

import numpy as np
import pymupdf

from ..document import device_matrix
from ..model import NOTE, UNCHECKED, WARN
from ..plates import page_inks, render_channels
from .base import Check
from .white_overprint import is_white

WARN_PT, NOTE_PT = 7.0, 10.0
INK_MIN_PCT = 30.0
DPI = 100  # a background colour needs no fine detail: a 6 pt text box is still ~9 px tall


def _candidate(e) -> bool:
    return (e.kind == "text" and e.paint == "fill" and e.size is not None and not e.overprint
            and e.bbox is not None and is_white(e.cs, e.colour)
            and not (e.loc is not None and e.loc[2] in (3, 7)))


class ReversedTextCheck(Check):
    """Small white text out of a background of two or more inks."""

    id = "reversed-text"
    name = "Reversed small text"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.hits = {WARN: [], NOTE: []}  # (page, size, background text, bbox)

    def page(self, page, content):
        candidates = [e for e in content.events if _candidate(e) and e.size * page.unit <= NOTE_PT
                      and page.print_area.intersects(e.bbox)]
        if not candidates:
            return
        try:
            mu_page = self.doc.mu[page.number - 1]
            names, data = render_channels(mu_page, DPI / 72)
            printing = {i.name for i in page_inks(mu_page) if i.printing}
            sx, sy = data.shape[1] / mu_page.rect.width, data.shape[0] / mu_page.rect.height
            to_mu = device_matrix(mu_page)
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Small white text could not be measured ({type(exc).__name__})", [page.number])
            return
        area = pymupdf.Rect(*page.print_area.as_list()) * to_mu
        area.normalize()
        for e in candidates:
            r = pymupdf.Rect(*e.bbox) * to_mu
            r.normalize()
            r &= area
            if r.is_empty:
                continue
            block = data[int(r.y0 * sy):int(np.ceil(r.y1 * sy)), int(r.x0 * sx):int(np.ceil(r.x1 * sx))]
            if block.size == 0:
                continue
            background = np.percentile(block.reshape(-1, block.shape[2]), 90, axis=0) / 2.55
            inks = [(n, v) for n, v in zip(names, background) if v >= INK_MIN_PCT and n in printing]
            if len(inks) < 2:
                continue
            size = e.size * page.unit
            tier = WARN if size <= WARN_PT + 0.05 else NOTE
            text = " ".join(f"{_short(n)}{round(v)}" for n, v in inks)
            self.hits[tier].append((page.number, size, text, list(e.bbox)))

    def finish(self):
        for severity, limit in ((WARN, f"up to {WARN_PT:g} pt"), (NOTE, f"{WARN_PT:g} to {NOTE_PT:g} pt")):
            hits = self.hits[severity]
            if not hits:
                continue
            smallest = min(h[1] for h in hits)
            backgrounds = {}
            for h in hits:
                backgrounds[h[2]] = backgrounds.get(h[2], 0) + 1
            common = max(backgrounds, key=backgrounds.get)
            message = (f"White text {limit} knocked out of two or more inks: {len(hits)} text object(s), smallest "
                       f"{smallest:.1f} pt, mostly out of {common}. Misregistration can fill fine strokes in")
            if severity == NOTE:
                message += "; most sans-serif type this size holds up"
            self.add(severity, message, sorted({h[0] for h in hits}), [(h[0], h[3]) for h in hits])
        return self.findings


def _short(ink: str) -> str:
    return {"Cyan": "C", "Magenta": "M", "Yellow": "Y", "Black": "K"}.get(ink, ink + " ")
