from __future__ import annotations

from collections import Counter

from ..model import WARN
from .base import Check

LABELS = {"image": "images", "path": "vector objects", "text": "text objects", "shading": "gradients",
          "imagemask": "vector objects"}


class RGBCheck(Check):
    """Flag RGB (and Lab) objects in the printed area. A warning, never a fail."""

    id = "rgb"
    name = "RGB"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.counts: Counter = Counter()
        self.pages: list[int] = []

    def page(self, page, content):
        area = page.print_area
        hit = False
        for e in content.events:
            if e.cs.kind not in ("rgb", "lab"):
                continue
            if e.bbox is not None and not area.intersects(e.bbox):
                continue  # entirely outside the printed area (slug)
            self.counts[LABELS.get(e.kind, "objects")] += 1
            hit = True
        if hit:
            self.pages.append(page.number)

    def finish(self):
        if self.counts:
            detail = ", ".join(f"{n} {label}" for label, n in self.counts.most_common())
            self.add(WARN, f"RGB objects found: {detail}", self.pages)
        return self.findings
