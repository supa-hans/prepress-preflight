import hashlib

import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.model import FAIL, NOTE, WARN
from prepress_preflight.report import page_list, render

from conftest import BLEEDBOX, MEDIA, MM, TRIM, gray_image, registration_space

K_BOX = b"0 0 0 1 k 100 100 50 50 re f"  # plain black box inside the trim


def found(result, check, severity):
    return [f for f in result.findings if f.check == check and f.severity == severity]


def test_clean_file_passes(make):
    result = run(make(K_BOX))
    assert result.status == "PASS"
    assert not [f for f in result.findings if f.severity != NOTE]


def test_input_file_is_never_modified(make):
    path = make(K_BOX)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    run(path)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before


# --- bleed box
def test_bleed_under_3mm_fails(make):
    b = 2 * MM
    short = (TRIM[0] - b, TRIM[1] - b, TRIM[2] + b, TRIM[3] + b)
    assert found(run(make(K_BOX, bleed=short)), "Bleed box", FAIL)


def test_bleed_short_on_one_edge_fails(make):
    uneven = (BLEEDBOX[0], BLEEDBOX[1], BLEEDBOX[2], TRIM[3] + 1 * MM)
    f = found(run(make(K_BOX, bleed=uneven)), "Bleed box", FAIL)
    assert f and "top" in f[0].message and "left" not in f[0].message


def test_more_than_3mm_bleed_is_fine(make):
    b = 5 * MM
    big = (TRIM[0] - b, TRIM[1] - b, TRIM[2] + b, TRIM[3] + b)
    assert run(make(K_BOX, bleed=big)).status == "PASS"


def test_bleed_box_not_defined_but_page_larger_warns(make):
    result = run(make(K_BOX, bleed=None))
    assert found(result, "Bleed box", WARN) and not found(result, "Bleed box", FAIL)


def test_bleed_box_equal_to_trim_counts_as_not_defined(make):
    assert found(run(make(K_BOX, bleed=TRIM)), "Bleed box", WARN)


def test_page_ending_at_trim_fails(make):
    assert found(run(make(K_BOX, media=TRIM, trim=None, bleed=None)), "Bleed box", FAIL)


# --- RGB
def test_rgb_is_a_warning_not_a_fail(make):
    result = run(make(b"1 0 0 rg 100 100 50 50 re f"))
    assert found(result, "RGB", WARN) and result.status == "WARN"


def test_rgb_in_the_slug_is_ignored(make):
    assert not found(run(make(b"1 0 0 rg 1 1 5 5 re f")), "RGB", WARN)


# --- white overprint
OP = {"/GS1": pikepdf.Dictionary(OP=True, op=True, OPM=1)}


def test_white_overprint_fails(make):
    result = run(make(b"/GS1 gs 0 0 0 0 k 100 100 50 50 re f", extgstate=OP))
    assert found(result, "White overprint", FAIL)


def test_white_without_overprint_is_fine(make):
    assert run(make(b"0 0 0 0 k 100 100 50 50 re f", extgstate=OP)).status == "PASS"


def test_black_overprint_is_fine(make):
    assert run(make(b"/GS1 gs 0 0 0 1 k 100 100 50 50 re f", extgstate=OP)).status == "PASS"


def test_overprint_is_restored_by_Q(make):
    content = b"q /GS1 gs 0 0 0 1 k 100 100 20 20 re f Q 0 0 0 0 k 100 100 50 50 re f"
    assert not found(run(make(content, extgstate=OP)), "White overprint", FAIL)


# --- registration
REG = {"/CS0": registration_space()}


def test_registration_inside_trim_fails(make):
    result = run(make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace=REG))
    assert found(result, "Registration inside trim", FAIL)


def test_crop_marks_outside_trim_are_fine(make):
    # Two marks at opposite corners drawn as ONE path: together they span the page,
    # but neither mark is inside the trim.
    x1, y1 = MEDIA[2], MEDIA[3]
    marks = f"/CS0 CS 1 SCN 0 {TRIM[1]} m 20 {TRIM[1]} l {x1 - 20} {TRIM[3]} m {x1} {TRIM[3]} l S".encode()
    assert not found(run(make(marks, colorspace=REG)), "Registration inside trim", FAIL)


def test_registration_at_zero_tint_is_fine(make):
    assert not found(run(make(b"/CS0 cs 0 scn 100 100 50 50 re f", colorspace=REG)), "Registration inside trim", FAIL)


# --- blend modes
def test_blend_mode_is_a_note(make):
    gs = {"/GS1": pikepdf.Dictionary(BM=pikepdf.Name.Multiply)}
    result = run(make(b"/GS1 gs " + K_BOX, extgstate=gs))
    assert found(result, "Blend modes", NOTE) and result.status == "PASS"


# --- total ink
def test_400_percent_ink_fails(make):
    assert found(run(make(b"1 1 1 1 k 100 100 50 50 re f")), "Total ink", FAIL)


def test_300_percent_ink_is_fine(make):
    assert not found(run(make(b"1 1 1 0 k 100 100 50 50 re f")), "Total ink", FAIL)


def test_registration_marks_in_slug_do_not_count_as_ink(make):
    assert not found(run(make(b"1 1 1 1 k 1 1 5 5 re f")), "Total ink", FAIL)


# --- image resolution
def test_low_resolution_image_warns(make):
    content = b"q 144 0 0 144 100 60 cm /Im0 Do Q"  # 100 px over 2 inches = 50 ppi
    f = found(run(make(content, xobject={"/Im0": gray_image(100, 100)})), "Image resolution", WARN)
    assert f and "50 ppi" in f[0].message


def test_300ppi_image_is_fine(make):
    content = b"q 24 0 0 24 100 60 cm /Im0 Do Q"  # 100 px over 1/3 inch = 300 ppi
    assert run(make(content, xobject={"/Im0": gray_image(100, 100)})).status == "PASS"


def test_image_inside_a_scaled_form_uses_the_final_size(make):
    def form(pdf):
        res = pikepdf.Dictionary(XObject=pikepdf.Dictionary({"/Im0": gray_image(100, 100)(pdf)}))
        return pdf.make_stream(b"q 72 0 0 72 0 0 cm /Im0 Do Q", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                               BBox=[0, 0, 72, 72], Resources=res)
    content = b"q 2 0 0 2 100 60 cm /Fm0 Do Q"  # form scaled x2: 100 px over 2 inches = 50 ppi
    f = found(run(make(content, xobject={"/Fm0": form})), "Image resolution", WARN)
    assert f and "50 ppi" in f[0].message


# --- page size
def test_expected_trim_mismatch_warns(make):
    path = make(K_BOX)
    assert not found(run(path, Options(expected_trim_mm=(100, 60))), "Page size", WARN)
    assert not found(run(path, Options(expected_trim_mm=(100.4, 60))), "Page size", WARN)  # within 0.5 mm
    f = found(run(path, Options(expected_trim_mm=(60, 100))), "Page size", WARN)
    assert f and "rotated" in f[0].message
    assert found(run(path, Options(expected_trim_mm=(90, 55))), "Page size", WARN)


# --- reporting
def test_page_list():
    assert page_list([1, 2, 3, 5], 10) == "pages 1–3, 5"
    assert page_list([4], 10) == "page 4"
    assert page_list([1, 2, 3], 3) == "all 3 pages"


def test_text_report_mentions_result(make):
    text = render(run(make(b"1 0 0 rg 100 100 50 50 re f")))
    assert "Result: WARN" in text and "RGB" in text
