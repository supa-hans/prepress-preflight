"""Shared data types: findings, severities and page geometry."""
from __future__ import annotations

from dataclasses import dataclass, field

MM_PER_PT = 25.4 / 72.0

FAIL = "fail"
WARN = "warn"
NOTE = "note"
UNCHECKED = "unchecked"  # the check could not interpret something; never a pass
SEVERITY_ORDER = {FAIL: 0, UNCHECKED: 1, WARN: 2, NOTE: 3}


def mm(points: float) -> float:
    return points * MM_PER_PT


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    pages: list[int] = field(default_factory=list)  # 1-based; empty = whole document
    # Optional locations: (page, [x0, y0, x1, y1]) in PDF points, default user space.
    locations: list[tuple[int, list[float]]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "pages": self.pages,
            "locations": [{"page": p, "bbox": [round(v, 2) for v in b]} for p, b in self.locations[:200]],
        }


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def intersects(self, bbox, inset: float = 0.0) -> bool:
        """True when bbox (x0, y0, x1, y1) overlaps this box shrunk by `inset`."""
        return (
            bbox[2] > self.x0 + inset
            and bbox[0] < self.x1 - inset
            and bbox[3] > self.y0 + inset
            and bbox[1] < self.y1 - inset
        )

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


def merge(findings: list[Finding]) -> list[Finding]:
    """Collapse findings that say the same thing on different pages into one."""
    merged: dict[tuple, Finding] = {}
    for f in findings:
        key = (f.check, f.severity, f.message)
        if key in merged:
            m = merged[key]
            m.pages = sorted(set(m.pages) | set(f.pages))
            m.locations.extend(f.locations)
        else:
            merged[key] = Finding(f.check, f.severity, f.message, sorted(set(f.pages)), list(f.locations))
    return sorted(merged.values(), key=lambda f: (SEVERITY_ORDER[f.severity], f.check, f.pages[:1]))
