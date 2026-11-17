"""The checks. Each is a self-contained class; see base.Check."""
from .base import Check, Options
from .page_size import PageSizeCheck
from .bleed import BleedBoxCheck
from .bleed_artwork import BleedArtworkCheck
from .rgb import RGBCheck
from .white_overprint import WhiteOverprintCheck
from .registration import RegistrationCheck
from .blend_modes import BlendModeCheck
from .blend_over_ink import BlendOverInkCheck
from .total_ink import TotalInkCheck
from .resolution import ImageResolutionCheck
from .hairlines import HairlineCheck
from .fonts import FontEmbeddingCheck

ALL_CHECKS = [
    PageSizeCheck,
    BleedBoxCheck,
    BleedArtworkCheck,
    RGBCheck,
    WhiteOverprintCheck,
    RegistrationCheck,
    BlendModeCheck,
    BlendOverInkCheck,
    TotalInkCheck,
    ImageResolutionCheck,
    HairlineCheck,
    FontEmbeddingCheck,
]

__all__ = ["Check", "Options", "ALL_CHECKS"]
