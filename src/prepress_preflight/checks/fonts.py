from __future__ import annotations

import re

from ..model import FAIL, UNCHECKED
from .base import Check

_FILES = ("/FontFile", "/FontFile2", "/FontFile3")


def font_name(font) -> str:
    name = str(font.get("/BaseFont", font.get("/Name", "/(unnamed)")))[1:]
    return re.sub(r"^[A-Z]{6}\+", "", name)  # drop the subset tag


def is_embedded(font) -> bool:
    """True when the font program travels with the file."""
    subtype = str(font.get("/Subtype"))
    if subtype == "/Type3":
        return True  # its glyphs are drawn by the file itself
    if subtype == "/Type0":
        font = font["/DescendantFonts"][0]
    descriptor = font.get("/FontDescriptor")
    return descriptor is not None and any(k in descriptor for k in _FILES)


class FontEmbeddingCheck(Check):
    """Fonts used for visible text must be embedded.

    Without the font the RIP substitutes another, and the text reflows or
    changes shape. Only fonts actually used to show visible text are checked;
    invisible text (an OCR layer) is ignored. Text converted to outlines has no
    font and is not seen here.
    """

    id = "fonts"
    name = "Font embedding"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.missing: dict[str, set] = {}

    def page(self, page, content):
        unreadable = 0
        for font in content.fonts.values():
            try:
                if not is_embedded(font):
                    self.missing.setdefault(font_name(font), set()).add(page.number)
            except Exception:
                unreadable += 1
        if unreadable:
            self.add(UNCHECKED, f"{unreadable} font(s) could not be read", [page.number])

    def finish(self):
        if self.missing:
            names = sorted(self.missing)
            shown = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
            self.add(FAIL, f"Font not embedded: {shown}", sorted(set().union(*self.missing.values())))
        return self.findings
