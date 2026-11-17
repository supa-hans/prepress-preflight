"""Bleed artwork: wherever artwork reaches the trim it must carry on 3 mm past it."""
import hashlib

import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.model import FAIL, NOTE

from conftest import BLEEDBOX, MEDIA, MM, TRIM, rect, registration_space

CHECK = "Bleed artwork"
K = b"0 0 0 1 k "


def fails(result):
    return [f for f in result.findings if f.check == CHECK and f.severity == FAIL]


def notes(result):
    return [f for f in result.findings if f.check == CHECK and f.severity == NOTE]


def test_artwork_filling_the_bleed_passes(make):
    assert not fails(run(make(K + rect(BLEEDBOX) + b"f")))


def test_artwork_stopping_at_the_trim_fails_on_every_edge(make):
    f = fails(run(make(K + rect(TRIM) + b"f")))
    assert f and "no bleed" in f[0].message
    assert all(edge in f[0].message for edge in ("left", "bottom", "right", "top"))


def test_white_edges_need_no_bleed(make):
    result = run(make(K + b"100 100 50 50 re f"))
    assert not fails(result) and result.status == "PASS"


def test_bleed_missing_on_one_edge_names_that_edge(make):
    box = (BLEEDBOX[0], BLEEDBOX[1], BLEEDBOX[2], TRIM[3])  # stops at the trim along the top
    f = fails(run(make(K + rect(box) + b"f")))
    assert f and "top" in f[0].message and "left" not in f[0].message and "bottom" not in f[0].message


def test_bleed_shorter_than_3mm_reports_its_depth(make):
    f = fails(run(make(K + rect(TRIM, grow=1.5 * MM) + b"f")))
    assert f and "under 3 mm" in f[0].message and "1.5 mm" in f[0].message


def test_gap_in_the_bleed_along_an_edge_fails(make):
    # Full bleed, then 20 mm of the left bleed strip painted out in white.
    gap = (BLEEDBOX[0], 100, TRIM[0], 100 + 20 * MM)
    f = fails(run(make(K + rect(BLEEDBOX) + b"f 0 0 0 0 k " + rect(gap) + b"f")))
    assert f and "left" in f[0].message and "right" not in f[0].message


def test_slanted_side_crossing_the_bleed_is_not_a_gap(make):
    # A shape that bleeds off the left edge and whose top side runs at 45 degrees.
    # Straight out from the trim near that side the ink stops short, but the
    # artwork does carry on past the trim.
    x, b = TRIM[0], 3 * MM
    shape = f"{x - b:.2f} 60 m {x + 60:.2f} 60 l {x + 60:.2f} {120 + 60 + b:.2f} l {x - b:.2f} 120 l h f".encode()
    assert not fails(run(make(K + shape)))


def test_square_notch_in_the_bleed_is_still_found(make):
    # Only 6 mm of the left bleed strip painted out: narrow, but a real gap.
    gap = (BLEEDBOX[0], 100, TRIM[0], 100 + 6 * MM)
    f = fails(run(make(K + rect(BLEEDBOX) + b"f 0 0 0 0 k " + rect(gap) + b"f")))
    assert f and "left" in f[0].message


def test_pale_tint_at_the_trim_counts_as_white(make):
    assert not fails(run(make(b"0 0 0 0.03 k " + rect(TRIM) + b"f")))


def test_fade_that_thins_out_past_the_trim_still_bleeds(make):
    # 6% at the trim, 3% in the bleed: lighter, but it is there.
    assert not fails(run(make(b"0 0 0 0.03 k " + rect(BLEEDBOX) + b"f 0 0 0 0.06 k " + rect(TRIM) + b"f")))


def test_artwork_clipped_at_the_trim_fails(make):
    # The object is drawn out to the bleed, but a clipping mask cuts it at the trim.
    assert fails(run(make(b"q " + rect(TRIM) + b"W n " + K + rect(BLEEDBOX) + b"f Q")))


def test_edge_that_is_white_needs_no_bleed_even_if_others_do(make):
    # Artwork touches only the left edge, and bleeds there.
    box = (BLEEDBOX[0], 100, 150, 150)
    assert not fails(run(make(K + rect(box) + b"f")))


def test_crop_marks_do_not_count_as_bleed(make):
    reg = {"/CS0": registration_space()}
    mark = f"/CS0 CS 1 SCN 0.25 w {TRIM[0] - 20:.2f} {TRIM[1] + 30:.2f} m {TRIM[0] - 5:.2f} {TRIM[1] + 30:.2f} l S".encode()
    assert fails(run(make(K + rect(TRIM) + b"f " + mark, colorspace=reg)))


def spot(name):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/" + name), pikepdf.Name.DeviceCMYK, tint])


DIELINE = b"/CS0 CS 1 SCN 1 w " + rect(TRIM) + b"S"


def test_dieline_on_the_trim_never_causes_a_fail(make):
    result = run(make(K + b"100 100 50 50 re f " + DIELINE, colorspace={"/CS0": spot("Dieline")}))
    assert not fails(result)
    assert notes(result) and "Dieline" in notes(result)[0].message


def test_dieline_inside_a_form_is_left_out_too(make):
    def form(pdf):
        return pdf.make_stream(DIELINE, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=list(MEDIA),
                               Resources=pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary({"/CS0": spot("CutContour")})))
    assert not fails(run(make(K + b"100 100 50 50 re f /Fm0 Do", xobject={"/Fm0": form})))


def test_dieline_over_artwork_does_not_hide_missing_bleed(make):
    # A dieline filled over the whole sheet must not stand in for the missing bleed.
    cover = b"/CS0 cs 1 scn " + rect(MEDIA) + b"f"
    assert fails(run(make(K + rect(TRIM) + b"f " + cover, colorspace={"/CS0": spot("dieline")})))


def test_printing_spot_colour_at_the_trim_still_needs_bleed(make):
    art = b"/CS0 cs 1 scn " + rect(TRIM) + b"f"
    assert fails(run(make(art, colorspace={"/CS0": spot("PANTONE 185 C")})))


def test_rotated_page_names_the_edge_in_the_files_own_terms(make):
    box = (BLEEDBOX[0], BLEEDBOX[1], BLEEDBOX[2], TRIM[3])  # no bleed along the top of the page boxes
    f = fails(run(make(K + rect(box) + b"f", rotate=90)))
    assert f and "top" in f[0].message and "left" not in f[0].message and "right" not in f[0].message


def test_file_is_not_modified_when_spots_are_left_out(make):
    path = make(K + rect(TRIM) + b"f " + DIELINE, colorspace={"/CS0": spot("Dieline")})
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert fails(run(path))
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before


def test_bound_job_does_not_ask_the_spine_edge_to_bleed(make):
    # Page 1 is a right-hand page, so its spine is the left edge.
    box = (TRIM[0], BLEEDBOX[1], BLEEDBOX[2], BLEEDBOX[3])  # bleeds everywhere except the left
    path = make(K + rect(box) + b"f")
    f = fails(run(path))
    assert f and "left" in f[0].message
    assert not fails(run(path, Options(bound=True)))


def test_bound_job_still_needs_bleed_on_the_outer_edges(make):
    box = (BLEEDBOX[0], BLEEDBOX[1], TRIM[2], BLEEDBOX[3])  # no bleed on the right of page 1
    assert fails(run(make(K + rect(box) + b"f"), Options(bound=True)))


def test_white_shape_exactly_over_a_coloured_one_at_the_trim_is_not_artwork_reaching_it(make):
    # A white band drawn exactly over a blue one, both ending on the trim: anti-aliasing
    # leaks a blue hairline at the trim on screen. That is not artwork reaching the trim.
    band = (TRIM[0], 100, TRIM[2], 130)
    content = b"0.9 0.6 0 0 k " + rect(band) + b"f 0 0 0 0 k " + rect(band) + b"f"
    assert not fails(run(make(content)))
