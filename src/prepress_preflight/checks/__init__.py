"""The checks. Each is a self-contained class; see base.Check."""
from .base import Check, Options
from .page_size import PageSizeCheck
from .bleed import BleedBoxCheck
from .bleed_artwork import BleedArtworkCheck
from .safe_margin import SafeMarginCheck
from .rgb import RGBCheck
from .cmyk_profiles import CmykProfileCheck
from .white_overprint import WhiteOverprintCheck
from .black_overprint import BlackOverprintCheck
from .small_text_inks import SmallTextInksCheck
from .reversed_text import ReversedTextCheck
from .blank_pages import BlankPageCheck
from .registration import RegistrationCheck
from .blend_modes import BlendModeCheck
from .transparency import TransparencyCheck
from .blend_over_ink import BlendOverInkCheck
from .total_ink import TotalInkCheck
from .resolution import ImageResolutionCheck
from .image_detail import ImageDetailCheck
from .hairlines import HairlineCheck
from .fonts import FontEmbeddingCheck
from .trim_marks import TrimMarkCheck
from .dielines import DielineCheck
from .spots import SpotColourCheck
from .annotations import AnnotationCheck
from .layers import LayerCheck
from .security import SecurityCheck
from .output_intent import OutputIntentCheck

ALL_CHECKS = [
    PageSizeCheck,
    BlankPageCheck,
    BleedBoxCheck,
    BleedArtworkCheck,
    SafeMarginCheck,
    RGBCheck,
    CmykProfileCheck,
    WhiteOverprintCheck,
    BlackOverprintCheck,
    SmallTextInksCheck,
    ReversedTextCheck,
    RegistrationCheck,
    BlendModeCheck,
    TransparencyCheck,
    BlendOverInkCheck,
    TotalInkCheck,
    ImageResolutionCheck,
    ImageDetailCheck,
    HairlineCheck,
    FontEmbeddingCheck,
    TrimMarkCheck,
    DielineCheck,
    SpotColourCheck,
    AnnotationCheck,
    LayerCheck,
    SecurityCheck,
    OutputIntentCheck,
]

__all__ = ["Check", "Options", "ALL_CHECKS"]
