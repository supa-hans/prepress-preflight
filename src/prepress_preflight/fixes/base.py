from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Change:
    page: int
    box: str  # e.g. "TrimBox"
    before: list | None  # page units; None when the box is not in the file
    after: list
    before_mm: str
    after_mm: str


@dataclass
class Proposal:
    fix: str
    path: str
    changes: list = field(default_factory=list)
    reasons: list = field(default_factory=list)  # one line per page explaining why
    skipped: list = field(default_factory=list)  # pages left alone, and why

    @property
    def empty(self) -> bool:
        return not self.changes
