"""Separations: each plate of a page on its own, overprint honoured."""
import pikepdf
import pymupdf
import pytest

from prepress_preflight.plates import ink_coverage, page_inks, render_channels, render_plate

from test_spots import spot


def page_of(path):
    return pymupdf.open(path)[0]


def at(data, names, ink, x, y, page_height, zoom):
    """Ink value of one plate at a PDF point (bottom-left origin)."""
    return int(data[int((page_height - y) * zoom), int(x * zoom), names.index(ink)])


def test_process_and_spot_inks_are_listed(make):
    page = page_of(make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace={"/CS0": spot("PANTONE 185 C")}))
    inks = page_inks(page)
    assert [i.name for i in inks] == ["Cyan", "Magenta", "Yellow", "Black", "PANTONE 185 C"]
    assert not inks[-1].process and inks[-1].printing


def test_a_spot_is_on_its_own_plate_only(make):
    page = page_of(make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace={"/CS0": spot("PANTONE 185 C", (0, 1, 0, 0))}))
    zoom = 1.0
    names, data = render_channels(page, zoom)
    h = page.rect.height
    assert at(data, names, "PANTONE 185 C", 125, 125, h, zoom) > 250
    assert at(data, names, "Magenta", 125, 125, h, zoom) < 5  # not on the process plate it looks like


def test_process_colour_splits_across_plates(make):
    page = page_of(make(b"0.5 0 0 1 k 100 100 50 50 re f"))
    zoom = 1.0
    names, data = render_channels(page, zoom)
    h = page.rect.height
    assert abs(at(data, names, "Cyan", 125, 125, h, zoom) - 128) <= 2
    assert at(data, names, "Black", 125, 125, h, zoom) > 250
    assert at(data, names, "Magenta", 125, 125, h, zoom) < 5


def test_overprint_keeps_the_ink_beneath(make):
    # black overprinting cyan: the cyan plate keeps its ink under the black
    gs = {"/OP": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
    page = page_of(make(b"1 0 0 0 k 100 100 80 80 re f /OP gs 0 0 0 1 k 120 120 40 40 re f", extgstate=gs))
    names, data = render_channels(page, 1.0)
    h = page.rect.height
    assert at(data, names, "Cyan", 140, 140, h, 1.0) > 250
    assert at(data, names, "Black", 140, 140, h, 1.0) > 250


def test_knockout_without_overprint(make):
    page = page_of(make(b"1 0 0 0 k 100 100 80 80 re f 0 0 0 1 k 120 120 40 40 re f"))
    names, data = render_channels(page, 1.0)
    assert at(data, names, "Cyan", 140, 140, page.rect.height, 1.0) < 5


def test_plate_png_is_ink_dark_on_white(make):
    page = page_of(make(b"0 0 0 1 k 100 100 50 50 re f"))
    png = render_plate(page, "Black", 1.0)
    pix = pymupdf.Pixmap(png)
    assert pix.n == 1
    assert pix.pixel(int(125), int(page.rect.height - 125))[0] < 5   # ink: dark
    assert pix.pixel(10, 10)[0] > 250                               # paper: white
    with pytest.raises(ValueError):
        render_plate(page, "PANTONE 999 C", 1.0)


def test_rotated_page_plates_match_the_normal_render(make):
    page = page_of(make(b"0 0 0 1 k 100 100 50 50 re f", rotate=90))
    _, data = render_channels(page, 1.0)
    pix = page.get_pixmap(dpi=72)
    assert data.shape[:2] == (pix.height, pix.width)


def test_non_printing_spot_is_marked(make):
    page = page_of(make(b"/CS0 CS 1 SCN 100 100 m 200 100 l S", colorspace={"/CS0": spot("Dieline")}))
    assert not page_inks(page)[-1].printing


def test_coverage(make):
    from conftest import MEDIA
    page = page_of(make(b"0 0 0 1 k 0 0 1000 1000 re f"))
    assert ink_coverage(page)["Black"] == 100.0
    assert ink_coverage(page)["Cyan"] == 0.0
