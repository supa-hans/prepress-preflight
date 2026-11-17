from __future__ import annotations

from ..model import UNCHECKED, WARN
from .base import Check


class SecurityCheck(Check):
    """Password protection and restrictions.

    A file that needs a password to open cannot be checked at all and is
    reported as unreadable before any check runs. One that opens but carries
    restrictions is a warning: some RIPs and imposition tools refuse it.
    """

    id = "security"
    name = "Security"
    needs_content = False

    def finish(self):
        try:
            if self.doc.pdf.is_encrypted:
                self.add(WARN, "File is encrypted (security restrictions); some RIPs and imposition tools refuse it")
        except Exception as exc:
            self.add(UNCHECKED, f"Security settings could not be read ({type(exc).__name__})")
        return self.findings
