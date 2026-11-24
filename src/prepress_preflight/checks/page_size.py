from __future__ import annotations

from collections import OrderedDict

from ..model import NOTE, WARN
from .base import Check


def _fmt(size) -> str:
    return f"{size[0]:.1f} × {size[1]:.1f} mm"


class PageSizeCheck(Check):
    """Report the trim size; warn when pages differ or miss the expected size."""

    id = "page-size"
    name = "Page size"
    needs_content = False

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.sizes: list[tuple[int, tuple[float, float]]] = []
        self.no_trim: list[int] = []
        self.inferred: list[int] = []

    def page(self, page, content):
        self.sizes.append((page.number, page.trim_size_mm))
        if page.trim_inferred:
            self.inferred.append(page.number)
        elif not page.has_trim:
            self.no_trim.append(page.number)

    def finish(self):
        tol = self.options.size_tolerance_mm
        groups: OrderedDict = OrderedDict()  # representative size -> pages
        for number, size in self.sizes:
            for rep in groups:
                if abs(rep[0] - size[0]) <= tol and abs(rep[1] - size[1]) <= tol:
                    groups[rep].append(number)
                    break
            else:
                groups[size] = [number]
        if len(groups) == 1:
            size, pages = next(iter(groups.items()))
            self.add(NOTE, f"Trim size {_fmt(size)}", pages)
        else:
            for size, pages in groups.items():
                self.add(WARN, f"Pages have different trim sizes: {_fmt(size)}", pages)
        if self.inferred:
            self.add(NOTE, "Trim size inferred from the crop marks, not taken from the file's trim box", self.inferred)
        if self.no_trim:
            self.add(NOTE, "No trim box defined; the full page size is treated as the trim", self.no_trim)
        if self.options.bound:
            self._page_count(len(self.sizes))
        exp = self.options.expected_trim_mm
        if exp:
            for size, pages in groups.items():
                if abs(size[0] - exp[0]) <= tol and abs(size[1] - exp[1]) <= tol:
                    continue
                rotated = abs(size[0] - exp[1]) <= tol and abs(size[1] - exp[0]) <= tol
                extra = " (matches if rotated)" if rotated else ""
                self.add(WARN, f"Trim size {_fmt(size)} does not match the expected {_fmt(exp)}{extra}", pages)
        return self.findings

    def _page_count(self, n: int):
        """A bound job: every leaf has two sides, and saddle stitching folds sheets into fours."""
        if n % 2:
            self.add(WARN, f"{n} pages: a bound job has an even number of pages (two to every leaf). A page is "
                           "missing or extra, or the file holds a single cover page")
        elif n % 4:
            self.add(NOTE, f"{n} pages: not a multiple of 4. Saddle stitching needs one, so {4 - n % 4} blank pages "
                           "would be added, unless the cover is a separate file; perfect binding only needs an even "
                           "count")
