"""Reversed small text: white type knocked out of two or more inks."""
import pikepdf

from prepress_preflight.engine import run
from prepress_preflight.model import NOTE, WARN


def helvetica(pdf):
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)


def page(make, background: bytes, size: float, extra=b"", **kw):
    content = (background + b" 60 60 240 60 re f BT /F1 " + f"{size}".encode() +
               b" Tf 0 0 0 0 k " + extra + b"70 80 Td (Reversed text here) Tj ET")
    return make(content, font={"/F1": helvetica}, **kw)


def found(path):
    return [(f.severity, f.message) for f in run(path).findings if f.check == "Reversed small text"]


def test_six_point_white_out_of_two_inks_warns(make):
    got = found(page(make, b"1 0.7 0 0 k", 6))
    assert got == [(WARN, "White text up to 7 pt knocked out of two or more inks: 1 text object(s), smallest 6.0 pt, "
                          "mostly out of C100 M70. Misregistration can fill fine strokes in")]


def test_nine_point_is_a_note(make):
    got = found(page(make, b"1 0.7 0 0 k", 9))
    assert [s for s, _ in got] == [NOTE] and "7 to 10 pt" in got[0][1]


def test_twelve_point_is_not_small(make):
    assert found(page(make, b"1 0.7 0 0 k", 12)) == []


def test_one_ink_or_paper_behind_is_fine(make):
    assert found(page(make, b"0 0 0 1 k", 6)) == []
    assert found(page(make, b"0 0 0 0 k", 6, name="paper.pdf")) == []


def test_a_light_second_ink_does_not_count(make):
    assert found(page(make, b"1 0.2 0 0 k", 6)) == []  # M20 is under 30%


def test_white_set_to_overprint_is_not_reversed(make):
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
    assert found(page(make, b"1 0.7 0 0 k", 6, extra=b"/GS0 gs ", extgstate=gs)) == []


def test_a_spot_ink_counts_as_a_plate(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 0.5, 1, 0], N=1)
    orange = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 151 C"), pikepdf.Name.DeviceCMYK, tint])
    # cyan box, then the spot on top set to overprint, so both plates print behind the text
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1), "/GS1": pikepdf.Dictionary(OP=False, op=False)}
    bg = b"1 0 0 0 k 60 60 240 60 re f /GS0 gs /Or cs 1 scn"  # the helper paints the box in it
    got = found(page(make, bg, 6, extra=b"/GS1 gs ", colorspace={"/Or": orange}, extgstate=gs))
    assert got and "C100 PANTONE 151 C 100" in got[0][1]


def test_a_non_printing_spot_does_not_count(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    die = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Dieline"), pikepdf.Name.DeviceCMYK, tint])
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1), "/GS1": pikepdf.Dictionary(OP=False, op=False)}
    bg = b"1 0 0 0 k 60 60 240 60 re f /GS0 gs /Die cs 1 scn"
    assert found(page(make, bg, 6, extra=b"/GS1 gs ", colorspace={"/Die": die}, extgstate=gs)) == []
