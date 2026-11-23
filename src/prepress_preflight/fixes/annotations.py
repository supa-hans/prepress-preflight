"""Flatten or remove annotations: comments, stamps and form fields on top of the artwork.

Annotations are not page content. Whether a RIP prints one depends on its Print
flag and on the RIP, and a form field's look is drawn by the viewer. Two fixes:

  flatten-annotations  Each annotation set to print, form fields included, is
                       drawn into the page as it looks now (its stored
                       appearance), then taken off the page. What prints no
                       longer depends on the RIP. Annotations not set to print
                       are left where they are.
  remove-annotations   Every comment, stamp and form field is taken off the
                       page, printing or not. The artwork itself is untouched.

Both leave links, trap networks and printer's marks alone, as the Annotations
check does, and annotations already hidden. A comment's pop-up note goes with
it. Form fields taken off every page are taken out of the form too, and the
form is removed once it has no fields left.

Refused for that annotation, and said so, rather than guessed: an appearance
that is missing or cannot be read, a form that asks the viewer to redraw its
fields (NeedAppearances, so the stored look may not show the value), an
annotation on a layer, one hidden on screen but set to print, and one that
stays upright when the page is rotated. Verified: the page is re-rendered with
its annotations and must look as it did, and the page content before the
flattened ones is the original, byte for byte.
"""
from __future__ import annotations

import numpy as np
import pikepdf
import pymupdf

from ..checks.annotations import HIDDEN, IGNORED, PRINT
from ..checks.base import Options
from .append import AppendError, append_update
from .base import Proposal

FLATTEN, REMOVE = "flatten-annotations", "remove-annotations"
NO_ROTATE, NO_VIEW = 16, 32  # annotation flag bits
# Comment marks: a note's icon or a proof-reading mark is never artwork, even
# when set to print (Acrobat sets sticky notes to print by default), so
# flattening leaves them for remove-annotations.
LABELS = {"Widget": "form field", "Text": "sticky note", "FreeText": "text box"}
COMMENTS = {"Text", "FileAttachment", "Sound", "Highlight", "Underline", "StrikeOut", "Squiggly", "Caret", "Redact"}


def _kind(annot) -> str:
    return str(annot.get("/Subtype", "/Unknown"))[1:]


def _appearance(annot):
    """The appearance form to draw, None if it draws nothing, or a reason it cannot be used."""
    ap = annot.get("/AP")
    if not isinstance(ap, pikepdf.Dictionary) or "/N" not in ap:
        return "it has no stored appearance"
    normal = ap.N
    if isinstance(normal, pikepdf.Dictionary) and not isinstance(normal, pikepdf.Stream):
        state = annot.get("/AS")
        if state is None:
            return "it has several appearances and does not say which is showing"
        if state not in normal:
            return None  # e.g. an unticked box with no 'Off' drawing: nothing to draw
        normal = normal[state]
    if not isinstance(normal, pikepdf.Stream):
        return "its appearance cannot be read"
    if normal.get("/Subtype") != pikepdf.Name.Form or "/BBox" not in normal:
        return "its appearance is not a drawing this fix can place"
    return normal


def _placement(annot, form):
    """The cm matrix placing the appearance on its Rect (PDF 32000-1, 12.5.5), or None if it has no size."""
    r = [float(v) for v in annot.Rect]
    rx0, ry0, rx1, ry1 = min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])
    b = [float(v) for v in form.BBox]
    a, bb, c, d, e, f = [float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0])]
    corners = [(x * a + y * c + e, x * bb + y * d + f) for x in (b[0], b[2]) for y in (b[1], b[3])]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    if bw <= 0 or bh <= 0 or rx1 <= rx0 or ry1 <= ry0:
        return None
    sx, sy = (rx1 - rx0) / bw, (ry1 - ry0) / bh
    return [sx, 0, 0, sy, rx0 - sx * min(xs), ry0 - sy * min(ys)]


def _unembedded(form, seen=None) -> set:
    """Names of fonts the appearance uses (in it or forms inside it) that are not embedded."""
    seen = set() if seen is None else seen
    if form.objgen in seen and form.is_indirect:
        return set()
    seen.add(form.objgen)
    out = set()
    try:
        res = form.get("/Resources", pikepdf.Dictionary())
        for font in res.get("/Font", pikepdf.Dictionary()).values():
            fonts = list(font.get("/DescendantFonts", [])) or [font]
            for f in fonts:
                desc = f.get("/FontDescriptor")
                if str(font.get("/Subtype")) != "/Type3" and (
                        desc is None or not any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))):
                    out.add(str(f.get("/BaseFont", "/unnamed"))[1:])
        for x in res.get("/XObject", pikepdf.Dictionary()).values():
            if x.get("/Subtype") == pikepdf.Name.Form:
                out |= _unembedded(x, seen)
    except Exception:
        out.add("fonts that could not be read")
    return out


def _counted(annot) -> bool:
    """One the Annotations check counts: not a link, trap net or printer's mark, and not hidden."""
    return _kind(annot) not in IGNORED and not int(annot.get("/F", 0)) & HIDDEN


def _propose(name, path, flatten):
    proposal = Proposal(name, path)
    with pikepdf.open(path) as pdf:
        form = pdf.Root.get("/AcroForm")
        redraw = bool(form is not None and form.get("/NeedAppearances", False))
        for number, page in enumerate(pdf.pages, 1):
            try:
                annots = list(page.obj.get("/Annots", []))
            except Exception:
                proposal.skipped.append((number, "the annotations could not be read; nothing on this page was changed"))
                continue
            rotated = int(page.obj.get("/Rotate", 0)) % 360 != 0
            draw, take, kinds, left, fonts = [], [], {}, [], set()
            for i, annot in enumerate(annots):
                try:
                    if not isinstance(annot, pikepdf.Dictionary) or not _counted(annot):
                        continue
                    kind, flags = _kind(annot), int(annot.get("/F", 0))
                except Exception:
                    left.append("annotation: it could not be read")
                    continue
                label = LABELS.get(kind, kind)
                if not flatten:
                    take.append(i)
                    kinds[label] = kinds.get(label, 0) + 1
                    continue
                if not flags & PRINT:
                    continue  # not set to print: left where it is
                why = None
                if kind in COMMENTS:
                    why = "a comment mark, not artwork; remove-annotations takes it off"
                elif kind == "Widget" and redraw:
                    why = "the form asks the viewer to redraw its fields, so the stored look may not show the value"
                elif "/OC" in annot:
                    why = "it is on a layer"
                elif flags & NO_VIEW:
                    why = "it is hidden on screen but set to print"
                elif flags & NO_ROTATE and rotated:
                    why = "it stays upright when the page is rotated"
                appearance = None if why else _appearance(annot)
                if isinstance(appearance, str):
                    why = appearance
                if why:
                    left.append(f"{label}: {why}")
                    continue
                if appearance is not None:
                    cm = _placement(annot, appearance)
                    if cm is None:
                        left.append(f"{label}: its appearance has no size")
                        continue
                    draw.append((i, cm))
                    fonts |= _unembedded(appearance)
                take.append(i)
                kinds[label] = kinds.get(label, 0) + 1
            # A comment's pop-up note goes with it.
            gone = {annots[i].objgen for i in take if annots[i].is_indirect}
            for i, annot in enumerate(annots):
                try:
                    parent = annot.get("/Parent") if _kind(annot) == "Popup" else None
                except Exception:
                    continue
                if parent is not None and parent.objgen in gone and i not in take:
                    take.append(i)
            for item in left:
                proposal.skipped.append((number, item))
            if not take:
                continue
            what = ", ".join(f"{n} {k}" + ("s" if n > 1 else "") for k, n in
                             sorted(kinds.items(), key=lambda kv: -kv[1]))
            if flatten:
                text = f"Draws {what} into the page as they look now, then takes them off the page"
                if fonts:
                    text += (f". Their lettering uses fonts that are not embedded ({', '.join(sorted(fonts))}), "
                             "which the Font embedding check will then report")
            else:
                text = f"Takes {what} off the page; the artwork is untouched"
            proposal.reasons.append((number, text + "."))
            proposal.plan[number] = {"take": sorted(take), "draw": draw}
    return proposal


def propose_flatten_annotations(path: str, options: Options | None = None, **_) -> Proposal:
    """What flatten-annotations would change. Reads only."""
    return _propose(FLATTEN, path, True)


def propose_remove_annotations(path: str, options: Options | None = None, **_) -> Proposal:
    """What remove-annotations would change. Reads only."""
    return _propose(REMOVE, path, False)


def _prune_fields(node, gone, changes):
    """The field (or None if nothing is left of it) with removed widgets taken out of its Kids."""
    if node.objgen in gone:
        return None
    kids = node.get("/Kids")
    if kids is None:
        return node
    kept = [k for k in (_prune_fields(k, gone, changes) for k in kids) if k is not None]
    if not kept:
        return None
    if len(kept) != len(kids):
        copy = pikepdf.Dictionary({k: node[k] for k in node.keys()})
        copy["/Kids"] = pikepdf.Array(kept)
        changes[node.objgen] = copy
    return node


def _render(path, numbers):
    out = {}
    with pymupdf.open(path) as doc:
        for n in numbers:
            pix = doc.load_page(n - 1).get_pixmap(dpi=72, alpha=False, annots=True)
            out[n] = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)
    return out


def _streams(page):
    """The page's content streams: object numbers, or the bytes of any stored directly."""
    c = page.get("/Contents")
    parts = list(c) if isinstance(c, pikepdf.Array) else ([c] if c is not None else [])
    return [p.objgen if p.is_indirect else p.read_raw_bytes() for p in parts]


def _check(src, plan, kept, originals, flatten):
    def check(new_path):
        with pikepdf.open(src) as old:
            contents = {n: _streams(old.pages[n - 1].obj) for n in plan}
        with pikepdf.open(new_path) as pdf:
            for number, page_plan in plan.items():
                page = pdf.pages[number - 1].obj
                if [a.objgen for a in page.get("/Annots", [])] != kept[number]:
                    raise AppendError(f"verification failed: page {number} does not keep exactly the annotations planned")
                if page_plan["draw"]:
                    parts = list(page.Contents)
                    if [p.objgen for p in parts[1:-1]] != originals[number]:
                        raise AppendError(f"verification failed: page {number}'s own content is not the original")
                elif not flatten:
                    if _streams(page) != contents[number]:
                        raise AppendError(f"verification failed: page {number}'s content changed")
        if not flatten:
            return
        before, after = _render(src, plan), _render(new_path, plan)
        for number in plan:
            a, b = before[number], after[number]
            if a.shape != b.shape:
                raise AppendError(f"verification failed: page {number} renders at a different size")
            off = np.abs(a - b).max(axis=2) > 24
            if off.mean() > 0.001:
                raise AppendError(f"verification failed: page {number} does not look as it did "
                                  f"({off.mean():.2%} of it differs)")
    return check


def _write(proposal: Proposal, dst: str) -> None:
    if proposal.empty:
        raise ValueError("nothing to change")
    with pikepdf.open(proposal.path) as pdf:
        changes, additions, kept, originals, gone_widgets = {}, {}, {}, {}, set()
        for number, page_plan in proposal.plan.items():
            obj = pdf.pages[number - 1].obj
            annots = list(obj.Annots)
            take = set(page_plan["take"])
            for i in take:
                if _kind(annots[i]) == "Widget" and annots[i].is_indirect:
                    gone_widgets.add(annots[i].objgen)
            remaining = [a for i, a in enumerate(annots) if i not in take]
            kept[number] = [a.objgen for a in remaining]
            copy = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
            if remaining:
                copy["/Annots"] = pikepdf.Array(remaining)
            else:
                del copy["/Annots"]
            if page_plan["draw"]:
                page = pdf.pages[number - 1]
                old_res = page.resources
                res = pikepdf.Dictionary({k: old_res[k] for k in old_res.keys()})
                xobj = res.get("/XObject")
                xobjects = pikepdf.Dictionary({k: xobj[k] for k in xobj.keys()}) if xobj is not None else pikepdf.Dictionary()
                lines = []
                for n, (i, cm) in enumerate(page_plan["draw"]):
                    key = f"Annot{n}"
                    while "/" + key in xobjects:
                        key += "x"
                    xobjects["/" + key] = _appearance(annots[i])
                    lines.append("q " + " ".join(f"{v:.6f}" for v in cm) + f" cm /{key} Do Q")
                res["/XObject"] = xobjects
                copy["/Resources"] = res
                contents = obj.get("/Contents")
                existing = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
                if any(not p.is_indirect for p in existing):
                    raise AppendError(f"page {number}'s content is not stored as separate streams; it cannot be kept as is")
                originals[number] = [p.objgen for p in existing]
                pre = pdf.make_indirect(pikepdf.Stream(pdf, b"q\n"))
                post = pdf.make_indirect(pikepdf.Stream(pdf, ("Q\n" + "\n".join(lines) + "\n").encode()))
                additions[pre.objgen], additions[post.objgen] = pre, post
                copy["/Contents"] = pikepdf.Array([pre, *existing, post])
            changes[obj.objgen] = copy
        form = pdf.Root.get("/AcroForm")
        if gone_widgets and form is not None:
            # Fields whose widgets are all gone leave the form; with none left, the form goes.
            field_changes = {}
            fields = [f for f in (_prune_fields(f, gone_widgets, field_changes) for f in form.get("/Fields", []))
                      if f is not None]
            root = pikepdf.Dictionary({k: pdf.Root[k] for k in pdf.Root.keys()})
            if fields:
                new_form = pikepdf.Dictionary({k: form[k] for k in form.keys()})
                new_form["/Fields"] = pikepdf.Array(fields)
                if form.is_indirect:
                    changes[form.objgen] = new_form
                else:
                    root["/AcroForm"] = new_form
                    changes[pdf.Root.objgen] = root
            else:
                del root["/AcroForm"]
                changes[pdf.Root.objgen] = root
            changes.update({og: d for og, d in field_changes.items() if og not in changes})
        append_update(proposal.path, dst, changes, additions,
                      extra_check=_check(proposal.path, proposal.plan, kept, originals, proposal.fix == FLATTEN))


write_flatten_annotations = write_remove_annotations = _write
