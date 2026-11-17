"""Add bleed to artwork that stops at the trim.

For each edge where artwork reaches the trim without enough bleed, the 3 mm
just inside the trim is rendered and reflected outward ("mirror"), or its
outermost row of pixels repeated ("stretch"). The result is placed as an
image underneath the original artwork, clipped so that it can only paint
outside the trim. The page grows where it has no room, the old trim is
written as the TrimBox, and the BleedBox is set to the trim plus the bleed.

The original content stream is not changed: the new drawing is added as a
separate content stream in front of it. Before the new file is put in place,
the area inside the trim is rendered from both files and must match pixel for
pixel, and the Bleed artwork check must pass on every edge that was filled.

Left alone, with the reason given: pages using a UserUnit, pages whose crop
marks disagree with the trim box (use trim-from-marks first), and edges whose
artwork uses spot colours, since a CMYK copy of a spot would change colour at
the cut.
"""
from __future__ import annotations

import hashlib
import zlib

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from ..content import walk_page
from ..document import Document
from ..edges import _silenced, is_non_printing, scan_edges, spine_edge
from ..marks import _lines, implied_trim
from ..model import Box, mm
from .append import AppendError, append_update
from .base import Proposal, box_change, size_text

NAME = "add-bleed"
PT_PER_MM = 72 / 25.4
LARGE_MM = 1000  # pages longer than this are rendered at the lower resolution
DPI, LARGE_DPI = 300, 150
# The made bleed runs this far past the bleed box, as a designer's would, so
# that it fills the bleed box completely whatever the pixel rounding.
OVERSHOOT_MM = 1.0
EDGE_INSET_PX = 2  # take the edge colour this many pixels inside the trim
# Artwork this close to the trim has its colour carried across the white
# sliver into the bleed. A little more than the 0.5 mm the Bleed artwork check
# counts as "reaching the trim", since that check reads anti-aliased pixels.
CARRY_MM = 1.0
# After filling, a filled edge may still read short for this length in all:
# spots where the artwork stops a hair short of the trim, leaving a white
# sliver inside it that no bleed can fix. They stay in the preflight report.
LEFT_SHORT_MM = 2.0
# Trim marks, as Illustrator draws them: a thin registration-colour line on a
# wider white keyline. They start at the bleed box and point at the trim.
MARK_LENGTH_MM = 5.0
MARK_WEIGHT_PT, KEYLINE_WEIGHT_PT = 0.25, 1.25
SLUG_MARGIN_MM = 1.0  # page room beyond the end of the marks
EDGES = ("left", "bottom", "right", "top")


def _edge_strip(t: Box, name: str, depth: float, outside: bool) -> list:
    """The strip along one edge of the trim, `depth` wide, inside or outside it."""
    if name == "left":
        return [t.x0 - depth, t.y0, t.x0, t.y1] if outside else [t.x0, t.y0, t.x0 + depth, t.y1]
    if name == "right":
        return [t.x1, t.y0, t.x1 + depth, t.y1] if outside else [t.x1 - depth, t.y0, t.x1, t.y1]
    if name == "bottom":
        return [t.x0, t.y0 - depth, t.x1, t.y0] if outside else [t.x0, t.y0, t.x1, t.y0 + depth]
    return [t.x0, t.y1, t.x1, t.y1 + depth] if outside else [t.x0, t.y1 - depth, t.x1, t.y1]


def _overlaps(a, b) -> bool:
    return a[2] > b[0] and a[0] < b[2] and a[3] > b[1] and a[1] < b[3]


def _edge_colour(content, strip):
    """Colour space for an edge's bleed: that of the artwork touching it.

    Returns ("cmyk" | "rgb" | None, note). None means the edge cannot be done.
    """
    kinds = {"cmyk": 0, "rgb": 0}
    for e in content.events:
        if e.bbox is None or not _overlaps(e.bbox, strip):
            continue
        if e.cs.kind == "sep":
            names = [n for n in e.cs.names if n != "None"]
            if e.cs.is_registration or all(is_non_printing(n) for n in names):
                continue
            if e.cs.spot_names:
                return None, "its artwork uses a spot colour (" + ", ".join(e.cs.spot_names) + ")"
            kinds["cmyk"] += 1
        elif e.cs.kind in ("cmyk", "gray"):
            kinds["cmyk"] += 1
        elif e.cs.kind == "rgb":
            kinds["rgb"] += 1
        elif e.cs.kind in ("lab", "unknown"):
            return None, f"its artwork uses a colour space this fix does not handle ({e.cs.kind})"
    if kinds["rgb"] and kinds["cmyk"]:
        chosen = "rgb" if kinds["rgb"] > kinds["cmyk"] else "cmyk"
        return chosen, f"mixed RGB and CMYK artwork; bleed made in {chosen.upper()}"
    return ("rgb" if kinds["rgb"] else "cmyk"), ""


def _old_marks(content, t: Box):
    """Existing crop-mark strokes to remove before new marks are drawn.

    Only registration-colour strokes whose every part is a thin straight line
    lying along a trim edge, outside the trim. Returns (removals in the page's
    own content as {position: ({"stroke"}, text render mode)}, number found
    inside placed graphics, the outline of every mark line found).
    """
    def is_mark(b):
        x0, y0, x1, y1 = b
        if y1 - y0 <= 0.6 and x1 - x0 > 2:  # horizontal
            y = (y0 + y1) / 2
            return min(abs(y - t.y0), abs(y - t.y1)) <= 0.5 and (x1 <= t.x0 + 0.5 or x0 >= t.x1 - 0.5)
        if x1 - x0 <= 0.6 and y1 - y0 > 2:  # vertical
            x = (x0 + x1) / 2
            return min(abs(x - t.x0), abs(x - t.x1)) <= 0.5 and (y1 <= t.y0 + 0.5 or y0 >= t.y1 - 0.5)
        return False

    removals, nested, boxes = {}, 0, []
    for e in content.events:
        if e.paint != "stroke" or not e.cs.is_registration or e.bbox is None or e.loc is None:
            continue
        if not all(is_mark(b) for b in e.boxes()):
            continue
        boxes.extend(e.boxes())
        names, idx, tr = e.loc
        if names:
            nested += 1
            continue
        removals.setdefault(idx, ({"stroke"}, tr))
    return removals, nested, boxes


def propose_add_marks(path: str, options: Options | None = None, clip: bool = False, **_) -> Proposal:
    """Trim marks only, with no bleed made: for files that already bleed.

    The same marks and checks as add-bleed --marks. Edges that lack bleed are
    left as they are (the Bleed artwork check still reports them).
    """
    proposal = propose_add_bleed(path, options, clip=clip, marks=True, fill=False)
    proposal.fix = "add-marks"
    return proposal


def propose_add_bleed(path: str, options: Options | None = None, method: str = "mirror",
                      clip: bool = False, marks: bool = False, fill: bool = True, **_) -> Proposal:
    """What the fix would change. Reads only."""
    options = options or Options()
    if method not in ("mirror", "stretch"):
        raise ValueError("method must be mirror or stretch")
    proposal = Proposal(NAME, path)
    need_mm = options.min_bleed_mm
    need = need_mm * PT_PER_MM
    doc = Document(path)
    try:
        for page in doc.pages:
            if page.unit != 1.0:
                proposal.skipped.append((page.number, "the page uses a UserUnit; not handled by this fix yet"))
                continue
            content = walk_page(page.obj)
            t, m = page.trim, page.media
            found = implied_trim(content)
            if found is not None and max(abs(a - b) for a, b in zip(found, t.as_list())) > 1.5:
                proposal.skipped.append((page.number, f"the crop marks point at {size_text(found)}, not at the trim box; "
                                                      "run trim-from-marks first"))
                continue
            scan = scan_edges(doc, page, content, need_mm)
            skip = spine_edge(page.number) if options.bound else None
            wanted = [n for n in EDGES if scan.edges[n].short_mm > 0 and n != skip] if fill else []
            if not wanted and not (clip or marks):
                continue
            edges, notes, refused = {}, [], []
            for name in wanted:
                colour, note = _edge_colour(content, _edge_strip(t, name, need, outside=False))
                if colour is None:
                    refused.append(f"{name}: {note}")
                    continue
                edges[name] = colour
                if note:
                    notes.append(f"{name}: {note}")
            if refused:
                proposal.skipped.append((page.number, "because " + "; ".join(refused)))
                continue
            depth = need + OVERSHOOT_MM * PT_PER_MM
            bleed = [t.x0 - need, t.y0 - need, t.x1 + need, t.y1 + need]
            if page.has_bleed:  # never shrink a bleed box that already gives more
                b = page.bleed
                bleed = [min(bleed[0], b.x0), min(bleed[1], b.y0), max(bleed[2], b.x1), max(bleed[3], b.y1)]
            removals, page_marks, kept = {}, marks, False
            if marks:
                removals, nested, mark_boxes = _old_marks(content, t)
                if found is not None and mark_boxes and not clip:
                    # Marks already there and pointing at the trim: keep them
                    # rather than replace like with like. (Applications put marks
                    # about 2 mm from the trim, so they usually reach into a 3 mm
                    # bleed; that is normal.) With a clip they would be cut down
                    # to stubs, so then they are replaced.
                    removals, page_marks, kept = {}, False, True
                elif nested:
                    proposal.skipped.append((page.number, "because its existing crop marks are inside a placed graphic, "
                                                          "so they cannot be replaced, and clipping would cut them off"))
                    continue
            if not edges and not clip and not page_marks:
                if kept:
                    proposal.skipped.append((page.number, "it already has trim marks that point at the trim"))
                continue
            # Grow the page only where it has less room than needed: the bleed,
            # the overshoot on edges being filled, and the marks if asked for.
            gap = {"left": t.x0 - bleed[0], "bottom": t.y0 - bleed[1], "right": bleed[2] - t.x1, "top": bleed[3] - t.y1}
            tail = (MARK_LENGTH_MM + SLUG_MARGIN_MM) * PT_PER_MM
            room = {"left": t.x0 - m.x0, "bottom": t.y0 - m.y0, "right": m.x1 - t.x1, "top": m.y1 - t.y1}
            extent = {n: max(depth if n in edges else need, gap[n] + tail if page_marks else 0.0) for n in EDGES}
            grow = {n: max(0.0, extent[n] - room[n]) for n in EDGES}
            media = [m.x0 - grow["left"], m.y0 - grow["bottom"], m.x1 + grow["right"], m.y1 + grow["top"]]
            obj = page.obj.obj
            if any(g > 0.01 for g in grow.values()):
                box_change(proposal, page.number, obj, "MediaBox", media)
                if "/CropBox" in obj:
                    box_change(proposal, page.number, obj, "CropBox", media)
            box_change(proposal, page.number, obj, "TrimBox", t.as_list())
            bleed = [max(bleed[0], media[0]), max(bleed[1], media[1]), min(bleed[2], media[2]), min(bleed[3], media[3])]
            box_change(proposal, page.number, obj, "BleedBox", bleed)
            long_side = max(mm(t.width), mm(t.height))
            dpi = LARGE_DPI if long_side > LARGE_MM else DPI
            proposal.plan[page.number] = {"edges": edges, "need": need, "depth": depth, "method": method, "dpi": dpi,
                                          "trim": t.as_list(), "bleed": bleed, "old_media": m.as_list(),
                                          "new_media": media, "clip": clip, "marks": page_marks,
                                          "remove": {i: (sorted(p), tr) for i, (p, tr) in removals.items()}}
            parts = []
            if edges:
                spaces = ", ".join(f"{n} ({c.upper()})" for n, c in edges.items())
                parts.append(f"Adds {need_mm:g} mm of {method}ed bleed on {spaces} at {dpi} ppi.")
            if clip:
                parts.append("Clips everything on the page to the bleed box.")
            if page_marks:
                parts.append(f"Adds trim marks{' in place of the existing ones' if removals else ''}.")
            elif kept:
                parts.append("Keeps its existing trim marks, which already point at the trim.")
            if any(g > 0.01 for g in grow.values()):
                parts.append("The page grows to make room; the trim is unchanged.")
            if notes:
                parts.append("; ".join(notes) + ".")
            proposal.reasons.append((page.number, " ".join(parts)))
    finally:
        doc.close()
    return proposal


# ---- writing -----------------------------------------------------------

def _render(mu_page, rect_user, colour, dpi):
    """Render a rectangle of the page. Returns pixels (rows top to bottom) and
    the device rectangle they cover, in points."""
    to_dev = mu_page.transformation_matrix * mu_page.rotation_matrix
    clip = (pymupdf.Rect(rect_user) * to_dev).normalize()
    cs = pymupdf.csCMYK if colour == "cmyk" else pymupdf.csRGB
    pix = mu_page.get_pixmap(colorspace=cs, dpi=dpi, clip=clip, alpha=False, annots=False)
    data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
    scale = 72.0 / dpi
    dev = pymupdf.Rect(pix.x * scale, pix.y * scale, (pix.x + pix.width) * scale, (pix.y + pix.height) * scale)
    return data, dev, to_dev


def _reflect(dev: pymupdf.Rect, line_value: float, axis: str) -> pymupdf.Rect:
    """Mirror a device rectangle across a vertical (axis x) or horizontal (axis y) line."""
    if axis == "x":
        return pymupdf.Rect(2 * line_value - dev.x1, dev.y0, 2 * line_value - dev.x0, dev.y1)
    return pymupdf.Rect(dev.x0, 2 * line_value - dev.y1, dev.x1, 2 * line_value - dev.y0)


def _blank(data, colour):
    """Pixels with no ink: 0 in every CMYK channel, or white in RGB."""
    return (data.max(axis=-1) <= 4) if colour == "cmyk" else (data.min(axis=-1) >= 251)


def _make_bleed(data, axis, outward_positive, method, colour, reach_px=6):
    """Turn the pixels just inside the trim into the pixels just outside it.

    axis: the device axis the edge faces along ("x" or "y"). outward_positive:
    whether outward is towards larger device coordinates on that axis.

    stretch repeats the row of pixels at the trim. mirror reflects the strip;
    where artwork at the trim is narrower than the bleed (a thin border or
    rule), the reflection would leave a gap, so there the colour at the trim
    is carried on instead.
    """
    flip_axis = 1 if axis == "x" else 0
    n = data.shape[flip_axis]
    # Work with rows along the edge and columns running from the trim inward.
    towards = data if axis == "x" else np.swapaxes(data, 0, 1)
    towards = towards[:, ::-1] if outward_positive else towards
    # The colour to carry on: the nearest inked pixel within CARRY_MM of the
    # trim. (The pixel right at the trim straddles the cut, and artwork often
    # stops a hair short of it.)
    reach = max(1, min(n, reach_px))
    near = towards[:, :reach]
    inked = ~_blank(near, colour)
    first = np.where(inked.any(axis=1), inked.argmax(axis=1), min(EDGE_INSET_PX, reach - 1))
    edge = near[np.arange(near.shape[0]), first][:, None]
    if axis == "y":
        edge = np.swapaxes(edge, 0, 1)
    stretched = np.repeat(edge, n, axis=flip_axis)
    if method == "stretch":
        return stretched
    mirrored = np.flip(data, axis=flip_axis).copy()
    gap = _blank(mirrored, colour) & ~_blank(stretched, colour)
    mirrored[gap] = stretched[gap]
    return mirrored


def _placement(dev: pymupdf.Rect, to_dev) -> list:
    """The cm matrix that draws an image's unit square over a device rectangle."""
    inv = ~to_dev
    p00 = pymupdf.Point(dev.x0, dev.y1) * inv  # image bottom-left
    p10 = pymupdf.Point(dev.x1, dev.y1) * inv
    p01 = pymupdf.Point(dev.x0, dev.y0) * inv  # image top-left (first row)
    return [p10.x - p00.x, p10.y - p00.y, p01.x - p00.x, p01.y - p00.y, p00.x, p00.y]


def _image(pdf, data, colour, cache):
    """One image XObject. Identical pixels are stored once and shared between pages."""
    key = (colour, data.shape, hashlib.sha256(data.tobytes()).digest())
    if key in cache:
        return cache[key]
    stream = pikepdf.Stream(pdf, zlib.compress(data.tobytes(), 9))
    stream["/Type"] = pikepdf.Name.XObject
    stream["/Subtype"] = pikepdf.Name.Image
    stream["/Width"], stream["/Height"] = int(data.shape[1]), int(data.shape[0])
    stream["/ColorSpace"] = pikepdf.Name.DeviceCMYK if colour == "cmyk" else pikepdf.Name.DeviceRGB
    stream["/BitsPerComponent"] = 8
    stream["/Filter"] = pikepdf.Name.FlateDecode
    cache[key] = pdf.make_indirect(stream)
    return cache[key]


def _pieces(plan):
    """Each area to fill: (user rect to read, which edges it extends past)."""
    t = Box(*plan["trim"])
    depth, edges = plan["depth"], plan["edges"]
    pieces = [(name, _edge_strip(t, name, depth, outside=False), (name,)) for name in edges]
    corners = {("left", "bottom"): [t.x0, t.y0], ("right", "bottom"): [t.x1 - depth, t.y0],
               ("left", "top"): [t.x0, t.y1 - depth], ("right", "top"): [t.x1 - depth, t.y1 - depth]}
    for (a, b), (x, y) in corners.items():
        if a in edges and b in edges:
            pieces.append((f"{a}-{b}", [x, y, x + depth, y + depth], (a, b)))
    return pieces


def build_underlay(pdf, mu_page, plan, cache=None):
    """The new images and the content that draws them, clipped to outside the trim."""
    cache = {} if cache is None else cache
    t = Box(*plan["trim"])
    depth, dpi, method = plan["depth"], plan["dpi"], plan["method"]
    images, ops = {}, []
    for key, rect, past in _pieces(plan):
        colour = "rgb" if any(plan["edges"][e] == "rgb" for e in past) else "cmyk"
        data, dev, to_dev = _render(mu_page, rect, colour, dpi)
        for edge in past:
            # where the trim edge falls in device space, and which way is outward
            a = pymupdf.Point(*{"left": (t.x0, t.y0), "right": (t.x1, t.y0), "bottom": (t.x0, t.y0), "top": (t.x0, t.y1)}[edge]) * to_dev
            b = pymupdf.Point(*{"left": (t.x0 - 1, t.y0), "right": (t.x1 + 1, t.y0), "bottom": (t.x0, t.y0 - 1), "top": (t.x0, t.y1 + 1)}[edge]) * to_dev
            axis = "x" if abs(b.x - a.x) > abs(b.y - a.y) else "y"
            outward_positive = (b.x > a.x) if axis == "x" else (b.y > a.y)
            data = _make_bleed(data, axis, outward_positive, method, colour,
                               reach_px=int(round(CARRY_MM / 25.4 * dpi)))
            dev = _reflect(dev, a.x if axis == "x" else a.y, axis)
        name = f"PreflightBleed{len(images)}"
        images[name] = _image(pdf, np.ascontiguousarray(data), colour, cache)
        cm = " ".join(f"{v:.6f}" for v in _placement(dev, to_dev))
        ops.append(f"q {cm} cm /{name} Do Q")
    outer = [t.x0 - depth, t.y0 - depth, t.x1 + depth, t.y1 + depth]
    ring = (f"{outer[0]:.4f} {outer[1]:.4f} {outer[2] - outer[0]:.4f} {outer[3] - outer[1]:.4f} re "
            f"{t.x0:.4f} {t.y0:.4f} {t.width:.4f} {t.height:.4f} re W* n")
    content = ("q " + ring + "\n" + "\n".join(ops) + "\nQ\n").encode()
    key = ("content", content)
    if key not in cache:
        cache[key] = pdf.make_indirect(pikepdf.Stream(pdf, content))
    return images, cache[key]


REG_CS = "PreflightRegistration"


def _registration(pdf):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[1, 1, 1, 1], N=1)
    return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name.All, pikepdf.Name.DeviceCMYK, tint])


def mark_lines(trim, bleed):
    """The eight trim marks: two per corner, starting at the bleed box and
    lying along the trim edges, pointing at the corner. (x0, y0, x1, y1) each."""
    t, b = Box(*trim), Box(*bleed)
    n = MARK_LENGTH_MM * PT_PER_MM
    lines = []
    for x, outward_x in ((t.x0, -(t.x0 - b.x0)), (t.x1, b.x1 - t.x1)):
        for y, outward_y in ((t.y0, -(t.y0 - b.y0)), (t.y1, b.y1 - t.y1)):
            sx = -1 if outward_x < 0 else 1
            sy = -1 if outward_y < 0 else 1
            hx = x + outward_x  # horizontal mark along the trim edge y, from the bleed box outward
            lines.append((hx, y, hx + sx * n, y))
            vy = y + outward_y
            lines.append((x, vy, x, vy + sy * n))
    return lines


def marks_content(plan) -> bytes:
    path = " ".join(f"{a:.4f} {b:.4f} m {c:.4f} {d:.4f} l" for a, b, c, d in mark_lines(plan["trim"], plan["bleed"]))
    return (f"q 0 J [] 0 d /{REG_CS} CS 0 SCN {KEYLINE_WEIGHT_PT} w {path} S "
            f"1 SCN {MARK_WEIGHT_PT} w {path} S Q\n").encode()


def _check_trim_unchanged(src, plans):
    """Refuse the new file unless the artwork inside each trim renders exactly as before."""
    def check(new_path):
        before, after = pymupdf.open(src), pymupdf.open(new_path)
        try:
            for number, plan in plans.items():
                old = plan["old_media"]
                # In memory only: put the new page back to the old size, so both
                # render on the same pixel grid; the bleed lies outside it.
                x = after.page_xref(number - 1)
                box = "[" + " ".join(f"{v}" for v in old) + "]"
                after.xref_set_key(x, "MediaBox", box)
                if after.xref_get_key(x, "CropBox")[0] != "null":
                    after.xref_set_key(x, "CropBox", box)
                w_in, h_in = (old[2] - old[0]) / 72, (old[3] - old[1]) / 72
                dpi = int(max(36, min(150, (4_000_000 / max(w_in * h_in, 1e-6)) ** 0.5)))
                for colour in sorted(set(plan["edges"].values()) or {"cmyk"}):
                    cs = pymupdf.csCMYK if colour == "cmyk" else pymupdf.csRGB
                    pa, pb = before.load_page(number - 1), after.load_page(number - 1)
                    # Only the trim: on a page that had room, the new bleed lies on the page.
                    clip = (pymupdf.Rect(plan["trim"]) * (pa.transformation_matrix * pa.rotation_matrix)).normalize()
                    a = pa.get_pixmap(colorspace=cs, dpi=dpi, clip=clip, alpha=False, annots=False)
                    b = pb.get_pixmap(colorspace=cs, dpi=dpi, clip=clip, alpha=False, annots=False)
                    if (a.width, a.height) != (b.width, b.height):
                        raise AppendError(f"verification failed: page {number} could not be compared")
                    # Skip the one row of pixels round the outside: it straddles
                    # the cut and so takes in a sliver of the new bleed.
                    shape = (a.height, a.width, a.n)
                    A = np.frombuffer(a.samples, np.uint8).reshape(shape)[1:-1, 1:-1]
                    B = np.frombuffer(b.samples, np.uint8).reshape(shape)[1:-1, 1:-1]
                    if not np.array_equal(A, B):
                        raise AppendError(f"verification failed: page {number} no longer looks the same inside the trim")
        finally:
            before.close()
            after.close()
        doc = Document(new_path)
        try:
            for number, plan in plans.items():
                page = doc.pages[number - 1]
                content = walk_page(page.obj)
                scan = scan_edges(doc, page, content, mm(plan["need"]))
                short = [n for n in plan["edges"] if scan.edges[n].short_mm > LEFT_SHORT_MM]
                if short:
                    raise AppendError(f"verification failed: page {number} still lacks bleed on " + ", ".join(short))
                if plan["marks"]:
                    _check_marks(number, content, plan)
        finally:
            doc.close()
        if any(plan["clip"] for plan in plans.values()):
            _check_clip(new_path, plans)
    return check


def _check_marks(number, content, plan):
    """Exactly one set of eight marks, pointing at the trim."""
    t = Box(*plan["trim"])
    horizontal, vertical = _lines(content)
    on_edges = [h for h in horizontal if min(abs(h[0] - t.y0), abs(h[0] - t.y1)) <= 0.5]
    on_edges += [v for v in vertical if min(abs(v[0] - t.x0), abs(v[0] - t.x1)) <= 0.5]
    box = implied_trim(content)
    if len(on_edges) != 8 or box is None or max(abs(a - b) for a, b in zip(box, plan["trim"])) > 0.5:
        raise AppendError(f"verification failed: page {number} does not have exactly one set of trim marks "
                          f"pointing at the trim ({len(on_edges)} mark lines found)")


def _check_clip(new_path, plans):
    """With the new marks left out, nothing may be painted outside the bleed box."""
    doc = pymupdf.open(new_path)  # in memory only; never saved
    try:
        for number, plan in plans.items():
            if not plan["clip"]:
                continue
            if plan.get("post_xref"):
                doc.update_stream(plan["post_xref"], b"Q\n")
            page = doc.load_page(number - 1)
            to_dev = page.transformation_matrix * page.rotation_matrix
            w, h = page.rect.width / 72, page.rect.height / 72
            dpi = int(max(18, min(72, (4_000_000 / max(w * h, 1e-6)) ** 0.5)))
            pix = page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, alpha=False, annots=False)
            ink = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4).max(axis=2) > 0
            r = (pymupdf.Rect(plan["bleed"]) * to_dev).normalize()
            s = dpi / 72
            x0, y0 = int(np.floor(r.x0 * s)) - 1, int(np.floor(r.y0 * s)) - 1
            x1, y1 = int(np.ceil(r.x1 * s)) + 1, int(np.ceil(r.y1 * s)) + 1
            ink[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = False
            if ink.any():
                raise AppendError(f"verification failed: page {number} has artwork outside the bleed box after clipping")
    finally:
        doc.close()


def write_add_bleed(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    boxes: dict[int, dict] = {}
    for c in proposal.changes:
        boxes.setdefault(c.page, {})[c.box] = c.after
    mu = pymupdf.open(proposal.path)
    try:
        with pikepdf.open(proposal.path) as pdf:
            changes, additions, cache = {}, {}, {}
            for number, plan in proposal.plan.items():
                page = pdf.pages[number - 1]
                obj = page.obj
                images, underlay = (build_underlay(pdf, mu.load_page(number - 1), plan, cache)
                                    if plan["edges"] else ({}, None))
                copy = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
                for key, value in boxes.get(number, {}).items():
                    copy["/" + key] = pikepdf.Array(value)
                # Resources: a copy for this page only, so shared resources are not changed.
                old_res = page.resources
                res = pikepdf.Dictionary({k: old_res[k] for k in old_res.keys()})
                xobj = res.get("/XObject")
                xobjects = pikepdf.Dictionary({k: xobj[k] for k in xobj.keys()}) if xobj is not None else pikepdf.Dictionary()
                for name, image in images.items():
                    xobjects["/" + name] = image
                    additions[image.objgen] = image
                if images:
                    res["/XObject"] = xobjects
                contents = obj.get("/Contents")
                existing = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
                if plan["remove"]:
                    # The old crop marks are taken out of a copy of the page's own
                    # content; the original streams stay in the file, unused.
                    removals = {int(i): (set(p), tr) for i, (p, tr) in plan["remove"].items()}
                    kept = _silenced(list(pikepdf.parse_content_stream(obj)), removals)
                    rewritten = pdf.make_indirect(pikepdf.Stream(pdf, pikepdf.unparse_content_stream(kept)))
                    additions[rewritten.objgen] = rewritten
                    existing = [rewritten]
                parts = ([underlay] if underlay is not None else []) + existing
                if underlay is not None:
                    additions[underlay.objgen] = underlay
                if plan["clip"] or plan["marks"]:
                    # Everything the page had, plus the bleed, inside q ... Q,
                    # clipped to the bleed box if asked; the marks come after.
                    b = plan["bleed"]
                    head = "q\n" + (f"{b[0]:.4f} {b[1]:.4f} {b[2] - b[0]:.4f} {b[3] - b[1]:.4f} re W n\n" if plan["clip"] else "")
                    pre = pdf.make_indirect(pikepdf.Stream(pdf, head.encode()))
                    post = pdf.make_indirect(pikepdf.Stream(pdf, b"Q\n" + (marks_content(plan) if plan["marks"] else b"")))
                    additions[pre.objgen], additions[post.objgen] = pre, post
                    plan["post_xref"] = post.objgen[0]
                    parts = [pre] + parts + [post]
                    if plan["marks"]:
                        spaces = res.get("/ColorSpace")
                        spaces = pikepdf.Dictionary({k: spaces[k] for k in spaces.keys()}) if spaces is not None else pikepdf.Dictionary()
                        spaces["/" + REG_CS] = _registration(pdf)
                        res["/ColorSpace"] = spaces
                copy["/Resources"] = res
                copy["/Contents"] = pikepdf.Array(parts)
                changes[obj.objgen] = copy
            append_update(proposal.path, dst, changes, additions, extra_check=_check_trim_unchanged(proposal.path, proposal.plan))
    finally:
        mu.close()
