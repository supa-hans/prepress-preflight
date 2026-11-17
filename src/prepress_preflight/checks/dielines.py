from __future__ import annotations

from ..model import NOTE, WARN
from .base import Check

# Spot names that mark a cutting or finishing line, as distinct from other
# non-printing spots such as varnish plates or templates.
CUT_WORDS = ("dieline", "diecut", "cutcontour", "cutter", "thrucut", "kisscut", "keyline", "crease", "perf")


def is_cut_line(name: str) -> bool:
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    return any(word in key for word in CUT_WORDS)


class DielineCheck(Check):
    """Measure dielines and check that they overprint.

    Reports the overall size of each cutting or finishing line, so it can be
    compared with the job spec. A dieline that is not set to overprint knocks
    its own shape out of the artwork beneath, leaving a white line when the
    dieline plate is dropped; that is a warning.

    Limits: dielines are recognised by spot colour name. The size is that of
    the objects' outer bounds, not a measured cutting path, and clipping is not
    applied. Whether artwork actually lies under a knocking-out dieline is not
    tested.
    """

    id = "dielines"
    name = "Dielines"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.sizes: dict[tuple, list] = {}
        self.knockout: dict[str, set] = {}

    def page(self, page, content):
        boxes: dict[str, list] = {}
        for e in content.events:
            if e.cs.kind != "sep":
                continue
            names = [n for n in e.cs.names if n != "None"]
            if not names or not all(is_cut_line(n) for n in names):
                continue
            if e.colour is not None and len(e.colour) and max(e.colour) <= 0.001:
                continue  # 0% tint: nothing there
            name = " + ".join(names)
            if not e.overprint:
                self.knockout.setdefault(name, set()).add(page.number)
            if e.bbox is not None:
                b = boxes.setdefault(name, list(e.bbox))
                b[0], b[1] = min(b[0], e.bbox[0]), min(b[1], e.bbox[1])
                b[2], b[3] = max(b[2], e.bbox[2]), max(b[3], e.bbox[3])
        tol = self.options.size_tolerance_mm
        for name, b in boxes.items():
            w, h = page.mm(b[2] - b[0]), page.mm(b[3] - b[1])
            tw, th = page.mm(page.trim.width), page.mm(page.trim.height)
            same = abs(w - tw) <= tol and abs(h - th) <= tol
            text = f"Dieline \"{name}\" measures {w:.1f} × {h:.1f} mm" + (", the size of the trim box" if same else "")
            self.sizes.setdefault((name, text), []).append((page.number, b))

    def finish(self):
        for name, pages in self.knockout.items():
            self.add(WARN, f"Dieline \"{name}\" is not set to overprint: it will knock a white line out of any artwork beneath it",
                     sorted(pages))
        for (_, text), hits in self.sizes.items():
            self.add(NOTE, text, [p for p, _ in hits], hits)
        return self.findings
