"""Blank pages: nothing printing inside the trim."""
import pikepdf

from prepress_preflight.engine import run
from prepress_preflight.model import NOTE, WARN

from conftest import registration_space


def found(path):
    return [(f.severity, f.message, f.pages) for f in run(path).findings if f.check == "Blank pages"]


def pages(tmp_path, *contents, colorspace=None):
    pdf = pikepdf.new()
    for content in contents:
        res = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(colorspace)) if colorspace else pikepdf.Dictionary()
        page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 300, 300], TrimBox=[20, 20, 280, 280],
                                  Resources=res, Contents=pdf.make_stream(content))
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    path = str(tmp_path / "doc.pdf")
    pdf.save(path)
    return path


INK = b"0 0 0 1 k 50 50 100 100 re f"


def test_a_blank_page_among_others_is_a_note(tmp_path):
    got = found(pages(tmp_path, INK, b"", INK))
    assert got == [(NOTE, "1 blank page(s) inside the trim: 2. Fine if deliberate (an inside cover, a notes page); "
                          "otherwise the artwork is missing", [2])]


def test_every_page_blank_warns(tmp_path):
    assert [s for s, _, _ in found(pages(tmp_path, b"", b""))] == [WARN]


def test_marks_outside_the_trim_white_and_invisible_text_do_not_count(tmp_path):
    marks = b"/All CS 1 SCN 0.25 w 0 10 m 15 10 l S"  # a crop mark, outside the trim
    white = b"0 0 0 0 k 50 50 100 100 re f"
    assert [p for _, _, p in found(pages(tmp_path, INK, marks, white, colorspace={"/All": registration_space()}))] \
        == [[2, 3]]


def test_a_non_printing_dieline_alone_is_blank(tmp_path):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    die = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Dieline"), pikepdf.Name.DeviceCMYK, tint])
    got = found(pages(tmp_path, INK, b"/Die CS 1 SCN 1 w 40 40 200 200 re S", colorspace={"/Die": die}))
    assert [p for _, _, p in got] == [[2]]


def test_a_light_tint_still_prints(tmp_path):
    assert found(pages(tmp_path, INK, b"0 0.05 0 0 k 50 50 100 100 re f")) == []



def test_an_image_counts_as_ink(tmp_path):
    pdf_path = pages(tmp_path, INK, b"q 100 0 0 100 50 50 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\x00\x00\x00 EI Q")
    assert found(pdf_path) == []
