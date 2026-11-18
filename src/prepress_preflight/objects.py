"""What is painted at a point on a page: the objects under it, topmost first.

Built on the same content walker the checks use, so an object here is exactly
what the checks measured. Positions on the rendered page (points from its
top-left corner, after rotation) are turned back into PDF coordinates with
`device_matrix`.

Limits, as for the walker: an object counts where it is drawn, even where a
clipping path hides it, and text positions are approximate (one em per run).
"""
from __future__ import annotations

import pymupdf

from .content import walk_page
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
    for e in reversed(content.events):  # painted last = on top
        for b in e.boxes():
            if b[0] - tolerance <= pt.x <= b[2] + tolerance and b[1] - tolerance <= pt.y <= b[3] + tolerance:
                hits.append(describe(e, to_view))
                break
        if len(hits) >= limit:
            break
    return hits
