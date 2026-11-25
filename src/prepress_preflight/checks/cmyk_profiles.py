"""CMYK tagged with an embedded ICC profile.

Plain CMYK (DeviceCMYK) goes to the plates as its numbers. CMYK tagged with a
profile (ICCBased) tells a colour-managing RIP what press those numbers were
made for, and a RIP set to manage colour converts them to its own press
profile when the two differ. The numbers then change: a K-only black can come
out in four inks, and a clean tint can pick up the others. Whether that
happens depends on the RIP's settings, so it is a note, naming each profile.
The untag-cmyk fix removes the tags and keeps every number as it is.

Only objects that are painted are counted (not alternate spaces of spot
colours, or transparency groups). Objects clipped away are left out.
"""
from __future__ import annotations

import io
from collections import Counter, defaultdict

from ..model import NOTE
from .base import Check

LABELS = {"image": "image(s)", "path": "vector object(s)", "text": "text object(s)", "shading": "gradient(s)",
          "imagemask": "image mask(s)"}


def profile_name(pdf, objgen) -> str:
    """The profile's own description, or a plain stand-in when it cannot be read."""
    try:
        from PIL import ImageCms
        data = pdf.get_object(objgen).read_bytes()
        name = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(data))).strip().strip("\x00")
        return name or "an unnamed profile"
    except Exception:
        return "an embedded profile that could not be read"


class CmykProfileCheck(Check):
    """CMYK objects tagged with an ICC profile, which a colour-managing RIP may convert."""

    id = "cmyk-profiles"
    name = "CMYK profiles"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.found: dict = defaultdict(Counter)  # profile -> {label: count}
        self.pages: dict = defaultdict(set)
        self.where: dict = defaultdict(list)

    def page(self, page, content):
        for e in content.events:
            if e.cs.kind != "cmyk" or e.cs.profile is None:
                continue
            self.found[e.cs.profile][LABELS.get(e.kind, "object(s)")] += 1
            self.pages[e.cs.profile].add(page.number)
            if e.bbox is not None:
                self.where[e.cs.profile].append((page.number, list(e.bbox)))

    def finish(self):
        for profile, counts in self.found.items():
            name = profile_name(self.doc.pdf, profile) if profile != (0, 0) else "an embedded profile"
            what = ", ".join(f"{n} {label}" for label, n in counts.most_common())
            self.add(NOTE, f"CMYK tagged with “{name}”: {what}. A colour-managing RIP may convert it to the press "
                           "profile and change the numbers (a K-only black can print in four inks); untag-cmyk keeps "
                           "the numbers as they are", sorted(self.pages[profile]), self.where[profile])
        return self.findings
