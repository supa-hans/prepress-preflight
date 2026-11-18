"""Separations: each printing plate of a page on its own.

MuPDF renders a page into the four process plates plus one channel per spot
ink, honouring overprint, through its separations interface (reached here
through PyMuPDF's low-level `mupdf` module). Colour management is off, so the
values are the file's own.

`page_inks` lists a page's inks; `render_plate` draws one of them as a
greyscale picture with ink dark on white, as a plate would print; and
`ink_coverage` gives how much of the page each ink covers.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymupdf

from .edges import is_non_printing

PROCESS = ("Cyan", "Magenta", "Yellow", "Black")
MAX_PIXELS = 12_000_000
pymupdf.TOOLS.set_icc(False)


@dataclass
class Ink:
    name: str
    process: bool
    printing: bool  # False for dielines and the like (recognised by name)


def _separations(page):
    m = pymupdf.mupdf
    fz_page = page.this
    seps = m.fz_page_separations(fz_page)
    count = m.fz_count_separations(seps) if seps.m_internal else 0
    names = [m.fz_separation_name(seps, i) for i in range(count)]
    for i in range(count):
        m.fz_set_separation_behavior(seps, i, m.FZ_SEPARATION_SPOT)
    return seps, names


def page_inks(page) -> list[Ink]:
    """The inks a page prints with: the four process inks, then its spots."""
    _, names = _separations(page)
    return [Ink(n, True, True) for n in PROCESS] + [Ink(n, False, not is_non_printing(n)) for n in names]


def render_channels(page, zoom: float):
    """All plates of a page: (names, array of height x width x inks, 0..255 ink)."""
    m = pymupdf.mupdf
    area = page.rect.width * page.rect.height * zoom * zoom
    if area > MAX_PIXELS:
        zoom *= (MAX_PIXELS / area) ** 0.5
    seps, names = _separations(page)
    if not seps.m_internal:
        # A page with no spots still goes through the separations path: only
        # there does MuPDF honour overprint (black overprinting cyan keeps the cyan).
        seps = m.fz_new_separations(0)
    ctm = m.FzMatrix(zoom, 0, 0, zoom, 0, 0)
    pix = m.fz_new_pixmap_from_page_with_separations(page.this, ctm, m.fz_device_cmyk(), seps, 0)
    w, h, n, stride = pix.w(), pix.h(), pix.n(), pix.stride()
    raw = np.frombuffer(bytes(m.fz_pixmap_samples_memoryview(pix)), dtype=np.uint8)
    data = raw[: h * stride].reshape(h, stride)[:, : w * n].reshape(h, w, n)
    return list(PROCESS) + names, data


def render_plate(page, ink: str, zoom: float) -> bytes:
    """One plate as a PNG: ink dark on white."""
    names, data = render_channels(page, zoom)
    if ink not in names:
        raise ValueError(f"This page has no {ink} plate")
    plate = 255 - data[..., names.index(ink)]
    h, w = plate.shape
    pix = pymupdf.Pixmap(pymupdf.csGRAY, w, h, np.ascontiguousarray(plate).tobytes(), False)
    return pix.tobytes("png")


def ink_coverage(page, zoom: float = 18 / 72) -> dict[str, float]:
    """Percentage of the page each ink touches (above 2%)."""
    names, data = render_channels(page, zoom)
    return {n: round(float((data[..., i] > 5).mean()) * 100, 1) for i, n in enumerate(names)}
