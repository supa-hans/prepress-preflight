from __future__ import annotations

from ..model import NOTE, WARN
from .base import Check

HIGH_PPI = 450  # Acrobat's and PitStop's usual downsampling threshold; downsample-images uses the same


class ImageResolutionCheck(Check):
    """Effective resolution of each placed image, at the size it is placed."""

    id = "image-resolution"
    name = "Image resolution"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.mid_count = 0
        self.mid_pages: list[int] = []
        self.low_count = 0
        self.low_pages: list[int] = []
        self.lowest: tuple[float, int] | None = None  # (ppi, page)
        self.low_locations: list = []
        self.high_count = 0
        self.high_pages: list[int] = []
        self.highest: tuple[float, int] | None = None

    def page(self, page, content):
        low = []
        mid = 0
        area = page.print_area
        for e in content.events:
            if e.kind != "image" or e.ppi is None or e.px is None:
                continue
            if min(e.px) <= 2:
                continue  # one-pixel strips used as gradients or rules; resolution is meaningless
            if e.bbox is not None and not area.intersects(e.bbox):
                continue
            ppi = e.ppi / page.unit
            if ppi < self.options.min_ppi - 0.5:
                low.append((ppi, e.bbox))
            elif ppi < self.options.preferred_ppi - 0.5:
                mid += 1
            elif ppi > HIGH_PPI + 0.5:
                self.high_count += 1
                if not self.high_pages or self.high_pages[-1] != page.number:
                    self.high_pages.append(page.number)
                if self.highest is None or ppi > self.highest[0]:
                    self.highest = (ppi, page.number)
        if low:
            lowest = min(p for p, _ in low)
            self.low_count += len(low)
            self.low_pages.append(page.number)
            self.low_locations.extend((page.number, list(b)) for _, b in low if b is not None)
            if self.lowest is None or lowest < self.lowest[0]:
                self.lowest = (lowest, page.number)
        if mid:
            self.mid_count += mid
            self.mid_pages.append(page.number)

    def finish(self):
        if self.low_count:
            where = f" on page {self.lowest[1]}" if len(self.low_pages) > 1 else ""
            self.add(WARN, f"{self.low_count} image(s) below {self.options.min_ppi:g} ppi; "
                           f"lowest is {self.lowest[0]:.0f} ppi{where}", self.low_pages, self.low_locations)
        if self.mid_count:
            self.add(NOTE, f"{self.mid_count} image(s) between {self.options.min_ppi:g} and "
                           f"{self.options.preferred_ppi:g} ppi", self.mid_pages)
        if self.high_count:
            where = f" on page {self.highest[1]}" if len(self.high_pages) > 1 else ""
            self.add(NOTE, f"{self.high_count} image(s) over {HIGH_PPI} ppi, more than the press can use; highest is "
                           f"{self.highest[0]:.0f} ppi{where}. Downsampling would make the file smaller",
                     self.high_pages)
        return self.findings
