"""downsample-images: images placed far over the press's resolution brought down."""
import hashlib
import io
import zlib

import numpy as np
import pikepdf
import pymupdf
import pytest
from PIL import Image

from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError

propose, write = FIXES["downsample-images"]
RNG = np.random.default_rng(7)


def noise(w, h, n):
    """Smooth-ish random pixels: they compress poorly, as photos do, so downsampling saves space."""
    small = RNG.integers(0, 256, (h // 4 + 1, w // 4 + 1, n), dtype=np.uint8)
    img = Image.fromarray(small if n > 1 else small[:, :, 0], {1: "L", 3: "RGB", 4: "CMYK"}[n])
    return np.asarray(img.resize((w, h), Image.BILINEAR))


def flate_image(pixels, space, **extra):
    h, w = pixels.shape[:2]

    def make(pdf):
        return pdf.make_stream(zlib.compress(pixels.tobytes()), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=w, Height=h, ColorSpace=space, BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode,
                               **extra)
    return make


def jpeg_image(pixels):
    h, w = pixels.shape[:2]
    buf = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buf, "JPEG", quality=90)

    def make(pdf):
        return pdf.make_stream(buf.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=w, Height=h,
                               ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode)
    return make


def placed(name, x, y, size):
    return f"q {size} 0 0 {size} {x} {y} cm /{name} Do Q ".encode()


def size_of(path, objgen=None, name="/Im0"):
    with pikepdf.open(path) as pdf:
        img = pdf.pages[0].resources.XObject[name]
        return int(img.Width), int(img.Height)


def test_an_image_over_450_ppi_comes_to_300(make, tmp_path):
    # 600 px across 72 pt (1 inch) = 600 ppi; the second image, 200 px on 1 inch, is left.
    src = make(placed("Im0", 60, 60, 72) + placed("Im1", 200, 60, 72),
               xobject={"/Im0": flate_image(noise(600, 600, 4), pikepdf.Name.DeviceCMYK),
                        "/Im1": flate_image(noise(200, 200, 4), pikepdf.Name.DeviceCMYK)})
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src)
    assert p.reasons == [(1, "Downsamples 1 image(s) placed over 450 ppi to 300 ppi; the highest is 600 ppi "
                             "(600 × 600 px, to 300 × 300 px).")]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert size_of(dst) == (300, 300) and size_of(dst, name="/Im1") == (200, 200)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    notes = [f.message for f in run(src).findings if f.check == "Image resolution" and "over 450" in f.message]
    assert notes == ["1 image(s) over 450 ppi, more than the press can use; highest is 600 ppi. "
                     "Downsampling would make the file smaller"]
    assert not [f for f in run(dst).findings if f.check == "Image resolution" and "over 450" in f.message]


def test_an_image_also_placed_large_is_judged_by_that_use(make):
    # 600 px at 2 inches is 300 ppi there, so the small 1000 ppi use does not bring it down.
    src = make(placed("Im0", 60, 60, 144) + placed("Im0", 250, 60, 43.2),
               xobject={"/Im0": flate_image(noise(600, 600, 3), pikepdf.Name.DeviceRGB)})
    p = propose(src)
    assert p.empty and p.skipped == [(1, "no image is placed at over 450 ppi")]


def test_large_format_settings_and_the_resolution_warning_said(make, tmp_path):
    src = make(placed("Im0", 60, 60, 72), xobject={"/Im0": flate_image(noise(300, 300, 1), pikepdf.Name.DeviceGray)})
    p = propose(src, max_ppi=200, to_ppi=100)
    assert "to 100 ppi" in p.reasons[0][1] and "under the Image resolution check's 150 ppi" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert size_of(dst) == (100, 100)


def test_a_jpeg_stays_a_jpeg(make, tmp_path):
    src = make(placed("Im0", 60, 60, 72), xobject={"/Im0": jpeg_image(noise(800, 600, 3))})
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    with pikepdf.open(src) as a, pikepdf.open(dst) as b:
        old, new = a.pages[0].resources.XObject.Im0, b.pages[0].resources.XObject.Im0
        assert new.Filter == pikepdf.Name.DCTDecode and (int(new.Width), int(new.Height)) == (400, 300)  # judged by the lower ppi side
        assert len(new.read_raw_bytes()) < len(old.read_raw_bytes())


def test_target_above_the_threshold_is_refused(make):
    src = make(placed("Im0", 60, 60, 72), xobject={"/Im0": flate_image(noise(600, 600, 1), pikepdf.Name.DeviceGray)})
    with pytest.raises(ValueError, match="--to-ppi must be at least 72 and no more than --max-ppi"):
        propose(src, max_ppi=300, to_ppi=400)


def test_indexed_images_are_listed_and_left(make):
    palette = pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 255, pikepdf.String(bytes(range(256)) * 3)])
    src = make(placed("Im0", 60, 60, 72), xobject={"/Im0": flate_image(noise(600, 600, 1), palette)})
    p = propose(src)
    assert p.empty and p.skipped == [(1, "1 image(s) over 450 ppi left as they are: indexed (palette) image(s)")]


def test_colour_hidden_under_a_soft_mask_does_not_fringe_the_edge(make, tmp_path):
    # White artwork on the left half; under the transparent right half the colour is black.
    w = h = 600
    colour = np.zeros((h, w, 3), np.uint8)
    colour[:, : w // 2] = 255
    alpha = np.zeros((h, w), np.uint8)
    alpha[:, : w // 2] = 255

    def with_mask(pdf):
        mask = pdf.make_stream(zlib.compress(alpha.tobytes()), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=w, Height=h, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8,
                               Filter=pikepdf.Name.FlateDecode)
        img = flate_image(noise(w, h, 3), pikepdf.Name.DeviceRGB, SMask=mask)(pdf)
        img.write(zlib.compress(colour.tobytes()), filter=pikepdf.Name.FlateDecode)
        return img
    src = make(placed("Im0", 60, 60, 72), xobject={"/Im0": with_mask})
    from prepress_preflight.fixes import downsample_images
    pymupdf.TOOLS.set_icc(False)
    with pikepdf.open(src) as pdf, pymupdf.open(src) as mu:
        img = pdf.pages[0].resources.XObject.Im0
        out = np.asarray(downsample_images._resample_under_mask(mu, img.SMask, downsample_images._pixels(mu, img), 3,
                                                                (300, 300)))
    edge = out[:, 148:150]  # the last visible columns, right against the transparent half
    assert edge.min() >= 250  # still white: no black pulled in from under the mask
    plain = np.asarray(Image.fromarray(colour, "RGB").resize((300, 300), Image.LANCZOS))
    assert plain[:, 148:150].min() < 250  # a plain resample would have darkened it


def test_a_wrong_image_is_refused(make, tmp_path, monkeypatch):
    from prepress_preflight.fixes import downsample_images
    src = make(placed("Im0", 60, 60, 72), xobject={"/Im0": flate_image(noise(600, 600, 4), pikepdf.Name.DeviceCMYK)})
    p = propose(src)
    real = downsample_images._build

    def inverted(pdf, mu, key, size):
        s = real(pdf, mu, key, size)
        s.write(zlib.compress(bytes(255 - b for b in zlib.decompress(s.read_raw_bytes()))), filter=pikepdf.Name.FlateDecode)
        return s
    monkeypatch.setattr(downsample_images, "_build", inverted)
    with pytest.raises(AppendError, match="does not look as it did"):
        write(p, str(tmp_path / "out.pdf"))
