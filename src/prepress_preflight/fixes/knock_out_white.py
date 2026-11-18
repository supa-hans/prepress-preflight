"""Knock out every white object that is set to overprint.

White set to overprint does not print: it vanishes, and what is beneath shows
through. The owner's rule (2026-11-19): it is never wanted, so this fixes all
of it, the objects the White overprint check reports.

How each object is changed, so that nothing else is: see bracket.py. A new
ExtGState turns off overprint for the side that is white (fill or stroke),
inside `q … Q`, or, in text and clipping paths, followed by one that puts back
the overprint that was in force (which must then be the same at every use).

Left alone, and said so (they stay as fails):
  * objects in pattern fills;
  * objects in a form that is also painted where they are not white (a form
    can inherit its colour), since the change would reach those too;
  * the restore case where uses of a form disagree on overprint;
  * objects in a form with no resources of its own, painted more than once.

After writing, every object in the file is compared: the only differences
allowed are white objects going from overprint to knockout, and every one
that was to be fixed must have changed.
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..checks.white_overprint import LABELS, is_white
from ..content import walk_page
from .append import AppendError, append_update
from .base import Proposal
from .bracket import PATH_PAINT, Plan, add_edit, find_spots, ins, known, write_plan

NAME = "knock-out-white"
FILL_AND_STROKE = {"B", "B*", "b", "b*"}


def _white(e) -> bool:
    return e.kind in LABELS and not e.cs.indexed and is_white(e.cs, e.colour)


def _on_media(e, media) -> bool:
    b = e.bbox
    return b is None or (b[0] < media[2] and b[2] > media[0] and b[1] < media[3] and b[3] > media[1])


def _analyse(pdf) -> Plan:
    plan = Plan()
    media = {n: [float(v) for v in page.mediabox] for n, page in enumerate(pdf.pages, 1)}

    def flag(number, snap):
        return bool(snap.event.overprint and _on_media(snap.event, media[number]))

    for spot in find_spots(pdf, _white, flag, plan, elsewhere="in a form that is also painted where it is not white"):
        fill_off = any(s.event.paint == "fill" for _, s in spot.flagged)
        stroke_off = any(s.event.paint == "stroke" for _, s in spot.flagged)
        paints_fill = spot.op not in PATH_PAINT or spot.op in FILL_AND_STROKE or spot.op in ("f", "F", "f*")
        needs_known = spot.restore or (stroke_off and not fill_off and paints_fill)
        state = known(spot, lambda s: (s.state.op_fill, s.state.op_stroke))
        if needs_known and state is None:
            plan.leave(spot, "in a form whose uses disagree on overprint")
            continue
        op_fill, op_stroke = state or (False, False)
        set_gs = {}
        if fill_off:
            set_gs["/op"] = False
        if stroke_off:
            set_gs["/OP"] = False
            set_gs["/op"] = False if fill_off else (op_fill if needs_known else False)
        back = {"/OP": op_stroke, "/op": op_fill}
        add_edit(plan, spot,
                 lambda add_gs, v=set_gs: [ins("gs", pikepdf.Name(add_gs(v)))],
                 lambda add_gs, v=back: [ins("gs", pikepdf.Name(add_gs(v)))],
                 lambda e: LABELS[e.kind])
    plan.targets.sort()
    return plan


def _count_text(counter) -> str:
    return ", ".join(f"{n} {label}" for label, n in counter.most_common())


def propose_knock_out_white(path: str, options: Options | None = None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        plan = _analyse(pdf)
    for page in sorted(plan.fixed):
        proposal.reasons.append((page, f"Knocks out white set to overprint: {_count_text(plan.fixed[page])}. "
                                       "They will now print white over what is beneath them."))
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} white object(s) set to overprint left as they are: {reason}"))
    if plan.targets:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets]}}
    elif not plan.left:
        proposal.skipped.append((1, "no white objects are set to overprint"))
    return proposal


def _check(src, targets):
    wanted = {(p, n) for p, n, _ in targets}

    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            if len(a.pages) != len(b.pages):
                raise AppendError("verification failed: the number of pages changed")
            for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
                before, after = walk_page(pa).events, walk_page(pb).events
                if len(before) != len(after):
                    raise AppendError(f"verification failed: page {number} paints a different number of objects")
                for i, (x, y) in enumerate(zip(before, after)):
                    if (x.kind, x.paint, x.cs, x.colour, x.bbox, x.opm, x.blend) != (y.kind, y.paint, y.cs, y.colour, y.bbox, y.opm, y.blend):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if x.overprint != y.overprint and not (x.overprint and not y.overprint and _white(x)):
                        raise AppendError(f"verification failed: page {number}, object {i}'s overprint changed")
                    if (number, i) in wanted and y.overprint:
                        raise AppendError(f"verification failed: page {number}, object {i} still overprints")
    return check


def write_knock_out_white(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    targets = [tuple(t) for t in proposal.plan[0]["targets"]]
    with pikepdf.open(proposal.path) as pdf:
        plan = _analyse(pdf)
        if plan.targets != targets:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets))
