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


# ---- the check that reports it ------------------------------------------------

from prepress_preflight.engine import run  # noqa: E402


def black_findings(result):
    return [f for f in result.findings if f.check == "Black overprint"]


def test_check_notes_small_black_that_knocks_out_and_nothing_else(make):
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1), "/GS1": pikepdf.Dictionary(OP=True, op=True, OPM=0)}
    src = make(b"BT /F1 9 Tf 0 0 0 1 k 100 100 Td (ko) Tj ET "                 # knocks out: noted
               b"q /GS1 gs BT /F1 9 Tf 0 0 0 1 k 100 120 Td (mode0) Tj ET Q "  # overprint in mode 0: noted
               b"q /GS0 gs BT /F1 9 Tf 0 0 0 1 k 100 140 Td (op) Tj ET Q "     # overprints: fine
               b"BT /F1 36 Tf 0 0 0 1 k 100 160 Td (BIG) Tj ET "              # large: fine
               b"0 0 0 1 K 0.5 w 60 60 m 300 60 l S",                          # thin rule: noted
               font={"/F1": font}, extgstate=gs)
    result = run(src)
    (f,) = black_findings(result)
    assert f.severity == "note" and "2 text object(s), 1 line(s)" in f.message
    assert len(f.locations) == 3
    after = propose(src)
    assert "2 text object(s), 1 line(s)" in after.reasons[0][1]  # the fix takes exactly what the check noted


def test_check_is_silent_once_fixed(make, tmp_path):
    src, dst, _ = fixed(make, tmp_path, b"BT /F1 9 Tf 0 0 0 1 k 100 100 Td (ko) Tj ET")
    assert black_findings(run(src)) and not black_findings(run(dst))


def test_copies_off_the_page_are_counted_apart(make):
    src = make(b"0 0 0 1 K 0.5 w 60 60 m 300 60 l S -300 60 m -100 60 l S", font={"/F1": font})
    text = propose(src).reasons[0][1]
    assert ": 1 line(s) (text up to" in text and "Also 1 line(s) off the page" in text
    assert "1 line(s)" in black_findings(run(src))[0].message  # the check sees only the one on the page
