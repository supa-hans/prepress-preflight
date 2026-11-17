from __future__ import annotations

from ..model import NOTE, UNCHECKED
from .base import Check


def _text(value) -> str:
    return str(value).replace("\x00", "").strip()


class OutputIntentCheck(Check):
    """Report the print condition the file says it was made for.

    Informational: many good print files carry no output intent. It matters
    when the stated condition is not the one the job will be printed to.
    """

    id = "output-intent"
    name = "Output intent"
    needs_content = False

    def finish(self):
        try:
            intents = list(self.doc.pdf.Root.get("/OutputIntents", []))
            found = []
            for intent in intents:
                ident = _text(intent.get("/OutputConditionIdentifier", ""))
                info = _text(intent.get("/Info", "")) or _text(intent.get("/OutputCondition", ""))
                label = info if info and info != ident else ""
                label = f"{label} ({ident})" if label and ident else (label or ident or "unnamed")
                if "/DestOutputProfile" not in intent:
                    label += ", no profile embedded"
                found.append(label)
        except Exception as exc:
            self.add(UNCHECKED, f"Output intent could not be read ({type(exc).__name__})")
            return self.findings
        self.add(NOTE, "Output intent: " + "; ".join(found) if found else "No output intent in the file")
        return self.findings
