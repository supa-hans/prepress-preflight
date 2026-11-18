"""Saved action lists, and per-step options."""
import pytest

from prepress_preflight.actions import load, parse_steps
from prepress_preflight.cli import main
from prepress_preflight.engine import run

from test_fixes import banner


@pytest.fixture(autouse=True)
def actions_folder(tmp_path, monkeypatch):
    folder = tmp_path / "actions"
    monkeypatch.setenv("PREFLIGHT_ACTIONS", str(folder))
    return folder


def test_save_list_show_and_delete(capsys):
    assert main(["actions", "save", "banner-press", "trim-from-marks", "add-marks --clip-to-bleed",
                 "--description", "Pull-up banners, press ready"]) == 0
    assert main(["actions", "list"]) == 0
    assert "@banner-press  Pull-up banners, press ready" in capsys.readouterr().out
    assert main(["actions", "show", "banner-press"]) == 0
    assert "add-marks --clip-to-bleed" in capsys.readouterr().out
    assert main(["actions", "delete", "banner-press"]) == 0
    assert main(["actions", "show", "banner-press"]) == 2


def test_save_refuses_mistakes_and_does_not_overwrite(capsys):
    assert main(["actions", "save", "x", "make-it-nice"]) == 2
    assert main(["actions", "save", "x", "add-bleed --sparkle"]) == 2
    assert main(["actions", "save", "Bad Name", "add-bleed"]) == 2
    assert main(["actions", "save", "x", "add-bleed"]) == 0
    assert main(["actions", "save", "x", "add-marks"]) == 2
    assert main(["actions", "save", "x", "add-marks", "--replace"]) == 0
    assert load("x") == [("add-marks", [])]


def test_parse_steps_ignores_comments_and_blank_lines():
    text = "# Canva press\n\nadd-bleed --clip-to-bleed --marks  \n# end\n"
    assert parse_steps(text) == [("add-bleed", ["--clip-to-bleed", "--marks"])]


def test_running_a_saved_list(make, tmp_path, capsys):
    assert main(["actions", "save", "banner-press", "trim-from-marks", "add-marks --clip-to-bleed"]) == 0
    dst = tmp_path / "press.pdf"
    assert main(["fix", "@banner-press", banner(make), "--write", str(dst), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "STEP 1 of 2" in out and "Clips everything on the page to the bleed box" in out
    result = run(str(dst))
    assert [f for f in result.findings if f.check == "Trim marks" and "agree" in f.message]


def test_a_steps_own_options_apply_only_to_that_step(make, tmp_path, capsys):
    # The clip is asked for on add-marks only; trim-from-marks has no such option to show.
    assert main(["actions", "save", "p", "trim-from-marks", "add-marks --clip-to-bleed"]) == 0
    assert main(["fix", "@p", banner(make), "--no-preview"]) == 0
    out = capsys.readouterr().out
    first, second = out.split("STEP 2 of 2")
    assert "Clips" not in first and "Clips" in second


def test_job_options_on_the_command_line_reach_every_step(make, capsys):
    from conftest import MEDIA
    # --min-bleed typed for the job: the bleed box made by trim-from-marks follows it.
    assert main(["fix", "--min-bleed", "5", "trim-from-marks", banner(make), "--no-preview"]) == 0
    assert "110.0 × 70.0 mm" in capsys.readouterr().out  # 100 x 60 trim + 5 mm each side


def test_unknown_list(make, capsys):
    assert main(["fix", "@nothing-here", make(b""), "--no-preview"]) == 2
    assert "no action list" in capsys.readouterr().err
