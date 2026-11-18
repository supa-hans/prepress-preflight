"""knock-out-white: every white object set to overprint, knocked out."""
import hashlib

import pikepdf
import pymupdf

from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.plates import render_channels

from test_spots import spot

propose, write = FIXES["knock-out-white"]
OP = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}


def overprints(path, page=0):
    with pikepdf.open(path) as pdf:
        return [e.overprint for e in walk_page(pdf.pages[page]).events]


def white_fails(path):
    return [f.message for f in run(path).findings if f.check == "White overprint"]


def helvetica(pdf):
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)


def plate(path, ink, box, zoom=1):
    with pymupdf.open(path) as doc:
        names, data = render_channels(doc.load_page(0), zoom)
        h = data.shape[0]
        x0, y0, x1, y1 = box
        return float(data[int(h - y1) + 2:int(h - y0) - 2, int(x0) + 2:int(x1) - 2, names.index(ink)].mean())


def test_all_white_overprint_on_a_page(make, tmp_path):
    content = (b"/CS0 cs 1 scn 50 50 300 100 re f "  # silver box (not white, not overprinting)
               b"/GS0 gs 0 0 0 0 k 60 60 20 20 re f "  # white fill, overprint: fixed
               b"BT /F1 12 Tf 100 60 Td (A) Tj ET "  # white text, overprint: fixed
               b"0 0 0 0 K 2 w 150 60 m 200 60 l S "  # white stroke, overprint: fixed
               b"0 0 0 1 k 250 60 20 20 re f")  # black, overprint: left alone
    src = make(content, colorspace={"/CS0": spot("Silver")}, extgstate=OP, font={"/F1": helvetica})
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert white_fails(src) == ["White set to overprint, it will not print: 2 vector object(s), 1 text object(s)"]
    p = propose(src)
    assert "Knocks out white set to overprint: 2 vector object(s), 1 text object(s)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert overprints(dst) == [False, False, False, False, True]
    assert white_fails(dst) == []
    assert plate(src, "Silver", (60, 60, 80, 80)) == 255 and plate(dst, "Silver", (60, 60, 80, 80)) == 0
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_a_clip_is_kept(make, tmp_path):
    # a white box that also clips, then black painted everywhere: the clip must still hold
    src = make(b"/GS0 gs 0 0 0 0 k 100 100 50 50 re W f 0 0 0 1 k 0 0 400 300 re f", extgstate=OP)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    assert overprints(dst) == [False, True]  # the black still overprints: its state was put back
    assert plate(dst, "Black", (10, 10, 60, 60)) == 0  # outside the clip: no black, as before
    assert plate(src, "Black", (10, 10, 60, 60)) == 0
    with pikepdf.open(dst) as pdf:
        ops = [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0])]
    assert "q" not in ops  # the clip case restores the state with an ExtGState, not q/Q


def form(content, indirect=True):
    def make_form(pdf):
        f = pdf.make_stream(content, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 20, 20],
                            Resources=pikepdf.Dictionary())
        return pdf.make_indirect(f) if indirect else f
    return make_form


def test_a_form_painted_twice_is_fixed_at_both_places(make, tmp_path):
    src = make(b"/GS0 gs q 1 0 0 1 100 100 cm /Fm0 Do Q q 1 0 0 1 200 100 cm /Fm0 Do Q",
               xobject={"/Fm0": form(b"0 0 0 0 k 0 0 20 20 re f")}, extgstate=OP)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    assert overprints(dst) == [False, False]


def test_a_form_that_is_not_always_white_is_left(make):
    # the form sets no colour: white at one place, black at the other
    src = make(b"/GS0 gs 0 0 0 0 k q 1 0 0 1 100 100 cm /Fm0 Do Q 0 0 0 1 k q 1 0 0 1 200 100 cm /Fm0 Do Q",
               xobject={"/Fm0": form(b"0 0 20 20 re f")}, extgstate=OP)
    p = propose(src)
    assert p.empty and "also painted where it is not white" in p.skipped[0][1]


def test_text_in_a_form_whose_uses_disagree_is_left(make):
    # white text in a form, overprinting at one place, not at the other: the state after it is not known
    font = {"/F1": helvetica}

    def text_form(pdf):
        return pdf.make_indirect(pdf.make_stream(b"BT /F1 12 Tf 0 0 0 0 k 0 5 Td (A) Tj ET", Type=pikepdf.Name.XObject,
                                                 Subtype=pikepdf.Name.Form, BBox=[0, 0, 20, 20],
                                                 Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helvetica(pdf)))))
    src = make(b"q /GS0 gs 1 0 0 1 100 100 cm /Fm0 Do Q q 1 0 0 1 200 100 cm /Fm0 Do Q",
               xobject={"/Fm0": text_form}, extgstate=OP, font=font)
    p = propose(src)
    assert p.empty and "disagree on overprint" in p.skipped[0][1]


def test_nothing_to_do(make):
    p = propose(make(b"0 0 0 0 k 100 100 50 50 re f"))
    assert p.empty and p.skipped == [(1, "no white objects are set to overprint")]
