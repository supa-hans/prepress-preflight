"""Set the trim box to where the crop marks point.

Changes only TrimBox and BleedBox, and only on pages whose crop marks
disagree with the trim box. The bleed box becomes the new trim grown by the
minimum bleed (Options.min_bleed_mm), kept within the page. MediaBox,
CropBox, ArtBox and the artwork are not touched.
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..content import walk_page
from ..document import Document
from ..marks import implied_trim
from ..model import mm
from .append import append_update
from .base import Change, Proposal

NAME = "trim-from-marks"


def _size(box) -> str:
    return f"{mm(box[2] - box[0]):.1f} × {mm(box[3] - box[1]):.1f} mm"


def _round(box):
    return [round(float(v), 3) for v in box]


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
            trim = _round(box)
            bleed = _round([max(m.x0, box[0] - grow), max(m.y0, box[1] - grow),
                            min(m.x1, box[2] + grow), min(m.y1, box[3] + grow)])
            obj = page.obj.obj
            for key, new in (("TrimBox", trim), ("BleedBox", bleed)):
                old = _round(obj["/" + key]) if "/" + key in obj else None
                if old != new:
                    proposal.changes.append(Change(page.number, key, old, new, _size(old) if old else "not set", _size(new)))
            short = min(box[0] - m.x0, box[1] - m.y0, m.x1 - box[2], m.y1 - box[3])
            note = ""
            if short < grow - 0.01:
                note = f" The page only allows {mm(short):.1f} mm of bleed, so the bleed box stops at the page edge."
            proposal.reasons.append((page.number, f"Crop marks point at {_size(box)}; the trim box is {_size([t.x0, t.y0, t.x1, t.y1])}."
                                                  + note))
    finally:
        doc.close()
    return proposal


def write_trim_from_marks(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    by_page: dict[int, dict] = {}
    for c in proposal.changes:
        by_page.setdefault(c.page, {})[c.box] = c.after
    with pikepdf.open(proposal.path) as pdf:
        changes = {}
        for number, boxes in by_page.items():
            page = pdf.pages[number - 1].obj
            copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
            for key, value in boxes.items():
                copy["/" + key] = pikepdf.Array(value)
            changes[page.objgen] = copy
        # The copies refer to objects of this open file; serialise them while it is open.
        append_update(proposal.path, dst, changes)
