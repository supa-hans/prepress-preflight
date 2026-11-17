"""add-bleed: making bleed for artwork that stops at the trim."""
import hashlib
import os

import numpy as np
import pikepdf
import pymupdf
import pytest

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.append import AppendError

from conftest import MEDIA, MM, TRIM, rect, registration_space

propose, write = FIXES["add-bleed"]
PAGE = TRIM  # these pages end at the trim: page = trim, no room for bleed
FULL_K = b"0 0 0 1 k " + rect(PAGE) + b"f"


def no_bleed(make, content=FULL_K, name="t.pdf", **kw):
    return make(content, name=name, media=PAGE, trim=None, bleed=None, **kw)


def fails(result, check):
    return [f for f in result.findings if f.check == check and f.severity == "fail"]


def boxes(path):
    with pikepdf.open(path) as pdf:
        p = pdf.pages[0].obj
        return {k[1:]: [float(v) for v in p[k]] for k in ("/MediaBox", "/CropBox", "/TrimBox", "/BleedBox") if k in p}


def written(make, tmp_path, src=None, **kw):
    src = src or no_bleed(make)
    dst = str(tmp_path / "bled.pdf")
    write(propose(src, Options(**kw.pop("options", {})), **kw), dst)
    return src, dst


# --- proposal
def test_proposal_grows_the_page_and_keeps_the_old_size_as_the_trim(make):
    p = propose(no_bleed(make))
    c = {x.box: x for x in p.changes}
    assert c["MediaBox"].after_mm == "108.0 × 68.0 mm"  # 3 mm bleed + 1 mm overshoot each side
    assert c["TrimBox"].after_mm == "100.0 × 60.0 mm"
    assert c["BleedBox"].after_mm == "106.0 × 66.0 mm"
    assert set(p.plan[1]["edges"]) == {"left", "bottom", "right", "top"}


def test_white_edges_get_nothing(make):
    assert propose(no_bleed(make, b"0 0 0 1 k 100 100 50 50 re f")).empty


def test_only_edges_with_artwork_are_filled(make):
    band = b"0 0 0 1 k " + rect((PAGE[0], 80, PAGE[2], 120)) + b"f"  # touches left and right only
    assert set(propose(no_bleed(make, band)).plan[1]["edges"]) == {"left", "right"}


def test_spot_colour_at_the_edge_is_left_alone(make):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    spot = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/PANTONE 185 C"), pikepdf.Name.DeviceCMYK, tint])
    p = propose(no_bleed(make, b"/CS0 cs 1 scn " + rect(PAGE) + b"f", colorspace={"/CS0": spot}))
    assert p.empty and "PANTONE 185 C" in p.skipped[0][1]


def test_rgb_edge_is_made_in_rgb(make, tmp_path):
    _, dst = written(make, tmp_path, no_bleed(make, b"0.9 0.2 0.1 rg " + rect(PAGE) + b"f"))
    with pikepdf.open(dst) as pdf:
        spaces = {str(x.get("/ColorSpace")) for _, x in pdf.pages[0].Resources.XObject.items() if x.get("/Subtype") == "/Image"}
    assert spaces == {"/DeviceRGB"}


def test_crop_marks_that_disagree_send_you_to_trim_from_marks(make):
    from test_fixes import banner
    p = propose(banner(make))
    assert p.empty and "trim-from-marks" in p.skipped[0][1]


# --- writing
def test_written_file_passes_bleed_and_keeps_the_original(make, tmp_path):
    src = no_bleed(make)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    _, dst = written(make, tmp_path, src)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    with open(dst, "rb") as f:
        assert hashlib.sha256(f.read(os.path.getsize(src))).hexdigest() == before
    result = run(dst)
    assert not fails(result, "Bleed artwork") and not fails(result, "Bleed box")
    assert boxes(dst)["TrimBox"] == pytest.approx(list(PAGE), abs=0.01)


def test_original_content_stream_is_kept_as_it_was(make, tmp_path):
    src, dst = written(make, tmp_path)
    with pikepdf.open(src) as a, pikepdf.open(dst) as b:
        old = a.pages[0].obj.Contents
        new = b.pages[0].obj.Contents
        assert isinstance(new, pikepdf.Array) and new[-1].objgen == old.objgen
        assert new[-1].read_raw_bytes() == old.read_raw_bytes()


def test_thin_border_at_the_edge_still_gets_full_bleed(make, tmp_path):
    # A 1 mm black border round the page, white inside: a mirror alone would give only 1 mm.
    border = b"0 0 0 1 k " + rect(PAGE) + rect(PAGE, grow=-1 * MM) + b"f*"
    _, dst = written(make, tmp_path, no_bleed(make, border))
    assert not fails(run(dst), "Bleed artwork")


def test_stretch_method_also_fills_the_bleed(make, tmp_path):
    photo_like = b"0.2 0 0 1 k " + rect(PAGE) + b"f 1 0 0 0 k " + rect((PAGE[0], 100, PAGE[0] + 20, 140)) + b"f"
    _, dst = written(make, tmp_path, no_bleed(make, photo_like), method="stretch")
    assert not fails(run(dst), "Bleed artwork")


def test_corners_are_filled(make, tmp_path):
    _, dst = written(make, tmp_path)
    page = pymupdf.open(dst)[0]
    pix = page.get_pixmap(colorspace=pymupdf.csCMYK, dpi=72, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4)
    for y, x in ((3, 3), (3, -4), (-4, 3), (-4, -4)):  # about 1 mm in from each corner of the new page
        assert a[y, x, 3] > 200


def test_rotated_page(make, tmp_path):
    band = b"0 0 0 1 k " + rect((PAGE[0], 80, PAGE[2], 120)) + b"f"
    _, dst = written(make, tmp_path, no_bleed(make, band, rotate=90))
    assert not fails(run(dst), "Bleed artwork")


def test_page_with_room_is_filled_without_growing(make, tmp_path):
    src = make(b"0 0 0 1 k " + rect(TRIM) + b"f")  # 10 mm of slug round the trim, artwork stops at the trim
    p = propose(src)
    assert "MediaBox" not in {c.box for c in p.changes}
    dst = str(tmp_path / "bled.pdf")
    write(p, dst)
    assert boxes(dst)["MediaBox"] == pytest.approx(list(MEDIA), abs=0.01)
    assert not fails(run(dst), "Bleed artwork")


def test_bound_job_leaves_the_spine_alone(make):
    p = propose(no_bleed(make), Options(bound=True))
    assert "left" not in p.plan[1]["edges"]  # page 1 is a right-hand page: its spine is the left edge


def test_verification_refuses_anything_painted_inside_the_trim(make, tmp_path, monkeypatch):
    import prepress_preflight.fixes.add_bleed as ab
    real = ab.build_underlay

    def leaky(pdf, page, plan, cache=None):
        images, underlay = real(pdf, page, plan, cache)
        t = plan["trim"]
        bad = pdf.make_indirect(pikepdf.Stream(pdf, f"q 1 0 0 0 k {t[0] + 20} {t[1] + 20} 10 10 re f Q".encode()))
        return images, bad
    monkeypatch.setattr(ab, "build_underlay", leaky)
    dst = tmp_path / "bled.pdf"
    band = b"0 0 0 1 k " + rect((PAGE[0], 80, PAGE[2], 120)) + b"f"  # leaves white where the leak lands
    with pytest.raises(AppendError, match="inside the trim"):
        write(propose(no_bleed(make, band)), str(dst))
    assert not dst.exists()


def test_command_shows_it_adds_artwork(make, capsys):
    from prepress_preflight.cli import main
    assert main(["fix", "add-bleed", no_bleed(make), "--no-preview"]) == 0
    out = capsys.readouterr().out
    assert "new bleed images are added beneath it, outside the trim" in out and "Nothing written" in out
