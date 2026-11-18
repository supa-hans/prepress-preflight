"""device_matrix: PDF page coordinates to rendered pixels, for every rotation,
with and without a separate crop box and a UserUnit. PyMuPDF's own matrix gets
rotated pages with an offset origin wrong, and rotated UserUnit pages."""
import numpy as np
import pikepdf
import pymupdf
import pytest

from prepress_preflight.document import device_matrix

from conftest import build_pdf


@pytest.mark.parametrize("unit", [None, 2])
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [None, (-60.0, -40.0, 400.0, 270.0)])
def test_square_renders_where_the_matrix_says(tmp_path, unit, rotate, crop):
    path = str(tmp_path / "p.pdf")
    build_pdf(path, b"0 0 0 1 k 0 0 10 10 re f", (-85.0, -56.7, 425.2, 283.5), None, None,
              rotate=rotate or None, user_unit=unit)
    if crop:
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.pages[0].obj["/CropBox"] = list(crop)
            pdf.save(path)
    page = pymupdf.open(path)[0]
    pix = page.get_pixmap(dpi=72)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    ys, xs = np.nonzero(a.max(axis=2) < 128)
    r = (pymupdf.Rect(0, 0, 10, 10) * device_matrix(page)).normalize()
    assert abs(xs.min() - r.x0) <= 1.5 and abs(ys.min() - r.y0) <= 1.5
    assert abs(xs.max() + 1 - r.x1) <= 1.5 and abs(ys.max() + 1 - r.y1) <= 1.5
