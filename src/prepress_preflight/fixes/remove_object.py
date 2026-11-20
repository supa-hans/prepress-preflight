"""Remove one object, chosen in the app's inspector (--object PAGE:NUMBER).

What "remove" does, so that nothing after the object changes:
  * A filled or stroked path: the painting operator becomes `n` (end the path
    without painting), so the path is still built and a clip set with it (W)
    still applies. A path both filled and stroked loses only the chosen side
    (B becomes S or f, and so on).
  * Text: deleting a Tj would move every later run in the same text block,
    since showing text advances the text position. So that run is shown in the
    invisible render mode (3 Tr), or with only its other side (fill or stroke)
    kept, and the render mode in force is put back right after.
  * An image (Do or inline) or a gradient (sh): the instruction is removed; it
    changes no state.

Refused, rather than changing more than asked: an object in a form painted more
than once (every copy would change), in a pattern cell, or no longer the one
chosen (--expect). Verified: the page then paints exactly the same objects
except the one removed.
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from ..content import walk_page
from ..objects import KINDS, describe_colour, fingerprint
from .append import AppendError, append_update
from .base import Proposal
from .object_overprint import _form_uses, _snapshot, parse_object
from .spot_to_cmyk import _flate

NAME = "remove-object"
# painting operator -> (without its fill, without its stroke)
KEEP = {"f": ("n", None), "F": ("n", None), "f*": ("n", None), "S": (None, "n"), "s": (None, "n"),
        "B": ("S", "f"), "B*": ("S", "f*"), "b": ("s", "f"), "b*": ("s", "f*")}
# text render mode -> (without its fill, without its stroke)
TEXT = {0: (3, None), 1: (None, 3), 2: (1, 0), 4: (7, None), 5: (None, 7), 6: (5, 4)}


def _ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


def _edit(instructions, index, event, tr):
    """New instructions with the object at `index` not painted, or a reason it cannot be."""
    ins = instructions[index]
    op = str(ins.operator)
    side = 0 if event.paint == "fill" else 1
    out = list(instructions)
    if event.kind == "path":
        new = KEEP.get(op, (None, None))[side]
        if new is None:
            return None, f"the path is painted with {op}, which this fix does not change"
        out[index] = _ins(new)
        return out, None
    if event.kind == "text":
        new = TEXT.get(tr, (None, None))[side]
        if new is None:
            return None, f"text in render mode {tr} cannot have only that side removed"
        out[index:index + 1] = [_ins("Tr", new), ins, _ins("Tr", tr)]
        return out, None
    if event.kind in ("image", "imagemask", "shading") and op in ("Do", "INLINE IMAGE", "sh"):
        del out[index]
        return out, None
    return None, "this kind of object cannot be removed on its own"


def propose_remove_object(path: str, options: Options | None = None, target=None, expect=None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    if target is None:
        raise ValueError("remove-object needs --object PAGE:NUMBER")
    page_number, number = parse_object(target)
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        snap = _snapshot(pdf, page_number, number)
        e = snap.event
        if expect and fingerprint(e) != expect:
            raise ValueError(f"object {number} on page {page_number} is not the one chosen (the file has changed); "
                             "choose it again")
        what = f"{KINDS.get((e.kind, e.paint), e.kind).lower()} in {describe_colour(e.cs, e.colour)[0]}"
        if e.loc is None:
            proposal.skipped.append((page_number, f"the {what} is inside a pattern cell, so it cannot be removed on its own"))
            return proposal
        if snap.chain:
            uses = _form_uses(pdf, snap.chain[-1].xobj.objgen)
            if uses > 1:
                proposal.skipped.append((page_number, f"the {what} is inside form {snap.chain[-1].name}, which is painted "
                                                      f"{uses} times; removing it would remove every copy"))
                return proposal
        instructions = list(pikepdf.parse_content_stream(snap.stream))
        edited, why = _edit(instructions, snap.index, e, snap.state.tr)
        if edited is None:
            proposal.skipped.append((page_number, f"the {what} is left: {why}"))
            return proposal
        both = e.kind == "path" and str(instructions[snap.index].operator) in ("B", "B*", "b", "b*")
    part = f" (its {e.paint} only; the path keeps its {'stroke' if e.paint == 'fill' else 'fill'})" if both else ""
    proposal.reasons.append((page_number, f"Removes the {what} (object {number}){part}. Everything else on the page "
                                          "stays as it is."))
    proposal.plan = {page_number: {"number": number, "fingerprint": fingerprint(e)}}
    return proposal


def _check(src, page_number, number):
    def check(new_path):
        key = lambda e: (e.kind, e.paint, e.cs, e.colour, e.bbox, e.overprint, e.opm, e.blend, e.width, e.font, e.px)
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            before = [key(e) for e in walk_page(a.pages[page_number - 1]).events]
            after = [key(e) for e in walk_page(b.pages[page_number - 1]).events]
        if after != before[:number] + before[number + 1:]:
            raise AppendError("verification failed: the page does not paint exactly the same objects less the one removed")
    return check


def write_remove_object(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    (page_number, plan), = proposal.plan.items()
    number = plan["number"]
    with pikepdf.open(proposal.path) as pdf:
        snap = _snapshot(pdf, page_number, number)
        if fingerprint(snap.event) != plan["fingerprint"]:
            raise AppendError("the file changed between the proposal and the write")
        edited, why = _edit(list(pikepdf.parse_content_stream(snap.stream)), snap.index, snap.event, snap.state.tr)
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
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, page_number, number))
