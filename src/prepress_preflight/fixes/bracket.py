"""Change chosen objects and nothing else, by bracketing each one.

Shared by fixes that change some objects' graphics state or colour (knock out
white, registration to black). The change is made right where the object is
painted:

  * Normally the object is wrapped in `q <change> … Q`. `Q` puts back whatever
    was in force, wherever the content is used, so this is right even for a
    form painted in several places with different settings.
  * In a text block `q`/`Q` are not allowed, and around a path that also sets
    a clip they would drop the clip. There the change is made before, and the
    old state is put back after, which needs the old state to be known: the
    same at every use of that spot.

A "spot" is one painting instruction in one content stream (the page's, or a
form's). Fixes say which objects are candidates (`select`, used to find every
use of a spot) and which are targets (`flag`); this module finds the spots,
refuses the ones that cannot be changed on their own, and writes the result.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pikepdf

from ..content import PageWalker
from .object_overprint import PATH_BUILD, _unique, form_use_counts
from .spot_to_cmyk import _flate

PATH_PAINT = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}


@dataclass
class Spot:
    key: tuple  # ("page", number) or ("form", objgen)
    index: int  # the painting instruction
    start: int  # where the change goes: before the path is built, for a path
    op: str
    stream: object
    resources: object
    found: list  # [(page number, Snapshot, flagged)] every candidate painted here
    clipped: bool = False
    instructions: list = field(default_factory=list)  # the stream's instructions, parsed once

    @property
    def flagged(self):
        return [(n, s) for n, s, f in self.found if f]

    @property
    def restore(self) -> bool:
        """True when the old state must be put back explicitly (no q/Q)."""
        return self.flagged[0][1].event.kind == "text" or self.clipped


@dataclass
class Plan:
    edits: dict = field(default_factory=lambda: defaultdict(list))  # stream key -> [edit]
    streams: dict = field(default_factory=dict)  # stream key -> (stream, resources, page number or None)
    targets: list = field(default_factory=list)  # (page, event number, fingerprint)
    fixed: dict = field(default_factory=lambda: defaultdict(Counter))  # page -> {label: count}
    left: dict = field(default_factory=lambda: defaultdict(Counter))  # page -> {reason: count}

    def leave(self, spot_or_pairs, reason):
        pairs = spot_or_pairs.flagged if isinstance(spot_or_pairs, Spot) else spot_or_pairs
        for n, _ in pairs:
            self.left[n][reason] += 1


HIDDEN = "clipped away, so it does not print"
HIDDEN_USE = "clipped away, in a form"  # counted apart from what shows: see note_hidden
HIDDEN_PAGE = "clipped away"


def count_text(counter) -> str:
    """'3 text object(s), 1 line(s)', most first; clipped-away objects are left to note_hidden."""
    items = [(label, n) for label, n in counter.most_common() if n > 0 and label not in (HIDDEN_USE, HIDDEN_PAGE)]
    return ", ".join(f"{n} {label}" for label, n in items)


def note_hidden(proposal, plan) -> None:
    """Say, after each page's reason, how many of the changed objects are clipped away and do not print."""
    for i, (page, text) in enumerate(proposal.reasons):
        in_form, on_page = plan.fixed[page][HIDDEN_USE], plan.fixed[page][HIDDEN_PAGE]
        if in_form:
            text += (f" Also {in_form} use(s) clipped away on this page, changed with the form they are in: "
                     "nothing printed there changes.")
        if on_page:
            text += f" Also {on_page} object(s) clipped away, which do not print, changed too."
        proposal.reasons[i] = (page, text)


def find_spots(pdf, select, flag, plan: Plan, every_use: bool = False,
               elsewhere: str = "in a form that is also painted where it does not apply",
               skip_hidden: bool = False) -> list[Spot]:
    """The spots holding targets, with those that cannot be changed on their own
    recorded in `plan.left`.

    select(event) -> bool: a candidate (all uses of a spot must be candidates).
    flag(page number, snapshot) -> bool | str: a target, not one, or why it is left.
    every_use: refuse a spot in a form unless every use of it is a target.
    elsewhere: the reason given for a form spot refused because of its other uses.
    skip_hidden: leave targets wholly clipped away (they do not print), as the
        checks do. Inside a form they stay targets: changing an unseen use is
        harmless, and refusing it would block the uses that show.
    """
    positions = defaultdict(list)
    for number, page in enumerate(pdf.pages, 1):
        walker = PageWalker(page, detail=select)
        walker.run()
        for snap in walker.snapshots:
            verdict = flag(number, snap)
            if skip_hidden and verdict is True and snap.event.hidden and not snap.chain:
                plan.left[number][HIDDEN] += 1
                continue
            if isinstance(verdict, str):
                plan.left[number][verdict] += 1
                continue
            if snap.event.loc is None:
                if verdict:
                    plan.left[number]["inside a pattern fill"] += 1
                continue
            key = ("form", snap.chain[-1].xobj.objgen) if snap.chain else ("page", number)
            positions[(key, snap.index)].append((number, snap, bool(verdict)))

    spots, uses_cache, parsed = [], None, {}
    for (key, index), found in positions.items():
        if not any(f for _, _, f in found):
            continue
        stream = found[0][1].stream
        if key not in parsed:
            parsed[key] = pikepdf.parse_content_stream(stream)
        instructions = parsed[key]
        op = str(instructions[index].operator)
        start = index
        if op in PATH_PAINT:
            while start > 0 and str(instructions[start - 1].operator) in PATH_BUILD:
                start -= 1
        spot = Spot(key, index, start, op, stream, found[0][1].resources, found,
                    any(str(i.operator) in ("W", "W*") for i in instructions[start:index]), instructions)
        if key[0] == "form":
            if uses_cache is None:
                uses_cache = form_use_counts(pdf)  # one walk for every form
            uses = uses_cache[key[1]]
            per_paint = Counter(s.event.paint for _, s, _ in found)
            flagged_per_paint = Counter(s.event.paint for _, s in spot.flagged)
            if max(per_paint.values()) < uses:
                plan.leave(spot, elsewhere)
                continue
            if every_use and max(flagged_per_paint.values()) < uses:
                plan.leave(spot, elsewhere)
                continue
            if "/Resources" not in spot.stream and uses > 1:
                plan.leave(spot, "in a form without resources of its own that is painted more than once")
                continue
        spots.append(spot)
    return spots


def known(spot: Spot, value):
    """The one value `value(snapshot)` takes at every use of the spot, or None if they differ."""
    values = {value(s) for _, s, _ in spot.found}
    return next(iter(values)) if len(values) == 1 else None


def add_edit(plan: Plan, spot: Spot, before, after, label) -> None:
    """Record an edit. before/after: functions (add_gs) -> [instructions]; `after`
    is used only when the state must be put back explicitly."""
    plan.edits[spot.key].append({"start": spot.start, "paint": spot.index, "restore": spot.restore,
                                 "before": before, "after": after})
    plan.streams[spot.key] = (spot.stream, spot.resources, spot.key[1] if spot.key[0] == "page" else None)
    from ..objects import fingerprint
    for n, s in spot.flagged:
        plan.targets.append((n, s.number, fingerprint(s.event)))
        plan.fixed[n][(HIDDEN_USE if spot.key[0] == "form" else HIDDEN_PAGE) if s.event.hidden else label(s.event)] += 1


def recolour(plan: Plan, spot: Spot, new: dict, label) -> bool:
    """Record an edit giving the spot's flagged sides new CMYK colours: {"fill"|"stroke": (c, m, y, k)}.
    Where the old colour must be put back explicitly, it is written as the file had it
    (same colour space resource, same values), which needs it to be the same at every use.
    False, with nothing recorded, when it is not."""
    from .spot_to_cmyk import _num
    before = [ins("k" if side == "fill" else "K", *[_num(v) for v in cmyk]) for side, cmyk in new.items()]
    after = []
    if spot.restore:
        for side in new:
            def written(s, side=side):
                d = s.state.fdef if side == "fill" else s.state.sdef
                cs = s.state.fcs if side == "fill" else s.state.scs
                return (str(d[0]) if d else None, cs.kind, tuple(s.state.fcol if side == "fill" else s.state.scol))
            old = known(spot, written)
            if old is None:
                return False
            name, kind, values = old
            if name is None:
                op = {"gray": "g", "rgb": "rg", "cmyk": "k"}.get(kind)
                if op is None:
                    return False
                after.append(ins(op if side == "fill" else op.upper(), *[_num(v) for v in values]))
            else:
                after += [ins("cs" if side == "fill" else "CS", pikepdf.Name(name)),
                          ins("scn" if side == "fill" else "SCN", *[_num(v) for v in values])]
    add_edit(plan, spot, lambda add_gs, b=before: list(b), lambda add_gs, a=after: list(a), label)
    return True


def ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


def write_plan(pdf, plan: Plan):
    """(changes, additions) for append_update."""
    changes, additions = {}, {}
    for key, edits in plan.edits.items():
        stream, resources, page_number = plan.streams[key]
        new_res = pikepdf.Dictionary({k: resources[k] for k in resources.keys()}) if resources is not None else pikepdf.Dictionary()
        old_gs = new_res.get("/ExtGState")
        gs = pikepdf.Dictionary({k: old_gs[k] for k in old_gs.keys()}) if old_gs is not None else pikepdf.Dictionary()
        made = {}

        def add_gs(values, gs=gs, made=made):
            k = tuple(sorted(values.items()))
            if k not in made:
                name = _unique(gs.keys(), "PFX")
                gs[name] = pikepdf.Dictionary({"/Type": pikepdf.Name.ExtGState, **values})
                made[k] = name
            return made[k]

        out = list(pikepdf.parse_content_stream(stream))
        for edit in sorted(edits, key=lambda e: e["paint"], reverse=True):
            before = edit["before"](add_gs)
            after = edit["after"](add_gs) if edit["restore"] else [ins("Q")]
            if not edit["restore"]:
                before = [ins("q")] + before
            out[edit["paint"] + 1:edit["paint"] + 1] = after
            out[edit["start"]:edit["start"]] = before
        data = pikepdf.unparse_content_stream(out)
        if len(gs):
            new_res["/ExtGState"] = gs
        if page_number is not None:
            page = pdf.pages[page_number - 1].obj
            content = pdf.make_indirect(_flate(pdf, data))
            additions[content.objgen] = content
            copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
            copy["/Contents"] = content
            copy["/Resources"] = new_res
            changes[page.objgen] = copy
        else:
            new = _flate(pdf, data)
            for k in stream.stream_dict.keys():
                if k not in ("/Length", "/Filter", "/DecodeParms"):
                    new[k] = stream.stream_dict[k]
            new["/Resources"] = new_res
            changes[stream.objgen] = new
    return changes, additions


def checked_pages(path, options) -> dict:
    """Each page's geometry exactly as the checks see it: {number: PageInfo},
    with the trim taken from the crop marks where they disagree with the trim box."""
    from ..document import Document
    from ..content import walk_page
    from ..marks import infer_trim

    doc = Document(path)
    try:
        pages = {}
        for page in doc.pages:
            infer_trim(page, walk_page(page.obj), options.size_tolerance_mm)
            page.obj = None  # the pikepdf objects close with the document; keep only the geometry
            pages[page.number] = page
        return pages
    finally:
        doc.close()
