from __future__ import annotations

import numpy as np
import pymupdf

from ..edges import printing_page
from ..plates import page_inks, render_channels
from ..model import FAIL, NOTE, UNCHECKED
from .base import Check
from ..document import device_matrix

MAX_PIXELS = 4_000_000
MIN_AREA_MM2 = 1.0  # ignore isolated pixels


def _area(mm2: float) -> str:
    if mm2 >= 100_000:
        return f"{mm2 / 1_000_000:.2f} m²"
    if mm2 >= 1000:
        return f"{mm2 / 100:,.0f} cm²"
    return f"{mm2:.0f} mm²"


class TotalInkCheck(Check):
    """Total ink coverage inside the trim, measured on a rendered page.

    Fails when at least 1 mm² is over the limit (Options.ink_limit_pct), and
    reports the peak and the area over the limit. A limit of 400% can only be
    reached, not passed, so there "over" means reaching it.

    Fine objects (small type, thin rules) can be over the limit without any
    rendered pixel showing it, so each vector and text object's own CMYK colour
    is checked as well, whatever its size.

    Measured on the separated plates, as the press prints: total ink is the sum
    of every printing plate, process and spot, and overprint is honoured (black
    set to overprint a rich colour adds to it rather than replacing it).
    Non-printing spot colours (recognised by name) are left out. Limits: RGB
    objects count through the renderer's simple conversion. Registration
    colour is handled by its own check.
    """

    id = "total-ink"
    name = "Total ink"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.peak = 0.0
        self.peak_pages: list[int] = []
        self.over: list[tuple] = []  # (page, peak %, area over the limit in mm², box)
        self.objects: list[tuple] = []  # (page, the object's own ink %)

    def _is_over(self, pct):
        limit = self.options.ink_limit_pct
        return pct >= limit - 1.0 if limit >= 399.5 else pct > limit + 0.5

    def page(self, page, content):
        for e in content.events:
            if e.kind in ("path", "text") and e.cs.kind == "cmyk" and not e.cs.indexed and e.colour and len(e.colour) == 4:
                pct = sum(e.colour) * 100.0
                if self._is_over(pct) and (e.bbox is None or page.trim.intersects(e.bbox)):
                    self.objects.append((page.number, pct))
        try:
            mu_page, _ = printing_page(self.doc, page, content)  # dielines and the like taken out, once
            to_mu = device_matrix(mu_page)
            clip = (pymupdf.Rect(page.trim.as_list()) * to_mu).normalize()
            area_in2 = max((clip.width / 72.0) * (clip.height / 72.0), 1e-6)
            dpi = int(min(72, max(18, (MAX_PIXELS / area_in2) ** 0.5)))
            # Every plate as it prints: overprint honoured (black overprinting a
            # rich colour adds to it), spot inks on their own plates.
            names, plates = render_channels(mu_page, dpi / 72)
            printing = {i.name for i in page_inks(mu_page) if i.printing}
            keep = [i for i, n in enumerate(names) if n in printing]
            sx, sy = plates.shape[1] / mu_page.rect.width, plates.shape[0] / mu_page.rect.height
            x0, y0 = max(0, int(clip.x0 * sx)), max(0, int(clip.y0 * sy))
            x1, y1 = min(plates.shape[1], int(np.ceil(clip.x1 * sx))), min(plates.shape[0], int(np.ceil(clip.y1 * sy)))
            data = plates[y0:y1, x0:x1][:, :, keep]
            coverage = data.sum(axis=2, dtype=np.int32) / 2.55
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Ink coverage could not be measured ({type(exc).__name__})", [page.number])
            return
        px_mm2 = (25.4 / 72) ** 2 / (sx * sy)  # the renderer already applies the page's UserUnit
        peak = float(coverage.max()) if coverage.size else 0.0
        if peak > self.peak + 0.5:
            self.peak, self.peak_pages = peak, [page.number]
        elif abs(peak - self.peak) <= 0.5:
            self.peak_pages.append(page.number)
        limit = self.options.ink_limit_pct
        mask = self._is_over(coverage)
        over_mm2 = float(mask.sum()) * px_mm2
        if over_mm2 >= MIN_AREA_MM2:
            rows, cols = np.where(mask)
            hit = pymupdf.Rect((x0 + cols.min()) / sx, (y0 + rows.min()) / sy,
                               (x0 + cols.max() + 1) / sx, (y0 + rows.max() + 1) / sy)
            hit = (hit * ~to_mu).normalize()
            self.over.append((page.number, peak, over_mm2, [hit.x0, hit.y0, hit.x1, hit.y1]))

    def finish(self):
        limit = self.options.ink_limit_pct
        at_max = limit >= 399.5  # 400% can be reached but not passed
        if self.over or self.objects:
            pages = sorted({o[0] for o in self.over} | {p for p, _ in self.objects})
            parts = []
            if self.over:
                worst = max(self.over, key=lambda o: o[1])
                where = f" (page {worst[0]})" if len(pages) > 1 else ""
                area = _area(sum(o[2] for o in self.over))
                parts.append(f"peaks at {worst[1]:.0f}%{where}, {area} {'at' if at_max else 'over'} the limit")
            if self.objects:
                top = max(pct for _, pct in self.objects)
                parts.append(f"{len(self.objects)} object(s) are coloured {'at' if at_max else 'over'} it, up to {top:.0f}%")
            self.add(FAIL, f"Total ink {'reaches' if at_max else 'is over'} {limit:g}%: " + "; ".join(parts),
                     pages, [(o[0], o[3]) for o in self.over])
        self.add(NOTE, f"Highest total ink coverage: {self.peak:.0f}%", self.peak_pages)
        return self.findings
