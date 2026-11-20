"""overprint-black: small 100% black text and thin black lines set to overprint."""
import hashlib

import pikepdf
import pytest

from prepress_preflight.actions import parse_steps
from prepress_preflight.content import walk_page
from prepress_preflight.fixes import FIXES

propose, write = FIXES["overprint-black"]


def font(pdf):
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)


def events(path):
    with pikepdf.open(path) as pdf:
        return walk_page(pdf.pages[0]).events


def fixed(make, tmp_path, content, **kw):
    src = make(content, font={"/F1": font}, **kw)
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    p = propose(src)
    dst = str(tmp_path / "out.pdf")
    write(p, dst)
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == before
    return src, dst, p


def test_small_black_text_and_thin_lines_overprint_large_and_rich_do_not(make, tmp_path):
    src, dst, p = fixed(make, tmp_path,
                        b"1 0 0 0 k 50 50 300 200 re f "                       # cyan panel
                        b"BT /F1 9 Tf 0 0 0 1 k 100 100 Td (small) Tj ET "    # 9 pt K100: overprints
                        b"BT /F1 36 Tf 100 150 Td (BIG) Tj ET "               # 36 pt: knocks out still
                        b"BT /F1 9 Tf 0.6 0.4 0.4 1 k 100 200 Td (rich) Tj ET "  # rich black: untouched
                        b"0 0 0 1 K 0.5 w 60 60 m 300 60 l S "                # hairline rule: overprints
                        b"4 w 60 80 m 300 80 l S")                            # 4 pt rule: knocks out
    assert "1 text object(s), 1 line(s)" in p.reasons[0][1] and "halo" in p.reasons[0][1]
    after = [(e.overprint, e.opm) for e in events(dst)]
    assert after == [(False, 0), (True, 1), (False, 0), (False, 0), (True, 1), (False, 0)]


def test_text_in_a_block_puts_the_overprint_back_after(make, tmp_path):
    src, dst, _ = fixed(make, tmp_path, b"BT /F1 9 Tf 0 0 0 1 k 100 100 Td (a) Tj 1 0 0 0 k (b) Tj ET")
    assert [(e.overprint, e.opm) for e in events(dst)] == [(True, 1), (False, 0)]


def test_scaled_text_is_measured_as_placed(make, tmp_path):
    # 6 pt type scaled up 4 times is 24 pt on the page: large
    src = make(b"q 4 0 0 4 0 0 cm BT /F1 6 Tf 0 0 0 1 k 20 20 Td (x) Tj ET Q", font={"/F1": font})
    assert propose(src).empty
    assert not propose(src, max_text=30).empty


def test_grey_black_is_left_and_said(make):
    src = make(b"BT /F1 9 Tf 0 g 100 100 Td (grey) Tj ET", font={"/F1": font})
    p = propose(src)
    assert p.empty and "DeviceGray" in p.skipped[0][1]


def test_one_side_of_a_filled_and_stroked_box(make, tmp_path):
    # white box with a black 0.5 pt edge: the edge overprints, the white fill still knocks out
    src, dst, _ = fixed(make, tmp_path, b"0 0 0 0 k 0 0 0 1 K 0.5 w 100 100 50 50 re B")
    assert [(e.paint, e.overprint) for e in events(dst)] == [("fill", False), ("stroke", True)]


def test_nothing_to_do_and_saved_lists(make):
    src = make(b"0 0 0 1 k 100 100 50 50 re f")  # a black fill is not text or a line
    p = propose(src)
    assert p.empty and "no 100% black text" in p.skipped[0][1]
    assert parse_steps("overprint-black --max-text 10")  # allowed in a saved action list
    with pytest.raises(ValueError, match="negative"):
        propose(src, max_text=-1)
