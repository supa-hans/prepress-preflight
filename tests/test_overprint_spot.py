"""overprint-spot: dielines (or named spots) set to overprint."""
import hashlib

import pikepdf
import pytest

from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES

propose, write = FIXES["overprint-spot"]


def sep(name):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/" + name), pikepdf.Name.DeviceCMYK, tint])


SPACES = {"/Die": sep("Dieline"), "/Var": sep("Spot Varnish")}
ART = b"0.2 0.4 0 0 k 50 50 200 100 re f "
DIE_LINE = b"/Die CS 1 SCN 0.5 w 60 60 180 80 re S "


def overprints(path):
    """[(colour space names, paint, overprint)] for each painted object on page 1."""
    with pikepdf.open(path) as pdf:
        return [(tuple(e.cs.names) if e.cs.kind == "sep" else e.cs.kind, e.paint, e.overprint)
                for e in walk_page(pdf.pages[0]).events]


def dieline_warnings(path):
    return [f.message for f in run(path).findings if f.check == "Dielines" and f.severity == "warn"]


def test_a_knocking_out_dieline_is_set_to_overprint(make, tmp_path):
    src = make(ART + DIE_LINE, colorspace=SPACES)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert dieline_warnings(src)
    p = propose(src)
    assert p.reasons == [(1, "Sets Dieline to overprint: 1 line(s). The artwork beneath then prints whole instead "
                             "of having the shape knocked out.")]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert overprints(dst) == [("cmyk", "fill", False), (("Dieline",), "stroke", True)]
    assert dieline_warnings(dst) == []
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_a_named_spot_instead_of_dielines(make, tmp_path):
    src = make(ART + DIE_LINE + b"/Var cs 1 scn 80 80 50 50 re f", colorspace=SPACES)
    p = propose(src, spots=["spot varnish"])  # spelling and case need not match exactly
    assert "Sets Spot Varnish to overprint: 1 shape(s)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert overprints(dst)[1:] == [(("Dieline",), "stroke", False), (("Spot Varnish",), "fill", True)]


def test_an_unknown_spot_is_refused(make):
    with pytest.raises(ValueError, match=r"no spot colour named Foil is used \(the file's spots: Dieline\)"):
        propose(make(ART + DIE_LINE, colorspace=SPACES), spots=["Foil"])


def test_already_overprinting_or_absent_changes_nothing(make):
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True)}
    p = propose(make(ART + b"/GS0 gs " + DIE_LINE, colorspace=SPACES, extgstate=gs))
    assert p.empty and p.skipped == [(1, "every dieline already overprints")]
    p = propose(make(ART, name="plain.pdf"))
    assert p.empty and p.skipped == [(1, "no dielines in the file")]


def test_zero_percent_dieline_is_left(make):
    p = propose(make(ART + b"/Die CS 0 SCN 60 60 180 80 re S", colorspace=SPACES))
    assert p.empty


def test_dieline_in_a_text_block_puts_the_state_back(make, tmp_path):
    font = lambda pdf: pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    src = make(ART + b"BT /F1 12 Tf 100 100 Td /Die cs 1 scn (CUT) Tj 0 0 0 1 k (here) Tj ET", colorspace=SPACES,
               font={"/F1": font})
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    assert overprints(dst) == [("cmyk", "fill", False), (("Dieline",), "fill", True), ("cmyk", "fill", False)]


def test_filled_artwork_stroked_with_the_dieline_is_left(make):
    p = propose(make(b"/Die CS 1 SCN 0.2 0.4 0 0 k 60 60 180 80 re B", colorspace=SPACES))
    assert p.empty and p.skipped and "left knocking out" in p.skipped[0][1]


def test_a_write_that_misses_an_object_is_refused(make, tmp_path, monkeypatch):
    from prepress_preflight.fixes import overprint_spot
    from prepress_preflight.fixes.append import AppendError
    src = make(ART + DIE_LINE, colorspace=SPACES)
    p = propose(src)
    real = overprint_spot.add_edit
    # the bracket sets the old state instead of overprint, so the object is untouched
    monkeypatch.setattr(overprint_spot, "add_edit", lambda plan, spot, before, after, label: real(plan, spot, after, after, label))
    with pytest.raises(AppendError, match="does not overprint"):
        write(p, str(tmp_path / "out.pdf"))
