from __future__ import annotations

from ..model import FAIL, WARN
from .base import Check

EPS_MM = 0.05  # boxes this close are treated as equal


class BleedBoxCheck(Check):
    """Check the declared bleed box. Does not look at whether artwork fills it."""

    id = "bleed-box"
    name = "Bleed box"
    needs_content = False

    def page(self, page, content):
        need = self.options.min_bleed_mm
        t, b, m = page.trim, page.bleed, page.media
        margins = {
            "left": page.mm(t.x0 - b.x0), "bottom": page.mm(t.y0 - b.y0),
            "right": page.mm(b.x1 - t.x1), "top": page.mm(b.y1 - t.y1),
        }
        defined = page.has_bleed and any(v > EPS_MM for v in margins.values())
        if defined:
            short = {k: v for k, v in margins.items() if v < need - EPS_MM}
            if short:
                detail = ", ".join(f"{k} {max(v, 0):.1f} mm" for k, v in short.items())
                self.add(FAIL, f"Bleed box is under {need:g} mm: {detail}", [page.number])
            return
        room = max(page.mm(t.x0 - m.x0), page.mm(t.y0 - m.y0), page.mm(m.x1 - t.x1), page.mm(m.y1 - t.y1))
        if room > EPS_MM:
            self.add(WARN, "Bleed box not defined, this artwork may not have the required bleed", [page.number])
        else:
            self.add(FAIL, "No bleed: the page ends at the trim", [page.number])
