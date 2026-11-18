"""Spot colours: listing, duplicates, conflicting definitions, CMYK-only jobs."""
import pikepdf

from prepress_preflight.checks import Options
from prepress_preflight.checks.spots import ink_key
from prepress_preflight.engine import run
from prepress_preflight.model import FAIL, NOTE, WARN

CHECK = "Spot colours"


def spot(name, c1=(0, 1, 0, 0)):
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=list(c1), N=1)
    return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/" + name), pikepdf.Name.DeviceCMYK, tint])


def found(result, severity):
    return [f for f in result.findings if f.check == CHECK and f.severity == severity]


def boxes(*names):
    return " ".join(f"/CS{i} cs 1 scn {100 + 30 * i} 100 20 20 re f" for i in range(len(names))).encode()


def test_spots_in_use_are_listed(make):
    result = run(make(boxes("PANTONE 185 C"), colorspace={"/CS0": spot("PANTONE 185 C")}))
    f = found(result, NOTE)
    assert f and "PANTONE 185 C (1 vector)" in f[0].message and "own plate" in f[0].message
    assert result.status == "PASS"


def test_cmyk_only_job_fails_on_a_printing_spot(make):
    f = found(run(make(boxes("PANTONE 185 C"), colorspace={"/CS0": spot("PANTONE 185 C")}), Options(cmyk_only=True)), FAIL)
    assert f and "process colours only" in f[0].message


def test_non_printing_spot_never_fails_a_cmyk_only_job(make):
    result = run(make(boxes("Dieline"), colorspace={"/CS0": spot("Dieline")}), Options(cmyk_only=True))
    assert not found(result, FAIL) and "Non-printing" in found(result, NOTE)[0].message


def test_one_ink_spelt_two_ways_warns(make):
    spaces = {"/CS0": spot("PANTONE 185 C"), "/CS1": spot("Pantone 185C")}
    f = found(run(make(boxes("a", "b"), colorspace=spaces)), WARN)
    assert f and '"PANTONE 185 C"' in f[0].message and '"Pantone 185C"' in f[0].message


def test_one_name_defined_two_ways_warns(make):
    spaces = {"/CS0": spot("PANTONE 185 C", (0, 1, 0.8, 0)), "/CS1": spot("PANTONE 185 C", (0, 0.9, 0.7, 0))}
    f = found(run(make(boxes("a", "b"), colorspace=spaces)), WARN)
    assert f and "defined 2 different ways" in f[0].message


def test_zero_tint_puts_down_no_ink(make):
    content = b"/CS0 cs 0 scn 100 100 20 20 re f"
    assert not [f for f in run(make(content, colorspace={"/CS0": spot("PANTONE 185 C")})).findings if f.check == CHECK]


def test_ink_key():
    assert ink_key("PANTONE 185 C") == ink_key("Pantone 185C") == ink_key("185 C") == ink_key("PMS 185 C")
    assert ink_key("PANTONE 185 C") != ink_key("PANTONE 185 U")
