"""Convert spot colours to CMYK, using each spot's own CMYK definition.

`preflight fix spot-to-cmyk file.pdf [--spot NAME ...]`. With no --spot, every
printing spot that can be converted is.

What changes:

  * flat colour and text: the colour instructions in the artwork are rewritten,
    e.g. "/CS0 cs 0.6 scn" in PANTONE 286 C becomes "/DeviceCMYK cs" with 60%
    of 286's CMYK definition. Rewritten streams are new or replacement objects;
    the original page content streams stay in the file, unused;
  * gradients in a spot become CMYK gradients with the same stops.

Overprint settings are left as they are.

A spot is converted only if every use of it can be: its definition must be a
plain CMYK one (Separation, DeviceCMYK alternate, linear-or-exponential tint
transform), and it must not be used inside images, mixed with other inks
(DeviceN), in inline images, or in annotations. Otherwise that spot is left
alone, with the reason. Non-printing spots are never converted.

Verification: every page that used a converted spot renders as a composite
CMYK proof the same before and after, within rounding, and the converted spots
are no longer used anywhere.
"""
from __future__ import annotations

import zlib
from collections import defaultdict

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from ..content import PROCESS_NAMES, walk_page
from ..document import Document
from ..edges import is_non_printing
from .append import AppendError, append_update
from .base import Proposal

NAME = "spot-to-cmyk"
CONTAINERS = (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)


# ---- the spot's CMYK definition --------------------------------------------

class Tint:
    """A Separation's tint transform, when it is a plain CMYK one."""

    def __init__(self, c0, c1, n):
        self.c0, self.c1, self.n = c0, c1, n

    def cmyk(self, t: float) -> list[float]:
        t = min(max(t, 0.0), 1.0)
        f = t ** self.n if self.n != 1 else t
        return [a + f * (b - a) for a, b in zip(self.c0, self.c1)]


def _tint(space) -> tuple[Tint | None, str]:
    """The Tint of a Separation space, or (None, why it cannot be converted)."""
    alt, fn = space[2], space[3]
    alt_name = str(alt[0]) if isinstance(alt, pikepdf.Array) else str(alt)
    if alt_name != "/DeviceCMYK":
        return None, f"its definition is in {alt_name[1:]}, not CMYK (converting it would need a colour profile)"
    if isinstance(fn, pikepdf.Stream) or int(fn.get("/FunctionType", -1)) != 2:
        return None, "its CMYK definition is not a simple one this fix can read"
    c0 = [float(v) for v in fn.get("/C0", [0, 0, 0, 0])]
    c1 = [float(v) for v in fn.get("/C1", [1, 1, 1, 1])]
    if len(c0) != 4 or len(c1) != 4:
        return None, "its CMYK definition does not have four values"
    return Tint(c0, c1, float(fn.get("/N", 1))), ""


def _space_of(obj, resources):
    """Resolve a colour space operand or entry to its array, if it is one."""
    if isinstance(obj, pikepdf.Name):
        spaces = resources.get("/ColorSpace") if resources is not None else None
        if spaces is not None and str(obj) in spaces:
            return spaces[str(obj)]
        return None
    return obj if isinstance(obj, pikepdf.Array) else None


def _spot_name(space):
    if isinstance(space, pikepdf.Array) and len(space) >= 4 and str(space[0]) == "/Separation":
        return str(space[1])[1:]
    return None


def _devicen_names(space):
    if isinstance(space, pikepdf.Array) and len(space) >= 4 and str(space[0]) == "/DeviceN":
        return [str(n)[1:] for n in space[1]]
    return []


# ---- rewriting content -------------------------------------------------------

def _num(v: float) -> pikepdf.Object:
    return round(float(v), 5)


def _ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


class Rewriter:
    """Finds the uses of the target spots and rewrites colour instructions.

    targets: {spot name: Tint}. After scan()/rewrite(), `blocked` holds the spots
    used in ways that cannot be converted, with reasons, and `used` the spots met.
    """

    def __init__(self, targets):
        self.targets = targets
        self.blocked: dict[str, set] = defaultdict(set)
        self.used: set = set()

    def _target(self, operand, resources):
        name = _spot_name(_space_of(operand, resources))
        return name if name in self.targets else None

    def _check_mixed(self, operand, resources, where):
        for n in _devicen_names(_space_of(operand, resources)):
            if n in self.targets:
                self.blocked[n].add(f"it is mixed with other inks {where}")

    def rewrite(self, instructions, resources, where="in the artwork"):
        """New instructions, and whether anything changed."""
        out, changed = [], False
        fill = stroke = None  # the target spot the current fill/stroke colour space is, if any
        stack = []
        for ins in instructions:
            op = str(ins.operator)
            ops = ins.operands
            if op == "q":
                stack.append((fill, stroke))
            elif op == "Q" and stack:
                fill, stroke = stack.pop()
            if op in ("cs", "CS"):
                spot = self._target(ops[0], resources)
                self._check_mixed(ops[0], resources, where)
                if op == "cs":
                    fill = spot
                else:
                    stroke = spot
                if spot:
                    self.used.add(spot)
                    # The colour a Separation starts with is a full tint.
                    values = [_num(v) for v in self.targets[spot].cmyk(1.0)]
                    out += [_ins(op, pikepdf.Name.DeviceCMYK), _ins("sc" if op == "cs" else "SC", *values)]
                    changed = True
                    continue
            elif op in ("sc", "scn", "SC", "SCN"):
                spot = fill if op in ("sc", "scn") else stroke
                if spot:
                    nums = [float(v) for v in ops if not isinstance(v, pikepdf.Name)]
                    t = nums[0] if nums else 1.0
                    values = [_num(v) for v in self.targets[spot].cmyk(t)]
                    out.append(_ins("sc" if op in ("sc", "scn") else "SC", *values))
                    changed = True
                    continue
            elif op == "INLINE IMAGE":
                try:
                    cs = ops[0].obj.get("/ColorSpace") if hasattr(ops[0], "obj") else None
                    spot = self._target(cs, resources) if cs is not None else None
                    if spot:
                        self.blocked[spot].add(f"it is used in an inline image {where}")
                except Exception:
                    pass
            out.append(ins)
        return out, changed


# ---- gradients ---------------------------------------------------------------

def _shading_function(fn, tint: Tint):
    """The shading's function with the spot's CMYK definition folded in, or None."""
    if isinstance(fn, pikepdf.Array):
        return None  # one function per output: not a single-ink gradient
    kind = int(fn.get("/FunctionType", -1)) if not isinstance(fn, pikepdf.Stream) else int(fn.stream_dict.get("/FunctionType", -1))
    if kind == 2 and tint.n == 1:
        c0 = [float(v) for v in fn.get("/C0", [0])]
        c1 = [float(v) for v in fn.get("/C1", [1])]
        if len(c0) != 1 or len(c1) != 1:
            return None
        new = pikepdf.Dictionary({k: fn[k] for k in fn.keys()})
        new["/C0"] = [_num(v) for v in tint.cmyk(c0[0])]
        new["/C1"] = [_num(v) for v in tint.cmyk(c1[0])]
        return new
    if kind == 3 and not isinstance(fn, pikepdf.Stream):
        parts = [_shading_function(f, tint) for f in fn.Functions]
        if any(p is None for p in parts):
            return None
        new = pikepdf.Dictionary({k: fn[k] for k in fn.keys()})
        new["/Functions"] = pikepdf.Array(parts)
        return new
    return None


def _convert_shadings(obj, resources_owner_spaces, rewriter, depth=0):
    """A copy of a direct object with spot gradients converted, or None if nothing in it changes."""
    if depth > 12:
        return None
    if isinstance(obj, pikepdf.Dictionary) and "/ShadingType" in obj and "/ColorSpace" in obj:
        space = obj.ColorSpace
        if isinstance(space, pikepdf.Name):
            space = resources_owner_spaces.get(str(space)) if resources_owner_spaces else None
        spot = _spot_name(space)
        for n in _devicen_names(space):
            if n in rewriter.targets:
                rewriter.blocked[n].add("it is mixed with other inks in a gradient")
        if spot in rewriter.targets:
            rewriter.used.add(spot)
            fn = _shading_function(obj.get("/Function"), rewriter.targets[spot])
            if fn is None:
                rewriter.blocked[spot].add("it is used in a gradient this fix cannot convert")
                return None
            new = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
            new["/ColorSpace"] = pikepdf.Name.DeviceCMYK
            new["/Function"] = fn
            return new
    if isinstance(obj, pikepdf.Array):
        changed, items = False, []
        for item in obj:
            new = (_convert_shadings(item, resources_owner_spaces, rewriter, depth + 1)
                   if isinstance(item, (pikepdf.Array, pikepdf.Dictionary)) and not item.is_indirect else None)
            changed |= new is not None
            items.append(new if new is not None else item)
        return pikepdf.Array(items) if changed else None
    if isinstance(obj, pikepdf.Dictionary):
        changed, copy = False, pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
        for k in obj.keys():
            item = obj[k]
            new = (_convert_shadings(item, resources_owner_spaces, rewriter, depth + 1)
                   if isinstance(item, (pikepdf.Array, pikepdf.Dictionary)) and not item.is_indirect else None)
            if new is not None:
                copy[k] = new
                changed = True
        return copy if changed else None
    return None


# ---- the whole file ----------------------------------------------------------

def _targets(pdf, wanted):
    """{spot: Tint} for the printing spots to convert, and {spot: why not} for the rest."""
    found, refused = {}, {}
    for obj in pdf.objects:
        _collect(obj, found, refused, 0)
    targets = {}
    for name, tints in found.items():
        if wanted and name not in wanted:
            continue
        if is_non_printing(name) or name in PROCESS_NAMES or name in ("All", "None"):
            continue
        if name in refused:
            continue
        if len({(tuple(t.c0), tuple(t.c1), t.n) for t in tints}) > 1:
            refused[name] = "it is defined more than one way"
            continue
        targets[name] = tints[0]
    if wanted:
        for name in wanted:
            if name not in found and name not in refused:
                refused[name] = "there is no such spot colour in the file"
            elif is_non_printing(name):
                refused[name] = "it is a non-printing colour"
    never = lambda n: is_non_printing(n) or n in PROCESS_NAMES or n in ("All", "None")
    refused = {n: r for n, r in refused.items() if (not wanted or n in wanted) and not (never(n) and not wanted)}
    return targets, refused


def _collect(obj, found, refused, depth):
    if depth > 12:
        return
    if isinstance(obj, pikepdf.Array):
        name = _spot_name(obj)
        if name:
            tint, why = _tint(obj)
            if tint is None:
                refused.setdefault(name, why)
            else:
                found.setdefault(name, []).append(tint)
            return
        for item in obj:
            if isinstance(item, CONTAINERS) and not item.is_indirect:
                _collect(item, found, refused, depth + 1)
    elif isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
        items = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
        for key in items.keys():
            item = items[key]
            if isinstance(item, CONTAINERS) and not item.is_indirect:
                _collect(item, found, refused, depth + 1)


def _plan(pdf, targets):
    """Run the rewriter over everything once: what would change, and what blocks which spot."""
    rw = Rewriter(targets)
    pages, forms, patterns, shadings = {}, {}, {}, {}
    for number, page in enumerate(pdf.pages, 1):
        res = page.resources
        new, changed = rw.rewrite(list(pikepdf.parse_content_stream(page)), res, "on the page")
        if changed:
            pages[number] = new
        for annot in page.obj.get("/Annots", []):
            for _, ap in (annot.get("/AP") or {}).items():
                for s in ([ap] if isinstance(ap, pikepdf.Stream) else [ap[k] for k in ap.keys()]):
                    probe = Rewriter(targets)
                    probe.rewrite(list(pikepdf.parse_content_stream(s)), s.get("/Resources", res))
                    for spot in probe.used:
                        rw.blocked[spot].add("it is used in an annotation")
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream):
            continue
        d = obj.stream_dict
        is_form = str(d.get("/Subtype")) == "/Form"
        is_tiling = int(d.get("/PatternType", 0)) == 1
        if is_form or is_tiling:
            res = d.get("/Resources")
            if res is None:
                continue
            try:
                instructions = list(pikepdf.parse_content_stream(obj))
            except Exception:
                continue
            new, changed = rw.rewrite(instructions, res, "in a placed graphic" if is_form else "in a pattern")
            if changed:
                (forms if is_form else patterns)[obj.objgen] = new
        elif str(d.get("/Subtype")) == "/Image":
            spot = _spot_name(d.get("/ColorSpace")) if isinstance(d.get("/ColorSpace"), pikepdf.Array) else None
            for n in ([spot] if spot else []) + _devicen_names(d.get("/ColorSpace")):
                if n in targets:
                    rw.blocked[n].add("it is used in an image")
    # Gradients: shading dictionaries anywhere, and shading patterns.
    for obj in pdf.objects:
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array)) or isinstance(obj, pikepdf.Stream):
            target = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
            if isinstance(target, pikepdf.Array):
                new = _convert_shadings(target, None, rw)
            else:
                spaces = None
                res = target.get("/Resources") if isinstance(target, pikepdf.Dictionary) else None
                if isinstance(res, pikepdf.Dictionary) and isinstance(res.get("/ColorSpace"), pikepdf.Dictionary):
                    spaces = res.ColorSpace
                new = _convert_shadings(target, spaces, rw)
            if new is not None:
                shadings[obj.objgen] = new
    return rw, pages, forms, patterns, shadings


def _flate(pdf, data: bytes):
    stream = pikepdf.Stream(pdf, zlib.compress(data, 9))
    stream["/Filter"] = pikepdf.Name.FlateDecode
    return stream


def propose_spot_to_cmyk(path: str, options: Options | None = None, spots=None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    proposal = Proposal(NAME, path)
    wanted = set(spots or [])
    doc = Document(path)
    try:
        uses = defaultdict(set)
        for page in doc.pages:
            for e in walk_page(page.obj).events:
                for n in e.cs.names:
                    uses[n].add(page.number)
    finally:
        doc.close()

    def where(name):
        return sorted(uses.get(name, ())) or [1]

    with pikepdf.open(path) as pdf:
        targets, refused = _targets(pdf, wanted)
        for name, why in sorted(refused.items()):
            for page in where(name):
                proposal.skipped.append((page, f'"{name}" because {why}'))
        if not targets:
            return proposal
        rw, *_rest = _plan(pdf, targets)
        for name, reasons in sorted(rw.blocked.items()):
            for page in where(name):
                proposal.skipped.append((page, f'"{name}" because ' + "; ".join(sorted(reasons))))
        convert = {n: t for n, t in targets.items() if n not in rw.blocked and n in rw.used}
    if not convert:
        return proposal
    for name in sorted(convert):
        c, m, y, k = (v * 100 for v in convert[name].cmyk(1.0))
        text = f'Converts "{name}" to CMYK: 100% becomes C{c:.0f} M{m:.0f} Y{y:.0f} K{k:.0f}, tints in proportion.'
        for page in where(name):
            proposal.reasons.append((page, text))
    proposal.plan = {0: {"spots": sorted(convert), "pages": sorted({p for n in convert for p in uses.get(n, ())})}}
    return proposal


def _check(src, plan):
    def check(new_path):
        doc = Document(new_path)
        try:
            for page in doc.pages:
                for e in walk_page(page.obj).events:
                    left = [n for n in e.cs.names if n in plan["spots"]]
                    if left:
                        raise AppendError(f'verification failed: "{left[0]}" is still used on page {page.number}')
        finally:
            doc.close()
        a, b = pymupdf.open(src), pymupdf.open(new_path)
        try:
            for number in plan["pages"]:
                pa, pb = a.load_page(number - 1), b.load_page(number - 1)
                w, h = pa.rect.width / 72, pa.rect.height / 72
                dpi = int(max(18, min(72, (3_000_000 / max(w * h, 1e-6)) ** 0.5)))
                x = pa.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, alpha=False, annots=False)
                y = pb.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, alpha=False, annots=False)
                if (x.width, x.height) != (y.width, y.height):
                    raise AppendError(f"verification failed: page {number} could not be compared")
                diff = np.abs(np.frombuffer(x.samples, np.uint8).astype(int) - np.frombuffer(y.samples, np.uint8).astype(int))
                if (diff > 3).mean() > 0.001:
                    raise AppendError(f"verification failed: page {number} looks different after converting")
        finally:
            a.close()
            b.close()
    return check


def write_spot_to_cmyk(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan = proposal.plan[0]
    with pikepdf.open(proposal.path) as pdf:
        targets, _ = _targets(pdf, set(plan["spots"]))
        targets = {n: t for n, t in targets.items() if n in plan["spots"]}
        rw, pages, forms, patterns, shadings = _plan(pdf, targets)
        if any(n in rw.blocked for n in targets):
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = {}, {}
        for number, instructions in pages.items():
            obj = pdf.pages[number - 1].obj
            stream = pdf.make_indirect(_flate(pdf, pikepdf.unparse_content_stream(instructions)))
            additions[stream.objgen] = stream
            copy = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
            copy["/Contents"] = stream
            changes[obj.objgen] = copy
        for objgen, instructions in {**forms, **patterns}.items():
            old = pdf.get_object(objgen)
            new = _flate(pdf, pikepdf.unparse_content_stream(instructions))
            head = shadings.pop(objgen, None) or old.stream_dict
            for k in head.keys():
                if k not in ("/Length", "/Filter", "/DecodeParms"):
                    new[k] = head[k]
            changes[objgen] = new
        for objgen, new_dict in shadings.items():
            old = pdf.get_object(objgen)
            if isinstance(old, pikepdf.Stream):
                stream = pikepdf.Stream(pdf, old.read_raw_bytes())
                for k in new_dict.keys():
                    if k != "/Length":
                        stream[k] = new_dict[k]
                changes[objgen] = stream
            elif objgen in changes:  # a page dictionary already being replaced
                for k in new_dict.keys():
                    if k != "/Contents":
                        changes[objgen][k] = new_dict[k]
            else:
                changes[objgen] = new_dict
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, plan))
