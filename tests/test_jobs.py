"""Saved job settings: --job NAME on every command."""
import pytest

from prepress_preflight import jobs
from prepress_preflight.cli import main


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_JOBS", str(tmp_path / "jobs"))
    return tmp_path / "jobs"


def test_save_list_show_delete(folder, capsys):
    assert main(["jobs", "save", "coated", "--description", "Coated sheetfed", "--", "--ink-limit", "320",
                 "--safe-margin", "3"]) == 0
    assert (folder / "coated.txt").read_text() == "# Coated sheetfed\n--ink-limit 320 --safe-margin 3\n"
    assert main(["jobs", "save", "coated", "--", "--ink-limit", "300"]) == 2  # exists
    assert main(["jobs", "save", "bad", "--", "--ink-limt", "300"]) == 2  # a typo is refused, not saved
    assert not (folder / "bad.txt").exists()
    capsys.readouterr()
    assert main(["jobs", "list"]) == 0
    assert "coated: --ink-limit 320 --safe-margin 3  (Coated sheetfed)" in capsys.readouterr().out
    assert main(["jobs", "delete", "coated"]) == 0 and not (folder / "coated.txt").exists()


def test_expand_puts_the_job_first_so_typed_options_win(folder):
    folder.mkdir()
    (folder / "coated.txt").write_text("# x\n--ink-limit 320\n--safe-margin 3 --cmyk-only\n")
    argv, name = jobs.expand(["a.pdf", "--job", "coated", "--ink-limit", "300"])
    assert name == "coated" and argv == ["--ink-limit", "320", "--safe-margin", "3", "--cmyk-only", "a.pdf", "--ink-limit", "300"]
    # a fix step takes only the options it knows
    assert jobs.expand(["fix", "--job=coated"], step_only=True)[0] == ["--ink-limit", "320", "fix"]
    with pytest.raises(ValueError, match="no job 'nope'"):
        jobs.expand(["--job", "nope"])
    with pytest.raises(ValueError, match="not a usable name"):
        jobs.expand(["--job", "../etc"])


def test_a_job_drives_the_check_and_the_report(folder, make, tmp_path, capsys):
    folder.mkdir()
    (folder / "press.txt").write_text("--ink-limit 300\n")
    src = make(b"1 1 1 1 k 100 100 50 50 re f")
    assert main([src, "--ink-limit", "400"]) == 1  # 400% reaches a 400 limit: a fail either way
    capsys.readouterr()
    main([src, "--job", "press"])
    out = capsys.readouterr().out
    assert out.startswith("Job settings: press") and "over 300%" in out
    main([src, "--job", "press", "--ink-limit", "350"])
    assert "over 350%" in capsys.readouterr().out  # typed wins
    dst = tmp_path / "r.html"
    main(["report", src, "--job", "press", "--out", str(dst)])
    page = dst.read_text()
    assert "Job settings" in page and ">press<" in page and "300% total ink" in page


def test_fix_takes_the_press_limit_from_the_job(folder, make, tmp_path, capsys):
    folder.mkdir()
    (folder / "press.txt").write_text("--ink-limit 300 --safe-margin 3\n")
    src = make(b"1 1 1 1 k 100 100 50 50 re f")
    assert main(["fix", "reduce-ink", src, "--job", "press", "--no-preview"]) == 0
    assert "over 300% total ink" in capsys.readouterr().out
