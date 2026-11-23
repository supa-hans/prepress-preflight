"""Set dielines (or other named spot colours) to overprint.

    preflight fix overprint-spot FILE                    (every dieline / cutting line)
    preflight fix overprint-spot FILE --spot "Varnish"   (a named spot; repeatable)

Why: a dieline that knocks out cuts its own shape out of the artwork beneath.
The dieline plate is never printed, so if the RIP still images the knock-out
the job carries a white hairline exactly where the cutter will run, and any
misregistration of the cut shows it. Set to overprint, the dieline sits on top
and the artwork beneath prints whole. The same holds for a varnish or foil
plate, which is why other spots can be named.

Dielines are recognised by name as the Dielines check does (dieline, cut
contour, crease, perf ...). Only objects painted wholly in the chosen spots,
at more than 0%, change; each is bracketed (see bracket.py), so nothing else
does. The overprint mode is not touched: it only matters for CMYK.

Left alone and said so: a path filled and stroked where only one side is the
spot and the other side's overprint would change with it, and objects in a
form whose uses disagree or which is also painted in other colours.
"""
from __future__ import annotations

from collections import Counter

import pikepdf

from ..checks.base import Options
from ..checks.dielines import is_cut_line
from ..content import walk_page
from .append import AppendError, append_update
from .base import Proposal
from .bracket import Plan, add_edit, find_spots, ins, known, write_plan

NAME = "overprint-spot"
FILL_AND_STROKE = {"B", "B*", "b", "b*"}
NAMES = {("path", "fill"): "shape(s)", ("path", "stroke"): "line(s)", ("text", "fill"): "text object(s)",
         ("text", "stroke"): "outlined text object(s)", ("imagemask", "fill"): "image mask(s)"}


def _key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _matcher(spots):
    if not spots:
        return is_cut_line, "dielines"
    wanted = {_key(s) for s in spots}
    return (lambda n: _key(n) in wanted), ", ".join(spots)


def _in_spots(e, match) -> bool:
    if e.kind not in ("path", "text", "imagemask") or e.cs.kind != "sep":
        return False
    names = [n for n in e.cs.names if n != "None"]
    return bool(names) and all(match(n) for n in names)


def _analyse(pdf, match) -> tuple[Plan, set]:
    plan, seen = Plan(), set()

    def select(e):
        return _in_spots(e, match)

    def flag(number, snap):
        e = snap.event
        seen.add(" + ".join(n for n in e.cs.names if n != "None"))
        if e.colour is not None and len(e.colour) and max(e.colour) <= 0.001:
            return False  # 0% tint: nothing there
        return not e.overprint

    for spot in find_spots(pdf, select, flag, plan, every_use=True,
                           elsewhere="in a form that is also painted where it is not this spot"):
        sides = {s.event.paint for _, s in spot.flagged}
        fill_on, stroke_on = "fill" in sides, "stroke" in sides
        state = known(spot, lambda s: (s.state.op_fill, s.state.op_stroke))
        if spot.op in FILL_AND_STROKE and len(sides) == 1:
            plan.leave(spot, "a path filled and stroked where only one side is this spot")
            continue
        needs_known = spot.restore or (stroke_on and not fill_on)
        if needs_known and state is None:
            plan.leave(spot, "in a form whose uses disagree on overprint")
            continue
        op_fill, op_stroke = state or (False, False)
        set_gs = {}
        if fill_on:
            set_gs["/op"] = True
        if stroke_on:
            set_gs["/OP"] = True
            set_gs["/op"] = True if fill_on else op_fill  # /OP alone would set the fill's overprint too
        back = {"/OP": op_stroke, "/op": op_fill}
        add_edit(plan, spot,
                 lambda add_gs, v=set_gs: [ins("gs", pikepdf.Name(add_gs(v)))],
                 lambda add_gs, v=back: [ins("gs", pikepdf.Name(add_gs(v)))],
                 lambda e: NAMES[(e.kind, e.paint)])
    plan.targets.sort()
    return plan, seen


def _spot_names(pdf) -> set:
    names = set()
    for page in pdf.pages:
        for e in walk_page(page).events:
            if e.cs.kind == "sep":
                names |= {n for n in e.cs.names if n != "None"}
    return names


def propose_overprint_spot(path: str, options: Options | None = None, spots=None, **_) -> Proposal:
    """What the fix would change. Reads only. `spots`: names; none means every dieline."""
    match, what = _matcher(spots or [])
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        if spots:
            present = _spot_names(pdf)
            missing = [s for s in spots if _key(s) not in {_key(n) for n in present}]
            if missing:
                raise ValueError(f"no spot colour named {', '.join(missing)} is used "
                                 f"(the file's spots: {', '.join(sorted(present)) or 'none'})")
        plan, seen = _analyse(pdf, match)
    for page in sorted(plan.fixed):
        counts = Counter(plan.fixed[page])
        names = ", ".join(sorted(seen))
        proposal.reasons.append((page, f"Sets {names} to overprint: "
                                       + ", ".join(f"{n} {label}" for label, n in counts.most_common())
                                       + ". The artwork beneath then prints whole instead of having the shape knocked out."))
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} object(s) left knocking out: {reason}"))
    if plan.targets:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets], "spots": list(spots or [])}}
    elif not plan.left:
        proposal.skipped.append((1, (f"nothing in {what} knocks out" if spots else "every dieline already overprints")
                                    if seen or spots else "no dielines in the file"))
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
                    if (x.kind, x.paint, x.cs, x.colour, x.bbox, x.blend, x.width) != (y.kind, y.paint, y.cs, y.colour, y.bbox, y.blend, y.width):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if (number, i) in wanted:
                        if not y.overprint:
                            raise AppendError(f"verification failed: page {number}, object {i} does not overprint")
                    elif (x.overprint, x.overprint and x.opm) != (y.overprint, y.overprint and y.opm):
                        raise AppendError(f"verification failed: page {number}, object {i}'s overprint changed")
    return check


def write_overprint_spot(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan0 = proposal.plan[0]
    targets = [tuple(t) for t in plan0["targets"]]
    with pikepdf.open(proposal.path) as pdf:
        plan, _ = _analyse(pdf, _matcher(plan0["spots"])[0])
        if plan.targets != targets:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets))
