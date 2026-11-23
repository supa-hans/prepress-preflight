"""remove-layer: what is on a layer stops printing, and the layer leaves the file."""
import hashlib

import pikepdf
import pymupdf
import pytest

from conftest import MM, TRIM, gray_image
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError

propose, write = FIXES["remove-layer"]


def layered(make, content, hidden=(), names=("Art", "Template"), ocmd=None, form=None, image_layer=None, **kw):
    """A page whose /Properties MC0, MC1 ... are layers with the given names."""
    path = make(content, **kw)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        groups = [pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(n))) for n in names]
        res = pdf.pages[0].obj.Resources
        res["/Properties"] = pikepdf.Dictionary({f"/MC{i}": g for i, g in enumerate(groups)})
        if ocmd is not None:
            res.Properties["/MD"] = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.OCMD, OCGs=pikepdf.Array([groups[i] for i in ocmd]), P=pikepdf.Name.AnyOn))
        if form is not None:
            f = pdf.make_stream(form, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 500, 500],
                                Resources=pikepdf.Dictionary(Properties=pikepdf.Dictionary(MC1=groups[1])))
            res["/XObject"] = pikepdf.Dictionary(Fm0=f)
        if image_layer is not None:
            res.XObject.Im0["/OC"] = groups[image_layer]
        config = pikepdf.Dictionary(Order=pikepdf.Array(groups), ON=pikepdf.Array([g for n, g in zip(names, groups) if n not in hidden]))
        if hidden:
            config["/OFF"] = pikepdf.Array([g for n, g in zip(names, groups) if n in hidden])
        pdf.Root["/OCProperties"] = pikepdf.Dictionary(OCGs=pikepdf.Array(groups), D=config)
        pdf.save(path)
    return path


def ink(path, x, y):
    with pymupdf.open(path) as doc:
        page = doc[0]
        pix = page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=72, alpha=False, annots=False)
        return pix.pixel(int(x), int(page.rect.height - y))


def layer_names(path):
    with pikepdf.open(path) as pdf:
        props = pdf.Root.get("/OCProperties")
        return None if props is None else [str(g.Name) for g in props.OCGs]


# Illustrator's way: a q opened on one layer is closed on the next, and a cm set
# there still applies to what follows. Only the painting may go.
ACROSS = (b"/OC /MC0 BDC q 1 0 0 1 10 0 cm 0 1 1 0 k 100 100 20 20 re f EMC "
          b"/OC /MC1 BDC 1 0 0 1 0 0 k 200 100 20 20 re f 1 0 0 1 0 50 cm 0 0 0 1 k 100 100 20 20 re f Q EMC "
          b"0 0 0 1 k 300 100 20 20 re f")


def test_removes_only_the_painting_and_keeps_the_state(make, tmp_path):
    src = layered(make, ACROSS)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src, layer=["template"])  # names match regardless of case
    assert p.reasons == [(1, "Takes 2 painted object(s) on Template off the page; everything else, and the drawing "
                             "state, is kept.")]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert ink(dst, 120, 110) == (0, 255, 255, 0)  # Art, shifted by its own cm
    assert ink(dst, 220, 110) == (0, 0, 0, 0)  # Template's magenta box gone
    assert ink(dst, 120, 160) == (0, 0, 0, 0)  # Template's black box gone
    assert ink(dst, 310, 110) == (0, 0, 0, 255)  # after Q: the state is back as it was
    assert layer_names(dst) == ["Art"]
    with pikepdf.open(dst) as pdf:
        assert pdf.Root.OCProperties.D.Order == pdf.Root.OCProperties.OCGs
        assert b"/MC1" not in pdf.pages[0].obj.Contents.read_bytes()
    assert [f.message for f in run(dst).findings if f.check == "Layers"] == ["Layers: Art"]
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_default_is_every_hidden_layer(make, tmp_path):
    src = layered(make, ACROSS, hidden=("Template",))
    p = propose(src)
    assert "on Template" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert layer_names(dst) == ["Art"]


def test_nothing_hidden_and_no_name_changes_nothing(make):
    p = propose(layered(make, ACROSS))
    assert p.empty
    assert p.skipped == [(1, "no layer is hidden; name the one to remove with --layer (the file's layers: Art, Template)")]


def test_an_unknown_name_is_refused(make):
    with pytest.raises(ValueError, match=r"no layer named Logo \(the file's layers: Art, Template\)"):
        propose(layered(make, ACROSS), layer=["Logo"])


def test_last_layer_removed_takes_the_layer_list(make, tmp_path):
    src = layered(make, b"/OC /MC0 BDC 0 1 1 0 k 100 100 20 20 re f EMC", names=("Only",))
    dst = str(tmp_path / "out.pdf")
    write(propose(src, layer=["Only"]), dst)
    assert layer_names(dst) is None and ink(dst, 110, 110) == (0, 0, 0, 0)


def test_text_after_hidden_text_stays_where_it_was(make, tmp_path):
    font = lambda pdf: pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    src = layered(make, b"BT /F1 12 Tf 100 100 Td /OC /MC1 BDC (AAAA) Tj EMC (BBBB) Tj ET", font={"/F1": font})
    dst = str(tmp_path / "out.pdf")
    write(propose(src, layer=["Template"]), dst)
    def first_b(path):
        with pymupdf.open(path) as doc:
            chars = [c for block in doc[0].get_text("rawdict")["blocks"] for line in block.get("lines", [])
                     for span in line["spans"] for c in span["chars"]]
        return next(c["origin"] for c in chars if c["c"] == "B")
    assert first_b(dst) == pytest.approx(first_b(src))  # BBBB still starts after the (now invisible) AAAA
    def inked(path):  # any ink where AAAA was shown
        return any(ink(path, x, y) != (0, 0, 0, 0) for x in range(100, 132) for y in range(100, 110))
    assert inked(src) and not inked(dst)


def test_a_placed_image_on_the_layer_goes(make, tmp_path):
    src = layered(make, b"q 50 0 0 50 100 100 cm /Im0 Do Q 0 1 1 0 k 200 100 20 20 re f",
                  xobject={"/Im0": gray_image(4, 4)}, image_layer=1)
    p = propose(src, layer=["Template"])
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert ink(dst, 120, 120) == (0, 0, 0, 0) and ink(dst, 210, 110) == (0, 255, 255, 0)


def test_a_layer_mixed_with_another_is_refused(make):
    src = layered(make, ACROSS + b" /OC /MD BDC 0 1 0 0 k 400 100 20 20 re f EMC", ocmd=(0, 1))
    p = propose(src, layer=["Template"])
    assert p.empty
    assert p.skipped[0] == (1, "part of the page is governed by this layer together with others; nothing on this "
                               "page was changed")
    assert p.skipped[-1] == (1, "Template was not removed, since it could not be taken off every page")


def test_a_layer_inside_a_placed_form_is_refused(make):
    src = layered(make, b"/Fm0 Do", form=b"/OC /MC1 BDC 0 1 1 0 k 100 100 20 20 re f EMC")
    p = propose(src, layer=["Template"])
    assert p.empty and "inside a placed form" in p.skipped[0][1]


def test_crop_marks_on_the_layer_are_flagged_when_the_trim_box_disagrees(make):
    from conftest import registration_space
    from test_marks_and_dielines import crop_marks
    content = b"0 1 1 0 k 120 120 20 20 re f /OC /MC1 BDC " + crop_marks(TRIM) + b"EMC"
    reg = {"/CS0": registration_space()}
    wrong = (TRIM[0] - 5 * MM, TRIM[1] - 5 * MM, TRIM[2] + 5 * MM, TRIM[3] + 5 * MM)
    p = propose(layered(make, content, trim=wrong, bleed=None, colorspace=reg), layer=["Template"])
    assert ("The crop marks are on this layer: they point at 100.0 × 60.0 mm while the trim box is 110.0 × 70.0 mm"
            in p.reasons[0][1])
    assert "trim-from-marks+remove-layer" in p.reasons[0][1]
    agreeing = propose(layered(make, content, name="b.pdf", colorspace=reg), layer=["Template"])
    assert "crop marks" not in agreeing.reasons[0][1]


def test_a_wrong_result_is_refused(make, tmp_path, monkeypatch):
    from prepress_preflight.fixes import remove_layer
    src = layered(make, ACROSS)
    p = propose(src, layer=["Template"])
    monkeypatch.setattr(remove_layer, "PAINT", set())  # nothing painted is taken out
    with pytest.raises(AppendError, match="does not look as the original does with the layer off"):
        write(p, str(tmp_path / "out.pdf"))
    assert not (tmp_path / "out.pdf").exists()
