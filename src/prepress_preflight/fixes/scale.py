"""Scale a page so its trim becomes the job size.

The whole page scales together about the centre of its trim: the artwork, the
page boxes, and the positions of links and other annotations. The original
content stream is not edited; it is wrapped in a scaling instruction.

The shape decides how:

  * within 1% of the job's shape: scaled to the exact size, the small
    distortion reported per axis;
  * further out: refused unless --fill (scale until the trim is covered,
    cropping the overflow) or --fit (scale until the artwork fits, leaving
    white margins) is chosen. It never distorts more than 1% on its own.

Before the new file is put in place, the area inside the new trim is compared
with the original rendered at the matching scale, allowing only for
resampling differences.

Left alone, with the reason: pages with a UserUnit, pages whose crop marks
disagree with the trim box (run trim-from-marks first), and pages that would
grow past 200 inches, which needs a UserUnit this fix does not write.
"""
from __future__ import annotations

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from ..content import walk_page
from ..document import Document
from ..marks import implied_trim
from ..model import Box, mm
from ..edges import _silenced
from .add_bleed import (MARK_LENGTH_MM, REG_CS, SLUG_MARGIN_MM, _check_marks, _old_marks, _registration,
                        marks_content)
from .append import AppendError, append_update
from .base import Proposal, box_change, size_text
from ..document import device_matrix

NAME = "scale"
PT_PER_MM = 72 / 25.4
DISTORT_LIMIT = 0.01  # up to 1% difference in shape is scaled to the exact size
MAX_PT = 14400  # 200 inches: the largest page size without a UserUnit
BOXES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")


def _apply(m, box):
    """Transform a box [x0, y0, x1, y1] by m = (sx, sy, ex, ey)."""
    sx, sy, ex, ey = m
    xs = (box[0] * sx + ex, box[2] * sx + ex)
    ys = (box[1] * sy + ey, box[3] * sy + ey)
    return [min(xs), min(ys), max(xs), max(ys)]


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _intersect(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def propose_scale(path: str, options: Options | None = None, mode: str | None = None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    options = options or Options()
    proposal = Proposal(NAME, path)
    if not options.expected_trim_mm:
        raise ValueError("scale needs the job size: --trim WxH")
    if mode not in (None, "fill", "fit"):
        raise ValueError("mode must be fill or fit")
    doc = Document(path)
    try:
        for page in doc.pages:
            if page.unit != 1.0:
                proposal.skipped.append((page.number, "the page uses a UserUnit; not handled by this fix yet"))
                continue
            t = page.trim
            content = walk_page(page.obj)
            found = implied_trim(content)
            if found is not None and max(abs(a - b) for a, b in zip(found, t.as_list())) > 1.5:
                proposal.skipped.append((page.number, f"the crop marks point at {size_text(found)}, not at the trim box; "
                                                      "run trim-from-marks first"))
                continue
            w, h = (v * PT_PER_MM for v in options.expected_trim_mm)
            if (w > h) != (t.width > t.height) and abs(t.width - t.height) > 1:
                w, h = h, w  # the job size, turned to match the page
            sx, sy = w / t.width, h / t.height
            if abs(sx - 1) < 1e-4 and abs(sy - 1) < 1e-4:
                continue
            shape = max(sx, sy) / min(sx, sy) - 1
            how = "exact"
            if shape > DISTORT_LIMIT:
                if mode is None:
                    proposal.skipped.append((page.number, f"its shape differs from the job's by {shape * 100:.1f}%; "
                                                          "choose --fill (crop to cover) or --fit (fit inside)"))
                    continue
                how = mode
                sx = sy = max(sx, sy) if mode == "fill" else min(sx, sy)
            # With --fill or --fit the scaled crop marks would mark the old
            # shape, not the new trim, so they are taken out and redrawn.
            removals, redraw = {}, False
            if how != "exact" and found is not None:
                removals, nested, _ = _old_marks(content, t)
                if nested:
                    proposal.skipped.append((page.number, "its crop marks are inside a placed graphic and would no longer "
                                                          "match the new trim; scale to the same shape, or remove them first"))
                    continue
                redraw = True
            cx, cy = (t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2
            m = (sx, sy, cx - sx * cx, cy - sy * cy)
            new_trim = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
            obj = page.obj.obj
            media = _apply(m, page.media.as_list())
            if how == "fit":
                media = _union(media, new_trim)  # the white margins must be on the page
            if max(media[2] - media[0], media[3] - media[1]) > MAX_PT:
                proposal.skipped.append((page.number, "scaled, the page would be over 200 inches, which needs a "
                                                      "UserUnit; not handled by this fix yet"))
                continue
            # The bleed box: the new trim plus the original bleed, scaled. (Not the
            # old bleed box scaled, which with --fill would take in the overflow.)
            b = page.bleed
            new_bleed = _intersect([new_trim[0] - (t.x0 - b.x0) * sx, new_trim[1] - (t.y0 - b.y0) * sy,
                                    new_trim[2] + (b.x1 - t.x1) * sx, new_trim[3] + (b.y1 - t.y1) * sy], media)
            if redraw:
                # Room on the page for the new marks, outside the bleed box.
                tail = (MARK_LENGTH_MM + SLUG_MARGIN_MM) * PT_PER_MM
                need = [new_bleed[0] - tail, new_bleed[1] - tail, new_bleed[2] + tail, new_bleed[3] + tail]
                media = _union(media, need)
            boxes = {"MediaBox": media, "TrimBox": new_trim}
            if "/CropBox" in obj:
                boxes["CropBox"] = media if (how == "fit" or redraw) else _intersect(_apply(m, [float(v) for v in obj.CropBox]), media)
            if "/BleedBox" in obj or page.has_bleed or redraw:
                boxes["BleedBox"] = new_bleed
            if "/ArtBox" in obj:
                boxes["ArtBox"] = _intersect(_apply(m, [float(v) for v in obj.ArtBox]), media)
            for key in BOXES:
                if key in boxes:
                    box_change(proposal, page.number, obj, key, boxes[key])
            proposal.plan[page.number] = {"m": m, "how": how, "old_trim": t.as_list(), "new_trim": new_trim,
                                          "trim": new_trim, "bleed": new_bleed, "marks": redraw,
                                          "remove": {i: (sorted(p), tr) for i, (p, tr) in removals.items()}}
            text = f"Scales to {size_text(new_trim)}: "
            if how == "exact":
                text += f"width {sx * 100:.2f}%, height {sy * 100:.2f}%"
                if abs(sx - sy) > 1e-4:
                    text += f" (a {shape * 100:.2f}% change of shape)"
            else:
                text += f"{sx * 100:.2f}% both ways"
                scaled = _apply(m, t.as_list())
                over = {"left": new_trim[0] - scaled[0], "bottom": new_trim[1] - scaled[1],
                        "right": scaled[2] - new_trim[2], "top": scaled[3] - new_trim[3]}
                if how == "fill":
                    lost = ", ".join(f"{n} {mm(v):.1f} mm" for n, v in over.items() if v > 0.01)
                    text += f"; cropped past the new trim: {lost}"
                else:
                    gaps = ", ".join(f"{n} {mm(-v):.1f} mm" for n, v in over.items() if v < -0.01)
                    text += f"; white margins inside the new trim: {gaps}"
            if max(sx, sy) > 1.01:
                text += f". Images lose resolution in proportion ({100 / max(sx, sy):.0f}% of their ppi)"
            elif min(sx, sy) < 0.99:
                text += f". Lines and type get thinner ({min(sx, sy) * 100:.0f}% of their weight)"
            if redraw:
                text += ". Its crop marks are replaced with marks for the new trim"
            proposal.reasons.append((page.number, text + "."))
    finally:
        doc.close()
    return proposal


# ---- writing -----------------------------------------------------------

def _scale_annotation(annot, m):
    copy = pikepdf.Dictionary({k: annot[k] for k in annot.keys()})
    if "/Rect" in copy:
        copy["/Rect"] = pikepdf.Array(_apply(m, [float(v) for v in copy.Rect]))
    if "/QuadPoints" in copy:
        q = [float(v) for v in copy.QuadPoints]
        sx, sy, ex, ey = m
        copy["/QuadPoints"] = pikepdf.Array([q[i] * sx + ex if i % 2 == 0 else q[i] * sy + ey for i in range(len(q))])
    return copy


def _wrap(m) -> bytes:
    """The instruction that scales the original content."""
    sx, sy, ex, ey = m
    return f"q {sx:.8f} 0 0 {sy:.8f} {ex:.6f} {ey:.6f} cm\n".encode()


def _check_scaled(src, plans):
    """Refuse the new file unless the new trim area matches the original at the matching scale."""
    def check(new_path):
        before, after = pymupdf.open(src), pymupdf.open(new_path)
        try:
            for number, plan in plans.items():
                sx, sy, ex, ey = plan["m"]
                # Compare where there is artwork in both: the new trim, less any
                # white margins that --fit leaves.
                new = _intersect(plan["new_trim"], _apply(plan["m"], plan["old_trim"]))
                old_area = [(new[0] - ex) / sx, (new[1] - ey) / sy, (new[2] - ex) / sx, (new[3] - ey) / sy]
                pa, pb = before.load_page(number - 1), after.load_page(number - 1)
                quarter = pa.rotation in (90, 270)
                w_in, h_in = (new[2] - new[0]) / 72, (new[3] - new[1]) / 72
                zoom = max(18, min(100, (3_000_000 / max(w_in * h_in, 1e-6)) ** 0.5)) / 72
                # The original, drawn at the scale the fix applies (axes swap on a quarter-turned page).
                za = pymupdf.Matrix(zoom * (sy if quarter else sx), zoom * (sx if quarter else sy))
                clip_a = (pymupdf.Rect(old_area) * device_matrix(pa)).normalize()
                clip_b = (pymupdf.Rect(new) * device_matrix(pb)).normalize()
                a = pa.get_pixmap(matrix=za, clip=clip_a, colorspace=pymupdf.csCMYK, alpha=False, annots=False)
                b = pb.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip_b, colorspace=pymupdf.csCMYK,
                                  alpha=False, annots=False)
                hgt, wid = min(a.height, b.height), min(a.width, b.width)
                if abs(a.height - b.height) > 2 or abs(a.width - b.width) > 2:
                    raise AppendError(f"verification failed: page {number} did not scale to the size intended")
                A = np.frombuffer(a.samples, np.uint8).reshape(a.height, a.width, 4)[1:hgt - 1, 1:wid - 1].astype(int)
                B = np.frombuffer(b.samples, np.uint8).reshape(b.height, b.width, 4)[1:hgt - 1, 1:wid - 1].astype(int)
                diff = np.abs(A - B).max(axis=2)
                # Resampling moves edges by under a pixel: allow small differences, and
                # a few larger ones along edges, but not a change of artwork.
                if diff.mean() > 4 or (diff > 96).mean() > 0.02:
                    raise AppendError(f"verification failed: page {number} does not match the original at the new scale")
        finally:
            before.close()
            after.close()
        if any(plan["marks"] for plan in plans.values()):
            with pikepdf.open(new_path) as pdf:
                for number, plan in plans.items():
                    if plan["marks"]:
                        _check_marks(number, walk_page(pdf.pages[number - 1]), plan)
    return check


def write_scale(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    boxes: dict[int, dict] = {}
    for c in proposal.changes:
        boxes.setdefault(c.page, {})[c.box] = c.after
    with pikepdf.open(proposal.path) as pdf:
        changes, additions = {}, {}
        for number, plan in proposal.plan.items():
            obj = pdf.pages[number - 1].obj
            sx, sy, ex, ey = plan["m"]
            copy = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
            for key, value in boxes.get(number, {}).items():
                copy["/" + key] = pikepdf.Array(value)
            contents = obj.get("/Contents")
            existing = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
            if plan["remove"]:
                removals = {int(i): (set(p), tr) for i, (p, tr) in plan["remove"].items()}
                kept = _silenced(list(pikepdf.parse_content_stream(obj)), removals)
                rewritten = pdf.make_indirect(pikepdf.Stream(pdf, pikepdf.unparse_content_stream(kept)))
                additions[rewritten.objgen] = rewritten
                existing = [rewritten]
            pre = pdf.make_indirect(pikepdf.Stream(pdf, _wrap(plan["m"])))
            post = pdf.make_indirect(pikepdf.Stream(pdf, b"Q\n" + (marks_content(plan) if plan["marks"] else b"")))
            if plan["marks"]:
                res = pdf.pages[number - 1].resources
                res = pikepdf.Dictionary({k: res[k] for k in res.keys()})
                spaces = res.get("/ColorSpace")
                spaces = pikepdf.Dictionary({k: spaces[k] for k in spaces.keys()}) if spaces is not None else pikepdf.Dictionary()
                spaces["/" + REG_CS] = _registration(pdf)
                res["/ColorSpace"] = spaces
                copy["/Resources"] = res
            additions[pre.objgen], additions[post.objgen] = pre, post
            copy["/Contents"] = pikepdf.Array([pre] + existing + [post])
            for annot in obj.get("/Annots", []):
                if annot.is_indirect:
                    changes[annot.objgen] = _scale_annotation(annot, plan["m"])
            if "/Annots" in obj and any(not a.is_indirect for a in obj.Annots):
                copy["/Annots"] = pikepdf.Array([a if a.is_indirect else _scale_annotation(a, plan["m"]) for a in obj.Annots])
            if "/Thumb" in copy:
                del copy["/Thumb"]  # a thumbnail of the old size would be wrong
            changes[obj.objgen] = copy
        append_update(proposal.path, dst, changes, additions, extra_check=_check_scaled(proposal.path, proposal.plan))
