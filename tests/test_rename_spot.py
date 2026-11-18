"""rename-spot: renaming a spot ink, or merging it into another."""
import hashlib
import os

import pikepdf
import pytest

from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.rename_spot import parse_mapping

from test_spots import spot

propose, write = FIXES["rename-spot"]


def spots_used(path):
    return {n for f in run(path).findings if f.check == "Spot colours" for n in
            [s.split(" (")[0] for s in f.message.split(": ", 1)[-1].split("; ")]}


def file_with(make, spaces, content=None, **kw):
    content = content or b" ".join(f"/CS{i} cs 1 scn {100 + 30 * i} 100 20 20 re f".encode() for i in range(len(spaces)))
    return make(content, colorspace={f"/CS{i}": s for i, s in enumerate(spaces)}, **kw)


def test_plain_rename(make, tmp_path):
    src = file_with(make, [spot("Pantone 185C")])
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src, spots=["Pantone 185C=PANTONE 185 C"])
    assert 'Renames "Pantone 185C" to "PANTONE 185 C"' in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)  # also verified to render identically
    assert spots_used(dst) == {"PANTONE 185 C"}
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_merge_takes_the_targets_definition(make, tmp_path):
    src = file_with(make, [spot("PANTONE 185 C", (0, 1, 0.8, 0)), spot("Pantone 185C", (0, 0.9, 0.7, 0))])
    result = run(src)
    assert [f for f in result.findings if "spelt more than one way" in f.message]
    p = propose(src, spots=["Pantone 185C=PANTONE 185 C"])
    assert "Merges" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    after = run(dst)
    assert spots_used(dst) == {"PANTONE 185 C"}
    assert not [f for f in after.findings if f.check == "Spot colours" and f.severity == "warn"]  # one ink, one definition


def test_devicen_colorant_is_renamed(make, tmp_path):
    fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    dn = pikepdf.Array([pikepdf.Name.DeviceN, pikepdf.Array([pikepdf.Name("/Pantone 185C")]), pikepdf.Name.DeviceCMYK, fn])
    src = make(b"/CS0 cs 1 scn 100 100 20 20 re f", colorspace={"/CS0": dn})
    dst = str(tmp_path / "out.pdf")
    write(propose(src, spots=["Pantone 185C=PANTONE 185 C"]), dst)
    assert spots_used(dst) == {"PANTONE 185 C"}


def test_spot_inside_a_placed_graphic_is_renamed(make, tmp_path):
    def form(pdf):
        return pdf.make_stream(b"/CS0 cs 1 scn 0 0 20 20 re f", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                               BBox=[0, 0, 20, 20], Resources=pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(
                                   {"/CS0": spot("Pantone 185C")})))
    src = make(b"q 1 0 0 1 100 100 cm /Fm0 Do Q", xobject={"/Fm0": form})
    dst = str(tmp_path / "out.pdf")
    write(propose(src, spots=["Pantone 185C=PANTONE 185 C"]), dst)
    assert spots_used(dst) == {"PANTONE 185 C"}


def test_unknown_spot_is_reported_and_nothing_changes(make):
    p = propose(file_with(make, [spot("PANTONE 185 C")]), spots=["PANTONE 999 C=X"])
    assert p.empty and 'no spot colour "PANTONE 999 C"' in p.skipped[0][1]


def test_mapping_rules():
    assert parse_mapping(["a=b", " c = d "]) == {"a": "b", "c": "d"}
    for bad in (["a"], ["a=a"], ["a=Cyan"], ["All=b"], ["a=b", "b=c"]):
        with pytest.raises(ValueError):
            parse_mapping(bad)
    with pytest.raises(ValueError):
        parse_mapping([])


def test_command(make, tmp_path, capsys):
    from prepress_preflight.cli import main
    src = file_with(make, [spot("Pantone 185C")])
    dst = tmp_path / "out.pdf"
    assert main(["fix", "rename-spot", src, "--spot", "Pantone 185C=PANTONE 185 C", "--write", str(dst), "--yes"]) == 0
    assert "only the spot colour definitions change" in capsys.readouterr().out and dst.exists()
