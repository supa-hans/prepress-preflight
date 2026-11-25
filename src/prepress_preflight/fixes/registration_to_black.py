"""Registration colour inside the trim, made black.

Registration prints on every plate: right for printer's marks, wrong for
artwork, where a "black" rule or word comes out as a four-colour (or more)
build that never registers perfectly. This turns each object the
Registration inside trim check reports into black at the same tint
(Registration 100% becomes K100). Marks outside the trim stay registration.

The trim is the checks' own: taken from the crop marks when they disagree
with the trim box, so what is changed is exactly what the check reported.

The change is bracketed around each object (see bracket.py), so nothing else
takes the new colour. Left alone and reported:
  * a path that also draws outside the trim (marks and artwork as one path);
  * registration mixed with other inks in one DeviceN colour;
  * objects in pattern fills, and form spots that cannot be changed on their
    own (also used outside the trim, or uses that disagree).
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..checks.registration import INSET_PT
from ..checks.white_overprint import LABELS
from ..content import CMYK, walk_page
from .append import AppendError, append_update
from .base import Proposal
from .bracket import count_text, note_hidden, Plan, checked_pages, find_spots, recolour, write_plan

NAME = "registration-to-black"


def _registration(e) -> bool:
    return e.cs.is_registration and e.kind in LABELS


def _tint(e) -> float:
    return float(e.colour[0]) if e.colour else 1.0


def _trims(path, options):
    return {n: page.trim for n, page in checked_pages(path, options).items()}


def _analyse(pdf, trims) -> Plan:
    plan = Plan()

    def flag(number, snap):
        e = snap.event
        if e.colour is not None and len(e.colour) and max(e.colour) <= 0.001:
            return False  # 0% tint puts down no ink
        if e.bbox is None:
            return False
        boxes = e.boxes()
        inside = [b for b in boxes if trims[number].intersects(b, INSET_PT)]
        if not inside:
            return False
        if len(inside) < len(boxes):
            return "the same path also draws outside the trim (marks and artwork together)"
        if len(e.cs.names) > 1:
            return "registration mixed with other inks in one colour"
        return True

    for spot in find_spots(pdf, _registration, flag, plan, every_use=True,
                           elsewhere="in a form that is also used outside the trim", skip_hidden=True):
        sides = {s.event.paint: (0, 0, 0, _tint(s.event)) for _, s in spot.flagged}
        if not recolour(plan, spot, sides, lambda e: LABELS[e.kind]):
            plan.leave(spot, "in a form whose uses disagree on colour")
    plan.targets.sort()
    return plan


def _count_text(counter) -> str:
    return count_text(counter)


def propose_registration_to_black(path: str, options: Options | None = None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    options = options or Options()
    proposal = Proposal(NAME, path)
    trims = _trims(path, options)
    with pikepdf.open(path) as pdf:
        plan = _analyse(pdf, trims)
    for page in sorted(plan.fixed):
        proposal.reasons.append((page, f"Makes registration inside the trim black at the same tint: "
                                       f"{_count_text(plan.fixed[page])}. Marks outside the trim stay registration."))
    note_hidden(proposal, plan)
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} registration object(s) inside the trim left as they are: {reason}"))
    if plan.targets:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets], "tolerance": options.size_tolerance_mm}}
    elif not plan.left:
        proposal.skipped.append((1, "no registration colour inside the trim"))
    return proposal


def _check(src, targets):
    wanted = {(p, n) for p, n, _ in targets}

    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
                before, after = walk_page(pa).events, walk_page(pb).events
                if len(before) != len(after):
                    raise AppendError(f"verification failed: page {number} paints a different number of objects")
                for i, (x, y) in enumerate(zip(before, after)):
                    if (x.kind, x.paint, x.bbox, x.overprint, x.opm, x.blend) != (y.kind, y.paint, y.bbox, y.overprint, y.opm, y.blend):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if (number, i) in wanted:
                        if y.cs != CMYK or tuple(y.colour) != (0, 0, 0, _tint(x)):
                            raise AppendError(f"verification failed: page {number}, object {i} is not black")
                    elif (x.cs, x.colour) != (y.cs, y.colour):
                        raise AppendError(f"verification failed: page {number}, object {i}'s colour changed")
    return check


def write_registration_to_black(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    targets = [tuple(t) for t in proposal.plan[0]["targets"]]
    trims = _trims(proposal.path, Options(size_tolerance_mm=proposal.plan[0]["tolerance"]))
    with pikepdf.open(proposal.path) as pdf:
        plan = _analyse(pdf, trims)
        if plan.targets != targets:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets))
