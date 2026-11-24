"""Page count of a bound job: even, and a multiple of 4 for saddle stitching."""
import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.model import NOTE, WARN


def doc(tmp_path, n):
    pdf = pikepdf.new()
    for _ in range(n):
        page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 300, 400], Resources=pikepdf.Dictionary(),
                                  Contents=pdf.make_stream(b"0 0 0 1 k 50 50 100 100 re f"))
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    path = str(tmp_path / f"{n}.pdf")
    pdf.save(path)
    return path


def counts(path, bound=True):
    return [(f.severity, f.message) for f in run(path, Options(bound=bound)).findings
            if f.check == "Page size" and " pages: " in f.message]


def test_odd_count_warns(tmp_path):
    assert counts(doc(tmp_path, 7)) == [(WARN, "7 pages: a bound job has an even number of pages (two to every leaf). "
                                                "A page is missing or extra, or the file holds a single cover page")]


def test_even_but_not_four_is_a_note(tmp_path):
    got = counts(doc(tmp_path, 10))
    assert [s for s, _ in got] == [NOTE] and "2 blank pages would be added" in got[0][1]


def test_multiple_of_four_is_fine(tmp_path):
    assert counts(doc(tmp_path, 8)) == []


def test_not_judged_unless_bound(tmp_path):
    assert counts(doc(tmp_path, 7), bound=False) == []
