"""Thicken lines thinner than 0.25 pt to 0.25 pt.

The lines are the ones the Hairlines check reports, chosen the same way:
stroke weight as placed on the page (after scaling, and UserUnit) under
0.25 pt, leaving out white strokes, non-printing spot colours (dielines) and
anything wholly outside the printed area. A width of 0 ("thinnest the device
can draw") counts as thin.

Only open lines are thickened (owner's decision, 2026-11-19): rules and
strokes that do not close a shape. A stroke that outlines a closed shape (a
rectangle, a path closed with h, s or b, or one that ends where it started)
is left: in illustrations those are often hair outlines on many small shapes,
and at 0.25 pt they draw where the artist meant them to vanish (Little Stars,
page 8: grey rims and white notches on a zebra). Outlined text counts as
closed shapes. They are listed in the preview, and the check still reports
them.

Each line gets exactly the width that prints at 0.25 pt at its own scale,
bracketed around that line only (see bracket.py), so no other line's weight
changes. Left alone and reported:
  * lines in pattern fills (not measured by the check either);
  * a line in a form that is drawn at different sizes, so needs a different
    width at each use, or that is also drawn where it is not thin.
"""
from __future__ import annotations

import math

import pikepdf

from ..checks.base import Options
from ..checks.hairlines import EPS, MIN_PT
from ..checks.white_overprint import is_white
from ..content import walk_page
from ..edges import is_non_printing
from .append import AppendError, append_update
from .base import Proposal
from .bracket import Plan, add_edit, checked_pages, find_spots, ins, known, write_plan
from .spot_to_cmyk import _num

NAME = "thicken-hairlines"
SHORT_PT = 72 / 25.4  # lines under 1 mm long are usually illustration detail, not rules
SHORT_SHARE = 0.25  # above this share, the preview asks for a look


def _scale(state) -> float:
    a, b, c, d = state.ctm[:4]
    return math.sqrt(abs(a * d - b * c))


def _line(e) -> bool:
    """A stroke the check measures (whatever its width)."""
    if e.paint != "stroke" or e.width is None or e.cs.indexed or is_white(e.cs, e.colour):
        return False
    names = [n for n in e.cs.names if n != "None"]
    return not (e.cs.kind == "sep" and names and all(is_non_printing(n) for n in names))


def _numbers(operands) -> list[float]:
    out = []
    for o in operands:
        try:
            out.append(float(o))
        except (TypeError, ValueError):
            pass
    return out


def _openness(instructions, start, index) -> str:
    """'open' when every part of the path is an open line, 'closed' when every
    part closes a shape, 'mixed' otherwise.

    A part is closed when it is a rectangle (re), is closed with h (or by the
    s, b, b* paint operators), or has length and ends where it started.
    """
    parts = []  # per subpath: {"first", "last", "segments", "closed"}
    for ins_ in instructions[start:index + 1]:
        op, v = str(ins_.operator), _numbers(ins_.operands)
        if op == "m" and len(v) >= 2:
            parts.append({"first": (v[0], v[1]), "last": (v[0], v[1]), "segments": 0, "closed": False})
        elif op == "re":
            parts.append({"first": None, "last": None, "segments": 4, "closed": True})
        elif op in ("l", "c", "v", "y") and len(v) >= 2:
            if not parts:
                parts.append({"first": (v[-2], v[-1]), "last": (v[-2], v[-1]), "segments": 0, "closed": False})
            parts[-1]["last"] = (v[-2], v[-1])
            parts[-1]["segments"] += 1
        elif op in ("h", "s", "b", "b*") and parts:
            parts[-1]["closed"] = True
    parts = [p for p in parts if p["segments"]]  # a lone moveto draws nothing
    if not parts:
        return "closed"

    def closed(p):
        if p["closed"]:
            return True
        (x0, y0), (x1, y1) = p["first"], p["last"]
        return abs(x0 - x1) < 1e-3 and abs(y0 - y1) < 1e-3

    flags = [closed(p) for p in parts]
    return "open" if not any(flags) else "closed" if all(flags) else "mixed"


def _analyse(pdf, pages) -> Plan:
    plan = Plan()

    def flag(number, snap):
        e, page = snap.event, pages[number]
        if e.width * page.unit >= MIN_PT - EPS:
            return False
        return e.bbox is None or page.print_area.intersects(e.bbox)

    def needed(s, unit):
        scale = _scale(s.state) * unit
        return round(MIN_PT / scale, 6) if scale > 1e-9 else None

    for spot in find_spots(pdf, _line, flag, plan, every_use=True,
                           elsewhere="in a form that is also drawn where the line is not thin"):
        if spot.flagged[0][1].event.kind == "text":
            plan.leave(spot, "outlined text (closed shapes)")
            continue
        shape = _openness(spot.instructions, spot.start, spot.index)
        if shape != "open":
            plan.leave(spot, "outlines a closed shape" if shape == "closed" else "the same path also outlines a closed shape")
            continue
        widths = {needed(s, pages[n].unit) for n, s, _ in spot.found}
        if None in widths or max(widths) > min(widths) * 1.01:
            plan.leave(spot, "in a form drawn at different sizes, so it would need a different width at each")
            continue
        width = max(widths)
        old = known(spot, lambda s: s.state.lw)
        if spot.restore and old is None:
            plan.leave(spot, "in a form whose uses disagree on line width")
            continue
        for n, s in spot.flagged:
            b = s.event.bbox
            if b is not None and math.hypot(b[2] - b[0], b[3] - b[1]) < SHORT_PT:
                plan.fixed[n]["short"] += 1
        add_edit(plan, spot, lambda add_gs, w=width: [ins("w", _num(w))],
                 lambda add_gs, w=old: [ins("w", _num(w))], lambda e: "line(s)")
    plan.targets.sort()
    return plan


def propose_thicken_hairlines(path: str, options: Options | None = None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    options = options or Options()
    proposal = Proposal(NAME, path)
    pages = checked_pages(path, options)
    with pikepdf.open(path) as pdf:
        plan = _analyse(pdf, pages)
    for page in sorted(plan.fixed):
        n = plan.fixed[page]["line(s)"]
        text = f"Thickens {n} line(s) thinner than {MIN_PT:g} pt to {MIN_PT:g} pt as printed."
        short = plan.fixed[page]["short"]
        if n and short / n > SHORT_SHARE:
            # owner's decision (2026-11-19): the preview decides on illustrated work
            text += (f" {short} of them are under 1 mm long, which usually means illustration detail drawn with "
                     "fine strokes: compare Before and After before keeping this.")
        proposal.reasons.append((page, text))
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} thin line(s) left as they are: {reason}"))
    if plan.targets:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets], "tolerance": options.size_tolerance_mm}}
    elif not plan.left:
        proposal.skipped.append((1, f"no lines thinner than {MIN_PT:g} pt"))
    return proposal


def _check(src, targets, pages):
    wanted = {(p, n) for p, n, _ in targets}

    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
                before, after = walk_page(pa).events, walk_page(pb).events
                if len(before) != len(after):
                    raise AppendError(f"verification failed: page {number} paints a different number of objects")
                unit = pages[number].unit
                for i, (x, y) in enumerate(zip(before, after)):
                    same = (x.kind, x.paint, x.cs, x.colour, x.bbox, x.overprint, x.opm, x.blend) == \
                        (y.kind, y.paint, y.cs, y.colour, y.bbox, y.overprint, y.opm, y.blend)
                    if not same:
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if (number, i) in wanted:
                        if not MIN_PT - EPS <= y.width * unit <= MIN_PT + 0.01:
                            raise AppendError(f"verification failed: page {number}, line {i} is {y.width * unit:.3f} pt")
                    elif x.width != y.width and not (x.width and y.width and math.isclose(x.width, y.width, rel_tol=1e-6)):
                        raise AppendError(f"verification failed: page {number}, object {i}'s line width changed")
    return check


def write_thicken_hairlines(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    targets = [tuple(t) for t in proposal.plan[0]["targets"]]
    pages = checked_pages(proposal.path, Options(size_tolerance_mm=proposal.plan[0]["tolerance"]))
    with pikepdf.open(proposal.path) as pdf:
        plan = _analyse(pdf, pages)
        if plan.targets != targets:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets, pages))
