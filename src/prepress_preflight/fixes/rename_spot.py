"""Rename spot colours, or merge one into another.

`--spot "Pantone 185C=PANTONE 185 C"` (repeatable). Only colour definitions
change: Separation and DeviceN colour spaces, wherever they are in the file.
The artwork refers to colours by a resource label, not by ink name, so no
content stream is touched.

When the new name is already an ink in the file, it is a merge: the renamed
spot also takes that ink's definition (its alternate colour), so the file ends
with one ink defined one way. A merge can change how the renamed spot looks on
a proof; a plain rename cannot, and is verified to render identically.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from ..checks.spots import separations
from ..content import PROCESS_NAMES, walk_page
from ..document import Document
from .append import AppendError, append_update
from .base import Proposal

NAME = "rename-spot"
CONTAINERS = (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)
RESERVED = PROCESS_NAMES | {"All", "None"}


def parse_mapping(items) -> dict:
    mapping = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f'"{item}": write it as OLD=NEW')
        old, new = (part.strip() for part in item.split("=", 1))
        if not old or not new or old == new:
            raise ValueError(f'"{item}": give two different names')
        if new in RESERVED or old in RESERVED:
            raise ValueError(f'"{item}": {", ".join(sorted(RESERVED))} are not spot colours')
        mapping[old] = new
    if not mapping:
        raise ValueError("rename-spot needs at least one --spot OLD=NEW")
    for new in mapping.values():
        if new in mapping:
            raise ValueError(f'"{new}" is both renamed and a new name; do it in two steps')
    return mapping


def _usage(doc):
    used = defaultdict(set)
    for page in doc.pages:
        for e in walk_page(page.obj).events:
            for name in e.cs.names:
                used[name].add(page.number)
    return used


def propose_rename_spot(path: str, options: Options | None = None, spots=None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    mapping = parse_mapping(spots)
    proposal = Proposal(NAME, path)
    doc = Document(path)
    try:
        defined = separations(doc.pdf)
        used = _usage(doc)
        plan = {}
        for old, new in mapping.items():
            if old not in defined and old not in used:
                proposal.skipped.append((1, f'there is no spot colour "{old}" in the file'))
                continue
            merge = new in defined or new in used
            if merge and len(defined.get(new, ())) > 1:
                proposal.skipped.append((1, f'"{new}" is defined more than one way, so "{old}" cannot be merged into it; '
                                            "rename it to a new name first"))
                continue
            plan[old] = {"new": new, "merge": merge}
            text = (f'Merges "{old}" into "{new}": one ink, with "{new}"\'s definition, so "{old}" may look slightly '
                    "different on a proof" if merge else f'Renames "{old}" to "{new}"')
            for page in sorted(used.get(old, ())) or [1]:
                proposal.reasons.append((page, text + "."))
        if plan:
            proposal.plan = {0: {"mapping": plan, "pages": sorted({p for o in plan for p in used.get(o, ())})}}
    finally:
        doc.close()
    return proposal


# ---- writing -----------------------------------------------------------

def _target_definitions(pdf, mapping):
    """The existing Separation array for each merge target, to copy its definition."""
    targets = {v["new"] for v in mapping.values() if v["merge"]}
    found = {}

    def visit(obj, depth=0):
        if depth > 12 or len(found) == len(targets):
            return
        if isinstance(obj, pikepdf.Array):
            if len(obj) >= 4 and str(obj[0]) == "/Separation" and str(obj[1])[1:] in targets:
                found.setdefault(str(obj[1])[1:], obj)
                return
            for item in obj:
                if isinstance(item, CONTAINERS) and not item.is_indirect:
                    visit(item, depth + 1)
        elif isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            items = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
            for key in items.keys():
                if isinstance(items[key], CONTAINERS) and not items[key].is_indirect:
                    visit(items[key], depth + 1)
    for obj in pdf.objects:
        visit(obj)
    return found


def _renamed(obj, mapping, targets, depth=0):
    """A copy of a direct object with the spot names changed, or None if nothing in it changes."""
    if depth > 12:
        return None
    if isinstance(obj, pikepdf.Array):
        head = str(obj[0]) if len(obj) else ""
        if head == "/Separation" and len(obj) >= 4 and str(obj[1])[1:] in mapping:
            rule = mapping[str(obj[1])[1:]]
            if rule["merge"] and rule["new"] in targets:
                target = targets[rule["new"]]
                return pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/" + rule["new"]), target[2], target[3]])
            return pikepdf.Array([obj[0], pikepdf.Name("/" + rule["new"]), obj[2], obj[3]])
        if head == "/DeviceN" and len(obj) >= 4 and any(str(n)[1:] in mapping for n in obj[1]):
            names = [mapping[str(n)[1:]]["new"] if str(n)[1:] in mapping else str(n)[1:] for n in obj[1]]
            if len(set(names)) != len(names):
                raise AppendError("a DeviceN colour would end up with the same ink twice")
            items = [obj[0], pikepdf.Array([pikepdf.Name("/" + n) for n in names])] + list(obj[2:])
            if len(obj) >= 5 and isinstance(obj[4], pikepdf.Dictionary) and "/Colorants" in obj[4]:
                attrs = pikepdf.Dictionary({k: obj[4][k] for k in obj[4].keys()})
                colorants = attrs.Colorants
                new_colorants = pikepdf.Dictionary()
                for k in colorants.keys():
                    space = colorants[k]
                    key = k[1:]
                    if key in mapping:
                        key = mapping[key]["new"]
                        space = _renamed(space, mapping, targets, depth + 1) or space
                    new_colorants["/" + key] = space
                attrs["/Colorants"] = new_colorants
                items[4] = attrs
            return pikepdf.Array(items)
        changed, items = False, []
        for item in obj:
            new = _renamed(item, mapping, targets, depth + 1) if isinstance(item, CONTAINERS) and not item.is_indirect else None
            changed |= new is not None
            items.append(new if new is not None else item)
        return pikepdf.Array(items) if changed else None
    if isinstance(obj, pikepdf.Dictionary):
        changed, copy = False, pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
        for k in obj.keys():
            item = obj[k]
            new = _renamed(item, mapping, targets, depth + 1) if isinstance(item, CONTAINERS) and not item.is_indirect else None
            if new is not None:
                copy[k] = new
                changed = True
        return copy if changed else None
    return None


def _check(src, plan):
    mapping = plan["mapping"]

    def check(new_path):
        before, after = Document(src), Document(new_path)
        try:
            was, now = _usage(before), _usage(after)
            for old, rule in mapping.items():
                if now.get(old):
                    raise AppendError(f'verification failed: "{old}" is still used')
                if not was.get(old, set()) <= now.get(rule["new"], set()):
                    raise AppendError(f'verification failed: "{rule["new"]}" is not used everywhere "{old}" was')
        finally:
            before.close()
            after.close()
        if any(r["merge"] for r in mapping.values()):
            return  # a merge may change how the renamed spot looks; nothing to compare
        a, b = pymupdf.open(src), pymupdf.open(new_path)
        try:
            for number in plan["pages"]:
                pa, pb = a.load_page(number - 1), b.load_page(number - 1)
                w, h = pa.rect.width / 72, pa.rect.height / 72
                dpi = int(max(18, min(72, (3_000_000 / max(w * h, 1e-6)) ** 0.5)))
                x = pa.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, alpha=False, annots=False)
                y = pb.get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, alpha=False, annots=False)
                if x.samples != y.samples:
                    raise AppendError(f"verification failed: page {number} looks different after a plain rename")
        finally:
            a.close()
            b.close()
    return check


def write_rename_spot(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan = proposal.plan[0]
    with pikepdf.open(proposal.path) as pdf:
        targets = _target_definitions(pdf, plan["mapping"])
        changes = {}
        for obj in pdf.objects:
            if not obj.is_indirect or not isinstance(obj, CONTAINERS):
                continue
            if isinstance(obj, pikepdf.Stream):
                head = pikepdf.Dictionary({k: obj.stream_dict[k] for k in obj.stream_dict.keys()})
                new_head = _renamed(head, plan["mapping"], targets)
                if new_head is not None:
                    stream = pikepdf.Stream(pdf, obj.read_raw_bytes())
                    for k in new_head.keys():
                        if k != "/Length":
                            stream[k] = new_head[k]
                    changes[obj.objgen] = stream
                continue
            new = _renamed(obj, plan["mapping"], targets)
            if new is not None:
                changes[obj.objgen] = new
        if not changes:
            raise AppendError("found nothing to rename")
        append_update(proposal.path, dst, changes, extra_check=_check(proposal.path, plan))
