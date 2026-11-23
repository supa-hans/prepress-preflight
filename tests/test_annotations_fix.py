"""flatten-annotations and remove-annotations."""
import hashlib

import pikepdf
import pymupdf

from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES

flatten, write_flatten = FIXES["flatten-annotations"]
remove, write_remove = FIXES["remove-annotations"]
PRINT = 4


def appearance(pdf, bbox=(0, 0, 10, 10), colour=b"0 1 1 0 k", matrix=None):
    x0, y0, x1, y1 = bbox
    form = pdf.make_stream(colour + f" {x0} {y0} {x1 - x0} {y1 - y0} re f".encode(),
                           Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=list(bbox))
    if matrix:
        form["/Matrix"] = list(matrix)
    return form


def add(pdf, subtype, rect, flags, **extra):
    annot = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name("/" + subtype),
                                                 Rect=list(rect), F=flags, **extra))
    page = pdf.pages[0].obj
    if "/Annots" not in page:
        page["/Annots"] = pikepdf.Array()
    page.Annots.append(annot)
    return annot


def build(make, tmp_path, change, name="t.pdf"):
    path = make(b"0 0 0 0.2 k 30 30 20 20 re f", name=name)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        change(pdf)
        pdf.save(path)
    return path


def everything(pdf, need_appearances=False):
    add(pdf, "Stamp", (100, 100, 150, 150), PRINT, AP=pikepdf.Dictionary(N=appearance(pdf)))
    comment = add(pdf, "Text", (200, 100, 220, 120), 0, Contents=pikepdf.String("move this"))
    add(pdf, "Popup", (200, 130, 300, 200), 0, Parent=comment)
    add(pdf, "Link", (40, 40, 60, 60), PRINT)
    field = add(pdf, "Widget", (100, 30, 160, 50), PRINT, FT=pikepdf.Name.Tx, T=pikepdf.String("name"),
                AP=pikepdf.Dictionary(N=appearance(pdf, colour=b"1 0 0 0 k")))
    form = pikepdf.Dictionary(Fields=pikepdf.Array([field]))
    if need_appearances:
        form["/NeedAppearances"] = True
    pdf.Root["/AcroForm"] = pdf.make_indirect(form)


def kinds(path):
    with pikepdf.open(path) as pdf:
        return sorted(str(a.Subtype)[1:] for a in pdf.pages[0].obj.get("/Annots", []))


def ink(path, x, y, annots=False):
    """CMYK at (x, y) in PDF points, rendered at 72 dpi."""
    with pymupdf.open(path) as doc:
        page = doc[0]
        pix = page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=72, alpha=False, annots=annots)
        return pix.pixel(int(x), int(page.rect.height - y))


def annotation_findings(path):
    return [(f.severity, f.message) for f in run(path).findings if f.check == "Annotations"]


def test_flatten_draws_printing_ones_and_leaves_the_rest(make, tmp_path):
    src = build(make, tmp_path, everything)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert ink(src, 125, 125) == (0, 0, 0, 0)  # the stamp is not page content yet
    p = flatten(src)
    assert p.reasons == [(1, "Draws 1 Stamp, 1 form field into the page as they look now, then takes them off the page.")]
    dst = str(tmp_path / "out.pdf")
    write_flatten(p, dst)
    assert kinds(dst) == ["Link", "Popup", "Text"]  # the comment is not set to print, so it stays
    assert ink(dst, 125, 125) == (0, 255, 255, 0)  # stamp now drawn, filling its Rect
    assert ink(dst, 102, 102) == (0, 255, 255, 0) and ink(dst, 148, 148) == (0, 255, 255, 0)
    assert ink(dst, 130, 40) == (255, 0, 0, 0)  # the field's look
    assert ink(dst, 40, 40) == (0, 0, 0, 51)  # the artwork is still there
    with pikepdf.open(dst) as pdf:
        assert "/AcroForm" not in pdf.Root  # its only field is flattened
    assert [s for s, _ in annotation_findings(dst)] == ["note"]
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_remove_takes_comments_popups_and_fields_but_keeps_links(make, tmp_path):
    src = build(make, tmp_path, everything)
    p = remove(src)
    assert p.reasons == [(1, "Takes 1 Stamp, 1 sticky note, 1 form field off the page; the artwork is untouched.")]
    dst = str(tmp_path / "out.pdf")
    write_remove(p, dst)
    assert kinds(dst) == ["Link"]
    assert ink(dst, 125, 125, annots=True) == (0, 0, 0, 0)
    assert annotation_findings(dst) == []
    with pikepdf.open(src) as a, pikepdf.open(dst) as b:
        assert a.pages[0].obj.Contents.objgen == b.pages[0].obj.Contents.objgen
        assert "/AcroForm" not in b.Root


def test_a_form_that_asks_to_be_redrawn_is_left(make, tmp_path):
    src = build(make, tmp_path, lambda pdf: everything(pdf, need_appearances=True))
    p = flatten(src)
    assert "1 Stamp" in p.reasons[0][1] and "form field" not in p.reasons[0][1]
    assert any("redraw its fields" in why for _, why in p.skipped)
    dst = str(tmp_path / "out.pdf")
    write_flatten(p, dst)
    assert kinds(dst) == ["Link", "Popup", "Text", "Widget"]
    with pikepdf.open(dst) as pdf:
        assert len(pdf.Root.AcroForm.Fields) == 1


def test_no_appearance_is_left_and_said_so(make, tmp_path):
    src = build(make, tmp_path, lambda pdf: add(pdf, "Stamp", (100, 100, 150, 150), PRINT))
    p = flatten(src)
    assert p.empty
    assert p.skipped == [(1, "Stamp: it has no stored appearance")]


def test_appearance_matrix_and_bbox_are_honoured(make, tmp_path):
    # A 20 x 10 drawing turned a quarter turn: its box becomes 10 x 20, then fits the Rect.
    def change(pdf):
        add(pdf, "Stamp", (100, 100, 120, 140), PRINT,
            AP=pikepdf.Dictionary(N=appearance(pdf, bbox=(0, 0, 20, 10), matrix=(0, 1, -1, 0, 0, 0))))
    src = build(make, tmp_path, change)
    dst = str(tmp_path / "out.pdf")
    write_flatten(flatten(src), dst)
    assert ink(dst, 110, 120) == (0, 255, 255, 0)
    assert ink(dst, 101, 139) == (0, 255, 255, 0)
    assert ink(dst, 125, 120) == (0, 0, 0, 0)


def test_unticked_box_with_no_off_drawing_draws_nothing(make, tmp_path):
    def change(pdf):
        box = add(pdf, "Widget", (100, 100, 110, 110), PRINT, FT=pikepdf.Name.Btn, T=pikepdf.String("ok"),
                  AS=pikepdf.Name.Off, AP=pikepdf.Dictionary(N=pikepdf.Dictionary(Yes=appearance(pdf))))
        pdf.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([box]))
    src = build(make, tmp_path, change)
    p = flatten(src)
    assert p.plan[1]["draw"] == [] and p.plan[1]["take"] == [0]
    dst = str(tmp_path / "out.pdf")
    write_flatten(p, dst)
    assert kinds(dst) == [] and ink(dst, 105, 105) == (0, 0, 0, 0)


def test_one_of_two_fields_flattened_keeps_the_other_in_the_form(make, tmp_path):
    def change(pdf):
        a = add(pdf, "Widget", (100, 30, 160, 50), PRINT, FT=pikepdf.Name.Tx, T=pikepdf.String("a"),
                AP=pikepdf.Dictionary(N=appearance(pdf)))
        b = add(pdf, "Widget", (100, 60, 160, 80), 0, FT=pikepdf.Name.Tx, T=pikepdf.String("b"),
                AP=pikepdf.Dictionary(N=appearance(pdf)))
        pdf.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([a, b]))
    src = build(make, tmp_path, change)
    dst = str(tmp_path / "out.pdf")
    write_flatten(flatten(src), dst)
    with pikepdf.open(dst) as pdf:
        assert [str(f.T) for f in pdf.Root.AcroForm.Fields] == ["b"]
        assert kinds(dst) == ["Widget"]


def test_nothing_to_do_on_a_clean_page(make):
    src = make(b"0 0 0 1 k 30 30 20 20 re f")
    assert flatten(src).empty and remove(src).empty


def test_a_misplaced_drawing_is_refused(make, tmp_path, monkeypatch):
    import pytest
    from prepress_preflight.fixes import annotations
    from prepress_preflight.fixes.append import AppendError
    src = build(make, tmp_path, everything)
    p = flatten(src)
    p.plan[1]["draw"] = [(i, [cm[0], 0, 0, cm[3], cm[4] + 40, cm[5]]) for i, cm in p.plan[1]["draw"]]
    dst = str(tmp_path / "out.pdf")
    with pytest.raises(AppendError, match="does not look as it did"):
        annotations._write(p, dst)
    assert not (tmp_path / "out.pdf").exists()


def test_sticky_notes_set_to_print_are_not_flattened(make, tmp_path):
    src = build(make, tmp_path, lambda pdf: add(pdf, "Text", (100, 100, 120, 120), PRINT + 24,
                                                 AP=pikepdf.Dictionary(N=appearance(pdf))))
    p = flatten(src)
    assert p.empty
    assert p.skipped == [(1, "sticky note: a comment mark, not artwork; remove-annotations takes it off")]
    assert not remove(src).empty


def test_unembedded_lettering_is_said_up_front(make, tmp_path):
    def change(pdf):
        form = appearance(pdf)
        form["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(
            Helv=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)))
        add(pdf, "FreeText", (100, 100, 150, 150), PRINT, AP=pikepdf.Dictionary(N=form))
    p = flatten(build(make, tmp_path, change))
    assert p.reasons[0][1].endswith("fonts that are not embedded (Helvetica), which the Font embedding check "
                                    "will then report.")
