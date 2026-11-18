"""Run all checks over a document. Read-only."""
from __future__ import annotations

from collections import Counter

from .checks import ALL_CHECKS, Options
from .content import walk_page
from .document import Document
from .marks import infer_trim
from .model import FAIL, NOTE, UNCHECKED, WARN, Finding, merge


class Result:
    def __init__(self, path, page_count, findings, check_names, geometry=None):
        self.path = path
        self.page_count = page_count
        self.findings: list[Finding] = findings
        self.check_names: list[str] = check_names
        # Per page, the boxes the checks measured from (PDF points), including a
        # trim inferred from crop marks. For displays; the report does not need it.
        self.geometry: list[dict] = geometry or []

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def status(self) -> str:
        if self.count(FAIL):
            return "FAIL"
        if self.count(WARN) or self.count(UNCHECKED):
            return "WARN"
        return "PASS"

    @property
    def passed_checks(self) -> list[str]:
        flagged = {f.check for f in self.findings if f.severity != NOTE}
        return [n for n in self.check_names if n not in flagged]

    def to_dict(self) -> dict:
        return {
            "file": self.path,
            "pages": self.page_count,
            "status": self.status,
            "counts": {s: self.count(s) for s in (FAIL, WARN, UNCHECKED, NOTE)},
            "passed_checks": self.passed_checks,
            "findings": [f.to_dict() for f in self.findings],
        }


def run(path: str, options: Options | None = None) -> Result:
    options = options or Options()
    doc = Document(path)
    try:
        checks = [cls(doc, options) for cls in ALL_CHECKS]
        problems: dict[str, set] = {}
        for page in doc.pages:
            content = walk_page(page.obj)
            # Before any check measures anything: if the crop marks disagree
            # with the trim box, the marks are taken as the trim. The Trim
            # marks check reports that this was done.
            infer_trim(page, content, options.size_tolerance_mm)
            for text in content.problems:
                problems.setdefault(text, set()).add(page.number)
            for check in checks:
                check.page(page, content if check.needs_content else None)
        findings: list[Finding] = []
        for check in checks:
            findings.extend(check.finish())
        for text, pages in problems.items():
            findings.append(Finding("Reading the file", UNCHECKED,
                                    f"Some content could not be checked: {text}", sorted(pages)))
        geometry = [{"page": p.number, "media": p.media.as_list(), "trim": p.trim.as_list(), "bleed": p.bleed.as_list(),
                     "has_trim": p.has_trim, "has_bleed": p.has_bleed, "trim_inferred": p.trim_inferred,
                     "declared_trim": p.declared_trim.as_list() if p.declared_trim else None}
                    for p in doc.pages]
        return Result(path, len(doc.pages), merge(findings), [c.name for c in checks], geometry)
    finally:
        doc.close()
