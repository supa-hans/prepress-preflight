from __future__ import annotations

from dataclasses import dataclass, field

import pikepdf

from ..model import mm


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
    plan: dict = field(default_factory=dict)  # fix-specific details the write step needs, by page

    @property
    def empty(self) -> bool:
        return not self.changes and not self.plan


def size_text(box) -> str:
    return f"{mm(box[2] - box[0]):.1f} × {mm(box[3] - box[1]):.1f} mm"


def rounded(box) -> list:
    return [round(float(v), 3) for v in box]


def box_change(proposal, page_number, page_dict, key, new) -> None:
    """Record a change to one page box, if it is a change."""
    new = rounded(new)
    old = rounded(page_dict["/" + key]) if "/" + key in page_dict else None
    if old is None or any(abs(a - b) > 0.01 for a, b in zip(old, new)):
        proposal.changes.append(Change(page_number, key, old, new, size_text(old) if old else "not set", size_text(new)))


def write_box_changes(proposal, dst: str) -> None:
    """Write a proposal that changes only page boxes to a new file `dst`.

    Each changed page gets one new copy of its page dictionary, appended to
    the original bytes (see append.py). The original is never written to.
    """
    from .append import append_update

    if proposal.empty:
        raise ValueError("nothing to change")
    by_page: dict[int, dict] = {}
    for c in proposal.changes:
        by_page.setdefault(c.page, {})[c.box] = c.after
    with pikepdf.open(proposal.path) as pdf:
        changes = {}
        for number, boxes in by_page.items():
            page = pdf.pages[number - 1].obj
            copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
            for key, value in boxes.items():
                copy["/" + key] = pikepdf.Array(value)
            changes[page.objgen] = copy
        append_update(proposal.path, dst, changes)
