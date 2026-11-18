"""Open a PDF read-only and expose page geometry."""
from __future__ import annotations

from dataclasses import dataclass

import pikepdf
import pymupdf

from .model import Box, mm

# Preserve the file's own CMYK values when rendering for ink coverage. With
# colour management on, MuPDF converts CMYK through profiles and the numbers
# no longer match what is in the file.
pymupdf.TOOLS.set_icc(False)


def _box(arr) -> Box:
    v = [float(x) for x in arr]
    return Box(min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))


def _rotation(node) -> int:
    """/Rotate is inherited from parent page-tree nodes."""
    for _ in range(50):
        if "/Rotate" in node:
            return int(node["/Rotate"]) % 360
        if "/Parent" not in node:
            break
        node = node["/Parent"]
    return 0


@dataclass
class PageInfo:
    number: int  # 1-based
    obj: pikepdf.Page
    media: Box
    trim: Box
    bleed: Box
    has_trim: bool
    has_bleed: bool
    rotate: int
    unit: float = 1.0  # UserUnit: size of one page unit in 1/72 inch
    # Set by marks.infer_trim. When the crop marks disagree with the trim box,
    # `trim` holds the rectangle the marks point at and `declared_trim` the
    # file's own box.
    marks_trim: Box | None = None
    declared_trim: Box | None = None
    trim_inferred: bool = False

    def mm(self, units: float) -> float:
        """Convert a length in page units to millimetres."""
        return mm(units * self.unit)

    @property
    def trim_size_mm(self) -> tuple[float, float]:
        w, h = self.mm(self.trim.width), self.mm(self.trim.height)
        return (h, w) if self.rotate in (90, 270) else (w, h)

    @property
    def print_area(self) -> Box:
        """The larger of trim and bleed: where printed artwork lives."""
        return Box(min(self.trim.x0, self.bleed.x0), min(self.trim.y0, self.bleed.y0),
                   max(self.trim.x1, self.bleed.x1), max(self.trim.y1, self.bleed.y1))


def device_matrix(mu_page) -> pymupdf.Matrix:
    """The matrix from PDF page coordinates to the rendered page (points, top-left
    origin, after /Rotate).

    PyMuPDF's own page.transformation_matrix leaves out the page's origin on
    rotated pages whose crop box does not start at 0,0 (seen with PyMuPDF 1.26),
    so this builds the mapping from the crop box itself. PyMuPDF reports the crop
    box with its y measured down from the top of the media box, which is undone
    here.
    """
    mb, cb = mu_page.mediabox, mu_page.cropbox
    x0, y1 = cb.x0, mb.y1 - cb.y0  # the crop box's top-left corner, in PDF coordinates
    # A UserUnit scales the whole page; PyMuPDF renders it at that size.
    rot = mu_page.rotation % 360
    across = cb.width if rot in (0, 180) else cb.height
    u = mu_page.rect.width / across if across else 1.0
    w, h = cb.width * u, cb.height * u  # the unrotated page, as rendered
    # PyMuPDF's rotation_matrix uses the unscaled size, so the turn is built here.
    turn = {0: pymupdf.Matrix(1, 0, 0, 1, 0, 0), 90: pymupdf.Matrix(0, 1, -1, 0, h, 0),
            180: pymupdf.Matrix(-1, 0, 0, -1, w, h), 270: pymupdf.Matrix(0, -1, 1, 0, 0, w)}[rot]
    return pymupdf.Matrix(u, 0, 0, -u, -x0 * u, y1 * u) * turn


class Document:
    def __init__(self, path: str):
        self.path = path
        self.pdf = pikepdf.open(path)  # read-only: nothing here ever saves
        self.mu = pymupdf.open(path)
        self._scratch: dict = {}
        self.cache: dict = {}  # results shared between checks
        self.pages: list[PageInfo] = []
        for i, page in enumerate(self.pdf.pages, 1):
            unit = float(page.obj.get("/UserUnit", 1))
            media = _box(page.mediabox)
            has_trim = "/TrimBox" in page.obj
            has_bleed = "/BleedBox" in page.obj
            trim = _box(page.trimbox)
            bleed = _box(page.obj["/BleedBox"]) if has_bleed else trim
            self.pages.append(PageInfo(i, page, media, trim, bleed, has_trim, has_bleed, _rotation(page.obj), unit))

    def scratch(self, purpose="default"):
        """A second, in-memory handle on the file for checks that need to render
        part of a page on its own. Its pages may be rearranged in memory; it is
        never saved, and the file on disk is never written to. Each purpose gets
        its own handle so one check's rearranging cannot affect another's."""
        if purpose not in self._scratch:
            self._scratch[purpose] = pymupdf.open(self.path)
        return self._scratch[purpose]

    def close(self):
        for handle in self._scratch.values():
            handle.close()
        self.mu.close()
        self.pdf.close()
