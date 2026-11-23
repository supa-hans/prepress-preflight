"""Remove a layer: what is on it stops printing, and the layer leaves the file.

    preflight fix remove-layer FILE --layer "Skoop Template" [--layer NAME ...]
    preflight fix remove-layer FILE          (every hidden layer)

A layer (optional content group) marks parts of the page content. Taking that
content out wholesale is not safe: Illustrator, for one, opens a saved graphics
state (q) on one layer and closes it (Q) on the next, so a layer's part of the
page does not stand on its own. So, as remove-object does, every instruction
that sets state is kept and only the painting goes:

  * a path's painting operator becomes `n` (the path is still built, so a clip
    made with it still applies);
  * images, gradients (sh) and placed forms (Do), and inline images, are taken
    out: none of them changes the state;
  * text is shown in the invisible render mode (3 Tr), with the mode in force
    put back after, so later text still starts in the same place;
  * the layer's begin and end markers (BDC ... EMC) are taken out.

A placed form or image that is itself on the layer is taken out, as are
annotations on it. The layer is then removed from the file's layer list (and
the list, if it is the last). Content on the layer inside a placed form, and
content governed by a mix of this layer and others (an OCMD), are refused for
that page and said so. Verified by rendering: each changed page must look as
the original does with the layer switched off.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from .append import AppendError, append_update
from .base import Proposal

NAME = "remove-layer"
PAINT = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}
SHOW = {"Tj", "TJ", "'", '"'}
DROP = {"sh", "Do", "BI", "INLINE IMAGE"}


def _ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


def _layers(pdf):
    """[(objgen, name, hidden)] for every layer the file lists."""
    props = pdf.Root.get("/OCProperties")
    if props is None:
        return []
    config = props.get("/D", pikepdf.Dictionary())
    off = {g.objgen for g in config.get("/OFF", [])}
    on = {g.objgen for g in config.get("/ON", [])}
    base_off = str(config.get("/BaseState", "/ON")) == "/OFF"
    return [(g.objgen, str(g.get("/Name", "(unnamed)")), g.objgen in off or (base_off and g.objgen not in on))
            for g in props.get("/OCGs", [])]


def _governed(oc, targets):
    """True if `oc` (an OCG or OCMD) is only ever the target layers, False if none of them,
    None if it mixes them with others (cannot be decided here)."""
    if oc is None:
        return False
    if oc.get("/Type") == pikepdf.Name.OCMD or "/OCGs" in oc or "/VE" in oc:
        if "/VE" in oc:
            return None
        groups = oc.get("/OCGs")
        groups = [groups] if isinstance(groups, pikepdf.Dictionary) else list(groups or [])
        hits = [g.objgen in targets for g in groups]
        return True if hits and all(hits) else (False if not any(hits) else None)
    return oc.objgen in targets


def _form_has(form, targets, seen) -> bool:
    """Whether a form's own content (or forms inside it) uses the target layers."""
    if form.objgen in seen:
        return False
    seen.add(form.objgen)
    res = form.get("/Resources", pikepdf.Dictionary())
    for prop in res.get("/Properties", pikepdf.Dictionary()).values():
        if _governed(prop, targets) is not False:
            return True
    for x in res.get("/XObject", pikepdf.Dictionary()).values():
        if _governed(x.get("/OC"), targets) is not False:
            return True
        if x.get("/Subtype") == pikepdf.Name.Form and _form_has(x, targets, seen):
            return True
    return False


def _rewrite(instructions, resources, targets):
    """(new instructions, removed painting count, reason) for one page's content."""
    props = resources.get("/Properties", pikepdf.Dictionary())
    xobjects = resources.get("/XObject", pikepdf.Dictionary())
    out, removed = [], 0
    marks = []  # for each open marked-content section: True if it is on a target layer
    tr_stack, tr = [], 0
    for ins in instructions:
        op = str(ins.operator)
        inside = any(marks)
        if op in ("BDC", "BMC"):
            target = False
            if op == "BDC" and len(ins.operands) == 2 and ins.operands[0] == pikepdf.Name.OC:
                prop = ins.operands[1]
                prop = props.get(prop) if isinstance(prop, pikepdf.Name) else prop
                target = _governed(prop, targets)
                if target is None:
                    return None, 0, "part of the page is governed by this layer together with others"
            marks.append(bool(target))
            if not target:
                out.append(ins)
            continue
        if op == "EMC":
            if not marks:
                out.append(ins)
                continue
            if not marks.pop():
                out.append(ins)
            continue
        if op == "q":
            tr_stack.append(tr)
        elif op == "Q":
            tr = tr_stack.pop() if tr_stack else 0
        elif op == "Tr":
            tr = int(ins.operands[0])
        if op == "Do" and not inside:
            x = xobjects.get(ins.operands[0])
            on = _governed(x.get("/OC"), targets) if x is not None else False
            if on is None:
                return None, 0, "a placed object is governed by this layer together with others"
            if on:
                removed += 1
                continue
        if inside:
            if op in PAINT:
                out.append(_ins("n"))
                removed += 1
                continue
            if op in DROP or isinstance(ins, pikepdf.ContentStreamInlineImage):
                removed += 1
                continue
            if op in SHOW:
                out += [_ins("Tr", 3), ins, _ins("Tr", tr)]
                removed += 1
                continue
        out.append(ins)
    return out, removed, None


def _marks_lost(page, new) -> str | None:
    """The size the crop marks point at, if they are only on the removed layer (else None)."""
    from ..content import walk_page
    from ..marks import implied_trim
    from .base import size_text
    before = implied_trim(walk_page(page))
    if before is None:
        return None
    with pikepdf.new() as scratch:
        copy = scratch.copy_foreign(page.obj)
        copy["/Contents"] = scratch.make_stream(pikepdf.unparse_content_stream(new))
        scratch.pages.append(pikepdf.Page(copy))
        after = implied_trim(walk_page(scratch.pages[0]))
    if after is not None:
        return None
    trim = [float(v) for v in page.trimbox]
    if max(abs(a - b) for a, b in zip(sorted(before[0::2]) + sorted(before[1::2]),
                                       sorted(trim[0::2]) + sorted(trim[1::2]))) * 25.4 / 72 <= 0.5:
        return None  # the trim box already says the same, so nothing is lost
    return f"{size_text(before)} while the trim box is {size_text(trim)}"


def _pick(pdf, wanted):
    layers = _layers(pdf)
    if wanted:
        names = {}
        for og, name, _ in layers:
            names.setdefault(name.casefold(), []).append(og)
        missing = [w for w in wanted if w.casefold() not in names]
        if missing:
            known = ", ".join(n for _, n, _ in layers) or "none"
            raise ValueError(f"no layer named {', '.join(missing)} (the file's layers: {known})")
        return {og for w in wanted for og in names[w.casefold()]}, layers
    return {og for og, _, hidden in layers if hidden}, layers


def propose_remove_layer(path: str, options: Options | None = None, layer=None, **_) -> Proposal:
    """What the fix would change. Reads only. `layer`: names; none means every hidden layer."""
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        targets, layers = _pick(pdf, list(layer or []))
        if not layers:
            proposal.skipped.append((1, "the file has no layers"))
            return proposal
        if not targets:
            proposal.skipped.append((1, "no layer is hidden; name the one to remove with --layer "
                                        f"(the file's layers: {', '.join(n for _, n, _ in layers)})"))
            return proposal
        names = ", ".join(n for og, n, _ in layers if og in targets)
        pages = {}
        for number, page in enumerate(pdf.pages, 1):
            res = page.resources
            for x in res.get("/XObject", pikepdf.Dictionary()).values():
                if x.get("/Subtype") == pikepdf.Name.Form and _form_has(x, targets, set()):
                    proposal.skipped.append((number, "the layer is used inside a placed form, which this fix does "
                                                     "not change; nothing on this page was changed"))
                    break
            else:
                new, removed, why = _rewrite(pikepdf.parse_content_stream(page), res, targets)
                annots = [i for i, a in enumerate(page.obj.get("/Annots", []))
                          if _governed(a.get("/OC"), targets)]
                if why:
                    proposal.skipped.append((number, why + "; nothing on this page was changed"))
                elif removed or annots:
                    what = f"{removed} painted object(s)" + (f" and {len(annots)} annotation(s)" if annots else "")
                    text = f"Takes {what} on {names} off the page; everything else, and the drawing state, is kept."
                    marks = _marks_lost(page, new)
                    if marks:
                        text += (f" The crop marks are on this layer: they point at {marks}. Once they are gone "
                                 "the trim comes from the trim box alone, so run trim-from-marks first if the marks "
                                 "are right (trim-from-marks+remove-layer).")
                    proposal.reasons.append((number, text))
                    pages[number] = {"annots": annots}
        if proposal.skipped:
            # All or nothing: a page left with the layer's content would print it once the layer is gone.
            proposal.reasons = []
            proposal.skipped.append((1, f"{names} was not removed, since it could not be taken off every page"))
            return proposal
        proposal.plan = {"targets": sorted(targets), "pages": pages, "names": names}
        if not pages:
            proposal.reasons.append((1, f"Removes {names} from the file's layer list; nothing is drawn on it."))
    return proposal


def _strip(value, targets):
    """A copy of a layer list (nested arrays allowed) without the target layers."""
    if isinstance(value, pikepdf.Array):
        return pikepdf.Array([_strip(v, targets) for v in value
                              if not (isinstance(v, pikepdf.Dictionary) and v.objgen in targets)])
    return value


def _config(config, targets):
    copy = pikepdf.Dictionary({k: config[k] for k in config.keys()})
    for key in ("/ON", "/OFF", "/Order", "/Locked", "/RBGroups"):
        if key in copy:
            copy[key] = _strip(copy[key], targets)
    if "/AS" in copy:
        usage = []
        for entry in copy.AS:
            e = pikepdf.Dictionary({k: entry[k] for k in entry.keys()})
            if "/OCGs" in e:
                e["/OCGs"] = _strip(e.OCGs, targets)
            usage.append(e)
        copy["/AS"] = pikepdf.Array(usage)
    return copy


def _hidden_copy(src, targets, folder):
    """A scratch copy of the original whose default layer settings switch the target layers off:
    the standard way to hide a layer, so the reference does not depend on this fix's own rewriting."""
    path = os.path.join(folder, "reference.pdf")
    with pikepdf.open(src) as pdf:
        config = pdf.Root.OCProperties.D
        off = [g for g in pdf.Root.OCProperties.OCGs if g.objgen in targets]
        config["/OFF"] = pikepdf.Array(list(config.get("/OFF", [])) + off)
        if "/ON" in config:
            config["/ON"] = _strip(config.ON, targets)
        pdf.save(path)
    return path


def _render(path, numbers):
    out = {}
    with pymupdf.open(path) as doc:
        for n in numbers:
            pix = doc.load_page(n - 1).get_pixmap(dpi=36, colorspace=pymupdf.csCMYK, alpha=False, annots=False)
            out[n] = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)
    return out


def _check(src, plan):
    def check(new_path):
        numbers = list(plan["pages"])
        with pikepdf.open(new_path) as pdf:
            if any(og in set(plan["targets"]) for og, _, _ in _layers(pdf)):
                raise AppendError("verification failed: the layer is still listed")
        if not numbers:
            return
        with tempfile.TemporaryDirectory(prefix="preflight-layer-") as folder:
            reference = _hidden_copy(src, {tuple(og) for og in plan["targets"]}, folder)
            before, after = _render(reference, numbers), _render(new_path, numbers)
        for n in numbers:
            if before[n].shape != after[n].shape:
                raise AppendError(f"verification failed: page {n} renders at a different size")
            off = np.abs(before[n] - after[n]).max(axis=2) > 24
            if off.mean() > 0.001:
                raise AppendError(f"verification failed: page {n} does not look as the original does with the "
                                  f"layer off ({off.mean():.2%} of it differs)")
    return check


def write_remove_layer(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan = proposal.plan
    targets = {tuple(og) for og in plan["targets"]}
    with pikepdf.open(proposal.path) as pdf:
        changes, additions = {}, {}
        for number, page_plan in plan["pages"].items():
            page = pdf.pages[number - 1]
            obj = page.obj
            new, _, why = _rewrite(pikepdf.parse_content_stream(page), page.resources, targets)
            if why:
                raise AppendError(f"page {number}: {why}")
            stream = pdf.make_indirect(pikepdf.Stream(pdf, pikepdf.unparse_content_stream(new)))
            additions[stream.objgen] = stream
            copy = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
            copy["/Contents"] = stream
            if page_plan["annots"]:
                kept = [a for i, a in enumerate(obj.Annots) if i not in set(page_plan["annots"])]
                if kept:
                    copy["/Annots"] = pikepdf.Array(kept)
                else:
                    del copy["/Annots"]
            changes[obj.objgen] = copy
        props = pdf.Root.OCProperties
        remaining = [g for g in props.OCGs if g.objgen not in targets]
        root = pikepdf.Dictionary({k: pdf.Root[k] for k in pdf.Root.keys()})
        if remaining:
            new_props = pikepdf.Dictionary({k: props[k] for k in props.keys()})
            new_props["/OCGs"] = pikepdf.Array(remaining)
            new_props["/D"] = _config(props.D, targets)
            if "/Configs" in props:
                new_props["/Configs"] = pikepdf.Array([_config(c, targets) for c in props.Configs])
            if props.is_indirect:
                changes[props.objgen] = new_props
            else:
                root["/OCProperties"] = new_props
                changes[pdf.Root.objgen] = root
        else:
            del root["/OCProperties"]
            changes[pdf.Root.objgen] = root
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, plan))
