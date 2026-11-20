"""set-output-intent: the print condition the file's CMYK was made for."""
import hashlib

import pikepdf
import pytest

from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES
from prepress_preflight.fixes.rgb_to_cmyk import Engine, find_profile

try:
    PROFILE = find_profile(None)
    Engine(PROFILE)
except ValueError:
    PROFILE = None
pytestmark = pytest.mark.skipif(PROFILE is None, reason="Adobe's Coated FOGRA39 profile is not installed")

propose, write = FIXES["set-output-intent"]


def intent_notes(path):
    return [f.message for f in run(path).findings if f.check == "Output intent"]


def test_sets_fogra39_with_the_profile_embedded(make, tmp_path):
    src = make(b"0 0 0 1 k 100 100 50 50 re f")
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert intent_notes(src) == ["No output intent in the file"]
    p = propose(src)
    assert "Coated FOGRA39 (ISO 12647-2:2004) (FOGRA39)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    with pikepdf.open(dst) as pdf:
        (oi,) = pdf.Root.OutputIntents
        assert oi.S == pikepdf.Name.GTS_PDFX and str(oi.OutputConditionIdentifier) == "FOGRA39"
        assert oi.DestOutputProfile.read_bytes() == open(PROFILE, "rb").read()
        assert int(oi.DestOutputProfile.N) == 4
    assert intent_notes(dst) == ["Output intent: Coated FOGRA39 (ISO 12647-2:2004) (FOGRA39)"]
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before


def test_an_existing_intent_is_left_unless_replace(make, tmp_path):
    src = make(b"0 0 0 1 k 100 100 50 50 re f")
    first = str(tmp_path / "first.pdf")
    write(propose(src), first)
    p = propose(first)
    assert p.empty and "already states an output intent" in p.skipped[0][1]
    gracol = find_profile("fogra39").replace("FOGRA39", "GRACoL2006")
    if not pytest.importorskip("os").path.isfile(gracol):
        pytest.skip("GRACoL 2006 profile not installed")
    swapped = propose(first, profile="gracol2006", replace=True)
    assert "Replaces: Coated FOGRA39" in swapped.reasons[0][1]
    second = str(tmp_path / "second.pdf")
    write(swapped, second)
    with pikepdf.open(second) as pdf:
        assert [str(o.OutputConditionIdentifier) for o in pdf.Root.OutputIntents] == ["CGATS TR 006"]
