"""preflight watch: a folder whose PDFs are checked (and fixed) on their own."""
import hashlib
import json
import os
import time

import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.cli import main
from prepress_preflight.watch import Watcher

GS = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
WHITE_OP = b"/GS0 gs 0 0 0 0 k 120 120 20 20 re f"


def drop(make, folder, name, content=WHITE_OP, **kw):
    folder.mkdir(exist_ok=True)
    src = make(content, name=name, extgstate=GS, **kw)
    dst = folder / name
    os.replace(src, dst)
    old = time.time() - 60
    os.utime(dst, (old, old))  # settled
    return dst


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_each_pdf_is_checked_fixed_and_taken_once(make, tmp_path, capsys):
    inbox, out = tmp_path / "in", tmp_path / "out"
    a = drop(make, inbox, "a.pdf")
    b = drop(make, inbox, "b.pdf", b"0 0 0 1 k 100 100 50 50 re f")
    (inbox / "broken.pdf").write_bytes(b"nope")
    os.utime(inbox / "broken.pdf", (time.time() - 60, time.time() - 60))
    before = {p.name: digest(p) for p in inbox.iterdir()}
    code = main(["watch", str(inbox), "--out", str(out), "--fix", "knock-out-white", "--once"])
    assert code == 2  # the broken one is an ERROR, logged; the others are done
    log = (out / "log.txt").read_text()
    assert "a.pdf: FAIL, report reports/a preflight report.html; fixed copy fixed/a fixed.pdf: PASS" in log
    assert "b.pdf: PASS" in log and "fixes: no change" in log and "broken.pdf: ERROR" in log
    assert sorted(os.listdir(out / "fixed")) == ["a fixed.pdf"]
    assert sorted(os.listdir(out / "reports")) == ["a fixed preflight report.html", "a preflight report.html",
                                                   "b preflight report.html"]
    assert {p.name: digest(p) for p in inbox.iterdir()} == before  # only read
    # a second look takes nothing new
    capsys.readouterr()
    assert main(["watch", str(inbox), "--out", str(out), "--fix", "knock-out-white", "--once"]) == 0
    assert capsys.readouterr().out == ""
    # a replaced file is taken again
    os.utime(a, (time.time() - 30, time.time() - 30))
    assert main(["watch", str(inbox), "--out", str(out), "--fix", "knock-out-white", "--once"]) == 0
    assert sorted(os.listdir(out / "fixed")) == ["a fixed 2.pdf", "a fixed.pdf"]


def test_a_file_still_arriving_waits(make, tmp_path):
    inbox = tmp_path / "in"
    path = drop(make, inbox, "a.pdf")
    os.utime(path, None)  # written just now
    w = Watcher(str(inbox), str(tmp_path / "out"), Options(), settle=5)
    assert w.waiting() == []
    assert w.waiting(now=time.time() + 10) == [str(path)]


def test_job_settings_reach_the_checks_and_the_fixes(make, tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "press.txt").write_text("--ink-limit 300\n")
    monkeypatch.setenv("PREFLIGHT_JOBS", str(jobs))
    inbox, out = tmp_path / "in", tmp_path / "out"
    drop(make, inbox, "a.pdf", b"1 1 1 1 k 100 100 50 50 re f")
    assert main(["watch", str(inbox), "--out", str(out), "--fix", "reduce-ink", "--job", "press", "--once"]) == 0
    log = (out / "log.txt").read_text()
    assert "a.pdf: FAIL" in log and "fixed copy fixed/a fixed.pdf" in log
    assert "300% total ink" in (out / "reports" / "a preflight report.html").read_text()


def test_refusals(tmp_path, capsys):
    (tmp_path / "in").mkdir()
    assert main(["watch", str(tmp_path / "in"), "--out", str(tmp_path / "in"), "--once"]) == 2
    assert main(["watch", str(tmp_path / "in"), "--out", str(tmp_path / "o"), "--fix", "remove-object", "--once"]) == 2
    assert main(["watch", str(tmp_path / "nope"), "--out", str(tmp_path / "o"), "--once"]) == 2
    err = capsys.readouterr().err
    assert "must not be the watched folder" in err and "cannot run on a watched folder" in err and "not a folder" in err
