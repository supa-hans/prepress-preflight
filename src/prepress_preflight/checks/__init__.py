"""The checks. Each is a self-contained class; see base.Check."""
from .base import Check, Options
from .page_size import PageSizeCheck
from .bleed import BleedBoxCheck
from .rgb import RGBCheck
from .white_overprint import WhiteOverprintCheck
from .registration import RegistrationCheck
from .blend_modes import BlendModeCheck
from .total_ink import TotalInkCheck
from .resolution import ImageResolutionCheck

ALL_CHECKS = [
    PageSizeCheck,
    BleedBoxCheck,
    RGBCheck,
    WhiteOverprintCheck,
    RegistrationCheck,
    BlendModeCheck,
    TotalInkCheck,
    ImageResolutionCheck,
]

__all__ = ["Check", "Options", "ALL_CHECKS"]
