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

import os
import shutil
import tempfile
import zlib
from collections import defaultdict

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from ..content import PROCESS_NAMES, walk_page
from ..document import Document
from ..edges import is_non_printing
from ..functions import Function, FunctionError
from .append import AppendError, append_update
from .base import Proposal

NAME = "spot-to-cmyk"
CONTAINERS = (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)


# ---- the spot's CMYK definition --------------------------------------------

class Tint:
    """A Separation's tint transform into CMYK, any PDF function type."""

    def __init__(self, fn: Function):
        self.fn = fn
        # A plain linear definition lets gradients be converted exactly.
        self.linear = fn.kind == 2 and fn.n == 1
        self.key = (fn.kind, tuple(round(self.cmyk(t / 8)[i], 4) for t in range(9) for i in range(4)))

    def cmyk(self, t: float) -> list[float]:
        return self.fn([min(max(t, 0.0), 1.0)])[:4]


class Mix:
    """A DeviceN colour space that can be converted: its tint transform into CMYK."""

    def __init__(self, names, fn: Function):
        self.names, self.fn = names, fn

    def cmyk(self, values) -> list[float]:
        values = list(values)[: len(self.names)] + [1.0] * max(0, len(self.names) - len(values))
        return self.fn([min(max(v, 0.0), 1.0) for v in values])[:4]


def _alternate_is_cmyk(space) -> str:
    alt = space[2]
    alt_name = str(alt[0]) if isinstance(alt, pikepdf.Array) else str(alt)
    return "" if alt_name == "/DeviceCMYK" else alt_name[1:]


def _tint(space) -> tuple[Tint | None, str]:
    """The Tint of a Separation space, or (None, why it cannot be converted)."""
    other = _alternate_is_cmyk(space)
    if other:
        return None, f"its definition is in {other}, not CMYK (converting it would need a colour profile)"
    try:
        fn = Function(space[3])
        if len(fn([1.0])) < 4:
            raise FunctionError("too few outputs")
    except (FunctionError, Exception):
        return None, "its CMYK definition is not one this fix can read"
    return Tint(fn), ""


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
        self.created: list = []  # new objects (sampled gradient functions) the write must add

    def _target(self, operand, resources):
        name = _spot_name(_space_of(operand, resources))
        return name if name in self.targets else None

    def mix(self, space, where):
        """A Mix for a DeviceN space using target spots, or None. A DeviceN that mixes
        a target with an ink that is not being converted blocks that target."""
        names = _devicen_names(space)
        spots = [n for n in names if n in self.targets]
        if not spots:
            return None
        others = [n for n in names if n not in self.targets and n not in PROCESS_NAMES]
        why = None
        if others:
            why = f"it is mixed with other inks {where}"
        elif _alternate_is_cmyk(space):
            why = f"it is mixed with other inks {where}, defined in a space that needs a colour profile"
        else:
            try:
                fn = Function(space[3])
                if len(fn([1.0] * len(names))) < 4:
                    raise FunctionError("too few outputs")
            except Exception:
                why = f"it is mixed with other inks {where}, in a way this fix cannot read"
        if why:
            for n in spots:
                self.blocked[n].add(why)
            return None
        self.used.update(spots)
        return Mix(names, fn)

    def _converter(self, operand, resources, where):
        """What turns this colour space's values into CMYK, if it is to be converted."""
        spot = self._target(operand, resources)
        if spot:
            self.used.add(spot)
            tint = self.targets[spot]
            return lambda values: tint.cmyk(values[0] if values else 1.0)
        space = _space_of(operand, resources)
        mix = self.mix(space, where) if space is not None else None
        if mix:
            return mix.cmyk
        return None

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
                convert = self._converter(ops[0], resources, where)
                if op == "cs":
                    fill = convert
                else:
                    stroke = convert
                if convert:
                    # A Separation or DeviceN colour starts at full tint in every ink.
                    values = [_num(v) for v in convert([1.0] * 32)]
                    out += [_ins(op, pikepdf.Name.DeviceCMYK), _ins("sc" if op == "cs" else "SC", *values)]
                    changed = True
                    continue
            elif op in ("sc", "scn", "SC", "SCN"):
                convert = fill if op in ("sc", "scn") else stroke
                if convert:
                    nums = [float(v) for v in ops if not isinstance(v, pikepdf.Name)]
                    values = [_num(v) for v in convert(nums or [1.0])]
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

SAMPLES = 256  # steps in a converted gradient that has to be sampled


def _sampled_function(pdf, fn, to_cmyk, domain):
    """A sampled (type 0) function: position along the gradient -> CMYK, worked out
    at SAMPLES steps through the gradient's own function and the inks' definition."""
    try:
        parts = [Function(f) for f in fn] if isinstance(fn, pikepdf.Array) else [Function(fn)]
    except Exception:
        return None
    d0, d1 = domain
    data = bytearray()
    for i in range(SAMPLES):
        t = d0 + (d1 - d0) * i / (SAMPLES - 1)
        tints = [v for part in parts for v in part([t])]
        for v in to_cmyk(tints):
            data += int(round(min(max(v, 0.0), 1.0) * 65535)).to_bytes(2, "big")
    stream = pikepdf.Stream(pdf, bytes(data))
    stream["/FunctionType"] = 0
    stream["/Domain"] = [d0, d1]
    stream["/Range"] = [0, 1, 0, 1, 0, 1, 0, 1]
    stream["/Size"] = [SAMPLES]
    stream["/BitsPerSample"] = 16
    return pdf.make_indirect(stream)


def _shading_function(fn, tint: Tint):
    """The shading's function with the spot's CMYK definition folded in exactly, or None."""
    if isinstance(fn, pikepdf.Array) or not tint.linear:
        return None
    kind = int(fn.get("/FunctionType", -1)) if not isinstance(fn, pikepdf.Stream) else int(fn.stream_dict.get("/FunctionType", -1))
    if kind == 2:
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


def _convert_shadings(obj, resources_owner_spaces, rewriter, depth=0, pdf=None):
    """A copy of a direct object with spot gradients converted, or None if nothing in it changes."""
    if depth > 12:
        return None
    if isinstance(obj, pikepdf.Dictionary) and "/ShadingType" in obj and "/ColorSpace" in obj:
        space = obj.ColorSpace
        if isinstance(space, pikepdf.Name):
            space = resources_owner_spaces.get(str(space)) if resources_owner_spaces else None
        spot = _spot_name(space)
        mix = rewriter.mix(space, "in a gradient") if _devicen_names(space) else None
        if spot in rewriter.targets or mix:
            names = [spot] if spot in rewriter.targets else [n for n in mix.names if n in rewriter.targets]
            if spot in rewriter.targets:
                rewriter.used.add(spot)
            kind = int(obj.ShadingType)
            fn = obj.get("/Function")
            new_fn = None
            if kind in (2, 3) and fn is not None:
                if spot in rewriter.targets:
                    new_fn = _shading_function(fn, rewriter.targets[spot])
                    tint = rewriter.targets[spot]
                    to_cmyk = lambda tints: tint.cmyk(tints[0])
                else:
                    to_cmyk = mix.cmyk
                if new_fn is None and pdf is not None:
                    domain = [float(v) for v in obj.get("/Domain", [0, 1])]
                    new_fn = _sampled_function(pdf, fn, to_cmyk, domain)
                    if new_fn is not None:
                        rewriter.created.append(new_fn)
            if new_fn is None:
                for n in names:
                    rewriter.blocked[n].add("it is used in a gradient this fix cannot convert")
                return None
            new = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
            new["/ColorSpace"] = pikepdf.Name.DeviceCMYK
            new["/Function"] = new_fn
            return new
    if isinstance(obj, pikepdf.Array):
        changed, items = False, []
        for item in obj:
            new = (_convert_shadings(item, resources_owner_spaces, rewriter, depth + 1, pdf)
                   if isinstance(item, (pikepdf.Array, pikepdf.Dictionary)) and not item.is_indirect else None)
            changed |= new is not None
            items.append(new if new is not None else item)
        return pikepdf.Array(items) if changed else None
    if isinstance(obj, pikepdf.Dictionary):
        changed, copy = False, pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
        for k in obj.keys():
            item = obj[k]
            new = (_convert_shadings(item, resources_owner_spaces, rewriter, depth + 1, pdf)
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
        if len({t.key for t in tints}) > 1:
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
                new = _convert_shadings(target, None, rw, pdf=pdf)
            else:
                spaces = None
                res = target.get("/Resources") if isinstance(target, pikepdf.Dictionary) else None
                if isinstance(res, pikepdf.Dictionary) and isinstance(res.get("/ColorSpace"), pikepdf.Dictionary):
                    spaces = res.ColorSpace
                new = _convert_shadings(target, spaces, rw, pdf=pdf)
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
        rw, _pages, _forms, _patterns, shadings = _plan(pdf, targets)
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
    proposal.plan = {0: {"spots": sorted(convert), "pages": sorted({p for n in convert for p in uses.get(n, ())}),
                         "gradients": bool(shadings)}}
    return proposal


def _reference(src: str, folder: str, spots) -> str:
    """A private copy of the original for the render comparison, with its spot
    gradients already in CMYK, worked out the same way as the conversion.

    MuPDF does not follow the PDF standard for gradients in spot inks: it blends
    between CMYK values along the gradient, where the standard blends the inks'
    tints and converts each point through the inks' definition. For two-ink
    gradients the two differ by up to about 9% at the midpoint. So the render
    comparison is left to check everything else on the page, and the gradients
    are checked on their own (`_check_gradients`), against the standard.
    """
    out = os.path.join(folder, "reference.pdf")
    with pikepdf.open(src) as pdf:
        targets, _ = _targets(pdf, set(spots))
        targets = {n: t for n, t in targets.items() if n in spots}
        rw, _pages, _forms, _patterns, shadings = _plan(pdf, targets)
        for objgen, new in shadings.items():
            old = pdf.get_object(objgen)
            target = old.stream_dict if isinstance(old, pikepdf.Stream) else old
            for key in ("/ColorSpace", "/Function"):
                if key in new:
                    target[key] = new[key]
        pdf.save(out)
    return out


def _check_gradients(src: str, new_path: str, spots) -> None:
    """Each converted gradient equals the inks' definition applied to the original
    gradient's tints, at 101 points along it."""
    with pikepdf.open(src) as before, pikepdf.open(new_path) as after:
        targets, _ = _targets(before, set(spots))
        targets = {n: t for n, t in targets.items() if n in spots}
        rw = Rewriter(targets)
        for obj in before.objects:
            if not isinstance(obj, pikepdf.Dictionary) or "/ShadingType" not in obj or "/Function" not in obj:
                continue
            space = obj.ColorSpace
            spot = _spot_name(space)
            mix = rw.mix(space, "in a gradient") if _devicen_names(space) else None
            if spot not in targets and not mix:
                continue
            new = after.get_object(obj.objgen)
            if str(new.get("/ColorSpace")) != "/DeviceCMYK":
                raise AppendError("verification failed: a spot gradient was not converted")
            fn = obj.Function
            parts = [Function(f) for f in fn] if isinstance(fn, pikepdf.Array) else [Function(fn)]
            convert = (lambda tints: targets[spot].cmyk(tints[0])) if spot in targets else mix.cmyk
            converted = Function(new.Function)
            d0, d1 = (float(v) for v in obj.get("/Domain", [0, 1]))
            for i in range(101):
                t = d0 + (d1 - d0) * i / 100
                want = convert([v for part in parts for v in part([t])])
                got = converted([t])
                if max(abs(a - b) for a, b in zip(want, got)) > 0.005:
                    raise AppendError("verification failed: a converted gradient does not match its inks' definition")


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
        folder = tempfile.mkdtemp(prefix="preflight-check-")
        if plan.get("gradients"):
            _check_gradients(src, new_path, plan["spots"])
            reference = _reference(src, folder, plan["spots"])
        else:
            reference = src
        a, b = pymupdf.open(reference), pymupdf.open(new_path)
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
            shutil.rmtree(folder, ignore_errors=True)
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
        for obj in rw.created:
            additions[obj.objgen] = obj
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
