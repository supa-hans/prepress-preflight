from __future__ import annotations

from ..model import NOTE
from .base import Check


class BlendModeCheck(Check):
    """List blend modes in use. Informational only."""

    id = "blend-modes"
    name = "Blend modes"

    def page(self, page, content):
        for mode in sorted(content.blend_modes):
            self.add(NOTE, f"Blend mode in use: {mode}", [page.number])
