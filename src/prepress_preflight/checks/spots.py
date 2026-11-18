from __future__ import annotations

import hashlib
import re
from collections import defaultdict

import pikepdf

from ..content import PROCESS_NAMES
from ..edges import is_non_printing
from ..model import FAIL, NOTE, UNCHECKED, WARN
from .base import Check

CONTAINERS = (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)
KINDS = {"path": "vector", "text": "text", "image": "image", "imagemask": "vector", "shading": "gradient"}


def ink_key(name: str) -> str:
    """Two spellings of one ink give the same key: case, spaces and a leading
    PANTONE or PMS do not count. 'PANTONE 185 C', 'Pantone 185C' and '185 C'
    all give '185c'; '185 U' stays different."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return re.sub(r"^(pantone|pms)", "", key) or key


def _fingerprint(space) -> str:
    """What a Separation or DeviceN colour space prints as on a proof: its
    alternate space and tint transform."""
    try:
        alt, fn = space[2], space[3]
        alt_text = str(alt[0]) if isinstance(alt, pikepdf.Array) else str(alt)
        if isinstance(fn, pikepdf.Stream):
            return alt_text + ":" + hashlib.sha256(fn.read_bytes()).hexdigest()[:12]
        if isinstance(fn, pikepdf.Dictionary) and int(fn.get("/FunctionType", 0)) == 2:
            c0 = [round(float(v), 3) for v in fn.get("/C0", [0])]
            c1 = [round(float(v), 3) for v in fn.get("/C1", [1])]
            return f"{alt_text}:{c0}->{c1}"
        return alt_text + ":" + fn.unparse(resolved=True).decode(errors="replace")[:200]
    except Exception:
        return "unreadable"


def separations(pdf):
    """Every Separation colour space in the file: {name: {fingerprint}}. Looks
    inside every object without following references, so spaces written
    directly into resource dictionaries are found too."""
    found: dict[str, set] = defaultdict(set)
    seen = set()

    def visit(obj, depth=0):
        if depth > 12:
            return
        if isinstance(obj, pikepdf.Array):
            if len(obj) >= 4 and str(obj[0]) == "/Separation":
                found[str(obj[1])[1:]].add(_fingerprint(obj))
                return
            for item in obj:
                if isinstance(item, CONTAINERS) and not item.is_indirect:
                    visit(item, depth + 1)
        elif isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            items = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
            for key in items.keys():
                item = items[key]
                if isinstance(item, CONTAINERS) and not item.is_indirect:
                    visit(item, depth + 1)

    for obj in pdf.objects:
        if obj.objgen in seen:
            continue
        seen.add(obj.objgen)
        visit(obj)
    return found


class SpotColourCheck(Check):
    """Spot colours: which print, where, and whether any are doubled up.

    Each printing spot ink prints on a plate of its own unless the RIP is set to
    convert it, so the report lists every one in use. Two spellings of the same
    ink ('PANTONE 185 C' and 'Pantone 185C') would make two plates; one name
    defined two ways gives the RIP a choice. Spots recognised as non-printing
    (dielines and the like) are listed separately. With Options.cmyk_only (the
    job is to print in process colours only) any printing spot is a fail.
    """

    id = "spots"
    name = "Spot colours"

    def __init__(self, doc, options):
        super().__init__(doc, options)
        self.used: dict[str, dict] = defaultdict(lambda: {"pages": set(), "kinds": defaultdict(int)})

    def page(self, page, content):
        for e in content.events:
            for name in e.cs.spot_names:
                if e.colour is not None and len(e.colour) and max(e.colour) <= 0.001 and e.cs.kind == "sep" \
                        and len(e.cs.names) == 1:
                    continue  # a 0% tint puts down no ink
                use = self.used[name]
                use["pages"].add(page.number)
                use["kinds"][KINDS.get(e.kind, e.kind)] += 1

    def finish(self):
        printing = {n: u for n, u in self.used.items() if not is_non_printing(n)}
        quiet = {n: u for n, u in self.used.items() if is_non_printing(n)}

        def describe(names):
            parts = []
            for n in sorted(names):
                u = names[n]
                kinds = ", ".join(f"{c} {k}" for k, c in sorted(u["kinds"].items(), key=lambda kv: -kv[1]))
                parts.append(f"{n} ({kinds})")
            return "; ".join(parts)

        if printing:
            pages = sorted({p for u in printing.values() for p in u["pages"]})
            text = describe(printing)
            if self.options.cmyk_only:
                self.add(FAIL, f"{len(printing)} spot colour(s) in a job for process colours only: {text}", pages)
            else:
                self.add(NOTE, f"{len(printing)} spot colour(s), each printing on its own plate unless converted: {text}",
                         pages)
        if quiet:
            self.add(NOTE, "Non-printing spot colour(s): " + describe(quiet), sorted({p for u in quiet.values() for p in u["pages"]}))

        groups = defaultdict(list)
        for n in printing:
            groups[ink_key(n)].append(n)
        for names in groups.values():
            if len(names) > 1:
                pages = sorted({p for n in names for p in printing[n]["pages"]})
                self.add(WARN, "One ink spelt more than one way, which would make separate plates: "
                               + ", ".join(f'"{n}"' for n in sorted(names)), pages)
        try:
            defined = separations(self.doc.pdf)
        except Exception as exc:
            self.add(UNCHECKED, f"Spot colour definitions could not be read ({type(exc).__name__})")
            return self.findings
        for n in sorted(printing):
            if len(defined.get(n, ())) > 1:
                self.add(WARN, f'"{n}" is defined {len(defined[n])} different ways; the RIP or proof may use either',
                         sorted(printing[n]["pages"]))
        return self.findings
