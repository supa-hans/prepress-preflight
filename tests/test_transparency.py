"""The Transparency check: opacity, soft masks and images with alpha, listed as a note."""
import pikepdf

from prepress_preflight.engine import run

from conftest import gray_image


def notes(result):
    return [f for f in result.findings if f.check == "Transparency"]


def test_opacity_soft_masks_and_alpha_are_listed(make):
    def masked(pdf):
        img = gray_image(4, 4)(pdf)
        img.SMask = gray_image(4, 4)(pdf)
        return img
    gs = {"/Half": pikepdf.Dictionary(ca=0.5), "/Zero": pikepdf.Dictionary(ca=0),
          "/Mask": pikepdf.Dictionary(SMask=pikepdf.Dictionary(S=pikepdf.Name.Luminosity))}
    src = make(b"q /Half gs 0 0 0 1 k 100 100 50 50 re f Q "
               b"q /Zero gs 0 0 0 1 k 160 100 50 50 re f Q "
               b"q /Mask gs 1 0 0 0 k 220 100 50 50 re f Q "
               b"q 72 0 0 72 100 200 cm /Im0 Do Q "
               b"0 0 0 1 k 300 100 20 20 re f",  # plain: not counted
               extgstate=gs, xobject={"/Im0": masked})
    (f,) = notes(run(src))
    assert f.severity == "note"
    for part in ("1 object(s) at under 100% opacity", "1 invisible object(s) (opacity 0)",
                 "1 object(s) under a soft mask", "1 image(s) with an alpha channel", "PDF/X-1a"):
        assert part in f.message
    assert len(f.locations) == 4


def test_opaque_artwork_has_no_note(make):
    gs = {"/Full": pikepdf.Dictionary(ca=1, SMask=pikepdf.Name("/None"))}
    assert not notes(run(make(b"/Full gs 0 0 0 1 k 100 100 50 50 re f", extgstate=gs)))
