from __future__ import annotations

import numpy as np
import pymupdf

from ..model import FAIL, NOTE, UNCHECKED
from .base import Check

MAX_PIXELS = 4_000_000
MIN_AREA_MM2 = 1.0  # ignore isolated pixels


class TotalInkCheck(Check):
    """Highest total ink coverage inside the trim, measured on a rendered page.

    Limits: spot colours are counted through their CMYK equivalents, RGB objects
    through a simple conversion, and overprint is not simulated. Registration
    colour is handled by its own check.
    """

    id = "total-ink"
    name = "Total ink"
    needs_content = False

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.peak = 0.0
        self.peak_pages: list[int] = []

    def page(self, page, content):
        try:
            mu_page = self.doc.mu[page.number - 1]
            clip = mu_page.trimbox
            area_in2 = max((clip.width / 72.0) * (clip.height / 72.0), 1e-6)
            dpi = int(min(72, max(18, (MAX_PIXELS / area_in2) ** 0.5)))
            pix = mu_page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, clip=clip, alpha=False)
            if pix.n != 4:
                raise ValueError("renderer did not return CMYK")
            data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4)
            coverage = data.sum(axis=2, dtype=np.int32) / 2.55
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Ink coverage could not be measured ({type(exc).__name__})", [page.number])
            return
        px_mm2 = (25.4 / dpi * page.unit) ** 2
        peak = float(coverage.max()) if coverage.size else 0.0
        if peak > self.peak + 0.5:
            self.peak, self.peak_pages = peak, [page.number]
        elif abs(peak - self.peak) <= 0.5:
            self.peak_pages.append(page.number)
        limit = self.options.ink_limit_pct
        over_mm2 = float((coverage >= limit - 1.0).sum()) * px_mm2
        if over_mm2 >= MIN_AREA_MM2:
            self.add(FAIL, f"Total ink reaches {limit:g}%", [page.number])

    def finish(self):
        self.add(NOTE, f"Highest total ink coverage: {self.peak:.0f}%", self.peak_pages)
        return self.findings
