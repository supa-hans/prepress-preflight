from __future__ import annotations

from collections import Counter

from ..model import NOTE, UNCHECKED, WARN
from .base import Check

IGNORED = {"Link", "Popup", "TrapNet", "PrinterMark"}  # no printed appearance, or a pre-press object
HIDDEN, PRINT = 2, 4  # annotation flag bits


class AnnotationCheck(Check):
    """Comments, stamps and form fields sitting on top of the artwork.

    Annotations are not part of the page content. Whether one prints depends
    on its Print flag and on the RIP, so anything set to print is a warning,
    and form fields are a warning either way because their appearance is drawn
    by the viewer. Comments that are not set to print are only noted. No other
    check in this tool measures annotations: bleed, ink and margins are judged
    on the page content alone.
    """

    id = "annotations"
    name = "Annotations"
    needs_content = False

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.printing: Counter = Counter()
        self.fields = 0
        self.quiet: Counter = Counter()
        self.pages = {"printing": set(), "fields": set(), "quiet": set()}

    def page(self, page, content):
        try:
            annots = list(page.obj.obj.get("/Annots", []))
        except Exception:
            self.add(UNCHECKED, "Annotations could not be read", [page.number])
            return
        for a in annots:
            try:
                kind = str(a.get("/Subtype", "/Unknown"))[1:]
                flags = int(a.get("/F", 0))
            except Exception:
                self.add(UNCHECKED, "Annotations could not be read", [page.number])
                continue
            if kind in IGNORED or flags & HIDDEN:
                continue
            if kind == "Widget":
                self.fields += 1
                self.pages["fields"].add(page.number)
            elif flags & PRINT:
                self.printing[kind] += 1
                self.pages["printing"].add(page.number)
            else:
                self.quiet[kind] += 1
                self.pages["quiet"].add(page.number)

    def finish(self):
        def kinds(counter):
            return ", ".join(f"{n} {k}" for k, n in counter.most_common())

        if self.printing:
            self.add(WARN, f"Annotations set to print, not part of the artwork: {kinds(self.printing)}",
                     sorted(self.pages["printing"]))
        if self.fields:
            self.add(WARN, f"{self.fields} form field(s): they may print differently or not at all; flatten them",
                     sorted(self.pages["fields"]))
        if self.quiet:
            self.add(NOTE, f"Comments that are not set to print: {kinds(self.quiet)}", sorted(self.pages["quiet"]))
        return self.findings
