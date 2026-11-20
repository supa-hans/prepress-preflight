"""Image detail: images enlarged from smaller ones, which pass the resolution check but print soft."""
import io
import zlib

import numpy as np
import pikepdf
import pytest
from PIL import Image

from prepress_preflight.checks.image_detail import decode, edge_sharpness
from prepress_preflight.engine import run


def photo_like(w=800, h=800, seed=1):
    """Sharp-edged shapes with texture, like a detailed photo at its own resolution."""
    rng = np.random.default_rng(seed)
    a = np.full((h, w), 128, np.float32)
    for _ in range(140):
        x, y = rng.integers(0, w - 60), rng.integers(0, h - 60)
        a[y:y + rng.integers(10, 60), x:x + rng.integers(10, 60)] = rng.integers(0, 255)
    a += rng.normal(0, 6, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def placed(make, img, inches):
    """A page with `img` placed `inches` wide (so its ppi is width / inches)."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    size = 72 * inches

    def image(pdf):
        return pdf.make_stream(zlib.compress(rgb.tobytes()), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=w, Height=h, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceRGB,
                               Filter=pikepdf.Name.FlateDecode)
    return make(f"q {size:.2f} 0 0 {size * h / w:.2f} 40 40 cm /Im0 Do Q".encode(), xobject={"/Im0": image},
                media=(0, 0, size + 80, size * h / w + 80), trim=None, bleed=None)


def detail(path):
    return [f for f in run(path).findings if f.check == "Image detail"]


def test_a_sharp_image_passes(make):
    assert detail(placed(make, photo_like(), 2.5)) == []  # 320 ppi, genuinely sharp


def test_an_enlarged_image_is_flagged_with_its_real_detail(make):
    small = photo_like(300, 300)
    big = small.resize((1200, 1200), Image.BICUBIC)  # enlarged 4x: 1200 px placed 4 in = 300 ppi
    (f,) = detail(placed(make, big, 4))
    assert f.severity == "warn" and "1 image(s) look enlarged" in f.message
    real = float(f.message.split("roughly ")[1].split(" ppi")[0])
    assert 45 <= real <= 130  # the truth is 75 ppi (300 px over 4 in)


def test_too_few_edges_gets_no_verdict():
    flat = Image.new("L", (800, 800), 140)
    assert edge_sharpness(flat) is None


def test_low_resolution_is_left_to_the_resolution_check(make):
    small = photo_like(300, 300).resize((600, 600), Image.BICUBIC)  # 600 px over 6 in = 100 ppi
    path = placed(make, small, 6)
    assert detail(path) == []
    assert any(f.check == "Image resolution" for f in run(path).findings)


def test_cmyk_jpeg_is_decoded_as_it_prints(make, tmp_path):
    """A CMYK JPEG stored inverted (Adobe's convention, with /Decode) must read as it prints."""
    img = Image.new("CMYK", (40, 20), (200, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)

    def image(pdf):
        return pdf.make_stream(buf.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=40, Height=20,
                               BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceCMYK, Filter=pikepdf.Name.DCTDecode,
                               Decode=[1, 0, 1, 0, 1, 0, 1, 0])
    path = make(b"q 40 0 0 20 100 100 cm /Im0 Do Q", xobject={"/Im0": image})
    with pikepdf.open(path) as pdf:
        xref = pdf.pages[0].obj.Resources.XObject.Im0.objgen[0]
    got = decode(path, xref)
    assert got.mode == "CMYK" and got.getpixel((5, 5))[0] == pytest.approx(200, abs=4) and got.getpixel((5, 5))[3] < 5
