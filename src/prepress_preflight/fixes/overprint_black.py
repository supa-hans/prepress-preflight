"""Set small 100% black text and thin black lines to overprint.

Why: black that knocks out leaves a gap in the colours beneath it. When the
plates register a fraction off on press, the gap shows as a white or coloured
halo along the edge of fine type and rules. Black overprinting sits on the
colour beneath, so there is no gap to show. It is the usual pre-press rule for
small black, which is dense enough to look the same either way.

Large black is left alone by default: a big black area or heading
overprinting a photo lets the photo show through (100% K alone is not a solid
black). `--max-text` (default 12 pt, as placed) and `--max-line` (default
2 pt) set what counts as small.

"100% black" is CMYK C0 M0 Y0 K100, or a separation named Black at 100%. A
rich black (CMY under the K) is not touched. The overprint mode is set to 1 as
well, so the C, M and Y at 0% leave the inks beneath instead of knocking them
out (with mode 0 a CMYK object overprints nothing).

Each object is bracketed (see bracket.py), so nothing else changes. Left alone
and said so:
  * black in DeviceGray: whether a RIP lets it overprint the CMY beneath
    varies, so convert it to CMYK first;
  * a path filled and stroked where only one side is small black and the
    other already overprints (the mode change would reach it);
  * objects in pattern fills, and form spots that cannot be changed on their
    own (the form is also painted large, or its uses disagree).
"""
from __future__ import annotations

from collections import Counter

import pikepdf

from ..checks.base import Options
from ..checks.black_overprint import MAX_LINE_PT, MAX_TEXT_PT, grey_black as _grey_black, is_black, is_small, knocks_out
from ..checks.white_overprint import LABELS
from ..content import CMYK, walk_page
from .append import AppendError, append_update
from .base import Proposal
from .bracket import Plan, add_edit, find_spots, ins, known, write_plan

NAME = "overprint-black"
FILL_AND_STROKE = {"B", "B*", "b", "b*"}
OFF = " off the page"
NAMES = {("path", "fill"): "shape(s)", ("path", "stroke"): "line(s)", ("text", "fill"): "text object(s)",
         ("text", "stroke"): "outlined text object(s)"}


def _candidate(e) -> bool:
    return e.kind in ("path", "text") and (is_black(e) or _grey_black(e))


def _on_media(b, m) -> bool:
    return b[0] < m[2] and b[2] > m[0] and b[1] < m[3] and b[3] > m[1]


def _analyse(pdf, max_text, max_line) -> Plan:
    plan = Plan()
    media = {n: [float(v) for v in page.mediabox] for n, page in enumerate(pdf.pages, 1)}

    def flag(number, snap):
        e = snap.event
        if not knocks_out(e):
            return False  # already overprints as intended
        if not is_small(e, max_text, max_line):
            return False
        if _grey_black(e):
            return "black in grey (DeviceGray): RIPs differ on whether it overprints the CMY beneath; convert it to CMYK first"
        return True

    for spot in find_spots(pdf, _candidate, flag, plan, every_use=True,
                           elsewhere="in a form that is also painted where it is not small black"):
        sides = {s.event.paint for _, s in spot.flagged}
        fill_on, stroke_on = "fill" in sides, "stroke" in sides
        state = known(spot, lambda s: (s.state.op_fill, s.state.op_stroke, s.state.opm))
        if spot.op in FILL_AND_STROKE and len(sides) == 1:
            other = (lambda s: s.state.op_stroke) if fill_on else (lambda s: s.state.op_fill)
            if known(spot, other) is not False:
                plan.leave(spot, "a path filled and stroked whose other side already overprints")
                continue
        needs_known = spot.restore or (stroke_on and not fill_on)
        if needs_known and state is None:
            plan.leave(spot, "in a form whose uses disagree on overprint")
            continue
        op_fill, op_stroke, opm = state or (False, False, 0)
        set_gs = {"/OPM": 1}
        if fill_on:
            set_gs["/op"] = True
        if stroke_on:
            set_gs["/OP"] = True
            set_gs["/op"] = True if fill_on else op_fill  # /OP alone would set the fill's overprint too
        back = {"/OP": op_stroke, "/op": op_fill, "/OPM": opm}
        add_edit(plan, spot,
                 lambda add_gs, v=set_gs: [ins("gs", pikepdf.Name(add_gs(v)))],
                 lambda add_gs, v=back: [ins("gs", pikepdf.Name(add_gs(v)))],
                 lambda e: NAMES[(e.kind, e.paint)])
        for n, s in spot.flagged:  # a copy off the page (artwork spread over two pages) changes nothing printed
            if s.event.bbox is not None and not _on_media(s.event.bbox, media[n]):
                label = NAMES[(s.event.kind, s.event.paint)]
                plan.fixed[n][label] -= 1
                plan.fixed[n][label + OFF] += 1
    for counts in plan.fixed.values():
        for label in [k for k, v in counts.items() if v <= 0]:
            del counts[label]
    plan.targets.sort()
    return plan


def _limits(max_text, max_line):
    t = MAX_TEXT_PT if max_text is None else float(max_text)
    l = MAX_LINE_PT if max_line is None else float(max_line)
    if t < 0 or l < 0:
        raise ValueError("--max-text and --max-line must not be negative")
    return t, l


def _count_text(counter: Counter) -> str:
    return ", ".join(f"{n} {label}" for label, n in counter.most_common())


def propose_overprint_black(path: str, options: Options | None = None, max_text=None, max_line=None,
                            **_) -> Proposal:
    """What the fix would change. Reads only."""
    max_text, max_line = _limits(max_text, max_line)
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        plan = _analyse(pdf, max_text, max_line)
    for page in sorted(plan.fixed):
        on = Counter({k: v for k, v in plan.fixed[page].items() if not k.endswith(OFF)})
        off = Counter({k[:-len(OFF)]: v for k, v in plan.fixed[page].items() if k.endswith(OFF)})
        text = (f"Sets 100% black to overprint (mode 1): {_count_text(on)} (text up to {max_text:g} pt, lines up to "
                f"{max_line:g} pt). They will print on top of the colour beneath, so misregistration cannot show a halo."
                if on else "Sets 100% black to overprint (mode 1), with no change to what prints on this page.")
        if off:
            text += (f" Also {_count_text(off)} off the page, in artwork placed on another page too, changed with it "
                     "(nothing printed here changes).")
        proposal.reasons.append((page, text))
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} small black object(s) left knocking out: {reason}"))
    if plan.targets:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets], "max_text": max_text, "max_line": max_line}}
    elif not plan.left:
        proposal.skipped.append((1, f"no 100% black text up to {max_text:g} pt or lines up to {max_line:g} pt "
                                    "that knock out"))
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
                        if not y.overprint or (y.cs == CMYK and y.opm != 1):
                            raise AppendError(f"verification failed: page {number}, object {i} does not overprint")
                    # the mode matters only to an object that overprints
                    elif (x.overprint, x.overprint and x.opm) != (y.overprint, y.overprint and y.opm):
                        raise AppendError(f"verification failed: page {number}, object {i}'s overprint changed")
    return check


def write_overprint_black(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan0 = proposal.plan[0]
    targets = [tuple(t) for t in plan0["targets"]]
    with pikepdf.open(proposal.path) as pdf:
        plan = _analyse(pdf, plan0["max_text"], plan0["max_line"])
        if plan.targets != targets:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets))
