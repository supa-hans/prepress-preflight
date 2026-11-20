"""recolour-object: one object given a new CMYK colour, everything else as it was."""
import hashlib

import pikepdf
import pymupdf
import pytest

from prepress_preflight.actions import parse_steps
from prepress_preflight.content import CMYK, walk_page
from prepress_preflight.fixes import FIXES
from prepress_preflight.objects import fingerprint

from conftest import gray_image, registration_space

propose, write = FIXES["recolour-object"]
GS = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def colours(path):
    return [(e.kind, e.paint, tuple(round(v, 3) for v in e.colour)) for e in events(path)]


def recoloured(make, tmp_path, content, number, cmyk, **kw):
    src = make(content, **kw)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src, target=f"1:{number}", cmyk=cmyk)
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    return src, dst, p


def test_a_filled_shape_and_the_next_one_keeps_the_colour_it_inherits(make, tmp_path):
    # the second square has no colour of its own: it inherits K100, and must still
    src, dst, p = recoloured(make, tmp_path, b"0 0 0 1 k 100 100 50 50 re f 160 100 50 50 re f", 0, "100,0,0,0")
    assert "Recolours the filled shape in C0 M0 Y0 K100 (object 0) to C100 M0 Y0 K0" in p.reasons[0][1]
    assert colours(dst) == [("path", "fill", (1, 0, 0, 0)), ("path", "fill", (0, 0, 0, 1))]
    assert [e.bbox for e in events(dst)] == [e.bbox for e in events(src)]


def test_only_the_chosen_side_of_a_filled_and_stroked_path(make, tmp_path):
    src, dst, _ = recoloured(make, tmp_path, b"0 0 0 1 k 0 1 0 0 K 2 w 100 100 50 50 re B", 1, "0,0,100,0")
    assert colours(dst) == [("path", "fill", (0, 0, 0, 1)), ("path", "stroke", (0, 0, 1, 0))]


def test_text_in_a_spot_puts_the_spot_back_for_the_next_run(make, tmp_path):
    def font(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    src, dst, _ = recoloured(make, tmp_path, b"/CS0 cs 0.5 scn BT /F1 12 Tf 100 100 Td (AB) Tj (CD) Tj ET", 0,
                             "0,0,0,100", font={"/F1": font}, colorspace={"/CS0": registration_space()})
    a, b = events(dst)
    assert (a.cs, a.colour) == (CMYK, (0.0, 0.0, 0.0, 1.0))
    assert (b.cs, b.colour) == (events(src)[1].cs, events(src)[1].colour)  # still the spot at 50%
    assert a.bbox == events(src)[0].bbox and b.bbox == events(src)[1].bbox
    with pikepdf.open(dst) as pdf:
        ops = [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0])]
    assert "q" not in ops[ops.index("BT"):ops.index("ET")]  # q/Q are not allowed in a text block


def test_a_clipping_path_keeps_its_clip(make, tmp_path):
    src, dst, _ = recoloured(make, tmp_path, b"q 0 0 0 1 k 100 100 50 50 re W f 0 0 400 300 re f Q", 0, "100,0,0,0")
    assert colours(dst) == [("path", "fill", (1, 0, 0, 0)), ("path", "fill", (0, 0, 0, 1))]
    pymupdf.TOOLS.set_icc(False)
    pix = pymupdf.open(dst).load_page(0).get_pixmap(colorspace=pymupdf.csCMYK, alpha=False)
    assert pix.pixel(20, pix.height - 20)[3] == 0  # outside the clip: nothing painted


def test_overprint_is_kept_and_said(make, tmp_path):
    src, dst, p = recoloured(make, tmp_path, b"/GS0 gs 0 0 0 1 k 100 100 50 50 re f", 0, "0,100,0,0", extgstate=GS)
    assert "keeps" in p.reasons[0][1] and "overprint mode 1" in p.reasons[0][1]
    e = events(dst)[0]
    assert e.overprint and e.colour == (0.0, 1.0, 0.0, 0.0)


def test_refusals(make):
    form = lambda pdf: pdf.make_indirect(pdf.make_stream(b"0 0 0 1 k 0 0 20 20 re f", Type=pikepdf.Name.XObject,
                                                         Subtype=pikepdf.Name.Form, BBox=[0, 0, 20, 20], Resources=pikepdf.Dictionary()))
    src = make(b"q 1 0 0 1 100 100 cm /Fm0 Do Q q 1 0 0 1 150 100 cm /Fm0 Do Q", xobject={"/Fm0": form})
    p = propose(src, target="1:0", cmyk="100,0,0,0")
    assert p.empty and "painted 2 times" in p.skipped[0][1]

    img = make(b"q 72 0 0 72 100 100 cm /Im0 Do Q", xobject={"/Im0": gray_image(10, 10)}, name="img.pdf")
    p = propose(img, target="1:0", cmyk="100,0,0,0")
    assert p.empty and "carry their own colours" in p.skipped[0][1]

    two = make(b"0 0 0 1 k 100 100 50 50 re f 1 0 0 0 k 120 120 50 50 re f", name="two.pdf")
    assert "already C0 M0 Y0 K100" in propose(two, target="1:0", cmyk="0,0,0,100").skipped[0][1]
    with pytest.raises(ValueError, match="not the one chosen"):
        propose(two, target="1:1", cmyk="0,0,0,100", expect=fingerprint(events(two)[0]))
    for bad in ("0,0,100", "0,0,0,120", "a,b,c,d"):
        with pytest.raises(ValueError, match="C,M,Y,K in percent"):
            propose(two, target="1:0", cmyk=bad)
    with pytest.raises(ValueError, match="needs --object"):
        propose(two, target="1:0")
    with pytest.raises(ValueError, match="cannot be part of a saved list"):
        parse_steps("recolour-object --object 1:0 --cmyk 0,0,0,100")
