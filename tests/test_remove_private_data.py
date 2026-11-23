"""remove-private-data: Illustrator's editing copy (page-piece data) taken out, nothing else."""
import hashlib
import os

import pikepdf
import pytest

from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError

propose, write = FIXES["remove-private-data"]
BLOB = os.urandom(200_000)


def with_private_data(make, root=False, form=True):
    path = make(b"0 0 0 1 k 30 30 20 20 re f /Fm0 Do")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        def piece(app):
            chunks = [pdf.make_stream(BLOB[i::2]) for i in range(2)]
            private = pikepdf.Dictionary(AIPrivateData1=chunks[0], AIPrivateData2=chunks[1], NumBlock=2)
            return pikepdf.Dictionary({"/" + app: pikepdf.Dictionary(LastModified=pikepdf.String("D:2025"),
                                                                    Private=pdf.make_indirect(private))})
        page = pdf.pages[0].obj
        page["/PieceInfo"] = piece("Illustrator")
        page["/LastModified"] = pikepdf.String("D:2025")
        fm = pdf.make_stream(b"0 1 1 0 k 60 60 20 20 re f", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                             BBox=[0, 0, 200, 200])
        if form:
            fm["/PieceInfo"] = piece("Illustrator")
        page.Resources["/XObject"] = pikepdf.Dictionary(Fm0=fm)
        if root:
            pdf.Root["/PieceInfo"] = piece("InDesign")
        pdf.save(path)
    return path


def pieces(path):
    with pikepdf.open(path) as pdf:
        page = pdf.pages[0].obj
        return ["/PieceInfo" in page, "/PieceInfo" in page.Resources.XObject.Fm0, "/PieceInfo" in pdf.Root]


def test_removes_page_form_and_document_private_data_only(make, tmp_path):
    src = with_private_data(make, root=True)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src)
    assert p.reasons[0][1].startswith("Removes Illustrator, InDesign private data from 1 page(s) and the document "
                                      "(about 0.6 MB).")
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert pieces(dst) == [False, False, False]
    with pikepdf.open(src) as a, pikepdf.open(dst) as b:
        pa, pb = a.pages[0].obj, b.pages[0].obj
        assert sorted(k for k in pa.keys() if k != "/PieceInfo") == sorted(pb.keys())
        assert pa.Contents.read_raw_bytes() == pb.Contents.read_raw_bytes()
        assert str(pb.LastModified) == "D:2025"  # only the private data goes
        assert pa.Resources.XObject.Fm0.read_raw_bytes() == pb.Resources.XObject.Fm0.read_raw_bytes()
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_compact_makes_it_smaller(make, tmp_path):
    from prepress_preflight.fixes.chain import deliver
    src = with_private_data(make)
    plain = str(tmp_path / "plain.pdf")
    write(propose(src), plain)
    small = str(tmp_path / "small.pdf")
    deliver(src, plain, small, compact=True)
    assert os.path.getsize(small) < 20_000 < os.path.getsize(src)
    assert pieces(small) == [False, False, False]


def test_nothing_to_remove(make):
    p = propose(make(b"0 0 0 1 k 30 30 20 20 re f"))
    assert p.empty and p.skipped == [(1, "the file holds no other application's private data")]


def test_a_write_that_changes_more_is_refused(make, tmp_path, monkeypatch):
    from prepress_preflight.fixes import remove_private_data as rpd
    src = with_private_data(make, form=False)
    p = propose(src)
    real = rpd._without

    def greedy(pdf, obj):
        copy = real(pdf, obj)
        if "/LastModified" in copy:
            del copy["/LastModified"]
        return copy
    monkeypatch.setattr(rpd, "_without", greedy)
    with pytest.raises(AppendError, match="lost or gained more than the private data"):
        write(p, str(tmp_path / "out.pdf"))
