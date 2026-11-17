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
from ..edges import is_non_printing, scan_edges, spine_edge
from ..marks import implied_trim
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


def propose_add_bleed(path: str, options: Options | None = None, method: str = "mirror", **_) -> Proposal:
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
            marks = implied_trim(content)
            if marks is not None and max(abs(a - b) for a, b in zip(marks, t.as_list())) > 1.5:
                proposal.skipped.append((page.number, f"the crop marks point at {size_text(marks)}, not at the trim box; "
                                                      "run trim-from-marks first"))
                continue
            scan = scan_edges(doc, page, content, need_mm)
            skip = spine_edge(page.number) if options.bound else None
            wanted = [n for n in EDGES if scan.edges[n].short_mm > 0 and n != skip]
            if not wanted:
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
                proposal.skipped.append((page.number, "left alone because " + "; ".join(refused)))
                continue
            # Grow the page only where it has less room than the bleed needs;
            # on the edges being filled, room for the overshoot as well.
            depth = need + OVERSHOOT_MM * PT_PER_MM
            room = {"left": t.x0 - m.x0, "bottom": t.y0 - m.y0, "right": m.x1 - t.x1, "top": m.y1 - t.y1}
            grow = {n: max(0.0, (depth if n in edges else need) - room[n]) for n in EDGES}
            media = [m.x0 - grow["left"], m.y0 - grow["bottom"], m.x1 + grow["right"], m.y1 + grow["top"]]
            obj = page.obj.obj
            if any(g > 0.01 for g in grow.values()):
                box_change(proposal, page.number, obj, "MediaBox", media)
                if "/CropBox" in obj:
                    box_change(proposal, page.number, obj, "CropBox", media)
            box_change(proposal, page.number, obj, "TrimBox", t.as_list())
            bleed = [t.x0 - need, t.y0 - need, t.x1 + need, t.y1 + need]
            if page.has_bleed:  # never shrink a bleed box that already gives more
                b = page.bleed
                bleed = [min(bleed[0], b.x0), min(bleed[1], b.y0), max(bleed[2], b.x1), max(bleed[3], b.y1)]
            box_change(proposal, page.number, obj, "BleedBox", bleed)
            long_side = max(mm(t.width), mm(t.height))
            dpi = LARGE_DPI if long_side > LARGE_MM else DPI
            proposal.plan[page.number] = {"edges": edges, "need": need, "depth": depth, "method": method, "dpi": dpi,
                                          "trim": t.as_list(), "old_media": m.as_list(), "new_media": media}
            spaces = ", ".join(f"{n} ({c.upper()})" for n, c in edges.items())
            text = f"Adds {need_mm:g} mm of {method}ed bleed on {spaces} at {dpi} ppi."
            if any(g > 0.01 for g in grow.values()):
                text += " The page grows to make room; the old page size becomes the trim box."
            if notes:
                text += " " + "; ".join(notes) + "."
            proposal.reasons.append((page.number, text))
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
                for colour in sorted(set(plan["edges"].values())):
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
                scan = scan_edges(doc, page, walk_page(page.obj), mm(plan["need"]))
                short = [n for n in plan["edges"] if scan.edges[n].short_mm > LEFT_SHORT_MM]
                if short:
                    raise AppendError(f"verification failed: page {number} still lacks bleed on " + ", ".join(short))
        finally:
            doc.close()
    return check


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
                images, underlay = build_underlay(pdf, mu.load_page(number - 1), plan, cache)
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
                res["/XObject"] = xobjects
                copy["/Resources"] = res
                contents = obj.get("/Contents")
                existing = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
                copy["/Contents"] = pikepdf.Array([underlay] + existing)
                additions[underlay.objgen] = underlay
                changes[obj.objgen] = copy
            append_update(proposal.path, dst, changes, additions, extra_check=_check_trim_unchanged(proposal.path, proposal.plan))
    finally:
        mu.close()
