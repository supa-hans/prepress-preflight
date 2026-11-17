from __future__ import annotations

import numpy as np
import pikepdf
import pymupdf

from ..model import FAIL, NOTE, UNCHECKED, WARN
from ..rebuild import FORM_NAME, NORMAL_GS, PageRebuild
from .base import Check

DARKENING = ("Multiply", "Darken", "ColorBurn")
CHANNELS = ("Cyan", "Magenta", "Yellow", "Black")
MAX_PIXELS = 2_000_000
MAX_OBJECTS = 25  # per page; each one costs two renders
MIN_AREA_MM2 = 1.0
INK_FLOOR = 5  # of 255; under 2% counts as no ink
FAIL_AT = 199.0  # solid on solid, allowing for rounding
WARN_OVER = 100.5

_PATH_BUILD = {"m", "l", "c", "v", "y", "h", "re", "W", "W*"}
_PATH_PAINT = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}
_DROPPED = {"sh", "Do", "INLINE IMAGE"}


def _ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


def _closers(instructions):
    """Operators needed to close whatever a cut-off stream left open."""
    depth = marked = 0
    in_text = False
    for i in instructions:
        op = str(i.operator)
        if op == "q":
            depth += 1
        elif op == "Q":
            depth = max(0, depth - 1)
        elif op == "BT":
            in_text = True
        elif op == "ET":
            in_text = False
        elif op in ("BDC", "BMC"):
            marked += 1
        elif op == "EMC":
            marked = max(0, marked - 1)
    return ([_ins("ET")] if in_text else []) + [_ins("Q")] * depth + [_ins("EMC")] * marked


def _silence(instructions, tr):
    """The same instructions, painting nothing: kept for their effect on
    position, clipping and colour. Also returns the text render mode in force
    at the end, given `tr` at the start."""
    out, stack = [], []
    for i in instructions:
        op = str(i.operator)
        if op == "q":
            stack.append(tr)
        elif op == "Q" and stack:
            tr = stack.pop()
        if op in _PATH_PAINT:
            out.append(_ins("n"))
        elif op in _DROPPED:
            continue
        elif op == "Tr":
            tr = int(i.operands[0])
            out.append(_ins("Tr", 3 if tr < 4 else 7))  # invisible, keeping any text clip
        else:
            out.append(i)
    return out, tr


def split(levels, obj):
    """Content for two pictures of the page. Nothing is written to the file.

    backdrop: everything painted before the object that it blends with.
    source:   the object alone, painted as Normal.

    `levels` holds the instructions of the page, then of each form in
    obj.chain. Each result is a list of bytes in the same order: the page's
    content, then the content of a stand-in for each form, cut off at the
    object. Inside an isolated group the object blends only with that group's
    own earlier content, so everything outside it is kept silent.
    """
    chain, depth = obj.chain, len(obj.chain)
    cut = obj.start
    while cut > 0 and str(levels[depth][cut - 1].operator) in _PATH_BUILD:
        cut -= 1
    first_painted = max((k + 1 for k, f in enumerate(chain) if f.isolated), default=0)
    normal = _ins("gs", pikepdf.Name("/" + NORMAL_GS))
    backdrop, source = [], []
    tr = 0
    for k in range(depth + 1):
        before = list(levels[k][:cut if k == depth else chain[k].index])
        silent, tr = _silence(before, tr)
        if k == 0:
            silent = [_ins("Tr", 3)] + silent
        if k >= first_painted:
            back = before
        else:
            back = silent + ([_ins("Tr", tr)] if k + 1 == first_painted else [])
        if k < depth:
            do = _ins("Do", pikepdf.Name(f"/{FORM_NAME}{k + 1}"))
            back = back + [do]
            src = silent + [normal, do]
        else:
            src = silent + [_ins("Tr", tr), normal]
            for i in levels[k][cut:obj.end + 1]:
                src.append(i)
                if str(i.operator) == "gs":
                    src.append(normal)
        backdrop.append(pikepdf.unparse_content_stream(back + _closers(back)))
        source.append(pikepdf.unparse_content_stream(src + _closers(src)))
    return backdrop, source


def _area(mm2: float) -> str:
    if mm2 >= 100_000:
        return f"{mm2 / 1_000_000:.2f} m²"
    if mm2 >= 1000:
        return f"{mm2 / 100:,.0f} cm²"
    return f"{mm2:.0f} mm²"


class BlendOverInkCheck(Check):
    """A darkening blend-mode object lying over the same ink.

    Blended correctly, solid black set to Multiply over solid black is still
    100% black. A RIP or flattener that blends another way can lay the ink down
    twice. A correct render shows nothing wrong, so this check does not look at
    the blended result. For each blend-mode object it renders, in memory:

      backdrop: the page as painted before the object
      source:   the object alone, as if it were Normal

    and reports, per process ink, backdrop + source wherever both have ink.

    Limits: only Multiply, Darken and ColorBurn are measured. Objects in spot
    inks only are not measured, and spot inks in the backdrop count through
    their CMYK equivalents. Artwork painted later on top is not taken into
    account. Objects inside placed or grouped artwork are measured by cutting
    each containing form off at the object; those inside pattern fills or
    knockout groups are reported as not checked.
    """

    id = "blend-over-ink"
    name = "Blend over same ink"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.results: list[dict] = []

    def page(self, page, content):
        pages = [page.number]
        if any(n for mode, n in content.nested_blends.items() if mode in DARKENING):
            self.add(UNCHECKED, "Blend-mode objects inside pattern fills or knockout groups could not be measured", pages)
        objects = [o for o in content.blend_objects if o.mode in DARKENING]
        spot = [o for o in objects if o.spot_only]
        if spot:
            self.add(NOTE, "Blend-mode objects in spot ink only were not measured; a spot ink prints on its own plate", pages)
        objects = [o for o in objects if not o.spot_only]
        if not objects:
            return
        if len(objects) > MAX_OBJECTS:
            self.add(UNCHECKED, f"More than {MAX_OBJECTS} blend-mode objects on a page; only the first {MAX_OBJECTS} were measured", pages)
            objects = objects[:MAX_OBJECTS]
        try:
            streams = {None: list(pikepdf.parse_content_stream(page.obj))}
            rebuild = PageRebuild(self.doc.scratch(), page.number - 1)
        except Exception as exc:  # never report a pass that was not earned
            self.add(UNCHECKED, f"Blend-mode objects could not be measured ({type(exc).__name__})", pages)
            return

        failed = 0
        for obj in objects:
            try:
                r = self._measure(page, rebuild, streams, obj)
            except Exception:
                failed += 1
                continue
            if r is not None:
                r["page"] = page.number
                self.results.append(r)
        if failed:
            self.add(UNCHECKED, "Some blend-mode objects could not be measured", pages)

    def finish(self):
        # One line per severity for the whole document, led by the worst case.
        for severity in (FAIL, WARN, NOTE):
            tier = [r for r in self.results if r["severity"] == severity]
            if not tier:
                continue
            worst = max(tier, key=lambda r: r["peak"])
            pages = sorted({r["page"] for r in tier})
            modes = ", ".join(sorted({r["mode"] for r in tier}))
            area = _area(sum(r["area"] for r in tier))
            what = f"{modes} over the same ink: {worst['ink']} stacks to {worst['peak']:.0f}%"
            if len(pages) > 1:
                what += f" at worst (page {worst['page']}), {area} in all"
            else:
                what += f" across {area}"
            if severity == NOTE:
                message = f"{what}; stays within 100%"
            else:
                message = f"{what}, if the RIP does not blend it correctly"
            self.add(severity, message, pages, [(r["page"], r["bbox"]) for r in tier])
        return self.findings

    # ---- one object ------------------------------------------------------
    def _measure(self, page, rebuild, streams, obj):
        area = page.print_area
        box = area.as_list()
        if obj.bbox is not None:
            box = [max(box[0], obj.bbox[0] - 1), max(box[1], obj.bbox[1] - 1),
                   min(box[2], obj.bbox[2] + 1), min(box[3], obj.bbox[3] + 1)]
            if box[2] <= box[0] or box[3] <= box[1]:
                return None  # outside the printed area
        area_in2 = max((box[2] - box[0]) / 72.0 * (box[3] - box[1]) / 72.0, 1e-6)
        dpi = int(min(72, max(12, (MAX_PIXELS / area_in2) ** 0.5)))
        levels = [streams[None]]
        for frame in obj.chain:
            key = frame.xobj.objgen
            if key not in streams:
                streams[key] = list(pikepdf.parse_content_stream(frame.xobj))
            levels.append(streams[key])
        backdrop_parts, source_parts = split(levels, obj)
        rebuild.prepare(obj.chain)
        backdrop, _, _ = rebuild.render(backdrop_parts, box, dpi)
        source, pix, to_mu = rebuild.render(source_parts, box, dpi)
        if backdrop.shape != source.shape:
            raise ValueError("renders differ in size")

        both = (backdrop > INK_FLOOR) & (source > INK_FLOOR)
        stacked = np.where(both, backdrop + source, 0).max(axis=2) / 2.55
        px_mm2 = (25.4 / dpi * page.unit) ** 2
        tiers = ((FAIL, stacked >= FAIL_AT), (WARN, stacked > WARN_OVER), (NOTE, (stacked > 0) & (stacked <= WARN_OVER)))
        for severity, mask in tiers:
            if float(mask.sum()) * px_mm2 >= MIN_AREA_MM2:
                break
        else:
            return None
        rows, cols = np.where(mask)
        peak_at = np.unravel_index(np.argmax(np.where(mask, stacked, 0)), stacked.shape)
        channel = int(np.argmax(np.where(both[peak_at], backdrop[peak_at] + source[peak_at], 0)))
        scale = 72.0 / dpi
        hit = pymupdf.Rect((pix.x + cols.min()) * scale, (pix.y + rows.min()) * scale,
                           (pix.x + cols.max() + 1) * scale, (pix.y + rows.max() + 1) * scale)
        hit = (hit * ~to_mu).normalize()
        return {"severity": severity, "mode": obj.mode, "peak": float(stacked[peak_at]), "ink": CHANNELS[channel],
                "area": float(mask.sum()) * px_mm2, "bbox": [hit.x0, hit.y0, hit.x1, hit.y1]}
