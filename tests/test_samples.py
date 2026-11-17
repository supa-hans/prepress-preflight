"""Run the tool against real sample files and compare with agreed results.

Sample PDFs are client artwork and are never committed. Put them in ./samples
(or point PREFLIGHT_SAMPLES at a folder) next to an expected.json like:

    {"Some file.pdf": {"status": "FAIL",
                       "must": [["Bleed box", "fail"]],
                       "must_not": [["White overprint", "fail"]]}}

Without that folder these tests are skipped.
"""
import json
import os
from pathlib import Path

import pytest

from prepress_preflight.engine import run

SAMPLES = Path(os.environ.get("PREFLIGHT_SAMPLES", Path(__file__).resolve().parent.parent / "samples"))
EXPECTED = SAMPLES / "expected.json"
CASES = json.loads(EXPECTED.read_text()) if EXPECTED.exists() else {}


@pytest.mark.skipif(not CASES, reason="no samples/expected.json")
@pytest.mark.parametrize("name", sorted(CASES))
def test_sample(name):
    case = CASES[name]
    result = run(str(SAMPLES / name))
    seen = {(f.check, f.severity) for f in result.findings}
    for check, severity in case.get("must", []):
        assert (check, severity) in seen, f"expected {severity} from {check}"
    for check, severity in case.get("must_not", []):
        assert (check, severity) not in seen, f"unexpected {severity} from {check}"
    if "status" in case:
        assert result.status == case["status"]
    for check, pages in case.get("pages", {}).items():
        got = sorted({p for f in result.findings if f.check == check and f.severity == "fail" for p in f.pages})
        assert got == pages
