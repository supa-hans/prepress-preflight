"""Compact delivery: a fresh file without what the fixes replaced, checked page by page."""
import os
import zlib

import pikepdf
import pytest

from prepress_preflight.engine import run
from prepress_preflight.fix_cli import main as fix_main
from prepress_preflight.fixes.append import AppendError
from prepress_preflight.fixes.chain import deliver, verify_compact, write_compact

from conftest import TRIM


def rgb_photo_page(make):
    pixels = os.urandom(200 * 200 * 3)  # incompressible, so the old image's size shows

    def image(pdf):
        return pdf.make_stream(zlib.compress(pixels), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=200, Height=200, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceRGB,
                               Filter=pikepdf.Name.FlateDecode)
    return make(b"q 100 0 0 100 100 50 cm /Im0 Do Q", xobject={"/Im0": image})


def test_compact_leaves_out_the_replaced_image(make, tmp_path):
    pytest.importorskip("PIL")
    from prepress_preflight.fixes import FIXES
    from prepress_preflight.fixes.rgb_to_cmyk import Engine, find_profile
    try:
        Engine(find_profile(None))
    except ValueError:
        pytest.skip("Adobe's Coated FOGRA39 profile is not installed")
    src = rgb_photo_page(make)
    propose, write = FIXES["rgb-to-cmyk"]
    full = str(tmp_path / "full.pdf")
    write(propose(src), full)
    plain, small = str(tmp_path / "plain.pdf"), str(tmp_path / "small.pdf")
    deliver(src, full, plain)
    deliver(src, full, small, compact=True)
    assert os.path.getsize(small) < os.path.getsize(plain) - 100_000  # the 120 KB RGB image is gone
    with pikepdf.open(small) as pdf:
        images = [o for o in pdf.objects if isinstance(o, pikepdf.Stream) and o.stream_dict.get("/Subtype") == "/Image"]
        assert [str(i.ColorSpace) for i in images] == ["/DeviceCMYK"]
    assert [f for f in run(small).findings if f.check == "RGB"] == []


def test_verification_catches_a_different_page(make, tmp_path):
    full = make(b"0 0 0 1 k 100 100 50 50 re f", name="full.pdf")
    other = make(b"0 0 0 1 k 100 100 60 50 re f", name="other.pdf")
    with pytest.raises(AppendError, match="paints different objects"):
        verify_compact(full, other)
    same = str(tmp_path / "same.pdf")
    write_compact(full, same)
    verify_compact(full, same)  # passes


def test_verification_catches_a_changed_box(make, tmp_path):
    full = make(b"0 0 0 1 k 100 100 50 50 re f", name="full.pdf")
    moved = make(b"0 0 0 1 k 100 100 50 50 re f", name="moved.pdf", trim=(TRIM[0] + 1, TRIM[1], TRIM[2], TRIM[3]))
    with pytest.raises(AppendError, match="TrimBox"):
        verify_compact(full, moved)


def test_command_line_compact(make, tmp_path, capsys):
    from conftest import MEDIA
    src = make(b"0 0 0 1 k 100 100 50 50 re f", trim=MEDIA, bleed=MEDIA)
    dst = str(tmp_path / "out.pdf")
    assert fix_main(["set-boxes", src, "--trim", "100x60", "--no-preview", "--yes", "--compact", "--write", dst]) == 0
    out = capsys.readouterr().out
    assert "Compact: only the objects in use were written" in out
    with pikepdf.open(dst) as pdf:  # the boxes the fix set, in a fresh file
        trim = [float(v) for v in pdf.pages[0].trimbox]
    assert (trim[2] - trim[0]) * 25.4 / 72 == pytest.approx(100, abs=0.1)
