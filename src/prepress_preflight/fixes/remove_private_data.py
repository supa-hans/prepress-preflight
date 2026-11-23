"""Remove other applications' private data: the editing copy Illustrator keeps inside its PDFs.

    preflight fix remove-private-data FILE --compact --write SMALLER.pdf

Why: a PDF saved from Illustrator with "Preserve Illustrator Editing
Capabilities" carries a second, complete copy of the artwork in Illustrator's
own format, so it can be reopened as an .ai file. Nothing in it prints. On the
samples it is most of every Illustrator file: 243 of the pull-up banner's
509 MB. InDesign leaves a small marker of the same kind.

It is stored as "page-piece" data (PieceInfo, PDF 32000-1, 14.5) on pages, on
placed forms and on the document catalogue; this fix takes those entries out
and changes nothing else. After it, Illustrator opens the file as plain PDF
objects rather than as the original artwork, so keep the original if the
artwork may need editing.

The saving shows only with --compact: a normal write appends to the original
bytes (an incremental update), so the old data is still in the file, unused.

Verified: no page-piece data is left, every changed page and form is the
original with only that entry removed, and the page content is untouched.
"""
from __future__ import annotations

import pikepdf

from ..checks.base import Options
from .append import AppendError, append_update
from .base import Proposal

NAME = "remove-private-data"
KEY = "/PieceInfo"


def _holders(pdf):
    """[(where, object)] for the catalogue, pages and forms (in pages and forms, at any depth) with PieceInfo."""
    out, seen = [], set()
    if KEY in pdf.Root:
        out.append(("document", pdf.Root))

    def forms(resources, number):
        for x in resources.get("/XObject", pikepdf.Dictionary()).values():
            if not isinstance(x, pikepdf.Stream) or x.get("/Subtype") != pikepdf.Name.Form or x.objgen in seen:
                continue
            seen.add(x.objgen)
            if KEY in x:
                out.append((number, x))
            forms(x.get("/Resources", pikepdf.Dictionary()), number)

    for number, page in enumerate(pdf.pages, 1):
        if KEY in page.obj:
            out.append((number, page.obj))
        forms(page.resources, number)
    return out


def _size(objects) -> int:
    """Bytes of the streams reachable from the private data (each counted once)."""
    seen, total, stack = set(), 0, list(objects)
    while stack:
        o = stack.pop()
        if not isinstance(o, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            continue
        if o.is_indirect:
            if o.objgen in seen:
                continue
            seen.add(o.objgen)
        if isinstance(o, pikepdf.Stream):
            total += len(o.read_raw_bytes())
            stack.extend(o.stream_dict.values())
        elif isinstance(o, pikepdf.Dictionary):
            stack.extend(o.values())
        else:
            stack.extend(list(o))
    return total


def propose_remove_private_data(path: str, options: Options | None = None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        holders = _holders(pdf)
        if not holders:
            proposal.skipped.append((1, "the file holds no other application's private data"))
            return proposal
        if any(not obj.is_indirect for _, obj in holders):
            raise ValueError("private data sits on an object stored directly inside another; not handled")
        apps = sorted({str(k)[1:] for _, obj in holders for k in obj[KEY].keys()})
        size = _size([obj[KEY] for _, obj in holders])
        pages = sorted({w for w, _ in holders if w != "document"})
        where = ([f"{len(pages)} page(s)"] if pages else []) + (["the document"] if any(w == "document" for w, _ in holders) else [])
        text = (f"Removes {', '.join(apps)} private data from {' and '.join(where)} (about {size / 1e6:.1f} MB). "
                "Nothing in it prints. Without it the file opens in its application as plain PDF objects, not as the "
                "original editable artwork. Write with --compact for the file to get smaller.")
        proposal.reasons.append((pages[0] if pages else 1, text))
        proposal.plan = {0: {"objects": [list(obj.objgen) for _, obj in holders], "bytes": size, "apps": apps}}
    return proposal


def _without(pdf, obj):
    if isinstance(obj, pikepdf.Stream):
        copy = pikepdf.Stream(pdf, obj.read_raw_bytes())  # the stored (still encoded) bytes, kept as they are
        for k in obj.stream_dict.keys():
            if k not in (KEY, "/Length"):
                copy[k] = obj.stream_dict[k]
        return copy
    return pikepdf.Dictionary({k: obj[k] for k in obj.keys() if k != KEY})


def _text(value):
    """A value as PDF syntax (references as N G R), for comparing; plain numbers come back as Python values."""
    return value.unparse() if isinstance(value, pikepdf.Object) else repr(value)


def _check(src, objects):
    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            if _holders(b):
                raise AppendError("verification failed: private data is still in the file")
            for og in objects:
                old, new = a.get_object(og), b.get_object(og)
                old_keys = sorted(k for k in (old.stream_dict if isinstance(old, pikepdf.Stream) else old).keys()
                                  if k not in (KEY, "/Length"))
                new_keys = sorted(k for k in (new.stream_dict if isinstance(new, pikepdf.Stream) else new).keys()
                                  if k != "/Length")
                if old_keys != new_keys:
                    raise AppendError(f"verification failed: object {og[0]} lost or gained more than the private data")
                for k in old_keys:
                    x = (old.stream_dict if isinstance(old, pikepdf.Stream) else old)[k]
                    y = (new.stream_dict if isinstance(new, pikepdf.Stream) else new)[k]
                    if _text(x) != _text(y):
                        raise AppendError(f"verification failed: object {og[0]}'s {k} changed")
                if isinstance(old, pikepdf.Stream) and old.read_raw_bytes() != new.read_raw_bytes():
                    raise AppendError(f"verification failed: form {og[0]}'s content changed")
    return check


def write_remove_private_data(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    objects = [tuple(og) for og in proposal.plan[0]["objects"]]
    with pikepdf.open(proposal.path) as pdf:
        if sorted(obj.objgen for _, obj in _holders(pdf)) != sorted(objects):
            raise AppendError("the file changed between the proposal and the write")
        changes = {og: _without(pdf, pdf.get_object(og)) for og in objects}
        append_update(proposal.path, dst, changes, {}, extra_check=_check(proposal.path, objects))
