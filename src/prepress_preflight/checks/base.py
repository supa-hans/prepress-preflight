"""Base class for checks.

A check never modifies the document. It sees each page in turn, then returns
its findings. If it meets something it cannot interpret it must report an
UNCHECKED finding rather than stay silent.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..content import PageContent
from ..document import Document, PageInfo
from ..model import Finding


@dataclass
class Options:
    expected_trim_mm: tuple[float, float] | None = None
    size_tolerance_mm: float = 0.5
    min_bleed_mm: float = 3.0
    min_ppi: float = 150.0
    preferred_ppi: float = 300.0
    ink_limit_pct: float = 400.0
    # A bound job (booklet, catalogue): the spine edge of each page is not
    # trimmed, so it needs no bleed. Page 1 is taken as a right-hand page.
    bound: bool = False
    safe_margin_mm: float = 1.5


class Check:
    id = "check"
    name = "Check"
    needs_content = True  # set False for checks that only read page boxes or render

    def __init__(self, doc: Document, options: Options):
        self.doc = doc
        self.options = options
        self.findings: list[Finding] = []

    def page(self, page: PageInfo, content: PageContent | None) -> None:
        """Called once per page, in order."""

    def finish(self) -> list[Finding]:
        """Called after the last page; returns all findings."""
        return self.findings

    def add(self, severity: str, message: str, pages=(), locations=()):
        self.findings.append(Finding(self.name, severity, message, list(pages), list(locations)))
