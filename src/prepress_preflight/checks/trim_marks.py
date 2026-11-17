from __future__ import annotations

from ..model import NOTE, WARN
from .base import Check


class TrimMarkCheck(Check):
    """Do the crop marks on the page agree with the trim box?

    The trim box is what every other check measures from. When the marks point
    at a different rectangle, the marks are taken as the trim (see
    marks.infer_trim, applied by the engine before any check runs) and this
    check says so: every bleed, margin, ink and registration result on that
    page is then measured from the marks, not from the file's trim box.

    Limits: only straight registration-colour marks in the usual corner
    arrangement are recognised. Marks drawn in black, or as filled shapes, are
    not seen, and a page without recognisable marks says nothing.
    """

    id = "trim-marks"
    name = "Trim marks"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.agree: list[int] = []
        self.inferred: dict[str, list] = {}

    def page(self, page, content):
        if page.marks_trim is None:
            return
        if not page.trim_inferred:
            self.agree.append(page.number)
            return
        tol = self.options.size_tolerance_mm
        marks, declared = page.trim, page.declared_trim
        w, h = page.mm(marks.width), page.mm(marks.height)
        dw, dh = page.mm(declared.width), page.mm(declared.height)
        if not page.has_trim:
            was = f"the file has no trim box and its page is {dw:.1f} × {dh:.1f} mm"
        elif abs(w - dw) <= tol and abs(h - dh) <= tol:
            off = max(abs(page.mm(a - b)) for a, b in zip(marks.as_list(), declared.as_list()))
            was = f"the file's trim box is the same size but sits {off:.1f} mm away"
        else:
            was = f"the file's trim box says {dw:.1f} × {dh:.1f} mm"
        message = (f"Trim inferred from the crop marks: {w:.1f} × {h:.1f} mm ({was}). "
                   "Bleed, margins, ink and registration are measured from the marks")
        self.inferred.setdefault(message, []).append((page.number, marks.as_list()))

    def finish(self):
        for message, hits in self.inferred.items():
            self.add(WARN, message, [p for p, _ in hits], hits)
        if self.agree:
            self.add(NOTE, "Trim marks agree with the trim box", self.agree)
        return self.findings
