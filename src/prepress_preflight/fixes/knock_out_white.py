"""Knock out every white object that is set to overprint.

White set to overprint does not print: it vanishes, and what is beneath shows
through. The owner's rule (2026-11-19): it is never wanted, so this fixes all
of it, the objects the White overprint check reports.

How each object is changed, so that nothing else is:
  * Normally the object is wrapped in `q /GS gs … Q`, where the new ExtGState
    turns off overprint for the side that is white (fill or stroke). `Q` puts
    back whatever was in force before, whatever it was, so this is right even
    for a form painted in several places with different settings.
  * Inside a text block `q`/`Q` are not allowed, and around a path that also
    sets a clip they would lose the clip. There, one ExtGState is set before
    and another after that restores the overprint that was in force. That
    needs the overprint to be known: it is the same at every use of that spot.

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

from collections import Counter, defaultdict

import pikepdf

from ..checks.base import Options
from ..checks.white_overprint import LABELS, is_white
from ..content import PageWalker, walk_page
from ..objects import fingerprint
from .append import AppendError, append_update
from .base import Proposal
from .object_overprint import PATH_BUILD, _form_uses, _unique
from .spot_to_cmyk import _flate

NAME = "knock-out-white"
PATH_PAINT = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}
FILL_AND_STROKE = {"B", "B*", "b", "b*"}


def _white(e) -> bool:
    return e.kind in LABELS and not e.cs.indexed and is_white(e.cs, e.colour)


def _on_media(e, media) -> bool:
    b = e.bbox
    return b is None or (b[0] < media[2] and b[2] > media[0] and b[1] < media[3] and b[3] > media[1])


class _Plan:
    """What to change: per stream, the edits; plus what is left and why."""

    def __init__(self):
        self.edits = defaultdict(list)  # stream key -> [edit dicts]
        self.streams = {}  # stream key -> (stream object, resources, page number or None)
        self.targets = []  # (page, event number, fingerprint): the objects to be fixed
        self.fixed = defaultdict(Counter)  # page -> {label: count}
        self.left = defaultdict(Counter)  # page -> {reason: count}


def _analyse(pdf) -> _Plan:
    plan = _Plan()
    positions = defaultdict(list)  # (stream key, instruction) -> [(page number, snapshot)]
    for number, page in enumerate(pdf.pages, 1):
        media = [float(v) for v in page.mediabox]
        walker = PageWalker(page, detail=_white)
        walker.run()
        for snap in walker.snapshots:
            e = snap.event
            flagged = e.overprint and _on_media(e, media)
            if e.loc is None:
                if flagged:
                    plan.left[number]["inside a pattern fill"] += 1
                continue
            key = ("form", snap.chain[-1].xobj.objgen) if snap.chain else ("page", number)
            positions[(key, snap.index)].append((number, snap, flagged))

    uses_cache = {}
    for (key, index), found in positions.items():
        flagged = [(n, s) for n, s, f in found if f]
        if not flagged:
            continue
        stream = found[0][1].stream
        instructions = pikepdf.parse_content_stream(stream)
        op = str(instructions[index].operator)
        start = index
        if op in PATH_PAINT:
            while start > 0 and str(instructions[start - 1].operator) in PATH_BUILD:
                start -= 1
        clipped = any(str(i.operator) in ("W", "W*") for i in instructions[start:index])
        restore = flagged[0][1].event.kind == "text" or clipped
        fill_off = any(s.event.paint == "fill" for _, s in flagged)
        stroke_off = any(s.event.paint == "stroke" for _, s in flagged)
        paints_fill = op in FILL_AND_STROKE or op not in PATH_PAINT or op in ("f", "F", "f*")
        needs_known = restore or (stroke_off and not fill_off and paints_fill)

        def leave(reason):
            for n, _ in flagged:
                plan.left[n][reason] += 1

        if key[0] == "form":
            if key not in uses_cache:
                uses_cache[key] = _form_uses(pdf, key[1])
            uses = uses_cache[key]
            captured = max(Counter(s.event.paint for _, s, _ in found).values())
            if captured < uses:
                leave("in a form that is also painted where it is not white")
                continue
            form = found[0][1].chain[-1].xobj
            if "/Resources" not in form and uses > 1:
                leave("in a form without resources of its own that is painted more than once")
                continue
        states = {(s.state.op_fill, s.state.op_stroke) for _, s, _ in found}
        if needs_known and len(states) > 1:
            leave("in a form whose uses disagree on overprint")
            continue
        op_fill, op_stroke = next(iter(states))
        set_gs = {}
        if fill_off:
            set_gs["/op"] = False
        if stroke_off:
            set_gs["/OP"] = False
            set_gs["/op"] = False if fill_off else (op_fill if needs_known else False)
        plan.edits[key].append({"start": start, "paint": index, "restore": restore, "set": set_gs,
                                "back": {"/OP": op_stroke, "/op": op_fill}})
        plan.streams[key] = (stream, found[0][1].resources, key[1] if key[0] == "page" else None)
        for n, s in flagged:
            plan.targets.append((n, s.number, fingerprint(s.event)))
            plan.fixed[n][LABELS[s.event.kind]] += 1
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


# ---- writing -----------------------------------------------------------

def _apply(instructions, edits, names):
    out = list(instructions)

    def gs(name):
        return pikepdf.ContentStreamInstruction([pikepdf.Name(name)], pikepdf.Operator("gs"))
    q = pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))
    Q = pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q"))
    for edit in sorted(edits, key=lambda e: e["paint"], reverse=True):
        before = [gs(names(edit["set"]))]
        after = [gs(names(edit["back"]))] if edit["restore"] else [Q]
        if not edit["restore"]:
            before = [q] + before
        out[edit["paint"] + 1:edit["paint"] + 1] = after
        out[edit["start"]:edit["start"]] = before
    return out


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
        changes, additions = {}, {}
        for key, edits in plan.edits.items():
            stream, resources, page_number = plan.streams[key]
            new_res = pikepdf.Dictionary({k: resources[k] for k in resources.keys()}) if resources is not None else pikepdf.Dictionary()
            old_gs = new_res.get("/ExtGState")
            gs = pikepdf.Dictionary({k: old_gs[k] for k in old_gs.keys()}) if old_gs is not None else pikepdf.Dictionary()
            made = {}

            def names(values, gs=gs, made=made):
                k = tuple(sorted(values.items()))
                if k not in made:
                    name = _unique(gs.keys(), "PFKO")
                    gs[name] = pikepdf.Dictionary({"/Type": pikepdf.Name.ExtGState, **values})
                    made[k] = name
                return made[k]
            data = pikepdf.unparse_content_stream(_apply(pikepdf.parse_content_stream(stream), edits, names))
            new_res["/ExtGState"] = gs
            if page_number is not None:
                page = pdf.pages[page_number - 1].obj
                content = pdf.make_indirect(_flate(pdf, data))
                additions[content.objgen] = content
                copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
                copy["/Contents"] = content
                copy["/Resources"] = new_res
                changes[page.objgen] = copy
            else:
                new = _flate(pdf, data)
                for k in stream.stream_dict.keys():
                    if k not in ("/Length", "/Filter", "/DecodeParms"):
                        new[k] = stream.stream_dict[k]
                new["/Resources"] = new_res
                changes[stream.objgen] = new
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, targets))
