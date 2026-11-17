from __future__ import annotations

from ..model import NOTE, UNCHECKED, WARN
from .base import Check


class LayerCheck(Check):
    """Layers (optional content), and whether any are switched off.

    A hidden layer is a risk in both directions: a RIP may ignore the setting
    and print it, or the hidden layer may hold something that was meant to
    print. Within this tool, checks that read the page content (RGB, white
    overprint, registration, fonts, hairlines) look at hidden layers too, while
    checks that work from a render (bleed, ink, margins) do not see them.
    """

    id = "layers"
    name = "Layers"
    needs_content = False

    def finish(self):
        try:
            props = self.doc.pdf.Root.get("/OCProperties")
            if props is None:
                return self.findings
            groups = list(props.get("/OCGs", []))
            if not groups:
                return self.findings
            config = props.get("/D", {})
            off = {g.objgen for g in config.get("/OFF", [])}
            on = {g.objgen for g in config.get("/ON", [])}
            base_off = str(config.get("/BaseState", "/ON")) == "/OFF"
            hidden, shown = [], []
            for g in groups:
                name = str(g.get("/Name", "(unnamed)"))
                is_hidden = g.objgen in off or (base_off and g.objgen not in on)
                (hidden if is_hidden else shown).append(name)
        except Exception as exc:
            self.add(UNCHECKED, f"Layers could not be read ({type(exc).__name__})")
            return self.findings
        if hidden:
            self.add(WARN, "Hidden layer(s): " + ", ".join(hidden) + ". Whether they print depends on the RIP")
        if shown:
            self.add(NOTE, "Layers: " + ", ".join(shown))
        return self.findings
