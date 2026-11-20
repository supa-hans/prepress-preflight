"""Give one chosen object a new CMYK colour (--object PAGE:NUMBER --cmyk C,M,Y,K).

Only the side chosen in the inspector changes: a path that is filled and
stroked keeps its other side's colour. Overprint, blending and everything else
about the object stay as they were.

How, so that nothing else takes the new colour: colour is part of the graphics
state, which every later object inherits. Normally the object is wrapped in
`q <colour> … Q`, and Q puts back whatever was in force. In a text block q/Q
are not allowed, and around a path that also clips they would drop the clip,
so there the new colour is set just before and the colour in force is written
back straight after, exactly as the file had it (the same colour space
resource and values).

Refused, rather than changing more than asked: an object in a form painted
more than once (every copy would change), in a pattern cell, an image or
gradient (they carry their own colours), or no longer the one chosen
(--expect). Verified: the page then paints exactly the same objects, and only
the chosen one has a different colour.
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..content import CMYK, GRAY, RGB, walk_page
from ..objects import KINDS, describe_colour, fingerprint
from .append import AppendError, append_update
from .base import Proposal
from .bracket import PATH_PAINT, ins
from .object_overprint import PATH_BUILD, _form_uses, _snapshot, parse_object
from .spot_to_cmyk import _flate, _num

NAME = "recolour-object"
DEVICE = {GRAY: "g", RGB: "rg", CMYK: "k"}


def parse_cmyk(text) -> tuple[float, float, float, float]:
    """'0,0,0,100' (percent) -> (0, 0, 0, 1)."""
    try:
        values = [float(v) for v in str(text).replace(" ", "").split(",")]
    except ValueError:
        values = []
    if len(values) != 4 or any(not 0 <= v <= 100 for v in values):
        raise ValueError(f'"{text}": give the colour as C,M,Y,K in percent, each 0 to 100, for example 0,0,0,100')
    return tuple(round(v / 100, 5) for v in values)


def _percent(cmyk) -> str:
    return " ".join(f"{ink}{round(v * 100, 1):g}" for ink, v in zip("CMYK", cmyk))


def _written(state, side):
    """Instructions that set the colour in force for `side` exactly as written, or None."""
    fill = side == "fill"
    space, colour, pattern, definition = ((state.fcs, state.fcol, state.fpat, state.fdef) if fill
                                          else (state.scs, state.scol, state.spat, state.sdef))
    if definition is None:
        op = DEVICE.get(space)
        if op is None or colour is None:
            return None
        return [ins(op if fill else op.upper(), *[_num(v) for v in colour])]
    name = definition[0]
    if not isinstance(name, pikepdf.Name):
        return None
    out = [ins("cs" if fill else "CS", name)]
    if colour is not None or pattern is not None:
        operands = [_num(v) for v in (colour or ())] + ([pattern] if pattern is not None else [])
        out.append(ins("scn" if fill else "SCN", *operands))
    return out


def _edit(instructions, index, event, state, cmyk):
    """New instructions with the object at `index` in the new colour, or a reason it cannot be."""
    op = str(instructions[index].operator)
    if event.kind == "path" and op not in PATH_PAINT:
        return None, f"the path is painted with {op}, which this fix does not change"
    if event.kind not in ("path", "text", "imagemask"):
        return None, "images and gradients carry their own colours; only shapes, text and stencil masks can be recoloured"
    side = event.paint
    start = index
    if event.kind == "path":
        while start > 0 and str(instructions[start - 1].operator) in PATH_BUILD:
            start -= 1
    clipped = any(str(i.operator) in ("W", "W*") for i in instructions[start:index])
    set_colour = [ins("k" if side == "fill" else "K", *[_num(v) for v in cmyk])]
    out = list(instructions)
    if event.kind == "text" or clipped:
        back = _written(state, side)
        if back is None:
            return None, "the colour in force could not be written back after it"
        out[index + 1:index + 1] = back
        out[start:start] = set_colour
    else:
        out[index + 1:index + 1] = [ins("Q")]
        out[start:start] = [ins("q")] + set_colour
    return out, None


def propose_recolour_object(path: str, options: Options | None = None, target=None, cmyk=None, expect=None,
                            **_) -> Proposal:
    """What the fix would change. Reads only."""
    if target is None or cmyk is None:
        raise ValueError("recolour-object needs --object PAGE:NUMBER and --cmyk C,M,Y,K")
    page_number, number = parse_object(target)
    new = parse_cmyk(cmyk)
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        snap = _snapshot(pdf, page_number, number)
        e = snap.event
        if expect and fingerprint(e) != expect:
            raise ValueError(f"object {number} on page {page_number} is not the one chosen (the file has changed); "
                             "choose it again")
        old = describe_colour(e.cs, e.colour)[0]
        what = f"{KINDS.get((e.kind, e.paint), e.kind).lower()} in {old}"
        if e.loc is None:
            proposal.skipped.append((page_number, f"the {what} is inside a pattern cell, so it cannot be changed on its own"))
            return proposal
        if snap.chain:
            uses = _form_uses(pdf, snap.chain[-1].xobj.objgen)
            if uses > 1:
                proposal.skipped.append((page_number, f"the {what} is inside form {snap.chain[-1].name}, which is painted "
                                                      f"{uses} times; recolouring it would recolour every copy"))
                return proposal
        if e.cs == CMYK and e.colour is not None and tuple(round(v, 5) for v in e.colour) == new:
            proposal.skipped.append((page_number, f"the {what} is already {_percent(new)}"))
            return proposal
        edited, why = _edit(list(pikepdf.parse_content_stream(snap.stream)), snap.index, e, snap.state, new)
        if edited is None:
            proposal.skipped.append((page_number, f"the {what} is left: {why}"))
            return proposal
    note = ""
    if e.overprint:
        note = (" It is set to overprint, which it keeps: where the new colour has 0% of an ink, that ink "
                "beneath shows through" + (" (overprint mode 1)." if e.opm == 1 else "."))
    proposal.reasons.append((page_number, f"Recolours the {what} (object {number}) to {_percent(new)}. "
                                          f"Everything else on the page stays as it is.{note}"))
    proposal.plan = {page_number: {"number": number, "fingerprint": fingerprint(e), "cmyk": list(new)}}
    return proposal


def _check(src, page_number, number, cmyk):
    def check(new_path):
        key = lambda e: (e.kind, e.paint, e.bbox, e.overprint, e.opm, e.blend, e.width, e.font, e.px)
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            before = walk_page(a.pages[page_number - 1]).events
            after = walk_page(b.pages[page_number - 1]).events
        if len(before) != len(after):
            raise AppendError("verification failed: the page paints a different number of objects")
        for i, (x, y) in enumerate(zip(before, after)):
            if key(x) != key(y):
                raise AppendError(f"verification failed: object {i} changed")
            if i == number:
                if y.cs != CMYK or tuple(round(v, 5) for v in y.colour) != tuple(cmyk):
                    raise AppendError(f"verification failed: object {i} is not {_percent(cmyk)}")
            elif (x.cs, x.colour) != (y.cs, y.colour):
                raise AppendError(f"verification failed: object {i}'s colour changed")
    return check


def write_recolour_object(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    (page_number, plan), = proposal.plan.items()
    number, cmyk = plan["number"], tuple(plan["cmyk"])
    with pikepdf.open(proposal.path) as pdf:
        snap = _snapshot(pdf, page_number, number)
        if fingerprint(snap.event) != plan["fingerprint"]:
            raise AppendError("the file changed between the proposal and the write")
        edited, why = _edit(list(pikepdf.parse_content_stream(snap.stream)), snap.index, snap.event, snap.state, cmyk)
        if edited is None:
            raise AppendError(why)
        data = pikepdf.unparse_content_stream(edited)
        changes, additions = {}, {}
        if not snap.chain:
            page = pdf.pages[page_number - 1].obj
            stream = pdf.make_indirect(_flate(pdf, data))
            additions[stream.objgen] = stream
            copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
            copy["/Contents"] = stream
            changes[page.objgen] = copy
        else:
            form = snap.chain[-1].xobj
            new = _flate(pdf, data)
            for k in form.stream_dict.keys():
                if k not in ("/Length", "/Filter", "/DecodeParms"):
                    new[k] = form.stream_dict[k]
            changes[form.objgen] = new
        append_update(proposal.path, dst, changes, additions,
                      extra_check=_check(proposal.path, page_number, number, cmyk))
