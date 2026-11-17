"""Blend over same ink: a darkening blend-mode object lying on ink of the same colourant."""
import hashlib

import pikepdf

from prepress_preflight.engine import run
from prepress_preflight.model import FAIL, NOTE, UNCHECKED, WARN

CHECK = "Blend over same ink"
GS = {
    "/M": pikepdf.Dictionary(BM=pikepdf.Name.Multiply),
    "/N": pikepdf.Dictionary(BM=pikepdf.Name.Normal),
    "/CB": pikepdf.Dictionary(BM=pikepdf.Name.ColorBurn),
    "/SC": pikepdf.Dictionary(BM=pikepdf.Name.Screen),
}
UNDER = b"100 100 80 80 re f "  # 28 x 28 mm, inside the trim
OVER = b"/M gs 140 100 80 80 re f"  # overlaps the right half of UNDER


def found(result, severity):
    return [f for f in result.findings if f.check == CHECK and f.severity == severity]


def findings(result):
    return [f for f in result.findings if f.check == CHECK]


def test_solid_multiply_over_the_same_solid_ink_fails(make):
    f = found(run(make(b"0 0 0 1 k " + UNDER + OVER, extgstate=GS)), FAIL)
    assert f and "Black stacks to 200%" in f[0].message and "Multiply" in f[0].message
    x0, y0, x1, y1 = f[0].locations[0][1]  # the overlap only: x 140..180, y 100..180
    assert abs(x0 - 140) < 2 and abs(x1 - 180) < 2 and abs(y0 - 100) < 2 and abs(y1 - 180) < 2


def test_tints_stacking_past_100_warn(make):
    result = run(make(b"0 0 0 0.6 k " + UNDER + OVER, extgstate=GS))
    f = found(result, WARN)
    assert f and "120%" in f[0].message and not found(result, FAIL)


def test_tints_staying_within_100_are_only_noted(make):
    result = run(make(b"0 0 0 0.4 k " + UNDER + OVER, extgstate=GS))
    assert found(result, NOTE) and not found(result, WARN) and not found(result, FAIL)
    assert result.status == "PASS"


def test_multiply_over_a_different_ink_is_fine(make):
    # Black multiplied over cyan behaves like ordinary overprint.
    assert not findings(run(make(b"1 0 0 0 k " + UNDER + b"0 0 0 1 k " + OVER, extgstate=GS)))


def test_multiply_over_bare_paper_is_fine(make):
    assert not findings(run(make(b"0 0 0 1 k " + OVER, extgstate=GS)))


def test_normal_object_over_the_same_ink_is_fine(make):
    assert not findings(run(make(b"0 0 0 1 k " + UNDER + b"/N gs 140 100 80 80 re f", extgstate=GS)))


def test_only_what_lies_under_the_object_counts(make):
    # Black elsewhere on the page, and black painted afterwards, are not under it.
    content = b"0 0 0 1 k 40 40 30 30 re f 1 0 0 0 k " + UNDER + b"0 0 0 1 k " + OVER + b" /N gs 230 100 40 40 re f"
    assert not findings(run(make(content, extgstate=GS)))


def test_overlap_under_one_square_mm_is_ignored(make):
    sliver = b"0 0 0 1 k " + UNDER + b"/M gs 179 100 80 2 re f"  # 1 x 2 pt of overlap
    assert not findings(run(make(sliver, extgstate=GS)))


def test_lightening_modes_are_not_flagged(make):
    assert not findings(run(make(b"0 0 0 1 k " + UNDER + b"/SC gs 140 100 80 80 re f", extgstate=GS)))


def test_color_burn_is_measured_too(make):
    f = found(run(make(b"0 0 0 1 k " + UNDER + b"/CB gs 140 100 80 80 re f", extgstate=GS)), FAIL)
    assert f and "ColorBurn" in f[0].message


def group(content, transparency=True, isolated=False, knockout=False, inner=None, matrix=None):
    """A form XObject; a transparency group unless transparency=False."""
    def make_form(pdf):
        res = pikepdf.Dictionary(ExtGState=pikepdf.Dictionary(GS))
        if inner:
            res["/XObject"] = pikepdf.Dictionary({k: v(pdf) for k, v in inner.items()})
        form = pdf.make_stream(content, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 400, 400],
                               Resources=res)
        if transparency:
            form["/Group"] = pikepdf.Dictionary(S=pikepdf.Name.Transparency, I=isolated, K=knockout)
        if matrix:
            form["/Matrix"] = list(matrix)
        return form
    return make_form


K_UNDER = b"0 0 0 1 k " + UNDER
K_OVER = b"0 0 0 1 k " + OVER


def test_multiply_group_over_the_same_ink_fails(make):
    # The pull-up banner: a black rectangle, then a group holding a black
    # rectangle, the group painted with Multiply.
    content = K_UNDER + b"q 0 g /M gs /Fm0 Do Q"
    f = found(run(make(content, extgstate=GS, xobject={"/Fm0": group(b"0 0 0 1 k 140 100 80 80 re f")})), FAIL)
    assert f and "200%" in f[0].message


# --- blend modes inside placed or grouped artwork
def test_multiply_inside_a_plain_form_is_measured_against_the_page(make):
    result = run(make(K_UNDER + b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": group(K_OVER, transparency=False)}))
    f = found(result, FAIL)
    assert f and "200%" in f[0].message and not found(result, UNCHECKED)
    x0, y0, x1, y1 = f[0].locations[0][1]
    assert abs(x0 - 140) < 2 and abs(x1 - 180) < 2 and abs(y0 - 100) < 2 and abs(y1 - 180) < 2


def test_multiply_inside_a_group_is_measured_against_the_page(make):
    assert found(run(make(K_UNDER + b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": group(K_OVER)})), FAIL)


def test_multiply_inside_a_form_over_ink_in_the_same_form_fails(make):
    assert found(run(make(b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": group(K_UNDER + OVER, transparency=False)})), FAIL)


def test_isolated_group_does_not_blend_with_the_page_beneath(make):
    # Inside an isolated group the object blends only with the group's own content.
    assert not findings(run(make(K_UNDER + b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": group(K_OVER, isolated=True)})))


def test_isolated_group_still_blends_within_itself(make):
    assert found(run(make(b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": group(K_UNDER + OVER, isolated=True)})), FAIL)


def test_multiply_two_forms_deep_is_measured(make):
    outer = group(b"q /Fm1 Do Q", transparency=False, inner={"/Fm1": group(K_OVER)})
    assert found(run(make(K_UNDER + b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": outer})), FAIL)


def test_form_placed_with_a_matrix_is_measured_where_it_lands(make):
    # The form's box is drawn at 40..120 and shifted 100 right: it lands on 140..220.
    form = group(b"0 0 0 1 k /M gs 40 100 80 80 re f", transparency=False, matrix=(1, 0, 0, 1, 100, 0))
    f = found(run(make(K_UNDER + b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": form})), FAIL)
    assert f and abs(f[0].locations[0][1][0] - 140) < 2 and abs(f[0].locations[0][1][2] - 180) < 2


def test_ink_painted_after_the_form_is_not_under_it(make):
    content = b"1 0 0 0 k " + UNDER + b"/Fm0 Do 0 0 0 1 k /N gs 230 100 40 40 re f"
    inner = b"0 0 0 1 k /M gs 140 100 80 80 re f /N gs 150 110 20 20 re f"
    assert not findings(run(make(content, extgstate=GS, xobject={"/Fm0": group(inner, transparency=False)})))


def test_blend_mode_inside_a_knockout_group_is_reported_as_not_checked(make):
    result = run(make(K_UNDER + b"/Fm0 Do", extgstate=GS, xobject={"/Fm0": group(K_OVER, knockout=True)}))
    assert found(result, UNCHECKED) and not found(result, FAIL)
    assert result.status == "WARN"  # never a pass it has not earned


def test_spot_only_object_never_fails(make):
    # A dieline set to Multiply over the artwork.
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 0, 0, 1], N=1)
    spot = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Dieline"), pikepdf.Name.DeviceCMYK, tint])
    content = b"0 0 0 1 k " + UNDER + b"/M gs /CS0 cs 1 scn 140 100 80 80 re f"
    result = run(make(content, extgstate=GS, colorspace={"/CS0": spot}))
    assert not found(result, FAIL) and not found(result, WARN)
    assert found(result, NOTE) and "not measured" in found(result, NOTE)[0].message


FONT = {"/F1": pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)}


def test_multiply_text_over_the_same_ink_fails(make):
    content = b"0 0 0 1 k " + UNDER + b"BT /F1 60 Tf 105 115 Td /M gs (HH) Tj (H) Tj ET"
    assert found(run(make(content, extgstate=GS, font=FONT)), FAIL)


def test_earlier_text_does_not_count_as_part_of_the_object(make):
    # Normal black text on the black box, then a Multiply box over cyan only.
    content = (b"0 0 0 1 k BT /F1 60 Tf 105 115 Td (HH) Tj ET 1 0 0 0 k 200 100 60 60 re f "
               b"0 0 0 1 k /M gs 210 110 40 40 re f")
    assert not findings(run(make(content, extgstate=GS, font=FONT)))


def test_file_is_not_modified_by_the_in_memory_renders(make):
    path = make(b"0 0 0 1 k " + UNDER + OVER, extgstate=GS)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert found(run(path), FAIL)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before
