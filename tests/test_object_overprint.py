"""object-overprint: overprint on or off for one chosen object."""
import hashlib

import numpy as np
import pikepdf
import pymupdf
import pytest

from prepress_preflight.actions import parse_steps
from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.objects import fingerprint
from prepress_preflight.plates import render_channels

from test_spots import spot

propose, write = FIXES["object-overprint"]
OP = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
# a silver box, then white text-like box over it set to overprint, then another white overprinting box
SILVER_THEN_WHITE = (b"/CS0 cs 1 scn 100 100 100 100 re f "
                     b"/GS0 gs 0 0 0 0 k 120 120 20 20 re f 160 160 20 20 re f")


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def silver_at(path, box):
    with pymupdf.open(path) as doc:
        names, data = render_channels(doc.load_page(0), 1)
        h = data.shape[0]
        x0, y0, x1, y1 = box
        return float(data[int(h - y1) + 2:int(h - y0) - 2, int(x0) + 2:int(x1) - 2, names.index("Silver")].mean())


def white_overprint(path):
    return [f.message for f in run(path).findings if f.check == "White overprint"]


def test_knock_out_one_white_object(make, tmp_path):
    src = make(SILVER_THEN_WHITE, colorspace={"/CS0": spot("Silver")}, extgstate=OP)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert silver_at(src, (120, 120, 140, 140)) == 255  # the white overprints: silver prints through
    p = propose(src, target="1:1", overprint="off")
    assert "Sets the filled shape in C0 M0 Y0 K0 (object 1) to knock out" in p.reasons[0][1]
    assert "It is white" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert silver_at(dst, (120, 120, 140, 140)) == 0  # knocked out now
    assert [e.overprint for e in events(dst)] == [False, False, True]  # only the chosen one changed
    assert white_overprint(dst) == ["White set to overprint, it will not print: 1 vector object(s)"]
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_turn_overprint_on(make, tmp_path):
    src = make(b"0 0 0 1 k 100 100 50 50 re f 1 0 0 0 k 120 120 10 10 re f")
    dst = str(tmp_path / "out.pdf")
    write(propose(src, target="1:0", overprint="on"), dst)
    assert [e.overprint for e in events(dst)] == [True, False]


def test_stroke_and_text_inside_a_text_block(make, tmp_path):
    def font(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    src = make(b"BT /F1 12 Tf 0 0 0 0 k 100 100 Td (A) Tj 20 0 Td (B) Tj ET 0 0 0 1 K 1 w 100 200 m 200 200 l S",
               font={"/F1": font})
    dst = str(tmp_path / "a.pdf")
    write(propose(src, target="1:1", overprint="on"), dst)  # only the "B"
    assert [(e.kind, e.overprint) for e in events(dst)] == [("text", False), ("text", True), ("path", False)]
    dst2 = str(tmp_path / "b.pdf")
    write(propose(dst, target="1:2", overprint="on"), dst2)  # the line: its stroke overprint
    assert [e.overprint for e in events(dst2)] == [False, True, True]


def test_object_inside_a_form_used_once(make, tmp_path):
    form = lambda pdf: pdf.make_stream(b"0 0 0 0 k 0 0 20 20 re f", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                                       BBox=[0, 0, 20, 20])
    src = make(b"/GS0 gs q 1 0 0 1 100 100 cm /Fm0 Do Q", xobject={"/Fm0": form}, extgstate=OP)
    dst = str(tmp_path / "out.pdf")
    write(propose(src, target="1:0", overprint="off"), dst)
    assert [e.overprint for e in events(dst)] == [False]


def test_object_inside_a_form_used_twice_is_refused(make):
    form = lambda pdf: pdf.make_indirect(pdf.make_stream(b"0 0 0 0 k 0 0 20 20 re f", Type=pikepdf.Name.XObject,
                                                         Subtype=pikepdf.Name.Form, BBox=[0, 0, 20, 20]))
    src = make(b"/GS0 gs q 1 0 0 1 100 100 cm /Fm0 Do Q q 1 0 0 1 150 100 cm /Fm0 Do Q", xobject={"/Fm0": form}, extgstate=OP)
    p = propose(src, target="1:0", overprint="off")
    assert p.empty and "painted 2 times" in p.skipped[0][1]


def test_already_as_asked(make):
    p = propose(make(b"0 0 0 1 k 100 100 50 50 re f"), target="1:0", overprint="off")
    assert p.empty and "already knocks out" in p.skipped[0][1]


def test_wrong_object_is_refused(make):
    src = make(b"0 0 0 1 k 100 100 50 50 re f 1 0 0 0 k 120 120 10 10 re f")
    first = fingerprint(events(src)[0])
    assert propose(src, target="1:0", overprint="on", expect=first).reasons
    with pytest.raises(ValueError, match="not the one chosen"):
        propose(src, target="1:1", overprint="on", expect=first)
    with pytest.raises(ValueError, match="no object 7"):
        propose(src, target="1:7", overprint="on")
    with pytest.raises(ValueError, match="PAGE:NUMBER"):
        propose(src, target="seven", overprint="on")


def test_not_allowed_in_a_saved_list():
    with pytest.raises(ValueError, match="cannot be part of a saved list"):
        parse_steps("object-overprint --object 1:0 --overprint off")
