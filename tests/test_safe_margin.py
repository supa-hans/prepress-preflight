"""Safe margin: text and loose artwork too close to the trim."""
import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.model import WARN

from conftest import BLEEDBOX, MM, TRIM, rect

CHECK = "Safe margin"
K = b"0 0 0 1 k "
FONT = {"/F1": pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)}


def warns(result):
    return [f for f in result.findings if f.check == CHECK and f.severity == WARN]


def text_at(x, y, words=b"Hello"):
    return K + f"BT /F1 10 Tf {x:.2f} {y:.2f} Td (".encode() + words + b") Tj ET"


def box_at(left_gap_mm, size_mm=10):
    """A black box whose left side is `left_gap_mm` inside the left trim."""
    x = TRIM[0] + left_gap_mm * MM
    return K + rect((x, 100, x + size_mm * MM, 100 + size_mm * MM)) + b"f"


def test_text_well_inside_the_trim_is_fine(make):
    assert not warns(run(make(text_at(TRIM[0] + 20 * MM, 100), font=FONT)))


def test_text_inside_the_margin_warns_with_its_distance(make):
    f = warns(run(make(text_at(TRIM[0] + 0.8 * MM, 100), font=FONT)))
    assert f and any("word(s) of text within 1.5 mm" in x.message and "0.8 mm" in x.message for x in f)


def test_text_crossing_the_trim_warns(make):
    f = warns(run(make(text_at(TRIM[0] - 2 * MM, 100), font=FONT)))
    assert f and any("cross the trim" in x.message for x in f)


def test_text_in_the_slug_is_ignored(make):
    assert not warns(run(make(text_at(2, 2, b"job 1234"), font=FONT)))


def test_text_near_the_edge_over_a_bleeding_background_still_warns(make):
    # The background reaches the trim, so only the text measurement can see this.
    content = b"0.2 0 0 0 k " + rect(BLEEDBOX) + b"f " + text_at(TRIM[0] + 0.8 * MM, 100)
    f = warns(run(make(content, font=FONT)))
    assert f and any("word(s) of text" in x.message for x in f)


def test_artwork_inside_the_margin_on_a_white_edge_warns(make):
    f = warns(run(make(box_at(0.9))))
    assert f and "(left)" in f[0].message
    # measured on a pixel grid of about 0.17 mm
    assert any(f"closest is {d} mm" in f[0].message for d in ("0.7", "0.8", "0.9", "1.0"))


def test_artwork_outside_the_margin_is_fine(make):
    assert not warns(run(make(box_at(2))))  # like the border on an approved label


def test_artwork_that_reaches_the_trim_is_not_a_margin_problem(make):
    # It bleeds off the left edge: meant to be cut through.
    assert not warns(run(make(K + rect((BLEEDBOX[0], 100, TRIM[0] + 20 * MM, 140)) + b"f")))


def test_the_margin_can_be_set(make):
    path = make(box_at(4))
    assert not warns(run(path))
    assert warns(run(path, Options(safe_margin_mm=5)))


def test_bound_job_leaves_the_spine_edge_out(make):
    path = make(box_at(0.9))  # page 1: the left edge is the spine
    assert warns(run(path))
    assert not warns(run(path, Options(bound=True)))


def test_rotated_page_names_the_edge_in_the_files_own_terms(make):
    f = warns(run(make(box_at(0.9), rotate=90)))
    assert f and "(left)" in f[0].message


def test_huge_display_type_running_off_the_page_is_not_reported_as_cut_text(make):
    big = K + f"BT /F1 200 Tf {TRIM[0] - 30:.2f} 60 Td (A) Tj ET".encode()
    assert not any("cross the trim" in x.message for x in warns(run(make(big, font=FONT))))


def test_pale_fade_near_the_edge_is_not_an_object(make):
    x = TRIM[0] + 0.9 * MM
    assert not warns(run(make(b"0 0 0 0.08 k " + rect((x, 100, x + 30, 130)) + b"f")))
