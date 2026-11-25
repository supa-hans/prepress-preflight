"""CMYK tagged with an ICC profile: the CMYK profiles note, and the untag-cmyk fix."""
import hashlib
import zlib

import pikepdf
import pytest

from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError

propose, write = FIXES["untag-cmyk"]


def icc(pdf):
    # Not a real profile (nothing here reads it as one): an ICC-based space only needs N and some data.
    return pdf.make_stream(b"not a real profile", N=4, Alternate=pikepdf.Name.DeviceCMYK)


def tagged(make, image=True, indexed=True, alternate=False):
    """K100 text-like box in a tagged CMYK space, a tagged image, an indexed image on a tagged base."""
    content = b"/CS0 cs 0 0 0 1 scn 60 60 40 40 re f "
    if image:
        content += b"q 40 0 0 40 120 60 cm /Im0 Do Q "
    if indexed:
        content += b"q 40 0 0 40 180 60 cm /Im1 Do Q "
    if alternate:
        content += b"/Sp cs 1 scn 240 60 20 20 re f "
    path = make(content)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        profile = pdf.make_indirect(pikepdf.Array([pikepdf.Name.ICCBased, icc(pdf)]))
        res = pdf.pages[0].resources
        res["/ColorSpace"] = pikepdf.Dictionary({"/CS0": profile})
        if alternate:
            tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 0.5, 1, 0], N=1)
            res.ColorSpace["/Sp"] = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 151 C"), profile,
                                                   tint])
        xo = pikepdf.Dictionary()
        if image:
            xo["/Im0"] = pdf.make_stream(zlib.compress(bytes([0, 0, 0, 255]) * 4), Type=pikepdf.Name.XObject,
                                         Subtype=pikepdf.Name.Image, Width=2, Height=2, ColorSpace=profile,
                                         BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        if indexed:
            palette = pikepdf.Array([pikepdf.Name.Indexed, profile, 1, pikepdf.String(bytes([0, 0, 0, 255, 255, 0, 0, 0]))])
            xo["/Im1"] = pdf.make_stream(zlib.compress(bytes([0, 1, 1, 0])), Type=pikepdf.Name.XObject,
                                         Subtype=pikepdf.Name.Image, Width=2, Height=2, ColorSpace=palette,
                                         BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        res["/XObject"] = xo
        pdf.save(path)
    return path


def notes(path):
    return [f.message for f in run(path).findings if f.check == "CMYK profiles"]


def test_tagged_cmyk_is_noted(make):
    (msg,) = notes(tagged(make))
    assert msg.startswith("CMYK tagged with “an embedded profile that could not be read”: 2 image(s), 1 vector object(s).")


def test_plain_cmyk_is_not_noted(make):
    assert notes(make(b"0 0 0 1 k 60 60 40 40 re f")) == []


def test_untag_keeps_every_number_and_removes_the_tags(make, tmp_path):
    src = tagged(make)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src)
    assert "1 colour space(s) named in resources or palettes, 2 image(s)" in p.reasons[0][1]  # the palette is inside its image
    dst = str(tmp_path / "out.pdf")
    write(p, dst)  # verified inside: identical plates, nothing painted left tagged
    assert notes(dst) == []
    with pikepdf.open(dst) as pdf:
        res = pdf.pages[0].resources
        assert res.ColorSpace.CS0 == pikepdf.Name.DeviceCMYK
        assert res.XObject.Im0.ColorSpace == pikepdf.Name.DeviceCMYK
        assert res.XObject.Im1.ColorSpace[1] == pikepdf.Name.DeviceCMYK
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_a_spot_colours_alternate_is_left(make, tmp_path):
    src = tagged(make, image=False, indexed=False, alternate=True)
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        spot = pdf.pages[0].resources.ColorSpace.Sp
        assert str(spot[2][0]) == "/ICCBased"  # how the spot would be simulated: not this fix's business


def test_nothing_to_untag(make):
    p = propose(make(b"0 0 0 1 k 60 60 40 40 re f"))
    assert p.empty and p.skipped == [(1, "nothing painted in CMYK is tagged with a profile")]


def test_a_write_that_changes_the_plates_is_refused(make, tmp_path, monkeypatch):
    from prepress_preflight.fixes import untag_cmyk
    src = tagged(make, indexed=False)
    p = propose(src)
    real = untag_cmyk._survey

    def wrong(pdf):
        holders, profiles, counts = real(pdf)
        for _, obj in holders:
            if obj.get("/Subtype") == "/Image":
                obj["/Decode"] = [1, 0, 1, 0, 1, 0, 1, 0]  # inverts the image: not just an untag
        return holders, profiles, counts
    monkeypatch.setattr(untag_cmyk, "_survey", wrong)
    with pytest.raises(AppendError, match="plates do not render the same"):
        write(p, str(tmp_path / "out.pdf"))


def test_a_shared_colour_space_dictionary_inside_page_resources(make, tmp_path):
    # Resources written inside the page, pointing at one indirect /ColorSpace dictionary (shared between pages)
    path = make(b"/CS0 cs 0 0 0 1 scn 60 60 40 40 re f")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        spaces = pdf.make_indirect(pikepdf.Dictionary({"/CS0": pikepdf.Array([pikepdf.Name.ICCBased, icc(pdf)])}))
        pdf.pages[0].obj["/Resources"] = pikepdf.Dictionary(ColorSpace=spaces)
        pdf.save(path)
    dst = str(tmp_path / "out.pdf")
    write(propose(path), dst)
    assert notes(dst) == []
