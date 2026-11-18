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


# ---- full details of one object ----------------------------------------------

from prepress_preflight.objects import icc_description, object_details  # noqa: E402

from conftest import TRIM, MM, gray_image  # noqa: E402


def details(path, x, y, which=0):
    with pikepdf.open(path) as pdf, pymupdf.open(path) as doc:
        hit = objects_at(pdf.pages[0], doc.load_page(0), x, y)[which]
        return object_details(pdf.pages[0], doc.load_page(0), hit["id"])


def test_details_of_a_spot_colour_give_its_cmyk_equivalent(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0.6, 0], N=1)
    spot = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 186 C"), pikepdf.Name.DeviceCMYK, tint])
    path = make(b"/CS0 cs 0.5 scn 100 100 50 50 re f", colorspace={"/CS0": spot})
    d = details(path, 125, HEIGHT - 125)
    assert d["space"] == {"resource": "CS0", "space": "Separation", "inks": ["PANTONE 186 C"], "alternate": "DeviceCMYK",
                          "tint_transform": "exponential", "cmyk_equivalent": "C0 M50 Y30 K0", "equivalent_at": "PANTONE 186 C 50%"}
    assert d["source"] == {"stream": "page", "instruction": 4, "excerpt": ["/CS0 cs", "0.5 scn", "100 100 50 50 re", "f"]}


def test_details_give_place_from_the_trim_in_mm(make):
    # a box 10 mm in from the trim's left and 5 mm down from its top, 20 x 8 mm
    x0, top = TRIM[0] + 10 * MM, TRIM[3] - 5 * MM
    path = make(f"0 0 0 1 k {x0:.4f} {top - 8 * MM:.4f} {20 * MM:.4f} {8 * MM:.4f} re f".encode())
    d = details(path, x0 + 5, HEIGHT - top + 5)
    place = d.pop("place")
    assert place.pop("trim") == "trim box"
    assert place == pytest.approx({"width_mm": 20, "height_mm": 8, "from_trim_left_mm": 10, "from_trim_top_mm": 5}, abs=0.01)
    assert d["space"] == {"space": "DeviceCMYK"}


def test_details_of_the_graphics_state(make):
    gs = {"/GS0": pikepdf.Dictionary(OP=True, OPM=1), "/GS1": pikepdf.Dictionary(ca=0.5, BM=pikepdf.Name.Screen)}
    path = make(b"/GS0 gs /GS1 gs /GS0 gs 0 0 0 1 k 100 100 50 50 re f", extgstate=gs)
    s = details(path, 125, HEIGHT - 125)["state"]
    assert s == {"overprint_fill": True, "overprint_stroke": True, "overprint_mode": 1, "blend": "Screen",
                 "opacity_fill": 0.5, "opacity_stroke": 1.0, "soft_mask": False, "extgstates": ["GS1", "GS0"]}


def test_details_of_text(make):
    def font(pdf):
        desc = pikepdf.Dictionary(Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/ABCDEF+Frutiger-Roman"),
                                  FontFile3=pdf.make_stream(b"x"))
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                  BaseFont=pikepdf.Name("/ABCDEF+Frutiger-Roman"), FontDescriptor=pdf.make_indirect(desc))
    # 10 pt type in a form scaled 1.5x prints at 15 pt
    path = make(b"q 1.5 0 0 1.5 0 0 cm BT /F1 10 Tf 0 0 0 1 k 70 70 Td (Hello) Tj ET Q", font={"/F1": font})
    d = details(path, 110, HEIGHT - 110)
    assert d["text"] == {"name": "Frutiger-Roman", "type": "Type1", "size": 15.0, "embedded": True, "sample": "Hello"}


def test_details_of_an_image(make):
    path = make(b"q 72 0 0 72 100 100 cm /Im0 Do Q", xobject={"/Im0": gray_image(100, 100)})
    d = details(path, 130, HEIGHT - 130)
    assert d["image"] == {"name": "Im0", "bits": 8, "compression": "FlateDecode", "soft_mask": False, "mask": False, "interpolate": False}
    assert d["space"] == {"space": "DeviceGray"}
    assert d["place"]["width_mm"] == pytest.approx(25.4, abs=0.01)


def test_details_of_an_object_that_is_not_there(make):
    path = make(b"0 0 0 1 k 100 100 50 50 re f")
    with pikepdf.open(path) as pdf, pymupdf.open(path) as doc, pytest.raises(ValueError):
        object_details(pdf.pages[0], doc.load_page(0), 5)


def _icc(desc: bytes, kind=b"desc") -> bytes:
    if kind == b"desc":
        tag = b"desc" + b"\0" * 4 + len(desc).to_bytes(4, "big") + desc
    else:
        text = desc[:-1].decode().encode("utf-16-be")
        tag = b"mluc" + b"\0" * 4 + (1).to_bytes(4, "big") + (12).to_bytes(4, "big") + b"enUS" + \
            len(text).to_bytes(4, "big") + (28).to_bytes(4, "big") + text
    header = bytearray(128)
    table = (1).to_bytes(4, "big") + b"desc" + (144).to_bytes(4, "big") + len(tag).to_bytes(4, "big")
    return bytes(header) + table + tag


@pytest.mark.parametrize("kind", [b"desc", b"mluc"])
def test_icc_profile_names(kind):
    pdf = pikepdf.new()
    stream = pdf.make_stream(_icc(b"Coated FOGRA39 (ISO 12647-2:2004)\0", kind), N=4)
    assert icc_description(stream) == "Coated FOGRA39 (ISO 12647-2:2004)"
    assert icc_description(pdf.make_stream(b"short")) is None
