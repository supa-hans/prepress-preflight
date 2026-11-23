"""reduce-ink: flat CMYK colours over the total ink limit brought down to it."""
import hashlib

import pikepdf
import pytest

from prepress_preflight.checks import Options
from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.reduce_ink import reduce

propose, write = FIXES["reduce-ink"]


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def test_the_colour_maths():
    assert reduce((1, 1, 1, 1), 300) == (0.6666, 0.6666, 0.6666, 1.0)        # K full: C, M, Y scaled together
    assert reduce((0.9, 0.8, 0.8, 0.6), 300) == pytest.approx((0.85, 0.75, 0.75, 0.65), abs=1e-4)  # grey into K
    assert reduce((0.8, 0.7, 0.7, 0.5), 300) == (0.8, 0.7, 0.7, 0.5)         # within: untouched
    for c in [(1, 1, 1, 1), (1, 0.9, 0, 1), (0.95, 0.95, 0.9, 0.2), (1, 1, 1, 0)]:
        r = reduce(c, 280)
        assert sum(r) <= 2.8 + 1e-9 and all(0 <= v <= 1 for v in r)


def test_only_colours_over_the_limit_change(make, tmp_path):
    src = make(b"1 1 1 1 k 100 100 50 50 re f "        # 400%: reduced
               b"0.8 0.7 0.7 0.5 k 160 100 50 50 re f "  # 270%: untouched
               b"1 1 1 1 K 2 w 60 60 m 300 60 l S "      # a 400% line: reduced
               b"220 100 50 50 re f")                    # inherits the 270% fill: untouched
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src, ink_limit=300)
    assert "1 shape(s), 1 line(s)" in p.reasons[0][1]
    assert "C100 M100 Y100 K100 (400%) to C67 M67 Y67 K100 (300%) x2" in p.reasons[1][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    assert [e.colour for e in events(dst)] == [(0.6666, 0.6666, 0.6666, 1.0), (0.8, 0.7, 0.7, 0.5),
                                             (0.6666, 0.6666, 0.6666, 1.0), (0.8, 0.7, 0.7, 0.5)]
    over = lambda path: [f for f in run(path, Options(ink_limit_pct=300)).findings
                         if f.check == "Total ink" and f.severity == "fail"]
    assert over(src) and not over(dst)


def cmyk_image(pixels, jpeg=False):
    """An image XObject maker from rows of (c, m, y, k) 0-255 tuples."""
    import io
    import zlib
    from PIL import Image
    h, w = len(pixels), len(pixels[0])
    raw = bytes(v for row in pixels for px in row for v in px)

    def build(pdf):
        if jpeg:
            buf = io.BytesIO()
            Image.frombytes("CMYK", (w, h), raw).save(buf, "JPEG", quality=95)
            return pdf.make_indirect(pdf.make_stream(buf.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                                     Width=w, Height=h, ColorSpace=pikepdf.Name.DeviceCMYK, BitsPerComponent=8,
                                                     Filter=pikepdf.Name.DCTDecode, Decode=[1, 0, 1, 0, 1, 0, 1, 0]))
        return pdf.make_indirect(pdf.make_stream(zlib.compress(raw), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                                 Width=w, Height=h, ColorSpace=pikepdf.Name.DeviceCMYK, BitsPerComponent=8,
                                                 Filter=pikepdf.Name.FlateDecode))
    return build


def image_pixels(path):
    import numpy as np
    import pymupdf
    from prepress_preflight.fixes.reduce_ink import _decode
    pymupdf.TOOLS.set_icc(False)
    with pikepdf.open(path) as pdf:
        ref = next(e.ref for e in walk_page(pdf.pages[0]).events if e.kind == "image")
    return np.asarray(_decode(pymupdf.open(path), ref[0]))


def test_image_pixels_over_the_limit_come_down_and_the_rest_stay(make, tmp_path):
    over, under = (255, 255, 255, 255), (200, 150, 100, 50)
    src = make(b"q 72 0 0 72 100 100 cm /Im0 Do Q", xobject={"/Im0": cmyk_image([[over, under] * 4] * 8)})
    p = propose(src, ink_limit=300)
    assert "in 1 CMYK image(s)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    a, b = image_pixels(src), image_pixels(dst)
    assert (b[:, 1::2] == a[:, 1::2]).all()  # within the limit: exactly as they were
    assert tuple(b[0, 0]) == (170, 170, 170, 255) and b.astype(int).sum(axis=2).max() / 2.55 <= 300


def test_a_jpeg_stays_a_jpeg_and_ends_within_the_limit(make, tmp_path):
    src = make(b"q 72 0 0 72 100 100 cm /Im0 Do Q",
               xobject={"/Im0": cmyk_image([[(255, 255, 255, 255)] * 16] * 16, jpeg=True)})
    dst = str(tmp_path / "out.pdf")
    write(propose(src, ink_limit=300), dst)
    with pikepdf.open(dst) as pdf:
        ref = next(e.ref for e in walk_page(pdf.pages[0]).events if e.kind == "image")
        assert str(pdf.get_object(ref).Filter) == "/DCTDecode"
    assert image_pixels(dst).astype(int).sum(axis=2).max() / 2.55 <= 300.5


def test_grey_images_are_not_touched(make):
    from conftest import gray_image
    src = make(b"1 1 1 1 k 100 100 50 50 re f q 72 0 0 72 200 100 cm /Im0 Do Q", xobject={"/Im0": gray_image(10, 10)})
    p = propose(src, ink_limit=300)
    assert len(p.reasons) == 2 and "image" not in p.reasons[0][1]  # the grey image is not CMYK: nothing to say


def test_needs_a_real_limit(make):
    src = make(b"1 1 1 1 k 100 100 50 50 re f")
    for bad in (None, 400):
        with pytest.raises(ValueError, match="press limit"):
            propose(src, ink_limit=bad)
    with pytest.raises(ValueError, match="200 or more"):
        propose(src, ink_limit=150)
    assert "no flat CMYK colours over 300%" in propose(make(b"0 0 0 1 k 0 0 9 9 re f", name="ok.pdf"), ink_limit=300).skipped[0][1]


def test_an_indexed_image_has_its_palette_reduced_exactly(make, tmp_path):
    import zlib

    def build(pdf):
        palette = bytes([255, 255, 255, 255, 200, 150, 100, 50])  # entry 0 at 400%, entry 1 at 196%
        return pdf.make_indirect(pdf.make_stream(zlib.compress(bytes([0, 1] * 32)), Type=pikepdf.Name.XObject,
                                                 Subtype=pikepdf.Name.Image, Width=8, Height=8, BitsPerComponent=8,
                                                 ColorSpace=[pikepdf.Name.Indexed, pikepdf.Name.DeviceCMYK, 1, pikepdf.String(palette)],
                                                 Filter=pikepdf.Name.FlateDecode))
    src = make(b"q 72 0 0 72 100 100 cm /Im0 Do Q", xobject={"/Im0": build})
    p = propose(src, ink_limit=300)
    assert "in 1 CMYK image(s)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    b = image_pixels(dst)
    assert tuple(b[0, 0]) == (170, 170, 170, 255) and tuple(b[0, 1]) == (200, 150, 100, 50)


def test_a_spot_image_is_listed(make):
    import zlib

    def build(pdf):
        sep = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 185 C"), pikepdf.Name.DeviceCMYK,
                             pdf.make_indirect(pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 1, 0], N=1))])
        return pdf.make_indirect(pdf.make_stream(zlib.compress(bytes([255] * 16)), Type=pikepdf.Name.XObject,
                                                 Subtype=pikepdf.Name.Image, Width=4, Height=4, BitsPerComponent=8,
                                                 ColorSpace=sep, Filter=pikepdf.Name.FlateDecode))
    src = make(b"1 1 1 1 k 0 0 9 9 re f q 72 0 0 72 100 100 cm /Im0 Do Q", xobject={"/Im0": build})
    assert any("spot, DeviceN or Lab" in t for _, t in propose(src, ink_limit=300).skipped)
