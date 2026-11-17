"""Helpers that build tiny synthetic PDFs, so tests need no client artwork."""
from __future__ import annotations

import pikepdf
import pytest

MM = 72 / 25.4


def build_pdf(path, content: bytes, media, trim=None, bleed=None, extgstate=None, colorspace=None, xobject=None):
    pdf = pikepdf.new()
    resources = pikepdf.Dictionary()
    if extgstate:
        resources["/ExtGState"] = pikepdf.Dictionary(extgstate)
    if colorspace:
        resources["/ColorSpace"] = pikepdf.Dictionary(colorspace)
    if xobject:
        resources["/XObject"] = pikepdf.Dictionary({k: v(pdf) for k, v in xobject.items()})
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=list(media), Resources=resources,
                              Contents=pdf.make_stream(content))
    if trim:
        page["/TrimBox"] = list(trim)
    if bleed:
        page["/BleedBox"] = list(bleed)
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    pdf.save(str(path))
    return str(path)


def registration_space():
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[1, 1, 1, 1], N=1)
    return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name.All, pikepdf.Name.DeviceCMYK, tint])


def gray_image(width, height):
    def make(pdf):
        return pdf.make_stream(bytes([128]) * (width * height), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=width, Height=height, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8)
    return make


# A 100 x 60 mm page with 3 mm bleed and a 10 mm slug all round.
SLUG, BLEED = 10 * MM, 3 * MM
W, H = 100 * MM, 60 * MM
MEDIA = (0, 0, W + 2 * SLUG, H + 2 * SLUG)
TRIM = (SLUG, SLUG, SLUG + W, SLUG + H)
BLEEDBOX = (SLUG - BLEED, SLUG - BLEED, SLUG + W + BLEED, SLUG + H + BLEED)


@pytest.fixture
def make(tmp_path):
    def _make(content: bytes, name="t.pdf", media=MEDIA, trim=TRIM, bleed=BLEEDBOX, **kw):
        return build_pdf(tmp_path / name, content, media, trim, bleed, **kw)
    return _make
