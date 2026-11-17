"""Trim marks and dielines."""
import pikepdf

from prepress_preflight.engine import run
from prepress_preflight.model import NOTE, WARN

from conftest import BLEEDBOX, MEDIA, MM, TRIM, rect, registration_space

REG = {"/CS0": registration_space()}
K_BOX = b"0 0 0 1 k 100 100 50 50 re f"


def found(result, check, severity):
    return [f for f in result.findings if f.check == check and f.severity == severity]


def crop_marks(box, offset=2 * MM, length=5 * MM):
    """The usual eight marks around `box`: a white keyline with a thin registration line on top."""
    x0, y0, x1, y1 = box
    lines = []
    for x in (x0, x1):
        lines += [(x, y0 - offset - length, x, y0 - offset), (x, y1 + offset, x, y1 + offset + length)]
    for y in (y0, y1):
        lines += [(x0 - offset - length, y, x0 - offset, y), (x1 + offset, y, x1 + offset + length, y)]
    path = " ".join(f"{a:.2f} {b:.2f} m {c:.2f} {d:.2f} l" for a, b, c, d in lines)
    return f"/CS0 CS 0 SCN 1.25 w {path} S 1 SCN 0.25 w {path} S ".encode()


def shifted(box, dx=0.0, dy=0.0, grow=0.0):
    return (box[0] + dx - grow, box[1] + dy - grow, box[2] + dx + grow, box[3] + dy + grow)


# --- trim marks
def test_marks_on_the_trim_box_agree(make):
    result = run(make(K_BOX + b" " + crop_marks(TRIM), colorspace=REG))
    assert found(result, "Trim marks", NOTE) and not found(result, "Trim marks", WARN)


def test_marks_pointing_at_a_smaller_size_warn_with_both_sizes(make):
    result = run(make(K_BOX + b" " + crop_marks(shifted(TRIM, grow=-5 * MM), offset=1 * MM, length=3 * MM), colorspace=REG))
    f = found(result, "Trim marks", WARN)
    assert f and "90.0 × 50.0 mm" in f[0].message and "100.0 × 60.0 mm" in f[0].message


def test_marks_of_the_right_size_in_the_wrong_place_say_so(make):
    f = found(run(make(K_BOX + b" " + crop_marks(shifted(TRIM, dx=2 * MM)), colorspace=REG)), "Trim marks", WARN)
    assert f and "2.0 mm away" in f[0].message


def test_marks_show_the_trim_when_no_trim_box_is_defined(make):
    f = found(run(make(K_BOX + b" " + crop_marks(TRIM), colorspace=REG, trim=None, bleed=None)), "Trim marks", WARN)
    assert f and "100.0 × 60.0 mm" in f[0].message and "no trim box is defined" in f[0].message


def test_bleed_marks_do_not_confuse_the_trim(make):
    content = K_BOX + b" " + crop_marks(TRIM) + crop_marks(BLEEDBOX, offset=4 * MM, length=2 * MM)
    result = run(make(content, colorspace=REG))
    assert found(result, "Trim marks", NOTE) and not found(result, "Trim marks", WARN)


def test_registration_target_alone_is_not_a_set_of_marks(make):
    cx, cy = (MEDIA[2]) / 2, 10
    target = f"/CS0 CS 1 SCN 0.25 w {cx - 6:.2f} {cy:.2f} m {cx + 6:.2f} {cy:.2f} l {cx:.2f} {cy - 6:.2f} m {cx:.2f} {cy + 6:.2f} l S".encode()
    assert not [f for f in run(make(K_BOX + b" " + target, colorspace=REG)).findings if f.check == "Trim marks"]


def test_page_without_marks_says_nothing(make):
    assert not [f for f in run(make(K_BOX)).findings if f.check == "Trim marks"]


# --- dielines
def spot(name):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/" + name), pikepdf.Name.DeviceCMYK, tint])


OVERPRINT = {"/OP": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
DIE_BOX = (TRIM[0] + 10 * MM, TRIM[1] + 10 * MM, TRIM[0] + 60 * MM, TRIM[1] + 40 * MM)  # 50 x 30 mm


def dieline(box, overprint=True):
    return (b"/OP gs " if overprint else b"") + b"/CS0 CS 1 SCN 0.5 w " + rect(box) + b"S"


def test_dieline_is_measured(make):
    result = run(make(K_BOX + b" " + dieline(DIE_BOX), colorspace={"/CS0": spot("Dieline")}, extgstate=OVERPRINT))
    f = found(result, "Dielines", NOTE)
    assert f and '"Dieline"' in f[0].message and "50.0 × 30.0 mm" in f[0].message
    assert not found(result, "Dielines", WARN) and result.status == "PASS"


def test_dieline_that_does_not_overprint_warns(make):
    f = found(run(make(K_BOX + b" " + dieline(DIE_BOX, overprint=False), colorspace={"/CS0": spot("CutContour")})), "Dielines", WARN)
    assert f and "not set to overprint" in f[0].message and "CutContour" in f[0].message


def test_dieline_on_the_trim_is_said_to_match_it(make):
    result = run(make(K_BOX + b" " + dieline(TRIM), colorspace={"/CS0": spot("Dieline")}, extgstate=OVERPRINT))
    assert "the size of the trim box" in found(result, "Dielines", NOTE)[0].message


def test_other_spot_colours_are_not_dielines(make):
    for name in ("Varnish", "PANTONE 185 C"):
        result = run(make(K_BOX + b" " + dieline(DIE_BOX, overprint=False), colorspace={"/CS0": spot(name)}))
        assert not [f for f in result.findings if f.check == "Dielines"]
