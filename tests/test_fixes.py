"""Fixes: proposals, the append-only writer and its verification."""
import hashlib
import os

import pikepdf
import pytest

from prepress_preflight.cli import main
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError, append_update, verify_append

from conftest import MEDIA, MM, TRIM, rect, registration_space

propose, write = FIXES["trim-from-marks"]
REG = {"/CS0": registration_space()}


def crop_marks(box, offset=2 * MM, length=5 * MM):
    x0, y0, x1, y1 = box
    lines = []
    for x in (x0, x1):
        lines += [(x, y0 - offset - length, x, y0 - offset), (x, y1 + offset, x, y1 + offset + length)]
    for y in (y0, y1):
        lines += [(x0 - offset - length, y, x0 - offset, y), (x1 + offset, y, x1 + offset + length, y)]
    path = " ".join(f"{a:.2f} {b:.2f} m {c:.2f} {d:.2f} l" for a, b, c, d in lines)
    return f"/CS0 CS 1 SCN 0.25 w {path} S".encode()


# The pull-up banner in miniature: trim box = the whole page, crop marks inside it, artwork to the edge.
BANNER = b"0 0 0 1 k " + rect(MEDIA) + b"f " + crop_marks(TRIM, offset=1 * MM, length=6 * MM)


def banner(make, name="banner.pdf"):
    return make(BANNER, name=name, colorspace=REG, trim=MEDIA, bleed=MEDIA)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_proposal_sets_trim_to_the_marks_and_bleed_to_3mm_beyond(make):
    p = propose(banner(make))
    boxes = {c.box: c for c in p.changes}
    assert set(boxes) == {"TrimBox", "BleedBox"}
    assert boxes["TrimBox"].after == pytest.approx(TRIM, abs=0.01)  # marks are drawn to 0.01 pt
    assert boxes["TrimBox"].after_mm == "100.0 × 60.0 mm"
    assert boxes["BleedBox"].after_mm == "106.0 × 66.0 mm"


def test_proposal_writes_nothing(make):
    path = banner(make)
    before = sha(path), sorted(os.listdir(os.path.dirname(path)))
    propose(path)
    assert (sha(path), sorted(os.listdir(os.path.dirname(path)))) == before


def test_nothing_to_change_when_marks_agree(make):
    assert propose(make(BANNER, colorspace=REG)).empty


def test_written_file_is_the_original_plus_the_change(make, tmp_path):
    src = banner(make)
    original, size = sha(src), os.path.getsize(src)
    dst = str(tmp_path / "fixed.pdf")
    write(propose(src), dst)
    assert sha(src) == original  # the input is never written to
    with open(dst, "rb") as f:
        assert hashlib.sha256(f.read(size)).hexdigest() == original
    with pikepdf.open(dst) as pdf:
        assert [float(v) for v in pdf.pages[0].obj.TrimBox] == pytest.approx(TRIM, abs=0.01)
        with pikepdf.open(src) as orig:
            for key in ("/MediaBox", "/CropBox", "/Contents", "/Resources"):
                if key in orig.pages[0].obj:
                    assert pdf.pages[0].obj[key].unparse(resolved=True) == orig.pages[0].obj[key].unparse(resolved=True)
        assert not pdf.check_pdf_syntax()
    result = run(dst)
    assert not any("inferred" in f.message for f in result.findings)
    assert not [f for f in result.findings if f.severity == "fail"]


def test_works_on_files_with_cross_reference_streams(make, tmp_path):
    src = str(tmp_path / "xrefstream.pdf")
    with pikepdf.open(banner(make)) as pdf:
        pdf.save(src, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    with open(src, "rb") as f:
        assert b"/XRef" in f.read()
    dst = str(tmp_path / "fixed.pdf")
    write(propose(src), dst)
    with pikepdf.open(dst) as pdf:
        assert [float(v) for v in pdf.pages[0].obj.TrimBox] == pytest.approx(TRIM, abs=0.01)
        assert not pdf.check_pdf_syntax()


def test_refuses_to_overwrite(make, tmp_path):
    src = banner(make)
    p = propose(src)
    with pytest.raises(AppendError):
        write(p, src)
    existing = tmp_path / "taken.pdf"
    existing.write_bytes(b"keep me")
    with pytest.raises(AppendError):
        write(p, str(existing))
    assert existing.read_bytes() == b"keep me"


def test_verification_catches_a_change_that_was_not_intended(make, tmp_path):
    src = banner(make)
    dst = str(tmp_path / "fixed.pdf")
    write(propose(src), dst)
    with pikepdf.open(src) as pdf:
        page = pdf.pages[0].obj
        other = pikepdf.Dictionary({k: page[k] for k in page.keys()})
        other["/TrimBox"] = [0, 0, 10, 10]
        with pytest.raises(AppendError):
            verify_append(src, dst, os.path.getsize(src), {page.objgen: other})


def test_verification_catches_another_object_changing(make, tmp_path):
    # Write an update that also replaces the page's content stream dictionary,
    # then verify against a list that only allows the page: it must refuse.
    src = banner(make)
    sneaky = str(tmp_path / "sneaky.pdf")
    import prepress_preflight.fixes.append as append
    real = append.verify_append
    try:
        append.verify_append = lambda *a: None  # write it without checking
        with pikepdf.open(src) as pdf:
            catalog = pdf.Root
            changed = pikepdf.Dictionary({k: catalog[k] for k in catalog.keys()})
            changed["/PageMode"] = pikepdf.Name.UseNone
            append_update(src, sneaky, {catalog.objgen: changed})
    finally:
        append.verify_append = real
    with pikepdf.open(src) as pdf:
        page = pdf.pages[0].obj
        allowed = {page.objgen: pikepdf.Dictionary({k: page[k] for k in page.keys()})}
        with pytest.raises(AppendError, match="not meant to"):
            verify_append(src, sneaky, os.path.getsize(src), allowed)


def test_failed_write_leaves_no_file(make, tmp_path, monkeypatch):
    import prepress_preflight.fixes.append as append
    def broken(*args):
        raise AppendError("verification failed: test")
    monkeypatch.setattr(append, "verify_append", broken)
    dst = tmp_path / "fixed.pdf"
    with pytest.raises(AppendError):
        write(propose(banner(make)), str(dst))
    assert not dst.exists() and not (tmp_path / "fixed.pdf.partial").exists()


# --- command line
def test_command_without_write_only_proposes(make, tmp_path, capsys):
    src = banner(make)
    assert main(["fix", "trim-from-marks", src]) == 0
    out = capsys.readouterr().out
    assert "845" not in out and "100.0 × 60.0 mm" in out and "Nothing written" in out
    assert "gone:" in out and "Trim marks" in out


def test_command_needs_confirmation(make, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    dst = tmp_path / "fixed.pdf"
    assert main(["fix", "trim-from-marks", banner(make), "--write", str(dst), "--no-preview"]) == 2
    assert not dst.exists()


def test_command_answer_other_than_yes_writes_nothing(make, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    dst = tmp_path / "fixed.pdf"
    assert main(["fix", "trim-from-marks", banner(make), "--write", str(dst), "--no-preview"]) == 1
    assert not dst.exists()


def test_command_writes_with_yes(make, tmp_path):
    dst = tmp_path / "fixed.pdf"
    assert main(["fix", "trim-from-marks", banner(make), "--write", str(dst), "--yes", "--no-preview"]) == 0
    assert dst.exists()
