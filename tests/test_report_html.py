"""The HTML report: one self-contained file to keep or send."""
import datetime
import hashlib
import os
import re

import pikepdf
import pytest

from prepress_preflight.checks import Options
from prepress_preflight.cli import main
from prepress_preflight.engine import run
from prepress_preflight.report_html import build_html, default_report_path, write_report

from conftest import registration_space

WHITE_OP = b"/GS0 gs 0 0 0 0 k 120 120 20 20 re f"
GS = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}


def test_report_has_status_findings_why_and_a_marked_page(make):
    src = make(WHITE_OP, extgstate=GS)
    page = build_html(run(src), Options(), now=datetime.datetime(2026, 11, 19, 9, 5))
    assert '<span class="status FAIL">FAIL</span>' in page
    assert "White set to overprint, it will not print: 1 vector object(s)" in page
    assert "White set to overprint does not print at all" in page  # why it matters
    assert "19 November 2026, 09:05" in page
    assert "3 mm bleed, 1.5 mm safe margin, 400% total ink" in page
    assert page.count("data:image/jpeg;base64,") == 1  # the page with the marked finding
    assert "<title>Finding 1</title>" in page
    assert "src=\"http" not in page and "href=\"http" not in page  # nothing loaded from elsewhere


def test_notes_have_no_why_line(make):
    page = build_html(run(make(b"0 0 0 1 k 100 100 50 50 re f")))
    assert 'class="sev note"' in page and 'class="why"' not in page


def test_a_clean_file_passes_with_no_pictures(make):
    from conftest import BLEEDBOX, rect
    page = build_html(run(make(b"0 0 0 1 k " + rect(BLEEDBOX) + b"f")))
    assert "data:image" not in page and "<h2>Passed</h2>" in page


def test_names_are_escaped(make, tmp_path):
    src = make(WHITE_OP, name="<b>&job.pdf", extgstate=GS)
    page = build_html(run(src))
    assert "<h1>&lt;b&gt;&amp;job.pdf</h1>" in page


def test_never_overwrites_and_needs_html(make, tmp_path):
    src = make(WHITE_OP, extgstate=GS)
    result = run(src)
    with pytest.raises(ValueError, match="already exists"):
        write_report(result, src)  # the PDF itself, above all
    with pytest.raises(ValueError, match=".html"):
        write_report(result, str(tmp_path / "report.pdf"))
    first = default_report_path(src)
    assert first.endswith("t preflight report.html")
    write_report(result, first)
    assert default_report_path(src).endswith("t preflight report 2.html")


def test_command_line(make, tmp_path, capsys):
    src = make(WHITE_OP, extgstate=GS)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    dst = tmp_path / "out.html"
    assert main(["report", src, "--out", str(dst), "--ink-limit", "320"]) == 1  # a FAIL, as for `preflight`
    assert "FAIL: report written to" in capsys.readouterr().out
    assert "320% total ink" in dst.read_text()
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    assert main(["report", src, "--out", str(dst)]) == 2  # will not overwrite
    assert "already exists" in capsys.readouterr().err


def test_marks_on_a_rotated_page_are_where_the_finding_is(make):
    # registration at x 100-150, y 100-150 (PDF points) on a page turned 90 degrees
    src = make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace={"/CS0": registration_space()}, rotate=90)
    page = build_html(run(src))
    stroke_rects = re.findall(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" fill="#e24b4a"', page)
    assert stroke_rects
    x, y, w, h = (float(v) for v in stroke_rects[0])
    pad = (w - 50) / 2
    assert (x + pad, y + pad, w - 2 * pad, h - 2 * pad) == pytest.approx((100, 100, 50, 50), abs=0.2)


# ---- several files -------------------------------------------------------------------

from prepress_preflight.report_html import build_index  # noqa: E402


def test_folder_of_files_gets_reports_and_a_summary(make, tmp_path, capsys):
    good = make(b"0 0 0 1 k 100 100 50 50 re f", name="good.pdf")
    bad = make(WHITE_OP, name="bad #1.pdf", extgstate=GS)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    (tmp_path / "notes.txt").write_text("ignored")
    out = tmp_path / "reports"
    code = main(["report", str(tmp_path), good, "--out-dir", str(out)])  # good.pdf named twice: checked once
    assert code == 2  # one file could not be checked
    names = sorted(os.listdir(out))
    assert names == ["bad #1 preflight report.html", "good preflight report.html", "index.html"]
    index = (out / "index.html").read_text()
    assert "Preflight summary: 3 files" in index and "1 fail, 1 could not be checked" in index
    assert index.index("bad #1.pdf") < index.index("broken.pdf") < index.index("good.pdf")  # worst first
    assert 'href="bad%20%231%20preflight%20report.html"' in index  # links survive # and spaces
    assert "Could not check" in index


def test_several_files_refuse_a_used_folder_and_out(make, tmp_path, capsys):
    a, b = make(b"", name="a.pdf"), make(b"", name="b.pdf")
    used = tmp_path / "used"
    used.mkdir()
    (used / "x").write_text("x")
    assert main(["report", a, b, "--out-dir", str(used)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert main(["report", a, b, "--out", str(tmp_path / "r.html")]) == 2
    assert "--out-dir" in capsys.readouterr().err


def test_index_escapes_names(make):
    page = build_index([{"path": "/x/<b>.pdf", "error": "<oops>"}])
    assert "&lt;b&gt;.pdf" in page and "&lt;oops&gt;" in page and "<oops>" not in page
