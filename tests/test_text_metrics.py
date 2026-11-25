"""Text runs measured from the font's glyph widths."""
import pikepdf
import pytest

from prepress_preflight.content import walk_page


def font_with_widths(pdf):
    # FirstChar 65 ('A'): A = 500, B = 1000 units; space (32) is outside the table so takes MissingWidth 250
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name("/Test"),
                              FirstChar=65, LastChar=66, Widths=[500, 1000],
                              FontDescriptor=pikepdf.Dictionary(Type=pikepdf.Name.FontDescriptor, MissingWidth=250))


def cid_font(pdf):
    cid = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2, BaseFont=pikepdf.Name("/Cid"),
                             DW=1000, W=[3, [600, 400], 10, 12, 300])
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=pikepdf.Name("/Cid"),
                              Encoding=pikepdf.Name("/Identity-H"), DescendantFonts=[cid])


def texts(make, body: bytes, font=font_with_widths):
    path = make(b"BT /F1 10 Tf " + body + b" ET", font={"/F1": font})
    with pikepdf.open(path) as pdf:
        return [e for e in walk_page(pdf.pages[0]).events if e.kind == "text"]


def width(e):
    return round(e.bbox[2] - e.bbox[0], 3)


def test_a_run_is_as_wide_as_its_glyphs(make):
    (e,) = texts(make, b"100 100 Td (AB) Tj")
    assert e.bbox[0] == pytest.approx(100) and width(e) == 15.0  # (500 + 1000) / 1000 * 10 pt


def test_the_next_run_starts_where_the_last_ended(make):
    a, b = texts(make, b"100 100 Td (AB) Tj (A) Tj")
    assert b.bbox[0] == pytest.approx(115) and width(b) == 5.0


def test_kerning_spacing_and_scaling(make):
    (e,) = texts(make, b"100 100 Td [(A) -500 (A)] TJ")
    assert width(e) == 15.0  # 5 + 5 + 5 (a negative adjustment moves right)
    (e,) = texts(make, b"2 Tc 100 100 Td (AA) Tj")
    assert width(e) == 14.0  # character spacing after each glyph
    (e,) = texts(make, b"3 Tw 100 100 Td (A A) Tj")
    assert width(e) == 15.5  # 5 + (2.5 + 3 word spacing) + 5
    (e,) = texts(make, b"50 Tz 100 100 Td (AB) Tj")
    assert width(e) == 7.5


def test_two_byte_identity_fonts(make):
    (e,) = texts(make, b"100 100 Td <000300040005000B> Tj", font=cid_font)
    assert width(e) == 23.0  # cids 3, 4 listed (600, 400), 5 takes DW (1000), 11 in the 10-12 range (300)


def test_a_font_without_widths_keeps_the_one_em_box(make):
    def helvetica(pdf):
        return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    a, b = texts(make, b"100 100 Td (AB) Tj (A) Tj", font=helvetica)
    assert width(a) == 10.0 and b.bbox[0] == pytest.approx(100)  # unknown advance: position not moved
