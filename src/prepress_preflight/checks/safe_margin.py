from __future__ import annotations

import pymupdf

from ..edges import EDGES, printing_page, scan_margin, spine_edge
from ..model import MM_PER_PT, UNCHECKED, WARN
from .base import Check
from ..document import device_matrix

DISPLAY_MM = 25.0  # type this large that runs off the page is treated as artwork


class SafeMarginCheck(Check):
    """Content too close to the trim, where a small shift in cutting shows.

    Two measurements, both against Options.safe_margin_mm:

      * live text: any word that comes inside the margin, or crosses the trim
      * artwork on a white edge: ink inside the margin that stops short of the
        trim (outlined type, logos, rules, picture boxes)

    Artwork that reaches the trim is meant to be cut through and belongs to the
    bleed checks. With Options.bound the spine edge of each page is left out.

    Very large display type (over 25 mm) that runs off the page is deliberate
    and is not reported as text being cut.

    Limits: artwork under 15% ink is not counted, so that a pale background
    fading out near the edge is not mistaken for an object. Type converted to
    outlines is only seen on a white edge, not over a
    background that bleeds. Word boxes include the full line height, so text is
    judged a little closer to the top and bottom than its letters are. Hidden
    text (an OCR layer) is counted like any other text.
    """

    id = "safe-margin"
    name = "Safe margin"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.words: list[tuple] = []  # (page, distance to the trim in mm, box)
        self.crossing: list[tuple] = []  # (page, box)
        self.artwork: list[tuple] = []  # (page, MarginScan)

    def page(self, page, content):
        margin = self.options.safe_margin_mm
        skip = spine_edge(page.number) if self.options.bound else None
        try:
            mu_page, _ = printing_page(self.doc, page, content)
            to_mu = device_matrix(mu_page)
            trim = (pymupdf.Rect(page.trim.as_list()) * to_mu).normalize()
            # Which side of the rendered page each edge of the trim ends up on.
            sides = {}
            for name, point in (("left", (page.trim.x0, page.trim.y0)), ("right", (page.trim.x1, page.trim.y0)),
                                ("bottom", (page.trim.x0, page.trim.y0)), ("top", (page.trim.x0, page.trim.y1))):
                p = pymupdf.Point(*point) * to_mu
                if name in ("left", "right"):
                    q = pymupdf.Point(page.trim.x0 if name == "right" else page.trim.x1, page.trim.y0) * to_mu
                else:
                    q = pymupdf.Point(page.trim.x0, page.trim.y0 if name == "top" else page.trim.y1) * to_mu
                horizontal = abs(p.x - q.x) >= abs(p.y - q.y)
                sides[name] = ("x0" if p.x < q.x else "x1") if horizontal else ("y0" if p.y < q.y else "y1")
            mm_per_unit = MM_PER_PT  # word boxes come from the renderer, already in points on paper
            for x0, y0, x1, y1, *_ in mu_page.get_text("words"):
                if x1 <= trim.x0 or x0 >= trim.x1 or y1 <= trim.y0 or y0 >= trim.y1:
                    continue  # wholly outside the trim: slug text
                gaps = {"x0": x0 - trim.x0, "y0": y0 - trim.y0, "x1": trim.x1 - x1, "y1": trim.y1 - y1}
                dist = min(gaps[side] for name, side in sides.items() if name != skip) * mm_per_unit
                box = list((pymupdf.Rect(x0, y0, x1, y1) * ~to_mu).normalize())
                if dist < -0.05:
                    if min(x1 - x0, y1 - y0) * mm_per_unit <= DISPLAY_MM:
                        self.crossing.append((page.number, box))
                elif dist < margin - 0.05:
                    self.words.append((page.number, max(dist, 0.0), box))
            for name in EDGES:
                if name == skip:
                    continue
                scan = scan_margin(mu_page, page, name, margin)
                if scan.close_mm > 0:
                    self.artwork.append((page.number, scan))
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Safe margin could not be measured ({type(exc).__name__})", [page.number])

    def finish(self):
        margin = self.options.safe_margin_mm

        def where(pages, page):
            return f" (page {page})" if len(pages) > 1 else ""

        if self.crossing:
            pages = sorted({p for p, _ in self.crossing})
            self.add(WARN, f"{len(self.crossing)} word(s) of text cross the trim and will be cut", pages,
                     list(self.crossing))
        if self.words:
            pages = sorted({p for p, _, _ in self.words})
            page, dist, _ = min(self.words, key=lambda w: w[1])
            self.add(WARN, f"{len(self.words)} word(s) of text within {margin:g} mm of the trim; "
                           f"closest is {dist:.1f} mm{where(pages, page)}", pages, [(p, b) for p, _, b in self.words])
        if self.artwork:
            pages = sorted({p for p, _ in self.artwork})
            page, scan = min(self.artwork, key=lambda a: a[1].nearest_mm)
            edges = ", ".join(sorted({s.name for _, s in self.artwork}))
            self.add(WARN, f"Artwork within {margin:g} mm of the trim without reaching it ({edges}); "
                           f"closest is {scan.nearest_mm:.1f} mm{where(pages, page)}", pages,
                     [(p, s.box) for p, s in self.artwork])
        return self.findings
