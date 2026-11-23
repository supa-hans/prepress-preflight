"""Transparency in use. A note.

Objects drawn at less than 100% opacity, under a soft mask, or images with an
alpha channel (soft edges, cut-out shadows) are live transparency: the RIP
must flatten them where they overlap other artwork. Modern RIPs (Adobe PDF
Print Engine) do it natively and well; older ones flatten crudely, and some
jobs must be supplied as PDF/X-1a, which allows no transparency at all. So it
is listed, never judged: whether it matters depends on the printer.

Blend modes are listed by their own check. Objects that are fully transparent
(opacity 0) are counted apart: they print nothing. The yearbook has black
picture-frame boxes at opacity 0, placeholders left by the layout tool.
"""
from __future__ import annotations

from collections import Counter

from ..model import NOTE
from .base import Check


class TransparencyCheck(Check):
    """Objects with opacity under 100%, soft masks, and images with alpha. Informational."""

    id = "transparency"
    name = "Transparency"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.pages: dict[str, list[int]] = {}

    def page(self, page, content):
        counts: Counter = Counter()
        locations = []
        for e in content.events:
            if e.bbox is not None and not page.media.intersects(e.bbox):
                continue
            kinds = []
            if e.opacity is not None and e.opacity < 0.999:
                kinds.append("invisible object(s) (opacity 0)" if e.opacity <= 0.001 else "object(s) at under 100% opacity")
            if e.smask:
                kinds.append("object(s) under a soft mask")
            if e.alpha:
                kinds.append("image(s) with an alpha channel")
            for k in kinds:
                counts[k] += 1
            if kinds and e.bbox is not None:
                locations.append((page.number, list(e.bbox)))
        if counts:
            detail = ", ".join(f"{n} {label}" for label, n in counts.most_common())
            self.add(NOTE, f"Transparency in use: {detail}. The RIP flattens it; PDF/X-1a allows none",
                     [page.number], locations)
