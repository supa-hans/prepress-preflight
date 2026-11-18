"""Set overprint on or off for one object.

The object is the one the app's inspector lists: a page number and the
object's number in the order the page paints (`--object 48:2439`). With
`--expect`, the object at that number must still be the one the user chose
(its kind, colour and place), or the fix refuses.

How: overprint is part of the graphics state, which every later object
inherits. So the fix sets it just before the object is painted and puts back
exactly what was in force just after, with two new ExtGStates. Nothing else
on the page sees a different state, and every other object is verified
unchanged.

Refused, rather than changing more than was asked:
  * an object inside a form that is painted more than once (changing the form
    would change every copy);
  * an object inside a pattern cell (it has no single place on the page).
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..content import PageWalker, walk_page
from ..objects import KINDS, describe_colour, fingerprint
from .append import AppendError, append_update
from .base import Proposal
from .spot_to_cmyk import _flate

NAME = "object-overprint"
PATH_BUILD = {"m", "l", "c", "v", "y", "h", "re", "W", "W*"}


def parse_object(text) -> tuple[int, int]:
    try:
        page, number = (int(v) for v in str(text).split(":"))
    except ValueError:
        raise ValueError(f'"{text}": give the object as PAGE:NUMBER, for example 48:2439') from None
    if page < 1 or number < 0:
        raise ValueError(f'"{text}": no such object')
    return page, number


class _FormCounter(PageWalker):
    """A page walk that counts how often one form is painted."""

    def __init__(self, page, objgen):
        super().__init__(page)
        self.objgen, self.count = objgen, 0

    def _do(self, name, st, resources, depth, located, idx):
        try:
            if resources["/XObject"][str(name)].objgen == self.objgen:
                self.count += 1
        except Exception:
            pass
        super()._do(name, st, resources, depth, located, idx)


def _form_uses(pdf, objgen) -> int:
    """How many times a form is painted across the document."""
    total = 0
    for page in pdf.pages:
        walker = _FormCounter(page, objgen)
        walker.run()
        total += walker.count
    return total


def _snapshot(pdf, page_number, number):
    if not 1 <= page_number <= len(pdf.pages):
        raise ValueError(f"there is no page {page_number}")
    walker = PageWalker(pdf.pages[page_number - 1], detail=number)
    walker.run()
    if walker.snapshot is None:
        raise ValueError(f"page {page_number} has no object {number}")
    return walker.snapshot


def propose_object_overprint(path: str, options: Options | None = None, target=None, overprint=None, expect=None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    if target is None or overprint is None:
        raise ValueError("object-overprint needs --object PAGE:NUMBER and --overprint on|off")
    page_number, number = parse_object(target)
    want = overprint == "on"
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        snap = _snapshot(pdf, page_number, number)
        e, st = snap.event, snap.state
        if expect and fingerprint(e) != expect:
            raise ValueError(f"object {number} on page {page_number} is not the one chosen (the file has changed); "
                             "choose it again")
        what = f'{KINDS.get((e.kind, e.paint), e.kind).lower()} in {describe_colour(e.cs, e.colour)[0]}'
        if e.loc is None:
            proposal.skipped.append((page_number, f"the {what} is inside a pattern cell, so it cannot be changed on its own"))
            return proposal
        if snap.chain:
            uses = _form_uses(pdf, snap.chain[-1].xobj.objgen)
            if uses > 1:
                proposal.skipped.append((page_number, f"the {what} is inside form {snap.chain[-1].name}, which is painted "
                                                      f"{uses} times; changing it would change every copy"))
                return proposal
        now = st.op_fill if e.paint == "fill" else st.op_stroke
        if now == want:
            proposal.skipped.append((page_number, f"the {what} already {'overprints' if want else 'knocks out'}"))
            return proposal
        effect = "overprint" if want else "knock out (overprint off)"
        note = ""
        if want and st.opm == 0 and e.cs.kind == "cmyk":
            note = " With overprint mode 0, its 0% inks still knock out; only spot inks and the like will overprint."
        if not want and e.cs.kind == "cmyk" and e.colour and not any(e.colour):
            note = " It is white, so it will now knock out whatever is beneath it."
        proposal.reasons.append((page_number, f"Sets the {what} (object {number}) to {effect}.{note}"))
        proposal.plan = {page_number: {"number": number, "overprint": want, "fingerprint": fingerprint(e)}}
    return proposal


# ---- writing -----------------------------------------------------------

def _unique(names, stem):
    i = 1
    while f"/{stem}{i}" in names:
        i += 1
    return f"/{stem}{i}"


def _check(src, page_number, number, want):
    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            before = walk_page(a.pages[page_number - 1]).events
            after = walk_page(b.pages[page_number - 1]).events
        if len(before) != len(after):
            raise AppendError("verification failed: the page paints a different number of objects")
        for i, (x, y) in enumerate(zip(before, after)):
            same = (x.kind, x.paint, x.cs, x.colour, x.bbox, x.opm, x.blend) == (y.kind, y.paint, y.cs, y.colour, y.bbox, y.opm, y.blend)
            if not same:
                raise AppendError(f"verification failed: object {i} changed")
            if i == number:
                if y.overprint != want:
                    raise AppendError("verification failed: the object's overprint did not change")
            elif x.overprint != y.overprint:
                raise AppendError(f"verification failed: object {i}'s overprint changed too")
    return check


def write_object_overprint(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    (page_number, plan), = proposal.plan.items()
    number, want = plan["number"], plan["overprint"]
    with pikepdf.open(proposal.path) as pdf:
        snap = _snapshot(pdf, page_number, number)
        e, st = snap.event, snap.state
        if fingerprint(e) != plan["fingerprint"]:
            raise AppendError("the file changed between the proposal and the write")
        instructions = list(pikepdf.parse_content_stream(snap.stream))
        paint = snap.index
        start = paint
        if str(instructions[paint].operator) in {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}:
            # the state may not change inside a path: set it before the path is built
            while start > 0 and str(instructions[start - 1].operator) in PATH_BUILD:
                start -= 1

        resources = snap.resources
        new_res = pikepdf.Dictionary({k: resources[k] for k in resources.keys()}) if resources is not None else pikepdf.Dictionary()
        old_gs = new_res.get("/ExtGState")
        gs = pikepdf.Dictionary({k: old_gs[k] for k in old_gs.keys()}) if old_gs is not None else pikepdf.Dictionary()
        fill = want if e.paint == "fill" else st.op_fill
        stroke = want if e.paint == "stroke" else st.op_stroke
        set_name = _unique(gs.keys(), "PFOP")
        gs[set_name] = pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, OP=stroke, op=fill, OPM=st.opm)
        back_name = _unique(gs.keys(), "PFOP")
        gs[back_name] = pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, OP=st.op_stroke, op=st.op_fill, OPM=st.opm)
        new_res["/ExtGState"] = gs

        def gs_op(name):
            return pikepdf.ContentStreamInstruction([pikepdf.Name(name)], pikepdf.Operator("gs"))
        edited = instructions[:start] + [gs_op(set_name)] + instructions[start:paint + 1] + [gs_op(back_name)] + instructions[paint + 1:]
        data = pikepdf.unparse_content_stream(edited)

        changes, additions = {}, {}
        if not snap.chain:
            page = pdf.pages[page_number - 1].obj
            stream = pdf.make_indirect(_flate(pdf, data))
            additions[stream.objgen] = stream
            copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
            copy["/Contents"] = stream
            copy["/Resources"] = new_res
            changes[page.objgen] = copy
        else:
            form = snap.chain[-1].xobj
            if not form.is_indirect:
                raise AppendError("the form is not a separate object, so it cannot be replaced on its own")
            new = _flate(pdf, data)
            for k in form.stream_dict.keys():
                if k not in ("/Length", "/Filter", "/DecodeParms"):
                    new[k] = form.stream_dict[k]
            new["/Resources"] = new_res
            changes[form.objgen] = new
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, page_number, number, want))
