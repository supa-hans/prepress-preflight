from __future__ import annotations

from ..edges import scan_edges, spine_edge
from ..model import FAIL, NOTE, UNCHECKED
from .base import Check

NONE_MM = 0.3  # bleed shallower than this is reported as none


class BleedArtworkCheck(Check):
    """Does the artwork itself bleed, wherever it reaches the trim?

    The Bleed box check only reads the declared box. This one looks at what is
    painted: on each edge, wherever ink comes within 0.5 mm of the trim, the ink
    must carry on unbroken for the required depth past it. An edge that is
    white at the trim needs no bleed.

    With Options.bound the spine edge of each page is not asked to bleed.

    Limits: it tests that ink is there, not that it continues the same image.
    Tints under 5% count as white. Non-printing spot colours are recognised by
    name and left out.
    """

    id = "bleed-artwork"
    name = "Bleed artwork"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.short: list[tuple] = []  # (page number, EdgeScan)

    def page(self, page, content):
        try:
            scan = scan_edges(self.doc, page, content, self.options.min_bleed_mm)
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Bleed could not be measured ({type(exc).__name__})", [page.number])
            return
        if scan.excluded:
            self.add(NOTE, "Left out of the bleed measurement as non-printing: " + ", ".join(scan.excluded), [page.number])
        skip = spine_edge(page.number) if self.options.bound else None
        self.short.extend((page.number, e) for e in scan.edges.values() if e.short_mm > 0 and e.name != skip)

    def finish(self):
        # One line for "no bleed" and one for "too little", each led by the worst case.
        need = self.options.min_bleed_mm
        if self.options.bound:
            self.add(NOTE, "Bound job: spine edges were not asked to bleed")
        # In a multi-page file, bleed missing only on spine-side edges is normal for bound work.
        hint = ""
        if len(self.doc.pages) > 2 and self.short and all(e.name == spine_edge(p) for p, e in self.short):
            hint = ". All of it is on the spine side; if the job is bound, run with --bound"
        for label, group in (
            ("with no bleed", [(p, e) for p, e in self.short if e.least_mm < NONE_MM]),
            (f"with under {need:g} mm of bleed", [(p, e) for p, e in self.short if e.least_mm >= NONE_MM]),
        ):
            if not group:
                continue
            pages = sorted({p for p, _ in group})
            if len(pages) == 1:
                detail = ", ".join(self._edge(e) for _, e in group)
            else:
                p, e = max(group, key=lambda pe: pe[1].short_mm)
                detail = f"worst is page {p}, {self._edge(e)}"
            self.add(FAIL, f"Artwork reaches the trim {label}: {detail}{hint}", pages, [(p, e.box) for p, e in group])
        return self.findings

    @staticmethod
    def _edge(e):
        depth = "" if e.least_mm < NONE_MM else f" {e.least_mm:.1f} mm deep"
        return f"{e.name}{depth} ({e.short_mm:.0f} of {e.length_mm:.0f} mm)"
