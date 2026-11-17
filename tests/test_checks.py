import hashlib

import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.model import FAIL, NOTE, WARN
from prepress_preflight.report import page_list, render

from conftest import BLEEDBOX, MEDIA, MM, TRIM, gray_image, rect, registration_space

K_BOX = b"0 0 0 1 k 100 100 50 50 re f"  # plain black box inside the trim
K_FULL = b"0 0 0 1 k " + rect(MEDIA) + b"f"  # black over the whole sheet: reaches every edge and bleeds


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
    assert found(run(make(K_FULL, bleed=short)), "Bleed box", FAIL)


def test_bleed_short_on_one_edge_fails(make):
    uneven = (BLEEDBOX[0], BLEEDBOX[1], BLEEDBOX[2], TRIM[3] + 1 * MM)
    f = found(run(make(K_FULL, bleed=uneven)), "Bleed box", FAIL)
    assert f and "top" in f[0].message and "left" not in f[0].message


def test_short_bleed_box_is_only_noted_when_the_edges_are_white(make):
    b = 2 * MM
    short = (TRIM[0] - b, TRIM[1] - b, TRIM[2] + b, TRIM[3] + b)
    result = run(make(K_BOX, bleed=short))
    assert found(result, "Bleed box", NOTE) and not found(result, "Bleed box", FAIL)
    assert result.status == "PASS"


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
    assert found(run(make(K_FULL, media=TRIM, trim=None, bleed=None)), "Bleed box", FAIL)


def test_page_ending_at_trim_with_white_edges_needs_no_bleed(make):
    result = run(make(K_BOX, media=TRIM, trim=None, bleed=None))
    f = found(result, "Bleed box", NOTE)
    assert f and "none needed" in f[0].message and result.status == "PASS"


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


def test_ink_limit_can_be_set_and_the_area_over_it_is_reported(make):
    path = make(b"1 1 1 0 k 100 100 50 50 re f")  # 300% over 17.6 x 17.6 mm = 311 mm²
    assert not found(run(path, Options(ink_limit_pct=300)), "Total ink", FAIL)  # at the limit, not over it
    f = found(run(path, Options(ink_limit_pct=280)), "Total ink", FAIL)
    assert f and "over 280%" in f[0].message and "peaks at 300%" in f[0].message
    assert any(f"{n} mm²" in f[0].message for n in range(300, 325))
    x0, y0, x1, y1 = f[0].locations[0][1]
    assert abs(x0 - 100) < 2 and abs(y0 - 100) < 2 and abs(x1 - 150) < 2 and abs(y1 - 150) < 2


def test_fine_object_in_400_percent_ink_fails_whatever_its_size(make):
    # A 0.3 pt rule in 400% black covers far less than 1 mm² of pixels.
    f = found(run(make(b"1 1 1 1 K 0.3 w 100 100 m 110 100 l S")), "Total ink", FAIL)
    assert f and "1 object(s)" in f[0].message


def test_non_printing_spot_does_not_count_as_ink(make):
    # A dieline whose screen colour is 400% lies over the artwork: it puts down no ink.
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[1, 1, 1, 1], N=1)
    die = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Dieline"), pikepdf.Name.DeviceCMYK, tint])
    content = b"0 0 0 1 k 100 100 50 50 re f /CS0 cs 1 scn 100 100 50 50 re f"
    result = run(make(content, colorspace={"/CS0": die}))
    assert not found(result, "Total ink", FAIL)
    assert "100%" in found(result, "Total ink", NOTE)[0].message  # the black beneath is what is measured


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


# --- hairlines
LINE = b"100 100 m 200 100 l S"


def test_line_under_quarter_point_warns(make):
    f = found(run(make(b"0 0 0 1 K 0.1 w " + LINE)), "Hairlines", WARN)
    assert f and "0.10 pt" in f[0].message


def test_quarter_point_line_is_fine(make):
    assert not found(run(make(b"0 0 0 1 K 0.25 w " + LINE)), "Hairlines", WARN)


def test_zero_width_line_warns(make):
    assert found(run(make(b"0 0 0 1 K 0 w " + LINE)), "Hairlines", WARN)


def test_line_weight_is_measured_after_scaling(make):
    # A 1 pt line in artwork placed at 10% is 0.1 pt on the page.
    f = found(run(make(b"0 0 0 1 K q 0.1 0 0 0.1 0 0 cm 1 w 1000 1000 m 2000 1000 l S Q")), "Hairlines", WARN)
    assert f and "0.10 pt" in f[0].message
    assert not found(run(make(b"0 0 0 1 K q 10 0 0 10 0 0 cm 0.1 w 10 10 m 20 10 l S Q")), "Hairlines", WARN)


def test_line_width_from_a_graphics_state_counts(make):
    gs = {"/GS1": pikepdf.Dictionary(LW=0.05)}
    assert found(run(make(b"0 0 0 1 K /GS1 gs " + LINE, extgstate=gs)), "Hairlines", WARN)


def test_white_and_dieline_hairlines_are_left_out(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    die = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Dieline"), pikepdf.Name.DeviceCMYK, tint])
    content = b"0.1 w 0 0 0 0 K " + LINE + b" /CS0 CS 1 SCN 100 120 m 200 120 l S"
    assert not found(run(make(content, colorspace={"/CS0": die})), "Hairlines", WARN)


def test_hairline_in_the_slug_is_left_out(make):
    assert not found(run(make(b"0 0 0 1 K 0.1 w 2 2 m 10 2 l S")), "Hairlines", WARN)


# --- font embedding
TEXT = b"0 0 0 1 k BT /F1 12 Tf 100 100 Td (Hello) Tj ET"


def type1(embedded, name="/ABCDEF+Frutiger-Roman"):
    def make_font(pdf):
        descriptor = pikepdf.Dictionary(Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name(name))
        if embedded:
            descriptor["/FontFile3"] = pdf.make_stream(b"not a real font program")
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name(name),
                                  FontDescriptor=pdf.make_indirect(descriptor))
    return make_font


def with_font(make, content, font):
    return make(content, font={"/F1": font})


def test_font_that_is_not_embedded_fails(make):
    f = found(run(with_font(make, TEXT, type1(embedded=False))), "Font embedding", FAIL)
    assert f and "Frutiger-Roman" in f[0].message and "ABCDEF" not in f[0].message


def test_standard_font_without_a_font_file_fails(make):
    helvetica = {"/F1": pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)}
    f = found(run(make(TEXT, font=helvetica)), "Font embedding", FAIL)
    assert f and "Helvetica" in f[0].message


def test_embedded_font_is_fine(make):
    assert not found(run(with_font(make, TEXT, type1(embedded=True))), "Font embedding", FAIL)


def test_font_that_is_listed_but_never_used_is_ignored(make):
    assert not found(run(with_font(make, K_BOX, type1(embedded=False))), "Font embedding", FAIL)


def test_invisible_text_does_not_need_its_font(make):
    hidden = b"BT 3 Tr /F1 12 Tf 100 100 Td (Hello) Tj ET"
    assert not found(run(with_font(make, hidden, type1(embedded=False))), "Font embedding", FAIL)


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
