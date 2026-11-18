"""scale: setting the trim to the job size by scaling the whole page."""
import hashlib
import os

import pikepdf
import pytest

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError

from conftest import MM, TRIM, gray_image, rect

propose, write = FIXES["scale"]
ART = b"0 0 0 1 k " + rect(TRIM, grow=3 * MM) + b"f 1 0 0 0 k 100 100 60 40 re f"  # bleeds 3 mm, plus a cyan box


def job(w, h, **kw):
    return Options(expected_trim_mm=(w, h), **kw)


def trim_mm(path):
    with pikepdf.open(path) as pdf:
        t = [float(v) for v in pdf.pages[0].obj.TrimBox]
    return round((t[2] - t[0]) / MM, 1), round((t[3] - t[1]) / MM, 1)


def scaled(make, tmp_path, src, options, **kw):
    dst = str(tmp_path / "scaled.pdf")
    write(propose(src, options, **kw), dst)
    return dst


def test_same_shape_scales_to_the_exact_size(make, tmp_path):
    src = make(ART)  # 100 x 60 mm
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    dst = scaled(make, tmp_path, src, job(150, 90))
    assert trim_mm(dst) == (150.0, 90.0)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    result = run(dst)
    assert not [f for f in result.findings if f.check in ("Bleed box", "Bleed artwork") and f.severity == "fail"]


def test_small_change_of_shape_is_made_and_reported(make, tmp_path):
    p = propose(make(ART), job(100.5, 60))  # 0.5% wider
    assert "width 100.50%, height 100.00%" in p.reasons[0][1] and "change of shape" in p.reasons[0][1]
    dst = str(tmp_path / "s.pdf")
    write(p, dst)
    assert trim_mm(dst) == (100.5, 60.0)


def test_large_change_of_shape_needs_fill_or_fit(make):
    p = propose(make(ART), job(100, 100))
    assert p.empty and "--fill" in p.skipped[0][1] and "--fit" in p.skipped[0][1]


def test_fill_covers_the_trim_and_reports_what_is_cropped(make, tmp_path):
    p = propose(make(ART), job(100, 100), mode="fill")
    text = p.reasons[0][1]
    assert "166.67% both ways" in text and "cropped past the new trim: left" in text
    dst = str(tmp_path / "s.pdf")
    write(p, dst)
    assert trim_mm(dst) == (100.0, 100.0)


def test_fit_fits_inside_and_reports_the_white_margins(make, tmp_path):
    p = propose(make(ART), job(100, 100), mode="fit")
    text = p.reasons[0][1]
    assert "100.00% both ways" in text and "white margins inside the new trim: bottom 20.0 mm" in text
    dst = str(tmp_path / "s.pdf")
    write(p, dst)
    assert trim_mm(dst) == (100.0, 100.0)


def test_job_size_is_turned_to_the_page(make):
    assert "Scales to 150.0 × 90.0 mm" in propose(make(ART), job(90, 150)).reasons[0][1]


def test_scaling_up_lowers_image_resolution_and_the_preview_shows_it(make, tmp_path):
    content = ART + b" q 72 0 0 72 100 60 cm /Im0 Do Q"  # 100 px over 1 inch: 100 ppi
    src = make(content, xobject={"/Im0": gray_image(100, 100)})
    p = propose(src, job(200, 120))
    assert "50% of their ppi" in p.reasons[0][1]
    dst = str(tmp_path / "s.pdf")
    write(p, dst)
    warns = [f.message for f in run(dst).findings if f.check == "Image resolution" and f.severity == "warn"]
    assert warns and "50 ppi" in warns[0]


def test_scaling_down_warns_that_lines_get_thinner(make):
    assert "thinner" in propose(make(ART), job(50, 30)).reasons[0][1]


def test_links_move_and_scale_with_the_page(make, tmp_path):
    src = make(ART)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        link = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
                                                    Rect=[100, 100, 150, 120], F=4))
        pdf.pages[0].obj["/Annots"] = pikepdf.Array([link])
        pdf.save(src)
    dst = scaled(make, tmp_path, src, job(200, 120))
    with pikepdf.open(dst) as pdf:
        r = [float(v) for v in pdf.pages[0].obj.Annots[0].Rect]
    assert r[2] - r[0] == pytest.approx(100, abs=0.01) and r[3] - r[1] == pytest.approx(40, abs=0.01)


def test_rotated_page_scales_and_verifies(make, tmp_path):
    dst = scaled(make, tmp_path, make(ART, rotate=90), job(150, 90))
    assert trim_mm(dst) == (150.0, 90.0)


def test_page_with_the_right_size_is_left_alone(make):
    assert propose(make(ART), job(100, 60)).empty


def test_crop_marks_that_disagree_send_you_to_trim_from_marks(make):
    from test_fixes import banner
    p = propose(banner(make), job(200, 120))
    assert p.empty and "trim-from-marks" in p.skipped[0][1]


def test_over_200_inches_is_refused(make):
    p = propose(make(ART), job(6000, 3600))
    assert p.empty and "200 inches" in p.skipped[0][1]


def test_verification_refuses_a_scale_that_does_not_match(make, tmp_path, monkeypatch):
    import prepress_preflight.fixes.scale as sc
    real = sc._wrap
    # The written content draws the artwork 20 pt off to one side of where the plan says.
    monkeypatch.setattr(sc, "_wrap", lambda m: real((m[0], m[1], m[2] + 20, m[3])))
    dst = tmp_path / "s.pdf"
    with pytest.raises(AppendError, match="does not match"):
        write(propose(make(ART), job(150, 90)), str(dst))
    assert not dst.exists()


def test_command_needs_the_job_size(make, capsys):
    from prepress_preflight.cli import main
    assert main(["fix", "scale", make(ART), "--no-preview"]) == 2
    assert main(["fix", "scale", make(ART, name="b.pdf"), "--trim", "150x90", "--no-preview"]) == 0
    assert "Scales to 150.0 × 90.0 mm" in capsys.readouterr().out



def test_fill_replaces_crop_marks_that_would_mark_the_old_shape(make, tmp_path):
    from test_fixes import crop_marks
    from conftest import MEDIA, registration_space
    from prepress_preflight.marks import implied_trim
    from prepress_preflight.content import walk_page
    src = make(ART + b" " + crop_marks(TRIM), colorspace={"/CS0": registration_space()})
    p = propose(src, job(100, 100), mode="fill")
    assert p.plan[1]["remove"] and "crop marks are replaced" in p.reasons[0][1]
    dst = str(tmp_path / "s.pdf")
    write(p, dst)
    with pikepdf.open(dst) as pdf:
        t = [float(v) for v in pdf.pages[0].obj.TrimBox]
        assert implied_trim(walk_page(pdf.pages[0])) == pytest.approx(t, abs=0.05)
    assert not [f for f in run(dst).findings if "inferred" in f.message]


def test_same_shape_scaling_keeps_the_marks_which_scale_with_the_page(make, tmp_path):
    from test_fixes import crop_marks
    from conftest import registration_space
    src = make(ART + b" " + crop_marks(TRIM), colorspace={"/CS0": registration_space()})
    p = propose(src, job(150, 90))
    assert not p.plan[1]["remove"] and not p.plan[1]["marks"]
    dst = str(tmp_path / "s.pdf")
    write(p, dst)
    assert [f for f in run(dst).findings if f.check == "Trim marks" and "agree" in f.message]


def test_tiny_scale_does_not_claim_a_change_of_resolution(make):
    assert "ppi" not in propose(make(ART), job(100.4, 60.2)).reasons[0][1]


def test_fill_bleed_box_is_the_scaled_bleed_not_the_overflow(make):
    p = propose(make(ART), job(100, 100), mode="fill")  # 166.67%: a 3 mm bleed becomes 5 mm
    bleed = {c.box: c for c in p.changes}["BleedBox"]
    assert bleed.after_mm == "110.0 × 110.0 mm"
