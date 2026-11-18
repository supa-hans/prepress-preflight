"""What is under a point on the page: the object inspector's engine."""
import pikepdf
import pymupdf
import pytest

from prepress_preflight.objects import objects_at

from conftest import MEDIA, registration_space

HEIGHT = MEDIA[3] - MEDIA[1]


def at(path, x, y, **kw):
    with pikepdf.open(path) as pdf, pymupdf.open(path) as doc:
        return objects_at(pdf.pages[0], doc.load_page(0), x, y, **kw)


def test_topmost_object_comes_first(make):
    # a black box, then a cyan box painted over part of it
    path = make(b"0 0 0 1 k 100 100 100 100 re f 1 0 0 0 k 150 150 100 100 re f")
    # (175, 175) in PDF points is inside both; the view measures y from the top
    hits = at(path, 175, HEIGHT - 175)
    assert [h["colour"] for h in hits] == ["C100 M0 Y0 K0", "C0 M0 Y0 K100"]
    assert hits[0]["kind"] == "Filled shape" and hits[0]["ink"] == 100.0
    assert hits[0]["view"] == pytest.approx([150, HEIGHT - 250, 250, HEIGHT - 150], abs=0.05)
    # only the black box at (120, 120)
    assert [h["colour"] for h in at(path, 120, HEIGHT - 120)] == ["C0 M0 Y0 K100"]


def test_nothing_painted_here(make):
    assert at(make(b"0 0 0 1 k 100 100 10 10 re f"), 300, 50) == []


def test_overprint_blend_and_spot_colours(make):
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1), "/GS1": pikepdf.Dictionary(BM=pikepdf.Name.Multiply)}
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0.6, 0], N=1)
    spot = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 186 C"), pikepdf.Name.DeviceCMYK, tint])
    path = make(b"q /GS0 gs /CS0 cs 0.5 scn 100 100 50 50 re f Q "
                b"q /GS1 gs /CS1 cs 1 scn 100 100 50 50 re f Q",
                extgstate=gs, colorspace={"/CS0": spot, "/CS1": registration_space()})
    top, under = at(path, 125, HEIGHT - 125)
    assert top["colour"] == "Registration 100%" and top["blend"] == "Multiply" and not top["overprint"]
    assert under["colour"] == "PANTONE 186 C 50%" and under["overprint"] and under["ink"] is None


def test_lines_get_their_printed_width(make):
    # a 0.5 pt line drawn inside a form scaled 2x prints 1 pt wide
    form = lambda pdf: pdf.make_stream(b"0 0 0 1 K 0.5 w 0 0 m 50 0 l S", Type=pikepdf.Name.XObject,
                                       Subtype=pikepdf.Name.Form, BBox=[-10, -10, 60, 10])
    path = make(b"q 2 0 0 2 100 100 cm /Fm0 Do Q", xobject={"/Fm0": form})
    (line,) = at(path, 150, HEIGHT - 100)
    assert line["kind"] == "Line" and line["width"] == pytest.approx(1.0) and line["inside"] == "Fm0"


def test_objects_in_a_blend_group_say_so(make):
    # the pull-up banner fault: a K100 Multiply group over a K100 box
    form = lambda pdf: pdf.make_stream(b"0 0 0 1 k 0 0 50 50 re f", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                                       BBox=[0, 0, 50, 50], Group=pikepdf.Dictionary(S=pikepdf.Name.Transparency))
    gs = {"/GS0": pikepdf.Dictionary(BM=pikepdf.Name.Multiply)}
    path = make(b"0 0 0 1 k 100 100 50 50 re f q /GS0 gs 1 0 0 1 100 100 cm /Fm0 Do Q", xobject={"/Fm0": form}, extgstate=gs)
    top, under = at(path, 125, HEIGHT - 125)
    assert top["blend"] == "Normal" and top["group_blend"] == "Multiply" and top["inside"] == "Fm0 (Multiply group)"
    assert "group_blend" not in under and "inside" not in under


def test_text_names_its_font(make):
    def font(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/ABCDEF+Frutiger-Roman"))
    path = make(b"BT /F1 20 Tf 0 0 0 1 k 100 100 Td (Hello) Tj ET", font={"/F1": font})
    (text,) = at(path, 110, HEIGHT - 105)
    assert text["kind"] == "Text" and text["font"] == "Frutiger-Roman"


def test_images_report_size_and_resolution(make):
    from conftest import gray_image
    # 100 x 100 pixels drawn 1 inch square: 100 ppi
    path = make(b"q 72 0 0 72 100 100 cm /Im0 Do Q", xobject={"/Im0": gray_image(100, 100)})
    (img,) = at(path, 130, HEIGHT - 130)
    assert img["kind"] == "Image" and img["pixels"] == [100, 100] and img["ppi"] == 100 and img["colour"] == "in Greyscale"


def test_rotated_page_points_map_back(make):
    # turned 90 degrees: the rendered page's top-left is the PDF's bottom-left
    path = make(b"0 0 0 1 k 100 100 50 50 re f", rotate=90)
    (hit,) = at(path, 125, 125)
    assert hit["view"] == pytest.approx([100, 100, 150, 150], abs=0.05)
    assert at(path, 125, HEIGHT - 125 + 300) == []
