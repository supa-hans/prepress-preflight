"""Set the trim box to where the crop marks point.

Changes only TrimBox and BleedBox, and only on pages whose crop marks
disagree with the trim box. The bleed box becomes the new trim grown by the
minimum bleed (Options.min_bleed_mm), kept within the page. MediaBox,
CropBox, ArtBox and the artwork are not touched.
"""
from __future__ import annotations

from ..checks.base import Options
from ..content import walk_page
from ..document import Document
from ..marks import implied_trim
from ..model import mm
from .base import Proposal, box_change, size_text, write_box_changes

NAME = "trim-from-marks"


def propose_trim_from_marks(path: str, options: Options | None = None) -> Proposal:
    """What the fix would change. Reads only."""
    options = options or Options()
    proposal = Proposal(NAME, path)
    doc = Document(path)
    try:
        grow = options.min_bleed_mm * 72 / 25.4
        for page in doc.pages:
            if page.unit != 1.0:
                proposal.skipped.append((page.number, "the page uses a UserUnit; not handled by this fix yet"))
                continue
            box = implied_trim(walk_page(page.obj))
            if box is None:
                continue
            t, m = page.trim, page.media
            off_mm = max(abs(mm(a - b)) for a, b in zip(box, (t.x0, t.y0, t.x1, t.y1)))
            if off_mm <= options.size_tolerance_mm:
                continue
            if box[0] < m.x0 - 0.5 or box[1] < m.y0 - 0.5 or box[2] > m.x1 + 0.5 or box[3] > m.y1 + 0.5:
                proposal.skipped.append((page.number, "the crop marks point outside the page"))
                continue
            bleed = [max(m.x0, box[0] - grow), max(m.y0, box[1] - grow), min(m.x1, box[2] + grow), min(m.y1, box[3] + grow)]
            box_change(proposal, page.number, page.obj.obj, "TrimBox", box)
            box_change(proposal, page.number, page.obj.obj, "BleedBox", bleed)
            short = min(box[0] - m.x0, box[1] - m.y0, m.x1 - box[2], m.y1 - box[3])
            note = ""
            if short < grow - 0.01:
                note = f" The page only allows {mm(short):.1f} mm of bleed, so the bleed box stops at the page edge."
            proposal.reasons.append((page.number, f"Crop marks point at {size_text(box)}; the trim box is {size_text([t.x0, t.y0, t.x1, t.y1])}."
                                                  + note))
    finally:
        doc.close()
    return proposal


def write_trim_from_marks(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    write_box_changes(proposal, dst)
