"""registration-to-black: registration colour inside the trim made black."""
import hashlib

import pikepdf

from prepress_preflight.content import CMYK, walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES

from conftest import MEDIA, MM, TRIM, registration_space
from test_marks_and_dielines import crop_marks

propose, write = FIXES["registration-to-black"]
REG = {"/CS0": registration_space()}


def colours(path):
    with pikepdf.open(path) as pdf:
        return [(e.cs.kind, tuple(round(v, 3) for v in e.colour or ())) for e in walk_page(pdf.pages[0]).events]


def reg_fails(path):
    return [f.message for f in run(path).findings if f.check == "Registration inside trim" and f.severity == "fail"]


def test_artwork_in_registration_becomes_black_and_marks_stay(make, tmp_path):
    # crop marks (registration, outside the trim) then a registration box and a 50% rule inside it
    content = (crop_marks(TRIM) + b"/CS0 cs 1 scn 100 100 50 50 re f "
               b"/CS0 CS 0.5 SCN 1 w 100 170 m 200 170 l S")
    src = make(content, colorspace=REG)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert reg_fails(src) == ["2 object(s) in registration colour inside the trim"]
    p = propose(src)
    assert "Makes registration inside the trim black at the same tint: 2 vector object(s)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    got = colours(dst)
    assert got[-2:] == [("cmyk", (0, 0, 0, 1)), ("cmyk", (0, 0, 0, 0.5))]
    assert all(kind == "sep" for kind, _ in got[:-2])  # the marks are still registration
    assert reg_fails(dst) == []
    assert any(f.check == "Trim marks" for f in run(dst).findings)  # marks still found
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_text_in_registration(make, tmp_path):
    def font(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    src = make(b"BT /F1 12 Tf /CS0 cs 1 scn 100 100 Td (A) Tj 20 0 Td (B) Tj ET", colorspace=REG, font={"/F1": font})
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    assert colours(dst) == [("cmyk", (0, 0, 0, 1)), ("cmyk", (0, 0, 0, 1))]
    with pikepdf.open(dst) as pdf:
        ops = [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0])]
    # in a text block: black set before each run and the registration put back after
    assert ops.count("k") == 2 and "q" not in ops and ops.count("scn") == 3


def test_a_path_that_is_marks_and_artwork_together_is_left(make):
    # one path: a short mark outside the trim and a box inside it
    x, y = TRIM[0], TRIM[1]
    content = (f"/CS0 CS 1 w {x:.2f} {y - 8 * MM:.2f} m {x:.2f} {y - 3 * MM:.2f} l "
               f"{x + 50:.2f} {y + 50:.2f} m {x + 100:.2f} {y + 50:.2f} l S").encode()
    p = propose(make(content, colorspace=REG))
    assert p.empty and "also draws outside the trim" in p.skipped[0][1]


def test_a_form_also_used_outside_the_trim_is_left(make):
    form = lambda pdf: pdf.make_indirect(pdf.make_stream(b"/CS0 cs 1 scn 0 0 5 5 re f", Type=pikepdf.Name.XObject,
                                                         Subtype=pikepdf.Name.Form, BBox=[0, 0, 5, 5],
                                                         Resources=pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(CS0=registration_space()))))
    # once inside the trim, once in the slug
    src = make(b"q 1 0 0 1 100 100 cm /Fm0 Do Q q 1 0 0 1 5 5 cm /Fm0 Do Q", xobject={"/Fm0": form})
    p = propose(src)
    assert p.empty and "also used outside the trim" in p.skipped[0][1]


def test_measured_from_the_trim_the_marks_show(make, tmp_path):
    # the banner case: trim box = whole page, marks show the real trim; a registration box in the slug
    # is outside the real trim and must stay registration
    marks = crop_marks(TRIM, offset=1 * MM, length=6 * MM)
    content = marks + b"/CS0 cs 1 scn 5 5 10 10 re f 100 100 50 50 re f"
    src = make(content, colorspace=REG, trim=MEDIA, bleed=MEDIA)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    got = colours(dst)
    assert got[-2] == ("sep", (1,)) and got[-1] == ("cmyk", (0, 0, 0, 1))


def test_nothing_to_do(make):
    p = propose(make(crop_marks(TRIM), colorspace=REG))
    assert p.empty and p.skipped == [(1, "no registration colour inside the trim")]
