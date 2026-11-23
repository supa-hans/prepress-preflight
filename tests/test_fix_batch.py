"""preflight fix on several PDFs: one summary, one confirmation, a new file for each that changes."""
import hashlib
import os

import pikepdf

from prepress_preflight.cli import main
from prepress_preflight.content import walk_page

GS = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
WHITE_OP = b"/GS0 gs 0 0 0 0 k 120 120 20 20 re f"


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_a_folder_is_fixed_into_a_new_folder(make, tmp_path, capsys):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    a = make(WHITE_OP, name="a.pdf", extgstate=GS)
    b = make(b"0 0 0 1 k 100 100 50 50 re f", name="b.pdf")  # nothing to change
    for p in (a, b):
        os.replace(p, jobs / os.path.basename(p))
    (jobs / "broken.pdf").write_bytes(b"not a pdf")
    before = {p.name: digest(p) for p in jobs.iterdir()}
    out = tmp_path / "fixed"
    code = main(["fix", "knock-out-white", str(jobs), "--out-dir", str(out), "--yes"])
    text = capsys.readouterr().out
    assert code == 2  # one file could not be fixed: said, and the others still done
    assert "CHANGE   a.pdf: changes page 1; preflight FAIL -> PASS" in text
    assert "same     b.pdf: no change" in text and "ERROR    broken.pdf" in text
    assert sorted(os.listdir(out)) == ["a fixed.pdf"]
    with pikepdf.open(out / "a fixed.pdf") as pdf:
        assert [e.overprint for e in walk_page(pdf.pages[0]).events] == [False]
    assert {p.name: digest(p) for p in jobs.iterdir()} == before  # the originals untouched


def test_without_out_dir_nothing_is_written(make, tmp_path, capsys):
    a = make(WHITE_OP, name="a.pdf", extgstate=GS)
    b = make(WHITE_OP, name="b.pdf", extgstate=GS)
    assert main(["fix", "knock-out-white", a, b]) == 0
    assert "2 of 2 would change" in capsys.readouterr().out
    assert sorted(os.listdir(os.path.dirname(a))) == ["a.pdf", "b.pdf"]


def test_refusals(make, tmp_path, capsys):
    a = make(WHITE_OP, name="a.pdf", extgstate=GS)
    b = make(WHITE_OP, name="b.pdf", extgstate=GS)
    assert main(["fix", "knock-out-white", a, b, "--write", str(tmp_path / "x.pdf")]) == 2
    assert "--out-dir" in capsys.readouterr().err
    assert main(["fix", "remove-object", a, b, "--object", "1:0"]) == 2
    assert "cannot run on several" in capsys.readouterr().err
    used = tmp_path / "used"
    used.mkdir()
    (used / "x").write_text("x")
    assert main(["fix", "knock-out-white", a, b, "--out-dir", str(used), "--yes"]) == 2
    assert "not empty" in capsys.readouterr().err


def test_one_file_still_works_as_before(make, tmp_path, capsys):
    a = make(WHITE_OP, name="a.pdf", extgstate=GS)
    dst = tmp_path / "one.pdf"
    assert main(["fix", "knock-out-white", a, "--write", str(dst), "--yes"]) == 0
    assert dst.exists()
