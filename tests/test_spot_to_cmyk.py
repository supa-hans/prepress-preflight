"""spot-to-cmyk: spot colours converted using their own CMYK definition."""
import hashlib

import pikepdf
import pytest

from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES

from test_spots import spot

propose, write = FIXES["spot-to-cmyk"]
P185 = spot("PANTONE 185 C", (0, 0.9, 0.8, 0))  # 100% = C0 M90 Y80 K0


def colours(path):
    """(colour space kind, names, colour) for every painted object on page 1."""
    with pikepdf.open(path) as pdf:
        return [(e.cs.kind, e.cs.names, tuple(round(v, 4) for v in (e.colour or ())), e.kind, e.paint)
                for e in walk_page(pdf.pages[0]).events]


def converted(make, tmp_path, content, spaces, **kw):
    src = make(content, colorspace=spaces, **{k: v for k, v in kw.items() if k != "spots"})
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src, spots=kw.get("spots"))
    dst = str(tmp_path / "cmyk.pdf")
    write(p, dst)  # verified: renders the same, spot no longer used
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    return p, dst


def test_flat_colour_and_tints_become_cmyk(make, tmp_path):
    content = b"/CS0 cs 0.5 scn 100 100 20 20 re f /CS0 CS 1 SCN 2 w 100 140 m 140 140 l S"
    p, dst = converted(make, tmp_path, content, {"/CS0": P185})
    assert "C0 M90 Y80 K0" in p.reasons[0][1]
    got = colours(dst)
    assert ("cmyk", (), (0.0, 0.45, 0.4, 0.0), "path", "fill") in got
    assert ("cmyk", (), (0.0, 0.9, 0.8, 0.0), "path", "stroke") in got
    assert not [f for f in run(dst).findings if f.check == "Spot colours"]


def test_a_spot_chosen_with_no_tint_set_is_full_strength(make, tmp_path):
    _, dst = converted(make, tmp_path, b"/CS0 cs 100 100 20 20 re f", {"/CS0": P185})
    assert colours(dst)[0][2] == (0.0, 0.9, 0.8, 0.0)


def test_colour_space_restored_by_Q_is_left_as_it_was(make, tmp_path):
    content = b"1 0 0 0 k q /CS0 cs 1 scn 100 100 20 20 re f Q 140 100 20 20 re f"
    _, dst = converted(make, tmp_path, content, {"/CS0": P185})
    got = [c[2] for c in colours(dst)]
    assert got == [(0.0, 0.9, 0.8, 0.0), (1.0, 0.0, 0.0, 0.0)]


def test_text_in_a_spot_is_converted(make, tmp_path):
    font = {"/F1": pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)}
    _, dst = converted(make, tmp_path, b"BT /F1 12 Tf /CS0 cs 1 scn 100 100 Td (Hello) Tj ET", {"/CS0": P185}, font=font)
    assert colours(dst)[0][:4] == ("cmyk", (), (0.0, 0.9, 0.8, 0.0), "text")


def test_spot_gradient_becomes_a_cmyk_gradient(make, tmp_path):
    fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0], C1=[1], N=1)
    shading = pikepdf.Dictionary(ShadingType=2, ColorSpace=P185, Coords=[100, 0, 200, 0], Function=fn)

    def make_pdf(content, **kw):
        path = make(content, **kw)
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.pages[0].obj.Resources["/Shading"] = pikepdf.Dictionary(Sh0=pdf.make_indirect(shading))
            pdf.save(path)
        return path
    src = make_pdf(b"q 100 100 100 50 re W n /Sh0 sh Q")
    dst = str(tmp_path / "cmyk.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        sh = pdf.pages[0].Resources.Shading.Sh0
        assert str(sh.ColorSpace) == "/DeviceCMYK"
        assert [float(v) for v in sh.Function.C1] == pytest.approx([0, 0.9, 0.8, 0])


def test_spot_in_a_placed_graphic_is_converted(make, tmp_path):
    def form(pdf):
        return pdf.make_stream(b"/CS0 cs 1 scn 0 0 20 20 re f", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                               BBox=[0, 0, 20, 20], Resources=pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary({"/CS0": P185})))
    src = make(b"q 1 0 0 1 100 100 cm /Fm0 Do Q", xobject={"/Fm0": form})
    dst = str(tmp_path / "cmyk.pdf")
    write(propose(src), dst)
    assert colours(dst)[0][:3] == ("cmyk", (), (0.0, 0.9, 0.8, 0.0))


def test_only_the_spots_asked_for(make, tmp_path):
    spaces = {"/CS0": P185, "/CS1": spot("PANTONE 286 C", (1, 0.66, 0, 0.02))}
    content = b"/CS0 cs 1 scn 100 100 20 20 re f /CS1 cs 1 scn 140 100 20 20 re f"
    _, dst = converted(make, tmp_path, content, spaces, spots=["PANTONE 286 C"])
    kinds = [c[1] for c in colours(dst)]
    assert ("PANTONE 185 C",) in kinds and () in kinds


def test_spot_used_in_an_image_is_left_alone(make, tmp_path):
    def image(pdf):
        return pdf.make_stream(bytes([200]) * 100, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=10, Height=10, ColorSpace=P185, BitsPerComponent=8)
    src = make(b"/CS0 cs 1 scn 50 50 20 20 re f q 20 0 0 20 100 100 cm /Im0 Do Q", colorspace={"/CS0": P185},
               xobject={"/Im0": image})
    p = propose(src)
    assert p.empty and "used in an image" in p.skipped[0][1]


def test_mixed_with_other_inks_is_left_alone(make):
    fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    dn = pikepdf.Array([pikepdf.Name.DeviceN, pikepdf.Array([pikepdf.Name("/PANTONE 185 C"), pikepdf.Name("/PANTONE 286 C")]),
                        pikepdf.Name.DeviceCMYK, fn])
    src = make(b"/CS0 cs 1 scn 50 50 20 20 re f /CS1 cs 1 1 scn 100 100 20 20 re f", colorspace={"/CS0": P185, "/CS1": dn})
    p = propose(src, spots=["PANTONE 185 C"])
    assert p.empty and "mixed with other inks" in p.skipped[0][1]


def test_spot_defined_in_lab_is_refused(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[100, 0, 0], C1=[50, 60, 40], N=1)
    lab = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 185 C"),
                         pikepdf.Array([pikepdf.Name.Lab, pikepdf.Dictionary(WhitePoint=[0.9505, 1, 1.089])]), tint])
    p = propose(make(b"/CS0 cs 1 scn 50 50 20 20 re f", colorspace={"/CS0": lab}))
    assert p.empty and "colour profile" in p.skipped[0][1]


def test_non_printing_spot_is_never_converted(make):
    assert propose(make(b"/CS0 CS 1 SCN 50 50 m 90 50 l S", colorspace={"/CS0": spot("Dieline")})).empty


def test_cmyk_only_passes_after_converting(make, tmp_path):
    from prepress_preflight.checks import Options
    src = make(b"/CS0 cs 1 scn 100 100 20 20 re f", colorspace={"/CS0": P185})
    assert [f for f in run(src, Options(cmyk_only=True)).findings if f.check == "Spot colours" and f.severity == "fail"]
    dst = str(tmp_path / "cmyk.pdf")
    write(propose(src), dst)
    assert not [f for f in run(dst, Options(cmyk_only=True)).findings if f.check == "Spot colours"]


def test_verification_refuses_a_wrong_conversion(make, tmp_path, monkeypatch):
    from prepress_preflight.fixes.append import AppendError
    import prepress_preflight.fixes.spot_to_cmyk as sc
    src = make(b"/CS0 cs 1 scn 100 100 80 80 re f", colorspace={"/CS0": P185})
    p = propose(src)
    monkeypatch.setattr(sc.Tint, "cmyk", lambda self, t: [1.0, 0.0, 0.0, 0.0])  # write cyan instead
    dst = tmp_path / "cmyk.pdf"
    with pytest.raises(AppendError, match="looks different"):
        write(p, str(dst))
    assert not dst.exists()
