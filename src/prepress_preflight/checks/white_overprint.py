from __future__ import annotations

from collections import Counter

from ..model import FAIL
from .base import Check

LABELS = {"path": "vector object(s)", "text": "text object(s)", "imagemask": "vector object(s)"}


def is_white(cs, colour) -> bool:
    if colour is None or len(colour) == 0:
        return False
    if cs.kind == "gray":
        return len(colour) == 1 and colour[0] >= 0.999
    if cs.kind == "cmyk":
        return len(colour) == 4 and max(colour) <= 0.001
    if cs.kind == "rgb":
        return len(colour) == 3 and min(colour) >= 0.999
    if cs.kind == "sep" and not cs.is_registration and cs.names != ("None",):
        return max(colour) <= 0.001  # a spot or DeviceN colour at 0% tint
    return False


class WhiteOverprintCheck(Check):
    """White objects set to overprint do not print. Always a fail."""

    id = "white-overprint"
    name = "White overprint"

    def page(self, page, content):
        counts: Counter = Counter()
        locations = []
        for e in content.events:
            if not e.overprint or e.kind not in LABELS or e.cs.indexed:
                continue
            if not is_white(e.cs, e.colour):
                continue
            if e.bbox is not None and not page.media.intersects(e.bbox):
                continue
            counts[LABELS[e.kind]] += 1
            if e.bbox is not None:
                locations.append((page.number, list(e.bbox)))
        if counts:
            detail = ", ".join(f"{n} {label}" for label, n in counts.most_common())
            self.add(FAIL, f"White set to overprint, it will not print: {detail}", [page.number], locations)
