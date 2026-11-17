"""Plain-text report, readable in a terminal and when pasted into a message."""
from __future__ import annotations

import os

from .model import FAIL, NOTE, UNCHECKED, WARN

HEADINGS = [(FAIL, "FAIL"), (UNCHECKED, "COULD NOT CHECK"), (WARN, "WARN"), (NOTE, "NOTES")]


def page_list(pages: list[int], total: int) -> str:
    if not pages:
        return ""
    if total > 1 and len(pages) == total:
        return f"all {total} pages"
    runs, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        runs.append((start, prev))
        start = prev = p
    runs.append((start, prev))
    parts = [str(a) if a == b else f"{a}–{b}" for a, b in runs]
    if len(parts) > 12:
        parts = parts[:12] + ["…"]
    return ("page " if len(pages) == 1 else "pages ") + ", ".join(parts)


def render(result) -> str:
    lines = [f"PREFLIGHT  {os.path.basename(result.path)}"]
    counts = []
    for sev, label in ((FAIL, "fail"), (WARN, "warning"), (UNCHECKED, "could not check")):
        n = result.count(sev)
        if n:
            counts.append(f"{n} {label}{'s' if n != 1 and sev != UNCHECKED else ''}")
    summary = f"Result: {result.status}" + (f"  ({', '.join(counts)})" if counts else "")
    lines += [f"{result.page_count} page{'s' if result.page_count != 1 else ''}", summary, ""]
    for sev, heading in HEADINGS:
        group = [f for f in result.findings if f.severity == sev]
        if not group:
            continue
        lines.append(heading)
        for f in group:
            where = page_list(f.pages, result.page_count)
            show = where and not (result.page_count == 1)
            lines.append(f"  • {f.check}: {f.message}" + (f" ({where})" if show else ""))
        lines.append("")
    passed = result.passed_checks
    if passed:
        lines.append("PASSED: " + ", ".join(passed))
    return "\n".join(lines).rstrip() + "\n"
