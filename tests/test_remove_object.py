"""remove-object: one object taken out, everything else as it was."""
import hashlib

import pikepdf
import pymupdf
import pytest

from prepress_preflight.actions import parse_steps
from prepress_preflight.content import walk_page
from prepress_preflight.fixes import FIXES
from prepress_preflight.objects import fingerprint

propose, write = FIXES["remove-object"]


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def keys(evs):
    return [(e.kind, e.paint, tuple(round(v, 2) for v in e.colour or ()), tuple(round(v, 1) for v in e.bbox or ())) for e in evs]


def removed(make, tmp_path, content, number, **kw):
    src = make(content, **kw)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src, target=f"1:{number}")
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    return src, dst, p


def test_a_filled_shape(make, tmp_path):
    src, dst, p = removed(make, tmp_path, b"0 0 0 1 k 100 100 50 50 re f 1 0 0 0 k 120 120 50 50 re f", 0)
    assert "Removes the filled shape in C0 M0 Y0 K100 (object 0)" in p.reasons[0][1]
    assert keys(events(dst)) == keys(events(src))[1:]


def test_one_side_of_a_filled_and_stroked_path(make, tmp_path):
    src, dst, p = removed(make, tmp_path, b"0 0 0 1 k 1 0 0 0 K 2 w 100 100 50 50 re B", 0)  # remove its fill
    assert "its fill only; the path keeps its stroke" in p.reasons[0][1]
    assert keys(events(dst)) == keys(events(src))[1:] and events(dst)[0].paint == "stroke"


def test_a_clip_set_with_the_path_still_holds(make, tmp_path):
    # the white box both clips and paints; removing it must keep the clip on the black that follows
    src, dst, _ = removed(make, tmp_path, b"q 0 0 0 0 k 100 100 50 50 re W f 0 0 0 1 k 0 0 400 300 re f Q", 0)
    pymupdf.TOOLS.set_icc(False)
    pix = pymupdf.open(dst).load_page(0).get_pixmap(colorspace=pymupdf.csCMYK, alpha=False)
    assert pix.pixel(20, pix.height - 20)[3] == 0  # outside the clip: still no black
    assert pix.pixel(125, pix.height - 125)[3] == 255  # inside: the black, now that the white is gone


def test_one_run_of_text_leaves_the_rest_where_it_was(make, tmp_path):
    def font(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    src, dst, _ = removed(make, tmp_path, b"BT /F1 12 Tf 0 0 0 1 k 100 100 Td (AB) Tj (CD) Tj (EF) Tj ET", 1, font={"/F1": font})
    assert keys(events(dst)) == [keys(events(src))[0], keys(events(src))[2]]
    with pikepdf.open(dst) as pdf:
        ops = [(str(i.operator), [float(o) for o in i.operands if isinstance(o, (int, float, pikepdf.Object)) and str(i.operator) == "Tr"])
               for i in pikepdf.parse_content_stream(pdf.pages[0])]
    trs = [o for op, o in ops if op == "Tr"]
    assert trs == [[3.0], [0.0]]  # invisible for that run only, then the mode in force put back


def test_an_image(make, tmp_path):
    from conftest import gray_image
    src, dst, _ = removed(make, tmp_path, b"0 0 0 1 k 10 10 20 20 re f q 72 0 0 72 100 100 cm /Im0 Do Q",
                          1, xobject={"/Im0": gray_image(10, 10)})
    assert [e.kind for e in events(dst)] == ["path"]


def test_refusals(make):
    form = lambda pdf: pdf.make_indirect(pdf.make_stream(b"0 0 0 1 k 0 0 20 20 re f", Type=pikepdf.Name.XObject,
                                                         Subtype=pikepdf.Name.Form, BBox=[0, 0, 20, 20], Resources=pikepdf.Dictionary()))
    src = make(b"q 1 0 0 1 100 100 cm /Fm0 Do Q q 1 0 0 1 150 100 cm /Fm0 Do Q", xobject={"/Fm0": form})
    p = propose(src, target="1:0")
    assert p.empty and "painted 2 times" in p.skipped[0][1]
    one = make(b"0 0 0 1 k 100 100 50 50 re f 1 0 0 0 k 120 120 50 50 re f", name="two.pdf")
    with pytest.raises(ValueError, match="not the one chosen"):
        propose(one, target="1:1", expect=fingerprint(events(one)[0]))
    with pytest.raises(ValueError, match="cannot be part of a saved list"):
        parse_steps("remove-object --object 1:0")
