"""Action lists: several fixes in a row, one preview, one confirmation."""
import hashlib
import os

import pytest

from prepress_preflight.cli import main
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError
from prepress_preflight.fixes.chain import deliver, parse_chain

from test_fixes import banner


def sha(path, length=None):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read(length) if length else f.read()).hexdigest()


def test_chain_runs_each_step_on_the_result_of_the_last(make, tmp_path, capsys):
    src = banner(make)  # trim box wrong, marks inside it, artwork to the page edge
    original = sha(src)
    dst = str(tmp_path / "press.pdf")
    code = main(["fix", "trim-from-marks+add-marks", src, "--clip-to-bleed", "--write", dst, "--yes"])
    out = capsys.readouterr().out
    assert code == 0 and "STEP 1 of 2" in out and "STEP 2 of 2" in out
    assert "FIX  trim-from-marks" in out and "FIX  add-marks" in out
    assert sha(src) == original
    assert sha(dst, os.path.getsize(src)) == original  # still the original, with the updates after it
    result = run(dst)
    assert [f for f in result.findings if f.check == "Trim marks" and "agree" in f.message]
    assert not [f for f in result.findings if f.severity == "fail"]


def test_add_marks_on_its_own_would_refuse_the_banner(make):
    # The point of the chain: add-marks alone sees marks that disagree with the trim box.
    p = FIXES["add-marks"][0](banner(make))
    assert p.empty and "trim-from-marks" in p.skipped[0][1]


def test_a_step_with_nothing_to_do_is_shown_and_passed_over(make, tmp_path, capsys):
    src = banner(make)
    dst = str(tmp_path / "out.pdf")
    assert main(["fix", "set-boxes+trim-from-marks", src, "--write", dst, "--yes", "--no-preview"]) == 0
    out = capsys.readouterr().out
    assert "use trim-from-marks instead" in out and "FIX  trim-from-marks" in out
    assert os.path.exists(dst)


def test_nothing_to_change_anywhere_writes_nothing(make, tmp_path, capsys):
    src = make(b"0 0 0 1 k 100 100 50 50 re f")
    dst = tmp_path / "out.pdf"
    assert main(["fix", "trim-from-marks+set-boxes", src, "--write", str(dst), "--yes"]) == 0
    assert not dst.exists()


def test_a_failing_step_writes_nothing(make, tmp_path, monkeypatch, capsys):
    import prepress_preflight.fixes as fixes
    propose, _ = fixes.FIXES["add-marks"]

    def broken(proposal, dst):
        raise AppendError("verification failed: test")
    monkeypatch.setitem(fixes.FIXES, "add-marks", (propose, broken))
    dst = tmp_path / "out.pdf"
    # with the clip, step 2 has work to do (it replaces the marks), so its writer runs
    assert main(["fix", "trim-from-marks+add-marks", banner(make), "--clip-to-bleed", "--write", str(dst), "--yes"]) == 2
    assert "step 2 (add-marks)" in capsys.readouterr().err and not dst.exists()


def test_unknown_fix_name(make, capsys):
    assert main(["fix", "trim-from-marks+make-it-nice", make(b""), "--no-preview"]) == 2
    assert "make-it-nice" in capsys.readouterr().err


def test_parse_chain():
    assert parse_chain("trim-from-marks + add-bleed", FIXES) == ["trim-from-marks", "add-bleed"]
    with pytest.raises(ValueError):
        parse_chain("", FIXES)


def test_deliver_refuses_a_result_that_lost_the_original(make, tmp_path):
    src = make(b"0 0 0 1 k 100 100 50 50 re f")
    other = make(b"1 0 0 0 k 10 10 50 50 re f", name="other.pdf")
    with pytest.raises(AppendError, match="original bytes"):
        deliver(src, other, str(tmp_path / "out.pdf"))
    assert not (tmp_path / "out.pdf").exists()
