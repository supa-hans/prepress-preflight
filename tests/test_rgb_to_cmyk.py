"""rgb-to-cmyk: RGB colour converted through a CMYK profile (FOGRA39 by default)."""
import hashlib
import io
import os
import zlib

import pikepdf
import pymupdf
import pytest
from PIL import Image, ImageCms

from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.rgb_to_cmyk import ADOBE_INVERTED, Engine, find_profile, jpeg_quality

try:
    ENGINE = Engine(find_profile(None))
except ValueError:
    ENGINE = None
pytestmark = pytest.mark.skipif(ENGINE is None, reason="Adobe's Coated FOGRA39 profile is not installed")

propose, write = FIXES["rgb-to-cmyk"]


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def rgb_findings(path):
    return [f.message for f in run(path).findings if f.check == "RGB"]


def cmyk_of(rgb):
    return tuple(round(v, 3) for v in ENGINE.colour(rgb))


def test_vector_and_text_colours(make, tmp_path):
    def font(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    content = (b"1 0 0 rg 100 100 50 50 re f "  # red fill
               b"0 0 0 RG 2 w 100 200 m 200 200 l S "  # pure black line: K100
               b"BT /F1 12 Tf 0 0 0 rg 100 160 Td (Black) Tj ET "  # pure black text: K100
               b"0.02 0.02 0.02 rg 160 100 20 20 re f "  # near black: follows the profile
               b"0 1 1 0 k 200 100 20 20 re f")  # already CMYK: untouched
    src = make(content, font={"/F1": font})
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert rgb_findings(src)
    p = propose(src)
    assert "Converts RGB to CMYK (Coated FOGRA39" in p.reasons[0][1]
    assert any("2 pure RGB black text or vector object(s) become K100 only" in r for _, r in p.reasons)
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    got = [(e.cs.kind, tuple(round(v, 3) for v in e.colour)) for e in events(dst)]
    assert got == [("cmyk", cmyk_of((1, 0, 0))), ("cmyk", (0, 0, 0, 1)), ("cmyk", (0, 0, 0, 1)),
                   ("cmyk", cmyk_of((0.02, 0.02, 0.02))), ("cmyk", (0, 1, 1, 0))]
    assert rgb_findings(dst) == []
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_rgb_colour_space_with_an_embedded_profile(make, tmp_path):
    def icc_rgb(pdf):
        data = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        return pikepdf.Array([pikepdf.Name.ICCBased, pdf.make_stream(data, N=3)])
    src = make(b"/CS0 cs 0 0.5 1 sc 100 100 50 50 re f")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Resources["/ColorSpace"] = pikepdf.Dictionary(CS0=icc_rgb(pdf))
        pdf.save(src)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    (e,) = events(dst)
    assert e.cs.kind == "cmyk" and tuple(round(v, 2) for v in e.colour) == tuple(round(v, 2) for v in cmyk_of((0, 0.5, 1)))


def image_xobject(data, w, h, **extra):
    def make_image(pdf):
        return pdf.make_stream(data, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=w, Height=h,
                               BitsPerComponent=8, **extra)
    return make_image


def placed(make, image):
    return make(b"q 100 0 0 50 100 100 cm /Im0 Do Q", xobject={"/Im0": image})


def ink_at(path, x, y):
    """The CMYK MuPDF reads at a point (colour management off: the file's own values)."""
    pymupdf.TOOLS.set_icc(False)
    pix = pymupdf.open(path).load_page(0).get_pixmap(colorspace=pymupdf.csCMYK, alpha=False)
    return tuple(round(v / 255, 2) for v in pix.pixel(int(x), int(pix.height - y)))


def test_a_lossless_rgb_image(make, tmp_path):
    # left half red, right half blue
    pixels = (bytes([255, 0, 0]) * 5 + bytes([0, 0, 255]) * 5) * 4
    src = placed(make, image_xobject(zlib.compress(pixels), 10, 4, ColorSpace=pikepdf.Name.DeviceRGB,
                                     Filter=pikepdf.Name.FlateDecode))
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        img = pdf.pages[0].obj.Resources.XObject.Im0
        assert img.ColorSpace == pikepdf.Name.DeviceCMYK and img.Filter == pikepdf.Name.FlateDecode
    want_red = tuple(round(v, 2) for v in ENGINE.colour((1, 0, 0)))
    assert ink_at(dst, 120, 125) == pytest.approx(want_red, abs=0.02)


def test_a_jpeg_photo_stays_jpeg_and_reads_back_right(make, tmp_path):
    img = Image.new("RGB", (40, 20), (255, 0, 0))
    img.paste((0, 0, 255), (20, 0, 40, 20))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    src = placed(make, image_xobject(buf.getvalue(), 40, 20, ColorSpace=pikepdf.Name.DeviceRGB, Filter=pikepdf.Name.DCTDecode))
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        new = pdf.pages[0].obj.Resources.XObject.Im0
        assert new.Filter == pikepdf.Name.DCTDecode and list(new.Decode) == ADOBE_INVERTED
        assert jpeg_quality(new.read_raw_bytes()) == pytest.approx(88, abs=2)  # the source's own quality
    assert ink_at(dst, 110, 125) == pytest.approx(tuple(round(v, 2) for v in ENGINE.colour((1, 0, 0))), abs=0.04)
    assert ink_at(dst, 190, 125) == pytest.approx(tuple(round(v, 2) for v in ENGINE.colour((0, 0, 1))), abs=0.04)


def test_an_indexed_image_gets_a_converted_palette(make, tmp_path):
    palette = pikepdf.String(bytes([255, 0, 0, 0, 0, 255]))
    pixels = bytes([0] * 5 + [1] * 5) * 4

    def make_image(pdf):
        return pdf.make_stream(zlib.compress(pixels), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=10,
                               Height=4, BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode,
                               ColorSpace=pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 1, palette]))
    src = placed(make, make_image)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        img = pdf.pages[0].obj.Resources.XObject.Im0
        assert img.ColorSpace[1] == pikepdf.Name.DeviceCMYK
        assert zlib.decompress(img.read_raw_bytes()) == pixels  # the pixels themselves are untouched
        entries = bytes(img.ColorSpace[3])
        assert tuple(round(v / 255, 2) for v in entries[:4]) == pytest.approx(tuple(round(v, 2) for v in ENGINE.colour((1, 0, 0))), abs=0.01)


def test_a_gradient_and_an_rgb_blending_group(make, tmp_path):
    def shading(pdf):
        return pdf.make_indirect(pikepdf.Dictionary(
            ShadingType=2, ColorSpace=pikepdf.Name.DeviceRGB, Coords=[100, 0, 200, 0],
            Function=pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[1, 0, 0], C1=[0, 0, 1], N=1)))
    src = make(b"q 100 100 100 50 re W n /Sh0 sh Q")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0].obj
        page.Resources["/Shading"] = pikepdf.Dictionary(Sh0=shading(pdf))
        page["/Group"] = pikepdf.Dictionary(S=pikepdf.Name.Transparency, CS=pikepdf.Name.DeviceRGB)
        pdf.save(src)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        page = pdf.pages[0].obj
        sh = page.Resources.Shading.Sh0
        assert sh.ColorSpace == pikepdf.Name.DeviceCMYK and int(sh.Function.FunctionType) == 0
        assert page.Group.CS == pikepdf.Name.DeviceCMYK
    assert ink_at(dst, 101, 125) == pytest.approx(tuple(round(v, 2) for v in ENGINE.colour((1, 0, 0))), abs=0.03)
    assert rgb_findings(dst) == []


def test_mesh_gradients_are_left_and_said(make):
    def mesh(pdf):
        return pdf.make_indirect(pdf.make_stream(b"\x00" * 16, ShadingType=4, ColorSpace=pikepdf.Name.DeviceRGB,
                                                 BitsPerCoordinate=8, BitsPerComponent=8, BitsPerFlag=8,
                                                 Decode=[0, 1, 0, 1, 0, 1, 0, 1, 0, 1]))
    src = make(b"1 0 0 rg 100 100 10 10 re f /Sh0 sh")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Resources["/Shading"] = pikepdf.Dictionary(Sh0=mesh(pdf))
        pdf.save(src)
    p = propose(src)
    assert any("mesh or function-based" in r for _, r in p.skipped)


def test_profiles(make, tmp_path):
    assert find_profile("fogra39").endswith("CoatedFOGRA39.icc")
    with pytest.raises(ValueError, match="not found"):
        find_profile("/nowhere/press.icc")
    srgb = tmp_path / "srgb.icc"
    srgb.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    with pytest.raises(ValueError, match="not a CMYK profile"):
        Engine(str(srgb))
    if os.path.isfile(find_profile("fogra39").replace("FOGRA39", "GRACoL2006")):
        p = propose(make(b"1 0 0 rg 100 100 50 50 re f"), profile="gracol2006")
        assert "GRACoL" in p.reasons[0][1]


def test_nothing_to_do(make):
    p = propose(make(b"0 0 0 1 k 100 100 50 50 re f"))
    assert p.empty and p.skipped == [(1, "there is no RGB colour to convert")]


def test_near_black_follows_the_profile_unless_asked(make, tmp_path):
    content = (b"0.02 0.02 0.02 rg 100 100 20 20 re f "  # near-black, neutral
               b"0.02 0.02 0.09 rg 130 100 20 20 re f "  # a navy-black: not neutral, never K100
               b"0.3 0.3 0.3 rg 160 100 20 20 re f")  # a dark grey: not near-black
    src = make(content)
    p = propose(src)
    assert any("1 near-black RGB text or vector object(s)" in r and "--near-black-to-k" in r for _, r in p.reasons)
    plain = str(tmp_path / "plain.pdf")
    write(p, plain)
    assert tuple(round(v, 3) for v in events(plain)[0].colour) == cmyk_of((0.02, 0.02, 0.02))
    asked = propose(src, near_black=True)
    assert any("also become K100 only, as asked" in r for _, r in asked.reasons)
    out = str(tmp_path / "asked.pdf")
    write(asked, out)
    got = [tuple(round(v, 3) for v in e.colour) for e in events(out)]
    assert got == [(0, 0, 0, 1), cmyk_of((0.02, 0.02, 0.09)), cmyk_of((0.3, 0.3, 0.3))]
