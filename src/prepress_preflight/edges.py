"""Measure what is painted along each edge of the trim.

For every edge this answers two questions, working along the edge in small
steps on a rendered strip (so clipping masks and transparency are applied):

  * does artwork reach the trim here? (ink within INSIDE_MM of it)
  * if so, how far does the ink carry on past the trim, unbroken?

Objects painted only in non-printing spot colours (dielines, embellishment
layers, templates) are left out, by silencing them in an in-memory copy of the
page that is never saved.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pikepdf
import pymupdf

from .content import PROCESS_NAMES
from .rebuild import PageRebuild

INSIDE_MM = 0.5  # ink this close to the trim counts as reaching it
INK_FLOOR = 12  # of 255; at the trim, a tint under 5% in every ink counts as white
BLEED_FLOOR = 4  # of 255; past the trim anything over 1.5% counts, so a fade that thins out still bleeds
MIN_RUN_MM = 1.0  # shorter stretches along an edge are ignored
SLANT = 2.0  # sideways leeway per unit of distance out, for artwork with slanted sides
MAX_DPI = 150
MAX_ALONG_PX = 16000
EDGES = ("left", "bottom", "right", "top")
# Spot colours whose names contain one of these never print as ink.
NON_PRINTING = ("dieline", "diecut", "cutcontour", "cutter", "thrucut", "kisscut", "crease", "perf", "skodix",
                "varnish", "spotuv", "foil", "emboss", "nonviewable", "template", "guide", "keyline")

_PATH_PAINT = {"f": ("fill",), "F": ("fill",), "f*": ("fill",), "S": ("stroke",), "s": ("stroke",),
               "B": ("fill", "stroke"), "B*": ("fill", "stroke"), "b": ("fill", "stroke"), "b*": ("fill", "stroke")}
_FILL_ONLY = {"B": ["f"], "B*": ["f*"], "b": ["h", "f"], "b*": ["h", "f*"]}
_STROKE_ONLY = {"B": ["S"], "B*": ["S"], "b": ["s"], "b*": ["s"]}


@dataclass
class EdgeScan:
    name: str
    length_mm: float
    room_mm: float  # how far the page extends past the trim on this edge
    inked_mm: float  # length of the edge where artwork reaches the trim
    short_mm: float  # ... of which the bleed is under the required depth
    least_mm: float | None  # shallowest bleed found along short_mm
    box: list | None  # where the short stretch is, in page points

    @property
    def reaches_trim(self) -> bool:
        return self.inked_mm > 0


@dataclass
class PageEdges:
    edges: dict
    excluded: list  # non-printing spot colours left out of the measurement


def spine_edge(page_number: int) -> str:
    """The bound edge of a page, reading left to right with page 1 on the right."""
    return "left" if page_number % 2 else "right"


def is_non_printing(name: str) -> bool:
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    return any(word in key for word in NON_PRINTING)


def _ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


def _non_printing_events(content):
    """Events painted only in non-printing spot colours, and the names involved."""
    hits, names = [], set()
    for e in content.events:
        if e.cs.kind != "sep":
            continue
        used = [n for n in e.cs.names if n != "None"]
        if used and all(n not in PROCESS_NAMES and n != "All" and is_non_printing(n) for n in used):
            hits.append(e)
            names.update(used)
    return hits, sorted(names)


def _silenced(instructions, removals):
    """`instructions` with the paints listed in `removals` taken out.

    removals: {position: (set of 'fill'/'stroke', text render mode)}
    """
    out = []
    for idx, ins in enumerate(instructions):
        if idx not in removals:
            out.append(ins)
            continue
        paints, tr = removals[idx]
        op = str(ins.operator)
        if op in _PATH_PAINT:
            left = [p for p in _PATH_PAINT[op] if p not in paints]
            if not left:
                out.append(_ins("n"))
            elif len(left) == len(_PATH_PAINT[op]):
                out.append(ins)
            else:
                out.extend(_ins(o) for o in (_FILL_ONLY if left == ["fill"] else _STROKE_ONLY)[op])
        elif op in ("Tj", "TJ", "'", '"'):
            out.extend([_ins("Tr", 3), ins, _ins("Tr", tr)])  # keeps the text position moving
        # images, image masks and shadings are simply left out
    return out


def _without(doc, page, events):
    """A scratch page with these events silenced. In memory only."""
    by_stream = defaultdict(dict)
    for e in events:
        if e.loc is None:
            raise ValueError("non-printing object inside a pattern")
        names, idx, tr = e.loc
        paints, _ = by_stream[names].setdefault(idx, (set(), tr))
        paints.add(e.paint)
    scratch = doc.scratch("edges")
    rebuild = PageRebuild(scratch, page.number - 1)
    for names, removals in by_stream.items():
        stream, resources = page.obj, page.obj.get("/Resources")
        if resources is None:
            resources = page.obj.resources
        for name in names:
            stream = resources["/XObject"]["/" + name]
            resources = stream.get("/Resources", resources)
        data = pikepdf.unparse_content_stream(_silenced(list(pikepdf.parse_content_stream(stream)), removals))
        if names:
            rebuild.set_form_content(names, data)
        else:
            rebuild.set_page_content(data)


def _runs(mask):
    """(start, stop) of each run of True in a 1-D array."""
    padded = np.concatenate(([False], mask, [False]))
    change = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(change[::2], change[1::2]))


def _bleed_depth(beyond):
    """How far, in pixels, ink carries on past the trim at each step along the edge.

    `beyond` has a row per step along the edge and a column per pixel outward.
    Artwork with a slanted side still bleeds properly although the ink straight
    out from a given point stops short, so at each distance out the ink may sit
    up to SLANT times that distance to either side. A square-ended gap in the
    bleed is still found, because close to the trim there is almost no leeway.
    """
    rows, cols = beyond.shape
    depth = np.full(rows, cols)
    open_ = np.ones(rows, bool)  # still unbroken so far
    for d in range(cols):
        reach = int(np.ceil(SLANT * (d + 1)))
        total = np.concatenate(([0], np.cumsum(beyond[:, d])))
        lo = np.clip(np.arange(rows) - reach, 0, rows)
        hi = np.clip(np.arange(rows) + reach + 1, 0, rows)
        covered = (total[hi] - total[lo]) > 0
        stop = open_ & ~covered
        depth[stop] = d
        open_ &= covered
    return depth


def _scan_edge(mu_page, page, name, need_mm):
    t, m = page.trim, page.media
    unit_mm = page.mm(1.0)
    inside, need = INSIDE_MM / unit_mm, need_mm / unit_mm
    # Strip in page units: `inside` within the trim to `need` beyond it; and the
    # outward direction, the trim line and the two ends of the edge.
    if name == "left":
        box, out, line, room = [max(t.x0 - need, m.x0), t.y0, t.x0 + inside, t.y1], (-1, 0), (t.x0, t.y0), t.x0 - m.x0
    elif name == "right":
        box, out, line, room = [t.x1 - inside, t.y0, min(t.x1 + need, m.x1), t.y1], (1, 0), (t.x1, t.y0), m.x1 - t.x1
    elif name == "bottom":
        box, out, line, room = [t.x0, max(t.y0 - need, m.y0), t.x1, t.y0 + inside], (0, -1), (t.x0, t.y0), t.y0 - m.y0
    else:
        box, out, line, room = [t.x0, t.y1 - inside, t.x1, min(t.y1 + need, m.y1)], (0, 1), (t.x0, t.y1), m.y1 - t.y1
    along_pt = (t.y1 - t.y0) if out[1] == 0 else (t.x1 - t.x0)
    dpi = int(max(36, min(MAX_DPI, MAX_ALONG_PX / max(along_pt / 72.0, 1e-6))))
    to_mu = mu_page.transformation_matrix * mu_page.rotation_matrix
    clip = (pymupdf.Rect(box) * to_mu).normalize()
    pix = mu_page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, clip=clip, alpha=False, annots=False)
    if pix.n != 4:
        raise ValueError("renderer did not return CMYK")
    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).max(axis=2)  # strongest ink

    # Turn the picture so rows run along the edge and columns run outward.
    origin = pymupdf.Point(0, 0) * to_mu
    v = pymupdf.Point(*out) * to_mu - origin
    trim_at = pymupdf.Point(*line) * to_mu
    scale = dpi / 72.0
    if abs(v.x) >= abs(v.y):
        split, outward = trim_at.x * scale - pix.x, v.x > 0
    else:
        ink, split, outward = ink.T, trim_at.y * scale - pix.y, v.y > 0
    if not outward:
        ink, split = ink[:, ::-1], ink.shape[1] - split
    split = int(round(split))
    split = max(0, min(ink.shape[1], split))
    within, beyond = ink[:, :split] > INK_FLOOR, ink[:, split:] > BLEED_FLOOR

    px_mm = 25.4 / dpi * page.unit
    reaches = within.any(axis=1) if within.shape[1] else np.zeros(ink.shape[0], bool)
    depth = _bleed_depth(beyond)
    need_px = need_mm / px_mm - 1.5  # allow for the pixel grid
    short = reaches & (depth < need_px)
    min_run = max(2, int(round(MIN_RUN_MM / px_mm)))
    inked_px = sum(b - a for a, b in _runs(reaches) if b - a >= min_run)
    short_runs = [(a, b) for a, b in _runs(short) if b - a >= min_run]
    short_px = sum(b - a for a, b in short_runs)
    least = hit = None
    if short_runs:
        least = min(float(depth[a:b].min()) for a, b in short_runs) * px_mm
        # Location: the whole edge strip. Good enough to point at the edge.
        hit = [box[0], box[1], box[2], box[3]]
    return EdgeScan(name, page.mm(along_pt), max(page.mm(room), 0.0), inked_px * px_mm, short_px * px_mm, least, hit)


def printing_page(doc, page, content):
    """The page as it prints: (renderable page, non-printing spot names left out).

    When the page has objects in non-printing spot colours they are silenced in
    an in-memory copy, once, and that copy is returned. Raises if it cannot be done.
    """
    key = ("printing", page.number)
    if key not in doc.cache:
        try:
            events, names = _non_printing_events(content)
            if events:
                _without(doc, page, events)
            doc.cache[key] = (bool(events), names)
        except Exception as exc:
            doc.cache[key] = exc
    if isinstance(doc.cache[key], Exception):
        raise doc.cache[key]
    silenced, names = doc.cache[key]
    source = doc.scratch("edges") if silenced else doc.mu
    return source.load_page(page.number - 1), names


def scan_edges(doc, page, content, need_mm) -> PageEdges:
    """Measured once per page and shared between checks. Raises if it cannot measure."""
    key = ("edges", page.number, need_mm)
    if key not in doc.cache:
        try:
            mu_page, names = printing_page(doc, page, content)
            doc.cache[key] = PageEdges({n: _scan_edge(mu_page, page, n, need_mm) for n in EDGES}, names)
        except Exception as exc:
            doc.cache[key] = exc
    if isinstance(doc.cache[key], Exception):
        raise doc.cache[key]
    return doc.cache[key]
