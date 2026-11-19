"""thicken-hairlines: open lines under 0.25 pt made 0.25 pt; outlines of shapes left."""
import hashlib

import pikepdf
import pytest

from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.thicken_hairlines import _openness

propose, write = FIXES["thicken-hairlines"]


def widths(path):
    with pikepdf.open(path) as pdf:
        return [round(e.width, 3) for e in walk_page(pdf.pages[0]).events if e.paint == "stroke"]


def hairline_warns(path):
    return [f.message for f in run(path).findings if f.check == "Hairlines"]


def test_open_lines_are_thickened_and_others_left(make, tmp_path):
    content = (b"0 0 0 1 K 0.1 w 100 100 m 200 100 l S "  # open thin rule: thickened
               b"0.5 w 100 120 m 200 120 l S "  # 0.5 pt: fine already
               b"0.1 w 100 140 m 200 140 l S "  # thin again: thickened, and the 0.1 after it kept
               b"100 150 m 200 160 l S")
    src = make(content)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src)
    assert p.reasons == [(1, "Thickens 3 line(s) thinner than 0.25 pt to 0.25 pt as printed.")]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert widths(dst) == [0.25, 0.5, 0.25, 0.25]
    assert hairline_warns(dst) == []
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_outlines_of_shapes_are_left(make):
    content = (b"0 0 0 1 K 0.03 w 100 100 20 20 re S "  # rectangle outline
               b"130 100 m 150 100 l 150 120 l h S "  # closed with h
               b"160 100 m 180 100 l 180 120 l 160 100 l S "  # ends where it started
               b"190 100 m 210 100 l 210 120 l s "  # closed by s
               b"220 100 m 240 100 l 230 110 5 5 re S")  # open line and a rectangle in one path
    p = propose(make(content))
    assert p.empty
    assert sorted(p.skipped) == [(1, "1 thin line(s) left as they are: the same path also outlines a closed shape"),
                                 (1, "4 thin line(s) left as they are: outlines a closed shape")]


def test_width_follows_the_scale(make, tmp_path):
    # a 0.1 pt line drawn at 4x prints at 0.4 pt (fine); one at 0.5x prints at 0.05 pt
    src = make(b"0 0 0 1 K q 4 0 0 4 0 0 cm 0.1 w 25 25 m 50 25 l S Q q 0.5 0 0 0.5 0 0 cm 0.1 w 200 300 m 400 300 l S Q")
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    assert widths(dst) == [0.4, 0.25]


def test_a_form_drawn_at_two_sizes_is_left(make):
    form = lambda pdf: pdf.make_indirect(pdf.make_stream(b"0 0 0 1 K 0.05 w 0 0 m 20 0 l S", Type=pikepdf.Name.XObject,
                                                         Subtype=pikepdf.Name.Form, BBox=[0, -1, 20, 1],
                                                         Resources=pikepdf.Dictionary()))
    src = make(b"q 1 0 0 1 100 100 cm /Fm0 Do Q q 2 0 0 2 100 150 cm /Fm0 Do Q", xobject={"/Fm0": form})
    p = propose(src)
    assert p.empty and "drawn at different sizes" in p.skipped[0][1]


def test_white_and_dielines_are_not_touched(make):
    from test_marks_and_dielines import spot
    p = propose(make(b"1 1 1 0 K 0 0 0 0 K 0.1 w 100 100 m 200 100 l S /CS0 CS 1 SCN 100 120 m 200 120 l S",
                     colorspace={"/CS0": spot("CutContour")}))
    assert p.empty and p.skipped == [(1, "no lines thinner than 0.25 pt")]


@pytest.mark.parametrize("ops, expected", [
    ("0 0 m 10 0 l S", "open"), ("0 0 m 10 0 l 10 10 l S", "open"), ("0 0 10 10 re S", "closed"),
    ("0 0 m 10 0 l 10 10 l h S", "closed"), ("0 0 m 10 0 l 0 0 l S", "closed"), ("0 0 m 10 0 l 10 10 l s", "closed"),
    ("0 0 m 10 0 l 20 20 m 30 30 l S", "open"), ("0 0 m 10 0 l 20 20 5 5 re S", "mixed"), ("5 5 m S", "closed"),
])
def test_openness(ops, expected):
    pdf = pikepdf.new()  # kept: a stream needs its document alive
    instructions = pikepdf.parse_content_stream(pikepdf.Stream(pdf, ops.encode()))
    assert _openness(instructions, 0, len(instructions) - 1) == expected


def test_many_short_lines_ask_for_a_look(make):
    # ten tiny open strokes, like illustration detail, and one rule
    tiny = b" ".join(f"{100 + 5 * i} 100 m {101 + 5 * i} 100 l S".encode() for i in range(10))
    p = propose(make(b"0 0 0 1 K 0.03 w " + tiny + b" 100 150 m 250 150 l S"))
    assert "10 of them are under 1 mm long" in p.reasons[0][1] and "compare Before and After" in p.reasons[0][1]


def test_rules_alone_do_not_ask(make):
    p = propose(make(b"0 0 0 1 K 0.1 w 100 100 m 200 100 l S"))
    assert "under 1 mm" not in p.reasons[0][1]
