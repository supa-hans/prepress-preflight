from __future__ import annotations

from ..edges import scan_edges, spine_edge
from ..model import FAIL, NOTE, WARN
from .base import Check

EPS_MM = 0.05  # boxes this close are treated as equal


class BleedBoxCheck(Check):
    """Check the declared bleed box.

    A missing or short bleed box only fails where artwork reaches the trim on
    that edge: an edge that is white at the trim needs no bleed. Whether the
    artwork really fills the bleed is the Bleed artwork check's job.
    """

    id = "bleed-box"
    name = "Bleed box"

    def _reaching(self, page, content):
        """Edges where artwork reaches the trim, or None if that could not be measured."""
        try:
            scan = scan_edges(self.doc, page, content, self.options.min_bleed_mm)
        except Exception:
            return None  # stay strict: the Bleed artwork check reports the reason
        skip = spine_edge(page.number) if self.options.bound else None
        return {name for name, e in scan.edges.items() if e.reaches_trim and name != skip}

    def page(self, page, content):
        need = self.options.min_bleed_mm
        reaching = self._reaching(page, content)
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
                if reaching is not None and not reaching & set(short):
                    self.add(NOTE, f"Bleed box is under {need:g} mm ({detail}), but no artwork reaches those edges", [page.number])
                else:
                    self.add(FAIL, f"Bleed box is under {need:g} mm: {detail}", [page.number])
            return
        room = max(page.mm(t.x0 - m.x0), page.mm(t.y0 - m.y0), page.mm(m.x1 - t.x1), page.mm(m.y1 - t.y1))
        if room > EPS_MM:
            self.add(WARN, "Bleed box not defined, this artwork may not have the required bleed", [page.number])
        elif reaching is not None and not reaching:
            self.add(NOTE, "No bleed, and none needed: no artwork reaches the trim", [page.number])
        else:
            self.add(FAIL, "No bleed: the page ends at the trim", [page.number])
