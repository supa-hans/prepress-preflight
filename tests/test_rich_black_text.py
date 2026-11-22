"""Small text inks (check) and rich-black-text (fix)."""
import hashlib

import pikepdf

from prepress_preflight.actions import parse_steps
from prepress_preflight.content import walk_page
from prepress_preflight.engine import run
from prepress_preflight.fixes import FIXES

from conftest import registration_space

propose, write = FIXES["rich-black-text"]
RICH = b"0.75 0.68 0.67 0.9 k"


def font(pdf):
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def findings(result):
    return [(f.severity, f.message) for f in result.findings if f.check == "Small text inks"]


CONTENT = (b"BT /F1 7 Tf " + RICH + b" 100 100 Td (rich small) Tj ET "    # warn, fixed
           b"BT /F1 24 Tf " + RICH + b" 100 140 Td (rich big) Tj ET "     # large: fine
           b"BT /F1 7 Tf 1 0.69 0 0.11 k 100 180 Td (blue) Tj ET "        # three inks: a note, left
           b"BT /F1 7 Tf 0 1 1 0 k 100 200 Td (red) Tj ET "               # two inks: fine
           b"BT /F1 7 Tf 3 Tr " + RICH + b" 100 220 Td (ocr) Tj ET")      # invisible: ignored


def test_the_check_warns_on_rich_black_and_notes_other_builds(make):
    got = findings(run(make(CONTENT, font={"/F1": font})))
    assert [s for s, _ in got] == ["warn", "note"]
    assert "rich black: 1 text object(s), 1 in C75 M68 Y67 K90" in got[0][1]
    assert "three or more inks: 1 text object(s), 1 in C100 M69 Y0 K11" in got[1][1]


def test_the_fix_makes_small_rich_black_k100_and_nothing_else(make, tmp_path):
    src = make(CONTENT, font={"/F1": font})
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src)
    assert "K100 only: 1 text object(s)" in p.reasons[0][1]
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    a, b = events(src), events(dst)
    assert b[0].colour == (0.0, 0.0, 0.0, 1.0)
    assert [(e.cs, e.colour) for e in b[1:]] == [(e.cs, e.colour) for e in a[1:]]
    assert [s for s, _ in findings(run(dst))] == ["note"]  # the warning is gone; the blue stays noted


def test_in_a_text_block_the_spot_in_force_is_put_back(make, tmp_path):
    src = make(b"BT /F1 7 Tf " + RICH + b" 100 100 Td (a) Tj /CS0 cs 0.5 scn (b) Tj " + RICH + b" (c) Tj ET",
               font={"/F1": font}, colorspace={"/CS0": registration_space()})
    dst = str(tmp_path / "out.pdf")
    write(propose(src), dst)
    assert [e.colour for e in events(dst)] == [(0.0, 0.0, 0.0, 1.0), events(src)[1].colour, (0.0, 0.0, 0.0, 1.0)]


def test_larger_limit_and_saved_lists(make):
    src = make(b"BT /F1 10 Tf " + RICH + b" 100 100 Td (x) Tj ET", font={"/F1": font})
    assert propose(src).empty and not propose(src, max_text=12).empty
    assert parse_steps("rich-black-text\noverprint-black")
