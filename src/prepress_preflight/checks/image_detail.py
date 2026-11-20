from __future__ import annotations

import numpy as np
import pymupdf
from PIL import Image

from ..model import UNCHECKED, WARN
from .base import Check

SOFT = 0.38  # edge sharpness below this: as soft as an image enlarged about 2x or more
NATIVE = 0.47  # typical edge sharpness of a photo at its own resolution
MIN_PIXELS = 300_000  # smaller images are judged by the resolution check alone
TILE = 512  # each of a 3 x 3 grid of tiles across the image
WINDOW = 11  # pixels across which local contrast is measured
EDGE_CONTRAST = 60  # levels across 7 px for a pixel to count as on a real edge
MIN_EDGE_PIXELS = 200


def _local_range(a: np.ndarray, k: int = 7):
    """Max minus min over a k x k window (edges clipped), with array maxima rather than a rank filter."""
    r = k // 2
    pad = np.pad(a, r, mode="edge")
    hi = lo = None
    for axis in (0, 1):
        src_hi = pad if hi is None else hi
        src_lo = pad if lo is None else lo
        n = src_hi.shape[axis] - 2 * r
        take = lambda arr, i: arr[i:i + n] if axis == 0 else arr[:, i:i + n]
        hi = np.maximum.reduce([take(src_hi, i) for i in range(k)])
        lo = np.minimum.reduce([take(src_lo, i) for i in range(k)])
    return hi - lo


def edge_sharpness(img: Image.Image, window: int = None) -> float | None:
    """How sharp the image's strongest edges are for its resolution: about 1 / edge
    width in pixels, the 99th percentile of |gradient| / local contrast over pixels
    on real edges, sampled in a grid of tiles across the whole image. None when
    there are too few edges to judge (a smooth sky, a flat tint)."""
    window = window or WINDOW
    if img.mode == "CMYK":
        # lightness from the inks without clipping (adding K to CMY flattened every dark area to
        # solid black and hid its edges): each ink multiplies the light that is left
        c = np.asarray(img).astype(np.float32) / 255
        light = (1 - c[..., 0]) * (1 - c[..., 1]) * (1 - c[..., 2]) * (1 - c[..., 3])
        g = Image.fromarray((light ** (1 / 2.2) * 255).astype(np.uint8))
    else:
        g = img.convert("L")
    w, h = g.size
    tw, th = min(w, TILE), min(h, TILE)
    values = []
    for fy in (0.0, 0.5, 1.0):
        for fx in (0.0, 0.5, 1.0):
            x, y = int((w - tw) * fx), int((h - th) * fy)
            a = np.asarray(g.crop((x, y, x + tw, y + th))).astype(np.float32)
            contrast = _local_range(a, window)
            gy, gx = np.gradient(a)
            on_edge = contrast >= EDGE_CONTRAST
            if on_edge.any():
                values.append(np.hypot(gx, gy)[on_edge] / contrast[on_edge])
    v = np.concatenate(values) if values else np.zeros(0)
    if v.size < MIN_EDGE_PIXELS:
        return None
    return float(np.percentile(v, 99))


def decode(doc_path_or_mu, xref: int) -> Image.Image:
    """An image's pixels as they print: decoded by MuPDF, which applies the
    image's /Decode array and the JPEG's own Adobe colour transform (Pillow's raw
    reading of an Adobe CMYK JPEG came back inverted). Colour management off,
    so CMYK stays the file's own ink values."""
    pymupdf.TOOLS.set_icc(False)
    mu = doc_path_or_mu if isinstance(doc_path_or_mu, pymupdf.Document) else pymupdf.open(doc_path_or_mu)
    pix = pymupdf.Pixmap(mu, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)  # drop the mask: only the picture's own detail matters
    mode = {1: "L", 3: "RGB", 4: "CMYK"}.get(pix.n)
    if mode is None:  # spot, DeviceN or Lab images: judge them in RGB
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        mode = "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


class ImageDetailCheck(Check):
    """Images whose pixels hold less detail than their resolution suggests:
    enlarged from a smaller image (or soft, out of focus). They pass the
    resolution check, since the pixel count is there, but print soft.

    Measured, not guessed: the sharpness of the image's strongest edges for its
    resolution (see edge_sharpness), on the pixels as MuPDF decodes them.
    Calibrated on the samples (2026-11-20, 11 px window): every one of the 199
    genuine images they paint that could be judged scored 0.436 or more; genuine
    RGB and CMYK photos enlarged (bicubic, then JPEG) scored 0.29-0.38 at 2x,
    0.25-0.31 at 3x, 0.18-0.26 at 4x. The upscaled sample (enlarged 4.6x from
    66 ppi) scores 0.24 and is estimated at 5x, 63 ppi. The estimate assumes
    sharpness falls as about 1/sqrt(enlargement), which those measurements follow.

    Only images placed at the minimum resolution or more (lower ones are
    already reported by Image resolution) with at least 300,000 pixels. Images
    with too few edges to judge get no verdict.
    """

    id = "image-detail"
    name = "Image detail"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.scores: dict = {}  # image object -> sharpness (or None)
        self._mu = None  # the file in MuPDF, opened when an image is first judged
        self.soft: list = []  # (page, ppi, detail ppi, bbox)
        self.unreadable: set = set()
        self.unreadable_pages: set = set()

    def _score(self, ref):
        if ref not in self.scores:
            try:
                if self._mu is None:
                    self._mu = pymupdf.open(self.doc.path)
                self.scores[ref] = edge_sharpness(decode(self._mu, ref[0]))
            except Exception:
                self.scores[ref] = "unreadable"
        return self.scores[ref]

    def page(self, page, content):
        area = page.print_area
        for e in content.events:
            if e.kind != "image" or e.ref is None or e.ppi is None or e.px is None:
                continue
            if e.px[0] * e.px[1] < MIN_PIXELS or (e.bbox is not None and not area.intersects(e.bbox)):
                continue
            ppi = e.ppi / page.unit
            if ppi < self.options.min_ppi - 0.5:
                continue  # Image resolution reports it
            score = self._score(e.ref)
            if score == "unreadable":
                self.unreadable.add(e.ref)
                self.unreadable_pages.add(page.number)
            elif score is not None and score < SOFT:
                enlarged = (NATIVE / score) ** 2
                self.soft.append((page.number, ppi, ppi / enlarged, e.bbox, e.ref))

    def finish(self):
        if self._mu is not None:
            self._mu.close()
        if self.soft:
            pages = sorted({t[0] for t in self.soft})
            page, ppi, detail, _, _ = min(self.soft, key=lambda t: t[2])
            images = len({t[4] for t in self.soft})
            placed = f" (placed {len(self.soft)} times)" if len(self.soft) > images else ""
            where = f" (page {page})" if len(pages) > 1 else ""
            self.add(WARN, f"{images} image(s){placed} look enlarged from a smaller image, or are soft: their edges are "
                           f"as soft as an image enlarged {ppi / detail:.0f}x. The worst holds roughly {detail:.0f} ppi of "
                           f"real detail, not the {ppi:.0f} ppi its pixels suggest{where}",
                     pages, [(t[0], list(t[3])) for t in self.soft if t[3] is not None])
        if self.unreadable:
            self.add(UNCHECKED, f"{len(self.unreadable)} image(s) could not be read to judge their detail",
                     sorted(self.unreadable_pages))
        return self.findings
