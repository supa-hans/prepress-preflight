"""Small white text and thin white lines knocked out of a background printed in several inks.

White text is not printed: it is the paper showing through a gap left in
every plate behind it. When that background is built from two or more inks,
the gap has to line up on each plate. On press the plates land a fraction of a
millimetre apart, so one ink creeps into the letters and fine strokes fill in
or grow a coloured edge. On a single ink there is nothing to misregister.

  * warn: white text up to 7 pt as placed, out of two or more inks;
  * note: the same from 7 up to 10 pt, where most sans-serif type holds up but
    light or serif faces may not;
  * warn: a white stroked line under 0.5 pt as placed, out of two or more inks.
    Thin coloured lines are the Hairlines check's business; a white one is a
    gap in the plates and fills in the same way small text does.

"Two or more inks" means two or more printing plates (process or spot) at 30%
or more behind the text, measured on the separated plates, so overprint and
spot inks count as they print. The inks must be together at the same point:
at least 40% of the text's box must carry two or more plates at 30%, and the
background reported is the median of those points. The letters themselves are
bare paper and cover well under half of the box, so text over a solid
two-ink panel passes the 40% easily, while artwork beside the text in
separate single inks (black stripes next to a cyan sky) does not count. A
line's box is widened by 1.5 pt each side so that the background around it is
measured, not the line. A white stroke that outlines a white-filled shape
(the same outline, as Illustrator draws a filled and stroked path) is left
out: it is the edge of a white area, not a line. Font weight is not judged. Invisible
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
MULTI_SHARE = 0.4  # share of the box that must be two or more inks
DPI = 100  # a background colour needs no fine detail: a 6 pt text box is still ~9 px tall


LINE_PT = 0.5  # white lines thinner than this fill in
LINE_PAD = 1.5  # points of background measured either side of a line
OUTLINE_TOL = 0.6  # a stroke whose box matches a white fill's within this (points) outlines that fill


def _white(e) -> bool:
    return not e.overprint and e.bbox is not None and is_white(e.cs, e.colour)


def _small_text(e, unit) -> bool:
    return (e.kind == "text" and e.paint == "fill" and e.size is not None and e.size * unit <= NOTE_PT and _white(e)
            and not (e.loc is not None and e.loc[2] in (3, 7)))


def _thin_line(e, unit) -> bool:
    return (e.kind == "path" and e.paint == "stroke" and e.width is not None and e.width * unit < LINE_PT - 0.005
            and _white(e))


class ReversedTextCheck(Check):
    """Small white text out of a background of two or more inks."""

    id = "reversed-text"
    name = "Reversed text and lines"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.hits = {WARN: [], NOTE: [], "line": []}  # (page, size or width, background text, bbox)

    def page(self, page, content):
        candidates = [e for e in content.events if (_small_text(e, page.unit) or _thin_line(e, page.unit))
                      and page.print_area.intersects(e.bbox)]
        if any(e.kind == "path" for e in candidates):
            # A white stroke around a white-filled shape (Illustrator's fill and stroke on one path) is the edge of a
            # white area, not a line: filling in only trims the shape a little.
            fills = [f.bbox for f in content.events if f.kind == "path" and f.paint == "fill" and _white(f)]
            candidates = [e for e in candidates if e.kind != "path" or not any(
                max(abs(a - b) for a, b in zip(f, e.bbox)) <= OUTLINE_TOL for f in fills)]
        if not candidates:
            return
        try:
            mu_page = self.doc.mu[page.number - 1]
            names, data = render_channels(mu_page, DPI / 72)
            printing = {i.name for i in page_inks(mu_page) if i.printing}
            sx, sy = data.shape[1] / mu_page.rect.width, data.shape[0] / mu_page.rect.height
            to_mu = device_matrix(mu_page)
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Small white text and thin white lines could not be measured ({type(exc).__name__})", [page.number])
            return
        plates = [i for i, n in enumerate(names) if n in printing]
        area = pymupdf.Rect(*page.print_area.as_list()) * to_mu
        area.normalize()
        for e in candidates:
            line = e.kind == "path"
            pad = LINE_PAD / page.unit if line else 0
            r = pymupdf.Rect(e.bbox[0] - pad, e.bbox[1] - pad, e.bbox[2] + pad, e.bbox[3] + pad) * to_mu
            r.normalize()
            r &= area
            if r.is_empty:
                continue
            block = data[int(r.y0 * sy):int(np.ceil(r.y1 * sy)), int(r.x0 * sx):int(np.ceil(r.x1 * sx))]
            if block.size == 0:
                continue
            pixels = block.reshape(-1, block.shape[2])[:, plates] / 2.55
            several = (pixels >= INK_MIN_PCT).sum(axis=1) >= 2
            if not plates or several.mean() < MULTI_SHARE:
                continue
            background = np.median(pixels[several], axis=0)
            inks = [(names[i], v) for i, v in zip(plates, background) if v >= INK_MIN_PCT]
            text = " ".join(f"{_short(n)}{round(v)}" for n, v in inks)
            if line:
                self.hits["line"].append((page.number, e.width * page.unit, text, list(e.bbox)))
                continue
            size = e.size * page.unit
            tier = WARN if size <= WARN_PT + 0.05 else NOTE
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
        lines = self.hits["line"]
        if lines:
            counts = {}
            for h in lines:
                counts[h[2]] = counts.get(h[2], 0) + 1
            self.add(WARN, f"White lines under {LINE_PT:g} pt knocked out of two or more inks: {len(lines)} line(s), "
                           f"thinnest {min(h[1] for h in lines):.2f} pt, mostly out of {max(counts, key=counts.get)}. "
                           "Misregistration can fill them in", sorted({h[0] for h in lines}),
                     [(h[0], h[3]) for h in lines])
        return self.findings


def _short(ink: str) -> str:
    return {"Cyan": "C", "Magenta": "M", "Yellow": "Y", "Black": "K"}.get(ink, ink + " ")
