"""Pages with a UserUnit (very large formats): one page unit is several points.

Every size, length and area must come out as it will be on paper. The same
artwork is checked at UserUnit 1 and 2; at 2 it is twice as large each way.
"""
import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.engine import run
from prepress_preflight.model import FAIL, WARN

from conftest import BLEEDBOX, MM, TRIM, rect

GS = {"/M": pikepdf.Dictionary(BM=pikepdf.Name.Multiply)}


def one(result, check, severity):
    hits = [f for f in result.findings if f.check == check and f.severity == severity]
    assert hits, f"no {severity} from {check}"
    return hits[0].message


def test_total_ink_area_is_four_times_larger(make):
    content = b"1 1 1 0 k 100 100 50 50 re f"  # 17.6 mm square at 1:1 = 311 mm²
    small = one(run(make(content), Options(ink_limit_pct=280)), "Total ink", FAIL)
    large = one(run(make(content, name="big.pdf", user_unit=2), Options(ink_limit_pct=280)), "Total ink", FAIL)
    assert any(f"{n} mm²" in small for n in range(300, 325))
    assert "12 cm²" in large or "13 cm²" in large  # 1,244 mm²


def test_blend_area_is_four_times_larger(make):
    content = b"0 0 0 1 k 100 100 80 80 re f /M gs 140 100 80 80 re f"  # overlap 40 x 80 pt = 398 mm²
    small = one(run(make(content, extgstate=GS)), "Blend over same ink", FAIL)
    large = one(run(make(content, name="big.pdf", extgstate=GS, user_unit=2)), "Blend over same ink", FAIL)
    assert any(f"{n} mm²" in small for n in range(385, 405))
    assert "16 cm²" in large  # 1,593 mm²


def test_bleed_lengths_are_twice_as_long(make):
    content = b"0 0 0 1 k " + rect((BLEEDBOX[0], BLEEDBOX[1], BLEEDBOX[2], TRIM[3])) + b"f"  # no bleed along the top
    assert "top (100 of 100 mm)" in one(run(make(content)), "Bleed artwork", FAIL)
    assert "top (200 of 200 mm)" in one(run(make(content, name="big.pdf", user_unit=2)), "Bleed artwork", FAIL)


def test_bleed_depth_is_judged_on_paper(make):
    # 2 mm of bleed in page units is 4 mm on paper at UserUnit 2: enough.
    content = b"0 0 0 1 k " + rect(TRIM, grow=2 * MM) + b"f"
    assert [f for f in run(make(content)).findings if f.check == "Bleed artwork" and f.severity == FAIL]
    assert not [f for f in run(make(content, name="big.pdf", user_unit=2)).findings if f.check == "Bleed artwork" and f.severity == FAIL]


def test_safe_margin_distance_is_judged_on_paper(make):
    x = TRIM[0] + 1.0 * MM  # 1 mm from the trim at 1:1, 2 mm on paper at UserUnit 2
    content = b"0 0 0 1 k " + rect((x, 100, x + 30, 130)) + b"f"
    assert [f for f in run(make(content)).findings if f.check == "Safe margin" and f.severity == WARN]
    assert not [f for f in run(make(content, name="big.pdf", user_unit=2)).findings if f.check == "Safe margin"]


def test_hairline_weight_is_judged_on_paper(make):
    line = b"0 0 0 1 K 0.2 w 100 100 m 200 100 l S"  # 0.2 pt at 1:1, 0.4 pt on paper at UserUnit 2
    assert [f for f in run(make(line)).findings if f.check == "Hairlines"]
    assert not [f for f in run(make(line, name="big.pdf", user_unit=2)).findings if f.check == "Hairlines"]
