"""Set the trim box to the job size and the bleed box to the required bleed.

PitStop's "set page boxes", for files whose trim box is missing or wrong:
Canva exports where the page is the trim, files saved with the page size as
the trim box, or a bleed box that is too small or missing.

The new trim is centred where the current trim is. Its size is the job size
(Options.expected_trim_mm, `--trim`), turned to match the current trim's
orientation; without a job size the current trim size is kept and only the
bleed box is set. The bleed box becomes the trim plus Options.min_bleed_mm,
kept within the page. MediaBox, CropBox, ArtBox and the artwork are not
touched, so a page with no room around the trim cannot gain bleed here: that
needs artwork changes, which this fix does not make.
"""
from __future__ import annotations

from ..checks.base import Options
from ..content import walk_page
from ..document import Document
from ..marks import implied_trim
from ..model import mm
from .base import Proposal, box_change, size_text, write_box_changes

NAME = "set-boxes"
PT_PER_MM = 72 / 25.4


def _same(a, b) -> bool:
    return all(abs(x - y) < 0.01 for x, y in zip(a, b))


def propose_set_boxes(path: str, options: Options | None = None) -> Proposal:
    """What the fix would change. Reads only."""
    options = options or Options()
    proposal = Proposal(NAME, path)
    doc = Document(path)
    try:
        grow = options.min_bleed_mm * PT_PER_MM
        for page in doc.pages:
            if page.unit != 1.0:
                proposal.skipped.append((page.number, "the page uses a UserUnit; not handled by this fix yet"))
                continue
            t, m = page.trim, page.media
            cx, cy = (t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2
            w, h = t.width, t.height
            turned = False
            if options.expected_trim_mm:
                w, h = (v * PT_PER_MM for v in options.expected_trim_mm)
                if (w > h) != (t.width > t.height) and abs(t.width - t.height) > 1:
                    w, h, turned = h, w, True
            trim = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
            if trim[0] < m.x0 - 0.5 or trim[1] < m.y0 - 0.5 or trim[2] > m.x1 + 0.5 or trim[3] > m.y1 + 0.5:
                proposal.skipped.append((page.number, f"a {size_text(trim)} trim does not fit on the "
                                                      f"{size_text([m.x0, m.y0, m.x1, m.y1])} page"))
                continue
            trim = [max(trim[0], m.x0), max(trim[1], m.y0), min(trim[2], m.x1), min(trim[3], m.y1)]
            bleed = [max(m.x0, trim[0] - grow), max(m.y0, trim[1] - grow), min(m.x1, trim[2] + grow), min(m.y1, trim[3] + grow)]
            if page.has_bleed and _same(trim, [t.x0, t.y0, t.x1, t.y1]):
                # Never shrink a bleed box that already gives more than the minimum.
                # (When the trim itself is being reset, the old bleed box was
                # drawn around the wrong trim, so it is replaced outright.)
                b = page.bleed
                bleed = [min(bleed[0], b.x0), min(bleed[1], b.y0), max(bleed[2], b.x1), max(bleed[3], b.y1)]
                bleed = [max(m.x0, bleed[0]), max(m.y0, bleed[1]), min(m.x1, bleed[2]), min(m.y1, bleed[3])]
            room = min(trim[0] - m.x0, trim[1] - m.y0, m.x1 - trim[2], m.y1 - trim[3])
            before = len(proposal.changes)
            # A box missing from the file already means something (the trim
            # defaults to the page). Writing it out with that same value would
            # be a change that changes nothing, so only write it when the value
            # differs, or when the job size was asked for.
            effective_trim = [t.x0, t.y0, t.x1, t.y1]
            effective_bleed = [page.bleed.x0, page.bleed.y0, page.bleed.x1, page.bleed.y1]
            if options.expected_trim_mm or page.has_trim or not _same(trim, effective_trim):
                box_change(proposal, page.number, page.obj.obj, "TrimBox", trim)
            if page.has_bleed or not _same(bleed, effective_bleed):
                box_change(proposal, page.number, page.obj.obj, "BleedBox", bleed)
            if len(proposal.changes) == before:
                if room < 0.01 and options.min_bleed_mm > 0:
                    marks = implied_trim(walk_page(page.obj))
                    if marks is not None and not _same(marks, effective_trim):
                        why = (f"the crop marks point at {size_text(marks)}, not at the trim box; "
                               "use trim-from-marks instead")
                    else:
                        why = "the page ends at the trim, so there is no room for a bleed box; adding bleed means changing the artwork"
                    proposal.skipped.append((page.number, why))
                continue
            notes = []
            if turned:
                notes.append("the job size was turned to match the page's orientation")
            if room < grow - 0.01:
                notes.append(f"the page only allows {max(mm(room), 0):.1f} mm of bleed, so the bleed box stops at the page edge")
            proposal.reasons.append((page.number, f"Trim {size_text(trim)}, bleed {options.min_bleed_mm:g} mm"
                                                  + ("; " + "; ".join(notes) if notes else "") + "."))
    finally:
        doc.close()
    return proposal


def write_set_boxes(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    write_box_changes(proposal, dst)
