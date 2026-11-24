"""Run all checks over a document. Read-only."""
from __future__ import annotations

import os
import threading
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context

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


# Checks that share measurements through the document's cache stay together,
# and the slow ones each get a process of their own. Every check still sees
# exactly the pages and pixels it would in one process: this only changes
# how long a run takes, never what it finds.
GROUPS = [
    ("Bleed box", "Bleed artwork", "Safe margin"),  # the edge strips
    ("Blend over same ink",),
    ("Total ink",),
    ("Image detail",),
]
PARALLEL_BYTES = 8_000_000  # smaller files are quicker in one process than starting workers
_pool = None
_pool_lock = threading.Lock()  # the app may run files from several threads


def _workers() -> int:
    try:
        return max(1, int(os.environ.get("PREFLIGHT_WORKERS", "0")) or min(len(GROUPS) + 1, os.cpu_count() or 1))
    except ValueError:
        return 1


def _check_pages(path: str, options: Options, names: tuple | None):
    """Run the named checks (all when None) over every page: (findings by check, problems, geometry, pages)."""
    doc = Document(path)
    try:
        checks = [cls(doc, options) for cls in ALL_CHECKS if names is None or cls.name in names]
        problems: dict[str, set] = {}
        for page in doc.pages:
            content = walk_page(page.obj)
            # Before any check measures anything: if the crop marks disagree
            # with the trim box, the marks are taken as the trim. The Trim
            # marks check reports that this was done.
            infer_trim(page, content, options.size_tolerance_mm)
            for text in content.problems:
                problems.setdefault(text, set()).add(page.number)
            visible = content.visible()
            for check in checks:
                check.page(page, (content if check.sees_hidden else visible) if check.needs_content else None)
        found = {check.name: check.finish() for check in checks}
        geometry = [{"page": p.number, "media": p.media.as_list(), "trim": p.trim.as_list(), "bleed": p.bleed.as_list(),
                     "has_trim": p.has_trim, "has_bleed": p.has_bleed, "trim_inferred": p.trim_inferred,
                     "declared_trim": p.declared_trim.as_list() if p.declared_trim else None}
                    for p in doc.pages]
        return found, problems, geometry, len(doc.pages)
    finally:
        doc.close()


def _parallel(path, options, workers):
    """The slow checks in worker processes, the rest here. (None, ...) when workers cannot be started."""
    global _pool
    grouped = {n for g in GROUPS for n in g}
    rest = tuple(cls.name for cls in ALL_CHECKS if cls.name not in grouped)
    with _pool_lock:
        if _pool is None:
            # a fresh worker per task: a long-running app or watched folder does not keep a big file's memory
            _pool = ProcessPoolExecutor(min(workers, len(GROUPS) + 1), mp_context=get_context("spawn"),
                                        max_tasks_per_child=1)
        pool = _pool
    try:
        jobs = [pool.submit(_check_pages, path, options, g) for g in GROUPS]
    except (BrokenProcessPool, RuntimeError):
        jobs = None
    if jobs is not None:
        found, problems, geometry, pages = _check_pages(path, options, rest)  # this process does the rest meanwhile
        try:
            for job in jobs:
                found.update(job.result()[0])
            return found, problems, geometry, pages
        except BrokenProcessPool:  # a worker could not start or died: the same checks, in one process
            pass
    with _pool_lock:
        if _pool is pool:
            _pool = None
    return None, None, None, None


def run(path: str, options: Options | None = None) -> Result:
    options = options or Options()
    workers = _workers()
    found = None
    if workers > 1 and os.path.getsize(path) >= PARALLEL_BYTES:
        found, problems, geometry, pages = _parallel(path, options, workers)
    if found is None:
        found, problems, geometry, pages = _check_pages(path, options, None)
    findings: list[Finding] = []
    for cls in ALL_CHECKS:
        findings.extend(found[cls.name])
    for text, where in problems.items():
        findings.append(Finding("Reading the file", UNCHECKED,
                                f"Some content could not be checked: {text}", sorted(where)))
    return Result(path, pages, merge(findings), [cls.name for cls in ALL_CHECKS], geometry)
