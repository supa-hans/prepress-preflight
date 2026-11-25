"""Small text in rich black, made K100 only.

Why: each ink prints from its own plate, and the plates never land exactly on
each other. Type 8 pt and under built from four inks (InDesign's rich black is
often C75 M68 Y67 K90) blurs and shows coloured fringes; K alone prints it
sharp, and at this size looks just as black. The Small text inks check warns
about exactly these objects; this fixes them.

Rich black: K 85% or more with any C, M or Y at 10% or more. Small: up to 8 pt
as placed (`--max-text` changes it). Only the colour changes, to C0 M0 Y0
K100; overprint stays as it was (run overprint-black after it to overprint the
new black too). Other small text in several inks is a deliberate colour more
often than not, so it is left.

Each object is bracketed (see bracket.py), so nothing else takes the colour.
Left alone and said so: objects in pattern fills, and form spots that cannot
be changed on their own (the form is also painted larger, or its uses
disagree on the colour to put back).
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..checks.small_text_inks import SMALL_TEXT_PT, is_rich_black
from ..content import CMYK, walk_page
from .append import AppendError, append_update
from .base import Proposal
from .bracket import count_text, note_hidden, Plan, find_spots, recolour, write_plan

NAME = "rich-black-text"
K100 = (0.0, 0.0, 0.0, 1.0)


def _candidate(e) -> bool:
    return e.kind == "text" and is_rich_black(e)


def _target(e, max_text) -> bool:
    return (_candidate(e) and e.size is not None and e.size <= max_text
            and not (e.loc is not None and e.loc[2] in (3, 7)))


def _analyse(pdf, max_text) -> Plan:
    plan = Plan()
    for spot in find_spots(pdf, _candidate, lambda n, s: _target(s.event, max_text), plan, every_use=True,
                           elsewhere="in a form that is also painted larger", skip_hidden=True):
        sides = {s.event.paint: K100 for _, s in spot.flagged}
        if not recolour(plan, spot, sides, lambda e: "text object(s)" if e.paint == "fill" else "outlined text object(s)"):
            plan.leave(spot, "in a form whose uses disagree on colour")
    plan.targets.sort()
    return plan


def _max(max_text) -> float:
    v = SMALL_TEXT_PT if max_text is None else float(max_text)
    if v <= 0:
        raise ValueError("--max-text must be more than 0")
    return v


def propose_rich_black_text(path: str, options: Options | None = None, max_text=None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    max_text = _max(max_text)
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        plan = _analyse(pdf, max_text)
    for page in sorted(plan.fixed):
        detail = count_text(plan.fixed[page])
        proposal.reasons.append((page, f"Makes small rich-black text (up to {max_text:g} pt) K100 only: {detail}. "
                                       "One plate instead of four, so it prints sharp whatever the registration."))
    note_hidden(proposal, plan)
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} small rich-black text object(s) left as they are: {reason}"))
    if plan.targets:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets], "max_text": max_text}}
    elif not plan.left:
        proposal.skipped.append((1, f"no rich-black text up to {max_text:g} pt"))
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
                    if (x.kind, x.paint, x.bbox, x.overprint, x.opm, x.blend, x.size) != (y.kind, y.paint, y.bbox, y.overprint, y.opm, y.blend, y.size):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if (number, i) in wanted:
                        if y.cs != CMYK or tuple(y.colour) != K100:
                            raise AppendError(f"verification failed: page {number}, object {i} is not K100")
                    elif (x.cs, x.colour) != (y.cs, y.colour):
                        raise AppendError(f"verification failed: page {number}, object {i}'s colour changed")
    return check


def write_rich_black_text(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    targets = [tuple(t) for t in proposal.plan[0]["targets"]]
    with pikepdf.open(proposal.path) as pdf:
        plan = _analyse(pdf, proposal.plan[0]["max_text"])
        if plan.targets != targets:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets))
