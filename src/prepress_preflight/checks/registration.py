from __future__ import annotations

from ..model import FAIL, UNCHECKED
from .base import Check

INSET_PT = 0.5  # marks that only touch the trim edge are not "inside"


class RegistrationCheck(Check):
    """Registration colour prints on every plate. Inside the trim it is a fail.

    Outside the trim it is expected: printer's marks use it.
    """

    id = "registration"
    name = "Registration inside trim"

    def page(self, page, content):
        inside = 0
        unlocated = 0
        locations = []
        for e in content.events:
            if not e.cs.is_registration:
                continue
            if e.colour is not None and len(e.colour) and max(e.colour) <= 0.001:
                continue  # 0% tint puts down no ink
            if e.bbox is None:
                unlocated += 1
                continue
            hits = [b for b in e.boxes() if page.trim.intersects(b, INSET_PT)]
            if hits:
                inside += 1
                locations.extend((page.number, list(b)) for b in hits[:20])
        if inside:
            self.add(FAIL, f"{inside} object(s) in registration colour inside the trim", [page.number], locations)
        if unlocated:
            self.add(UNCHECKED, f"{unlocated} registration-colour object(s) could not be located on the page", [page.number])
