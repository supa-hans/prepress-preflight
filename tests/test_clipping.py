"""Clipping: an object's position is cut to the clip, and an object wholly clipped away is not judged."""
import pikepdf

from prepress_preflight.content import walk_page
from prepress_preflight.engine import run

OP = {"/GS1": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
WHITE_OP_BOX = b"/GS1 gs 0 0 0 0 k 150 100 10 10 re f"
INK = b"0 0 0 1 k 60 60 20 20 re f "


def helvetica(pdf):
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)


def white_op_fails(path):
    return [f.message for f in run(path).findings if f.check == "White overprint"]


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def test_an_object_outside_the_clip_is_not_judged(make):
    assert white_op_fails(make(INK + WHITE_OP_BOX, extgstate=OP))
    assert not white_op_fails(make(INK + b"q 40 40 50 50 re W n " + WHITE_OP_BOX + b" Q", extgstate=OP))


def test_the_clip_ends_with_its_graphics_state(make):
    assert white_op_fails(make(INK + b"q 40 40 50 50 re W n Q " + WHITE_OP_BOX, extgstate=OP))


def test_a_form_is_clipped_to_its_bounding_box(make):
    def form(pdf):
        return pdf.make_stream(WHITE_OP_BOX, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 20, 20],
                               Resources=pikepdf.Dictionary(ExtGState=pikepdf.Dictionary(OP)))
    assert not white_op_fails(make(INK + b"/Fm0 Do", xobject={"/Fm0": form}))


def test_text_that_may_run_into_the_clip_is_still_judged(make):
    # the run starts left of the clip; its first em is outside, but ten characters reach in
    text = b"q 100 40 100 100 re W n /GS1 gs 0 0 0 0 k BT /F1 10 Tf 60 80 Td (white text) Tj ET Q"
    assert white_op_fails(make(INK + text, extgstate=OP, font={"/F1": helvetica}))


def test_a_partly_clipped_object_is_placed_where_it_shows(make):
    e = events(make(b"q 100 100 50 50 re W n 0 0 0 1 k 80 80 200 200 re f Q"))[0]
    assert not e.hidden and e.clip == (100, 100, 150, 150)
    assert [round(v, 1) for v in e.bbox] == [100, 100, 150, 150]


def test_a_hidden_spot_is_still_listed(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 0.5, 1, 0], N=1)
    orange = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 151 C"), pikepdf.Name.DeviceCMYK, tint])
    path = make(INK + b"q 40 40 50 50 re W n /Or cs 1 scn 150 100 10 10 re f Q", colorspace={"/Or": orange})
    assert events(path)[1].hidden
    assert any("PANTONE 151 C" in f.message for f in run(path).findings if f.check == "Spot colours")


def test_a_short_run_that_cannot_reach_the_clip_is_hidden(make):
    text = b"q 100 40 100 100 re W n /GS1 gs 0 0 0 0 k BT /F1 10 Tf 60 80 Td (w) Tj ET Q"
    assert not white_op_fails(make(INK + text, extgstate=OP, font={"/F1": helvetica}))


def test_fixes_leave_objects_that_are_clipped_away(make, tmp_path):
    from prepress_preflight.fixes import FIXES
    propose, write = FIXES["rich-black-text"]
    rich = b"0.75 0.68 0.67 0.9 k BT /F1 6 Tf "
    shown = rich + b"60 80 Td (a) Tj ET "
    clipped = b"q 40 40 10 10 re W n " + rich + b"150 100 Td (b) Tj ET Q"
    src = make(INK + shown + clipped, font={"/F1": helvetica})
    p = propose(src)
    assert p.reasons == [(1, "Makes small rich-black text (up to 8 pt) K100 only: 1 text object(s). "
                             "One plate instead of four, so it prints sharp whatever the registration.")]
    assert p.skipped == [(1, "1 small rich-black text object(s) left as they are: clipped away, so it does not print")]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    colours = [e.colour for e in events(dst) if e.kind == "text"]
    assert colours == [(0.0, 0.0, 0.0, 1.0), (0.75, 0.68, 0.67, 0.9)]


def test_shapes_clipped_away_leave_the_box(make):
    # one path, two squares; the clip keeps only the left one
    e = events(make(b"q 50 50 40 40 re W n 0 0 0 1 k 60 60 10 10 re 120 60 10 10 re f Q"))[0]
    assert [round(v) for v in e.bbox] == [60, 60, 70, 70]
