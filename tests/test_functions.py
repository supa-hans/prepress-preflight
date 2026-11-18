"""PDF functions: each type evaluated, and checked against MuPDF's own rendering."""
import numpy as np
import pikepdf
import pymupdf
import pytest

from prepress_preflight.functions import Function, FunctionError


def test_exponential():
    f = Function(pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0.2], C1=[1, 0.6], N=2))
    assert f([0.5]) == pytest.approx([0.25, 0.3])
    assert f([2.0]) == pytest.approx([1.0, 0.6])  # inputs are clipped to the domain


def test_stitching():
    a = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0], C1=[1], N=1)
    b = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[1], C1=[0], N=1)
    f = Function(pikepdf.Dictionary(FunctionType=3, Domain=[0, 1], Functions=[a, b], Bounds=[0.5], Encode=[0, 1, 0, 1]))
    assert f([0.25]) == pytest.approx([0.5]) and f([0.75]) == pytest.approx([0.5]) and f([0.5]) == pytest.approx([1.0])


def test_sampled_one_and_two_inputs():
    pdf = pikepdf.new()
    one = pdf.make_stream(bytes([0, 255]), FunctionType=0, Domain=[0, 1], Range=[0, 1], Size=[2], BitsPerSample=8)
    assert Function(one)([0.25]) == pytest.approx([0.25])
    # 2 x 2 samples: corners 0, 1 / 1, 0 -> bilinear centre 0.5
    two = pdf.make_stream(bytes([0, 255, 255, 0]), FunctionType=0, Domain=[0, 1, 0, 1], Range=[0, 1], Size=[2, 2],
                          BitsPerSample=8)
    f = Function(two)
    assert f([0.5, 0.5]) == pytest.approx([0.5]) and f([1, 0]) == pytest.approx([1.0])
    sixteen = pdf.make_stream((0).to_bytes(2, "big") + (65535).to_bytes(2, "big"), FunctionType=0, Domain=[0, 1],
                              Range=[0, 1], Size=[2], BitsPerSample=16)
    assert Function(sixteen)([0.5]) == pytest.approx([0.5], abs=1e-4)


def postscript(pdf, program, inputs, outputs):
    return pdf.make_stream(program, FunctionType=4, Domain=[0, 1] * inputs, Range=[0, 1] * outputs)


def test_postscript_calculator():
    pdf = pikepdf.new()
    # x -> (2x folded about 0.5, then three zeros): x=0.25 gives 0.5, x=0.75 gives 0.5
    f = Function(postscript(pdf, b"{ dup 0.5 gt { 1 exch sub } if 2 mul 0 0 0 }", 1, 4))
    assert f([0.25]) == pytest.approx([0.5, 0, 0, 0]) and f([0.75]) == pytest.approx([0.5, 0, 0, 0])
    g = Function(postscript(pdf, b"{ 2 copy mul 3 1 roll add }", 2, 2))  # (a, b) -> (a*b, a+b)
    assert g([0.5, 0.25]) == pytest.approx([0.125, 0.75])
    h = Function(postscript(pdf, b"{ 0.3 lt { 1 } { 0 } ifelse }", 1, 1))
    assert h([0.1]) == [1.0] and h([0.9]) == [0.0]
    with pytest.raises(FunctionError):
        Function(postscript(pdf, b"{ dup frobnicate }", 1, 1))([0.5])


def test_unsupported_type():
    with pytest.raises(FunctionError):
        Function(pikepdf.Dictionary(FunctionType=7, Domain=[0, 1]))


# InDesign's two-ink mixing model, as written in the sample catalogue: for each process
# ink, 1 - (1 - a*A)(1 - b*B), where A and B are each spot's value for that ink.
INDESIGN = (b"{1.000000 3 1 roll 1.000000 3 1 roll 1.000000 3 1 roll 1.000000 3 1 roll "
            b"6 -1 roll 2 index 0.913451 mul 1.000000 cvr exch sub mul 1 index 0.401221 mul 1.000000 cvr exch sub mul "
            b"1.000000 cvr exch sub 6 1 roll 5 -1 roll 2 index 0.526726 mul 1.000000 cvr exch sub mul 1 index "
            b"0.138613 mul 1.000000 cvr exch sub mul 1.000000 cvr exch sub 5 1 roll 4 -1 roll 2 index 0.0 mul "
            b"1.000000 cvr exch sub mul 1 index 0.0 mul 1.000000 cvr exch sub mul 1.000000 cvr exch sub 4 1 roll "
            b"3 -1 roll 2 index 0.0 mul 1.000000 cvr exch sub mul 1 index 0.0 mul 1.000000 cvr exch sub mul "
            b"1.000000 cvr exch sub 3 1 roll pop pop}")


def test_postscript_agrees_with_mupdf(tmp_path):
    """The same DeviceN colours, worked out by this module and rendered by MuPDF."""
    pymupdf.TOOLS.set_icc(False)
    pdf = pikepdf.new()
    tint = postscript(pdf, INDESIGN, 2, 4)
    space = pikepdf.Array([pikepdf.Name.DeviceN, [pikepdf.Name("/PANTONE 285 C"), pikepdf.Name("/PANTONE 283 C")],
                           pikepdf.Name.DeviceCMYK, tint])
    pairs = [(0, 0), (1, 0), (0, 1), (1, 1), (0.5, 0.25), (0.3, 0.9)]
    content = b"".join(f"/CS0 cs {a} {b} scn {10 + 40 * i} 10 30 30 re f ".encode() for i, (a, b) in enumerate(pairs))
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 260, 50],
                              Resources=pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(CS0=space)),
                              Contents=pdf.make_stream(content))
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    path = str(tmp_path / "dn.pdf")
    pdf.save(path)
    pix = pymupdf.open(path)[0].get_pixmap(colorspace=pymupdf.csCMYK, dpi=72, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4) / 255
    f = Function(tint)
    for i, pair in enumerate(pairs):
        assert np.abs(a[25, 25 + 40 * i] - np.array(f(list(pair)))).max() <= 0.006  # within MuPDF's 8-bit step
