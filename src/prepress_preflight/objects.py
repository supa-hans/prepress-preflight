"""What is painted at a point on a page: the objects under it, topmost first.

Built on the same content walker the checks use, so an object here is exactly
what the checks measured. Positions on the rendered page (points from its
top-left corner, after rotation) are turned back into PDF coordinates with
`device_matrix`.

Limits, as for the walker: an object counts where it is drawn, even where a
clipping path hides it, and text positions are approximate (one em per run).
"""
from __future__ import annotations

import math

import pikepdf
import pymupdf

from .content import PageWalker, mat_mul, walk_page
from .document import device_matrix

KINDS = {
    ("path", "fill"): "Filled shape", ("path", "stroke"): "Line", ("text", "fill"): "Text",
    ("text", "stroke"): "Outlined text", ("image", "fill"): "Image", ("imagemask", "fill"): "Image mask",
    ("shading", "fill"): "Gradient", ("shading", "stroke"): "Gradient",
}


def _pct(v: float) -> str:
    return f"{v * 100:.0f}"


def describe_colour(cs, colour) -> tuple[str, float | None]:
    """A colour as a pre-press operator would write it, and its total ink (CMYK only)."""
    if cs.kind == "cmyk" and colour and len(colour) == 4:
        c, m, y, k = colour
        return f"C{_pct(c)} M{_pct(m)} Y{_pct(y)} K{_pct(k)}", round(sum(colour) * 100, 1)
    if cs.kind == "gray" and colour:
        return f"Grey, {_pct(1 - colour[0])}% black", round((1 - colour[0]) * 100, 1)
    if cs.kind == "rgb" and colour and len(colour) == 3:
        return "RGB " + " ".join(str(round(v * 255)) for v in colour), None
    if cs.kind == "sep":
        if cs.is_registration:
            return f"Registration {_pct(colour[0]) if colour else '100'}%", None
        names = [n for n in cs.names if n != "None"]
        values = list(colour or [1.0] * len(names))
        return ", ".join(f"{n} {_pct(v)}%" for n, v in zip(names, values)), None
    if cs.kind == "lab":
        return "Lab", None
    if cs.kind == "pattern":
        return "Pattern", None
    return cs.kind.upper() if cs.kind != "unknown" else "Unknown colour space", None


def fingerprint(e) -> str:
    """A short text that identifies a painted object: kind, colour and place.
    Used to make sure an edit lands on the object the user chose."""
    box = ",".join(f"{v:.1f}" for v in e.bbox) if e.bbox is not None else "-"
    return f"{e.kind}/{e.paint}/{describe_colour(e.cs, e.colour)[0]}/{box}"


def describe(e, to_view) -> dict:
    """One painted object, as plain data."""
    colour, ink = describe_colour(e.cs, e.colour)
    if e.kind in ("image", "shading") and e.colour is None:
        space = {"cmyk": "CMYK", "rgb": "RGB", "gray": "Greyscale", "lab": "Lab"}.get(e.cs.kind)
        if e.cs.kind == "sep":
            space = " + ".join(n for n in e.cs.names if n != "None")
        colour = f"in {space or e.cs.kind}"
    box = None
    if e.bbox is not None:
        r = (pymupdf.Rect(e.bbox) * to_view).normalize()
        box = [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
    out = {"kind": KINDS.get((e.kind, e.paint), e.kind), "colour": colour, "ink": ink,
           "overprint": bool(e.overprint), "blend": e.blend, "view": box}
    if e.width is not None:
        out["width"] = round(e.width, 3)
    if e.font:
        out["font"] = e.font
    if e.px:
        out["pixels"] = list(e.px)
    if e.ppi:
        out["ppi"] = round(e.ppi)
    if e.groups:
        out["inside"] = " > ".join(name if mode == "Normal" else f"{name} ({mode} group)" for name, mode in e.groups)
        modes = [mode for _, mode in e.groups if mode != "Normal"]
        if modes:
            out["group_blend"] = modes[-1]  # the nearest group's: it is what blends this object onto the page
    return out


def objects_at(pdf_page, mu_page, x: float, y: float, tolerance: float = 2.0, content=None, limit: int = 40) -> list[dict]:
    """The objects whose outline covers a point on the rendered page, topmost first.

    pdf_page: the pikepdf page; mu_page: the same page in PyMuPDF; x, y: the point
    in points from the rendered page's top-left corner.
    """
    content = content or walk_page(pdf_page)
    to_view = device_matrix(mu_page)
    pt = pymupdf.Point(x, y) * ~to_view
    hits = []
    for number in range(len(content.events) - 1, -1, -1):  # painted last = on top
        e = content.events[number]
        for b in e.boxes():
            if b[0] - tolerance <= pt.x <= b[2] + tolerance and b[1] - tolerance <= pt.y <= b[3] + tolerance:
                hits.append(describe(e, to_view) | {"id": number})
                break
        if len(hits) >= limit:
            break
    return hits


# ---- full details of one object --------------------------------------------------

MM = 25.4 / 72
DEVICE_NAMES = {"gray": "DeviceGray", "rgb": "DeviceRGB", "cmyk": "DeviceCMYK"}
SHADING_TYPES = {1: "function-based", 2: "linear", 3: "radial", 4: "free-form mesh", 5: "lattice mesh",
                 6: "Coons patch mesh", 7: "tensor patch mesh"}


def icc_description(stream) -> str | None:
    """The profile's own name, from its 'desc' tag (ICC v2 'desc' or v4 'mluc')."""
    try:
        data = stream.read_bytes()
        count = int.from_bytes(data[128:132], "big")
        for i in range(min(count, 100)):
            at = 132 + 12 * i
            sig = data[at:at + 4]
            off = int.from_bytes(data[at + 4:at + 8], "big")
            if sig != b"desc":
                continue
            kind = data[off:off + 4]
            if kind == b"desc":
                n = int.from_bytes(data[off + 8:off + 12], "big")
                return data[off + 12:off + 12 + n].split(b"\0")[0].decode("latin-1").strip() or None
            if kind == b"mluc":
                size = int.from_bytes(data[off + 20:off + 24], "big")
                start = off + int.from_bytes(data[off + 24:off + 28], "big")
                return data[start:start + size].decode("utf-16-be").strip("\0 ") or None
    except Exception:
        return None
    return None


def _resolve_space(obj, resources):
    if isinstance(obj, pikepdf.Name) and str(obj) not in ("/DeviceGray", "/DeviceRGB", "/DeviceCMYK", "/Pattern"):
        spaces = resources.get("/ColorSpace") if resources is not None else None
        if spaces is not None and str(obj) in spaces:
            return spaces[str(obj)], str(obj)[1:]
    return obj, None


def _cmyk_like(space) -> bool:
    if isinstance(space, pikepdf.Name):
        return str(space) == "/DeviceCMYK"
    return isinstance(space, pikepdf.Array) and len(space) == 2 and str(space[0]) == "/ICCBased" and int(space[1].get("/N", 0)) == 4


def space_definition(obj, resources, values=None) -> dict:
    """How a colour space is defined in the file, in plain terms."""
    from .functions import Function

    space, label = _resolve_space(obj, resources)
    out = {"resource": label} if label else {}
    if isinstance(space, pikepdf.Name):
        out["space"] = str(space)[1:]
        return out
    if not isinstance(space, pikepdf.Array) or not len(space):
        out["space"] = "could not be read"
        return out
    head = str(space[0])[1:]
    out["space"] = head
    if head == "ICCBased":
        out["channels"] = int(space[1].get("/N", 0))
        out["profile"] = icc_description(space[1]) or "(no name in the profile)"
    elif head == "Indexed":
        out["base"] = space_definition(space[1], resources)
        out["entries"] = int(space[2]) + 1
    elif head in ("Separation", "DeviceN"):
        names = [str(space[1])[1:]] if head == "Separation" else [str(n)[1:] for n in space[1]]
        out["inks"] = names
        alt = space[2]
        out["alternate"] = space_definition(alt, resources)["space"] if not isinstance(alt, pikepdf.Name) else str(alt)[1:]
        try:
            fn = Function(space[3])
            out["tint_transform"] = {0: "sampled", 2: "exponential", 3: "stitching", 4: "PostScript calculator"}[fn.kind]
            if _cmyk_like(alt):
                tints = list(values) if values else [1.0] * len(names)
                c, m, y, k = fn(tints)[:4]
                out["cmyk_equivalent"] = f"C{_pct(c)} M{_pct(m)} Y{_pct(y)} K{_pct(k)}"
                out["equivalent_at"] = ", ".join(f"{n} {_pct(t)}%" for n, t in zip(names, tints))
        except Exception:
            out["tint_transform"] = "could not be read"
    elif head == "Lab":
        wp = space[1].get("/WhitePoint")
        out["white_point"] = [round(float(v), 4) for v in wp] if wp is not None else None
    elif head == "Pattern" and len(space) > 1:
        out["base"] = space_definition(space[1], resources)
    return out


def _font_details(font, size) -> dict:
    out = {"name": describe_font_name(font), "type": str(font.get("/Subtype", "/?"))[1:], "size": round(size, 2)}
    fonts = [font]
    if "/DescendantFonts" in font:
        fonts = list(font.DescendantFonts)
    embedded = None
    for f in fonts:
        d = f.get("/FontDescriptor")
        if d is not None:
            embedded = any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    out["embedded"] = embedded if out["type"] != "Type3" else True
    return out


def describe_font_name(font) -> str:
    name = str(font.get("/BaseFont", "/(unnamed)"))[1:]
    return name[7:] if len(name) > 7 and name[6] == "+" else name


def _text_sample(operands, font) -> str | None:
    """The characters shown, when the font is a simple one and they are plain text."""
    if font is None or str(font.get("/Subtype", "")) == "/Type0":
        return None
    arg = operands[-1]
    parts = [arg] if isinstance(arg, pikepdf.String) else [a for a in arg if isinstance(a, pikepdf.String)] if isinstance(arg, pikepdf.Array) else []
    raw = b"".join(bytes(p) for p in parts)
    text = raw.decode("latin-1")
    if not text or any(ord(ch) < 32 for ch in text):
        return None
    return text if len(text) <= 80 else text[:80] + "…"


PAINT_OPS = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*", "n", "Do", "sh", "Tj", "TJ", "'", '"', "BI", "INLINE IMAGE", "ET", "BT"}


def _excerpt(stream, index, before=10) -> list[str]:
    """The instructions that build and paint the object, back to the previous painted object."""
    try:
        instructions = pikepdf.parse_content_stream(stream)
    except Exception:
        return []
    start = index
    while start > 0 and index - start < before and str(instructions[start - 1].operator) not in PAINT_OPS:
        start -= 1
    lines = []
    for ins in instructions[start:index + 1]:
        if str(ins.operator) == "INLINE IMAGE":
            lines.append("BI … ID … EI  (inline image)")
            continue
        text = pikepdf.unparse_content_stream([ins]).decode("latin-1").strip()
        lines.append(text if len(text) <= 120 else text[:117] + "…")
    return lines


def object_details(pdf_page, mu_page, number: int, trim=None) -> dict:
    """Everything known about one painted object (its number in the walk order).

    trim: (box on the rendered page, inferred) to measure the position from, when
    the report has one (it may be inferred from crop marks); else the file's trim box.
    """
    walker = PageWalker(pdf_page, detail=number)
    walker.run()
    snap = walker.snapshot
    if snap is None:
        raise ValueError("No such object on this page")
    e, st = snap.event, snap.state
    to_view = device_matrix(mu_page)
    out = describe(e, to_view)
    out["id"] = number
    out["fingerprint"] = fingerprint(e)
    out["overprint_now"] = st.op_fill if e.paint == "fill" else st.op_stroke
    out["editable"] = e.loc is not None
    out["recolourable"] = e.kind in ("path", "text", "imagemask")  # images and gradients carry their own colours
    try:
        ins = pikepdf.parse_content_stream(snap.stream)[snap.index]
        op, operands = str(ins.operator), ins.operands
    except Exception:
        op, operands = "", []

    # where it is, in millimetres from the trim's top-left corner, as the page is shown
    if out["view"]:
        x0, y0, x1, y1 = out["view"]
        place = {"width_mm": round((x1 - x0) * MM, 2), "height_mm": round((y1 - y0) * MM, 2)}
        if trim is not None:
            t, place["trim"] = pymupdf.Rect(trim[0]), "inferred from the crop marks" if trim[1] else "trim box"
        else:
            box = pdf_page.obj.get("/TrimBox")
            place["trim"] = "trim box" if box is not None else "page edge (no trim box)"
            box = box if box is not None else pdf_page.obj.get("/MediaBox")
            t = (pymupdf.Rect([float(v) for v in box]) * to_view).normalize()
        place["from_trim_left_mm"] = round((x0 - t.x0) * MM, 2)
        place["from_trim_top_mm"] = round((y0 - t.y0) * MM, 2)
        out["place"] = place

    # how its colour is defined
    resources = snap.resources
    if e.kind in ("path", "text") and e.cs.kind != "pattern":
        written = st.fdef if e.paint == "fill" else st.sdef
        if written is None:
            out["space"] = {"space": DEVICE_NAMES.get(e.cs.kind, e.cs.kind)}
        else:
            out["space"] = space_definition(written[0], written[1], e.colour)
    elif e.kind == "shading":
        try:
            if op == "sh":
                sh = resources["/Shading"][str(operands[0])]
            else:
                sh = resources["/Pattern"][str(st.fpat if e.paint == "fill" else st.spat)]["/Shading"]
            out["gradient"] = SHADING_TYPES.get(int(sh.get("/ShadingType", 0)), "unknown type")
            out["space"] = space_definition(sh.get("/ColorSpace"), resources)
        except Exception:
            out["space"] = {"space": "could not be read"}
    elif e.kind in ("image", "imagemask") and op == "Do":
        img = resources["/XObject"][str(operands[0])]
        filters = img.get("/Filter")
        filters = [str(f)[1:] for f in filters] if isinstance(filters, pikepdf.Array) else [str(filters)[1:]] if filters is not None else []
        out["image"] = {"name": str(operands[0])[1:], "bits": int(img.get("/BitsPerComponent", 1)),
                        "compression": ", ".join(filters) or "none",
                        "soft_mask": "/SMask" in img, "mask": "/Mask" in img,
                        "interpolate": bool(img.get("/Interpolate", False))}
        if e.kind == "image":
            out["space"] = space_definition(img.get("/ColorSpace"), resources)

    if e.kind == "text" and st.font is not None:
        a, b, c, d = mat_mul(st.tm, st.ctm)[:4]
        size = st.fsize * math.sqrt(abs(a * d - b * c))
        out["text"] = _font_details(st.font, size)
        sample = _text_sample(operands, st.font) if operands else None
        if sample:
            out["text"]["sample"] = sample

    # graphics state
    out["state"] = {"overprint_fill": st.op_fill, "overprint_stroke": st.op_stroke, "overprint_mode": st.opm,
                    "blend": st.bm, "opacity_fill": st.ca, "opacity_stroke": st.CA, "soft_mask": st.smask,
                    "extgstates": list(st.gs_names)}

    # where it comes from
    out["source"] = {"stream": " > ".join(f.name for f in snap.chain) or "page", "instruction": snap.index + 1,
                     "excerpt": _excerpt(snap.stream, snap.index)}
    return out
