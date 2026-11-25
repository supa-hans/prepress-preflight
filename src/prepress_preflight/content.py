"""Walk a page's content and report every painted object.

This is the read-only core of the engine. It interprets the page content
stream (and any nested form XObjects), tracking the graphics state, and emits
one Event per painted object: its colour space, colour, overprint setting and
approximate position. Checks consume these events; nothing here writes to the
file.

Known limits, stated so no check claims more than it knows:
  * Clipping is applied by bounding box: an object's position is cut to the
    clipping region's box, and an object wholly outside it is marked hidden.
    The clip's shape is not followed (an object inside the box but outside a
    curved clip still counts), nor is clipping by text (render modes 4-7).
  * Text runs are measured from the fonts' glyph widths (simple fonts, and
    Identity-H two-byte fonts); their height is a fixed share of the size.
    Fonts without widths fall back to a one-em box where the run starts.
  * Optional content (layers) visibility is ignored; hidden layers are walked.
  * Blend-mode objects inside pattern cells or knockout groups are only
    counted (`nested_blends`), not recorded individually.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import pikepdf

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
PROCESS_NAMES = {"Cyan", "Magenta", "Yellow", "Black"}
MAX_DEPTH = 12


def intersect(a, b):
    """The overlap of two boxes (None means unbounded); an empty overlap is a zero-size box."""
    if a is None:
        return b
    if b is None:
        return a
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, max(x0, x1), max(y0, y1))


def _disjoint(a, b, grow=0.0) -> bool:
    return a[0] - grow > b[2] or a[2] + grow < b[0] or a[1] - grow > b[3] or a[3] + grow < b[1]


def _clip(event, clip, reach=None):
    """Apply the clipping region to an event's position: cut its box to the clip, or mark it hidden."""
    event.clip = clip
    if clip is None or event.bbox is None:
        return
    grow = (event.width or 0.0) / 2 + 0.01  # a stroke reaches half its width past the path
    if _disjoint(reach or event.bbox, clip, grow):
        event.hidden = True
        return
    if _disjoint(event.bbox, clip, grow):
        return  # text whose first em is outside but whose run may reach in: position left as drawn
    event.bbox = intersect(event.bbox, (clip[0] - grow, clip[1] - grow, clip[2] + grow, clip[3] + grow))
    if event.parts:
        parts = [intersect(p, clip) for p in event.parts if not _disjoint(p, clip, grow)]
        if parts:  # the box covers only the shapes left showing
            shown = (min(p[0] for p in parts), min(p[1] for p in parts), max(p[2] for p in parts), max(p[3] for p in parts))
            event.bbox = intersect(event.bbox, (shown[0] - grow, shown[1] - grow, shown[2] + grow, shown[3] + grow))
        event.parts = parts if len(parts) > 1 else None


class FontMetrics:
    """Glyph advance widths: code bytes per glyph, and width per code in text space per unit of size."""

    def __init__(self, code_bytes, widths, default, scale):
        self.code_bytes = code_bytes
        self.widths = widths  # code -> width in glyph space
        self.default = default
        self.scale = scale  # glyph space to text space (1/1000, or a Type 3 font's matrix)

    def width(self, code) -> float:
        return self.widths.get(code, self.default) * self.scale


def _font_metrics(font):
    """The font's advance widths, or None when they cannot be read reliably (then positions fall back)."""
    subtype = str(font.get("/Subtype"))
    if subtype == "/Type0":
        if str(font.get("/Encoding")) != "/Identity-H":
            return None  # other CMaps (and vertical writing) are not decoded
        cid = font.DescendantFonts[0]
        widths, w = {}, cid.get("/W", [])
        i = 0
        while i < len(w):
            first = int(w[i])
            if isinstance(w[i + 1], pikepdf.Array):
                for j, v in enumerate(w[i + 1]):
                    widths[first + j] = float(v)
                i += 2
            else:
                last, v = int(w[i + 1]), float(w[i + 2])
                if last - first > 70000:
                    return None
                for c in range(first, last + 1):
                    widths[c] = v
                i += 3
        return FontMetrics(2, widths, float(cid.get("/DW", 1000)), 0.001)
    if "/Widths" not in font or "/FirstChar" not in font:
        return None  # the standard 14 fonts may leave widths out: not guessed
    first = int(font.FirstChar)
    widths = {first + i: float(v) for i, v in enumerate(font.Widths)}
    default = float(font.get("/FontDescriptor", {}).get("/MissingWidth", 0))
    scale = float(font.FontMatrix[0]) if subtype == "/Type3" else 0.001
    return FontMetrics(1, widths, default, scale)


def _advance(st, operand, metrics):
    """How far a text-showing operand moves the text position, in unscaled text space; None when unknown."""
    if metrics is None:
        return None
    total = 0.0
    parts = list(operand) if isinstance(operand, pikepdf.Array) else [operand]
    for part in parts:
        if isinstance(part, pikepdf.String):
            data = bytes(part)
            n = metrics.code_bytes
            for i in range(0, len(data) - n + 1, n):
                code = int.from_bytes(data[i:i + n], "big")
                space = st.tw if n == 1 and code == 32 else 0.0
                total += (metrics.width(code) * st.fsize + st.tc + space) * st.th
        else:
            try:
                total -= float(part) / 1000 * st.fsize * st.th
            except (TypeError, ValueError):
                return None
    return total


def _text_bytes(operand) -> int:
    if isinstance(operand, pikepdf.Array):
        return sum(len(bytes(x)) for x in operand if isinstance(x, pikepdf.String))
    try:
        return len(bytes(operand))
    except Exception:
        return 1


def mat_mul(m, n):
    """Matrix for 'apply m, then n'."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def mat_apply(m, x, y):
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


@dataclass(frozen=True)
class ColourSpace:
    kind: str  # gray | rgb | cmyk | lab | sep | pattern | unknown
    names: tuple = ()  # colourant names for sep (Separation / DeviceN)
    indexed: bool = False
    # The embedded ICC profile (its object number, generation) when the space is
    # ICC-based. Not part of equality: tagged CMYK is still CMYK to every check.
    profile: tuple | None = field(default=None, compare=False)

    @property
    def is_registration(self) -> bool:
        return self.kind == "sep" and "All" in self.names

    @property
    def spot_names(self) -> tuple:
        return tuple(n for n in self.names if n not in PROCESS_NAMES and n not in ("All", "None"))


GRAY = ColourSpace("gray")
RGB = ColourSpace("rgb")
CMYK = ColourSpace("cmyk")
PATTERN = ColourSpace("pattern")
UNKNOWN = ColourSpace("unknown")
_DEVICE = {
    "/DeviceGray": GRAY, "/G": GRAY, "/CalGray": GRAY,
    "/DeviceRGB": RGB, "/RGB": RGB, "/CalRGB": RGB,
    "/DeviceCMYK": CMYK, "/CMYK": CMYK,
    "/Pattern": PATTERN, "/Lab": ColourSpace("lab"),
}


class Event:
    """One painted object."""

    __slots__ = ("kind", "paint", "cs", "colour", "bbox", "overprint", "opm", "px", "ppi", "parts", "loc", "width",
                 "font", "blend", "groups", "ref", "size", "opacity", "smask", "alpha", "clip", "hidden")

    def __init__(self, kind, paint, cs, colour, bbox, overprint, opm, px=None, ppi=None, parts=None, loc=None,
                 width=None, font=None, blend="Normal", groups=(), ref=None):
        self.kind = kind  # path | text | image | imagemask | shading
        self.paint = paint  # fill | stroke
        self.cs = cs
        self.colour = colour  # tuple of floats, or None when not a plain colour
        self.bbox = bbox  # (x0, y0, x1, y1) in page points, or None when unknown
        self.overprint = overprint
        self.opm = opm
        self.px = px  # (width, height) in pixels, images only
        self.ppi = ppi  # effective resolution at placed size, images only
        # A path can hold several separate shapes (all eight crop marks are often
        # one path). `parts` lists each shape's own bounding box when there are several.
        self.parts = parts
        # Where the painting instruction is: (names of the forms leading to its
        # stream, position in that stream, text render mode). None when it is
        # inside a pattern cell.
        self.loc = loc
        self.width = width  # stroke weight in points as placed on the page, strokes only
        self.font = font  # the font's name, text only
        self.blend = blend  # the blend mode in force
        # The forms it sits inside, outermost first, each as (name, blend mode
        # of the group): a group is blended as a whole, so an object inside a
        # Multiply group is painted Normal but still multiplies on the page.
        self.groups = groups
        self.ref = ref  # (object number, generation) of the image XObject, images only
        self.size = None  # text only: the font size as placed on the page, in points
        self.opacity = 1.0  # constant opacity in force for this side (ca for fill, CA for stroke)
        self.smask = False  # a soft mask in force (from the graphics state)
        self.alpha = False  # images only: the image carries its own soft mask (an alpha channel)
        self.clip = None  # bounding box of the clipping region in force (page points), None when unclipped
        # Wholly outside the clipping region: it does not print. Kept, so that
        # objects are numbered the same everywhere, but checks are not shown it.
        self.hidden = False

    def boxes(self):
        """Bounding boxes to test for position: one per shape when known."""
        if self.parts:
            return self.parts
        return [self.bbox] if self.bbox is not None else []


@dataclass(eq=False)
class FormFrame:
    """One form XObject on the way down to a nested object."""

    name: str  # its name in the resources of the stream that paints it
    index: int  # position of that `Do` in the painting stream's instructions
    xobj: object  # the form itself (pikepdf stream)
    isolated: bool  # an isolated transparency group: blends only within itself
    knockout: bool
    blend: str = "Normal"  # the blend mode the group is painted with ("Normal" for a plain form)


@dataclass(eq=False)
class BlendObject:
    """One object (or transparency group) painted with a blend mode.

    `start`..`end` are positions in the list of content instructions
    (pikepdf.parse_content_stream) of the stream that paints it: the page when
    `chain` is empty, otherwise the last form in `chain`. With these a check can
    rebuild the page with and without the object. A run of text in one text
    block counts as one object.
    """

    mode: str
    start: int
    end: int
    kind: str  # path | text | image | imagemask | shading | group
    bbox: tuple | None  # page points when known, else None
    chain: tuple = ()  # FormFrames from the page down to the painting stream
    events: int = 0
    all_spot: bool = True

    @property
    def spot_only(self) -> bool:
        """True when everything in it is painted in spot inks only."""
        return self.events > 0 and self.all_spot


class PageContent:
    def __init__(self):
        self.events: list[Event] = []
        self.blend_modes: set[str] = set()
        self.problems: Counter = Counter()
        # Objects painted with a blend mode other than Normal, on the page or
        # inside forms.
        self.blend_objects: list[BlendObject] = []
        # Blend-mode objects that cannot be isolated (inside pattern cells or
        # knockout groups), by mode. A check must report them as not checked.
        self.nested_blends: Counter = Counter()
        # Fonts used to show visible text, keyed by object identity.
        self.fonts: dict = {}

    def visible(self) -> "PageContent":
        """The same page without objects wholly clipped away (see Event.hidden)."""
        out = PageContent.__new__(PageContent)
        out.__dict__.update(self.__dict__)
        out.events = [e for e in self.events if not e.hidden]
        return out


class _State:
    __slots__ = ("ctm", "fcs", "fcol", "fpat", "scs", "scol", "spat", "op_fill", "op_stroke", "opm", "bm", "lw", "font",
                 "tm", "tlm", "fsize", "leading", "tr", "fdef", "sdef", "gs_names", "smask", "ca", "CA",
                 "clip", "tc", "tw", "th", "rise")

    def __init__(self):
        self.ctm = IDENTITY
        self.fcs = GRAY
        self.fcol = (0.0,)
        self.fpat = None
        self.scs = GRAY
        self.scol = (0.0,)
        self.spat = None
        self.op_fill = False
        self.op_stroke = False
        self.opm = 0
        self.bm = "Normal"
        self.lw = 1.0
        self.font = None
        self.tm = IDENTITY
        self.tlm = IDENTITY
        self.fsize = 0.0
        self.leading = 0.0
        self.tr = 0
        # For object details only: the colour space as written (operand, resources),
        # the ExtGStates applied, soft mask and constant opacity.
        self.fdef = self.sdef = None
        self.gs_names = ()
        self.smask = False
        self.ca = self.CA = 1.0
        self.tc = self.tw = 0.0  # character and word spacing (unscaled text space units)
        self.th = 1.0  # horizontal scaling (Tz / 100)
        self.rise = 0.0
        # The clipping region's bounding box in page space, or None for no clip.
        # A box, not the clip's shape: an object outside it is certainly hidden.
        self.clip = None

    def copy(self):
        s = _State.__new__(_State)
        for k in _State.__slots__:
            setattr(s, k, getattr(self, k))
        return s


def _nums(operands):
    out = []
    for o in operands:
        try:
            out.append(float(o))
        except (TypeError, ValueError):
            pass
    return tuple(out)


@dataclass
class Snapshot:
    """Everything known when one object was painted (see `PageWalker(detail=...)`)."""

    event: Event
    state: _State
    resources: object
    stream: object  # the page or form whose content paints it
    index: int  # the painting instruction's position in that stream
    chain: tuple
    number: int = -1  # the event's number in the page's walk order


class PageWalker:
    def __init__(self, page: pikepdf.Page, detail=None):
        self.page = page
        # Which events to keep a Snapshot of: an event number, or a test
        # (event -> bool). `snapshot` is the first kept, `snapshots` all of them.
        self.detail = detail
        self.snapshot: Snapshot | None = None
        self.snapshots: list[Snapshot] = []
        self._stream = page
        self.out = PageContent()
        self._cs_cache: dict = {}
        self._fonts: dict = {}  # font -> FontMetrics or None
        self._active: set = set()
        self._text_run: BlendObject | None = None
        self._chain: list[FormFrame] = []
        self._knockout = 0  # > 0 while inside a knockout group
        self._idx = 0  # position of the instruction being interpreted

    # ---- blend-mode objects --------------------------------------------
    def _blend_begin(self, st, depth, idx, kind, bbox, located):
        """Call before every paint. Records it when a blend mode is in force."""
        prev, self._text_run = self._text_run, None
        if st.bm == "Normal":
            return None
        if not located or self._knockout:
            self.out.nested_blends[st.bm] += 1
            return None
        if kind == "text" and prev is not None and prev.mode == st.bm:
            prev.end = idx
            obj = prev
        else:
            obj = BlendObject(st.bm, idx, idx, kind, bbox, tuple(self._chain))
            self.out.blend_objects.append(obj)
        if kind == "text":
            self._text_run = obj
        return obj, len(self.out.events)

    def _blend_end(self, mark):
        if mark is None:
            return
        obj, first = mark
        for e in self.out.events[first:]:
            obj.events += 1
            names = [n for n in e.cs.names if n != "None"]
            if e.cs.kind != "sep" or not names or any(n in PROCESS_NAMES or n == "All" for n in names):
                obj.all_spot = False

    # ---- colour spaces -------------------------------------------------
    def resolve_cs(self, obj, resources, depth=0) -> ColourSpace:
        if depth > 6 or obj is None:
            return UNKNOWN
        try:
            if isinstance(obj, pikepdf.Name):
                name = str(obj)
                if name in _DEVICE:
                    return _DEVICE[name]
                spaces = resources.get("/ColorSpace") if resources is not None else None
                if spaces is not None and name in spaces:
                    return self.resolve_cs(spaces[name], resources, depth + 1)
                return UNKNOWN
            if isinstance(obj, pikepdf.Array) and len(obj) > 0:
                head = str(obj[0])
                if head == "/ICCBased":
                    n = int(obj[1].get("/N", 0))
                    if n == 4:
                        return ColourSpace("cmyk", profile=obj[1].objgen if obj[1].is_indirect else (0, 0))
                    return {1: GRAY, 3: RGB}.get(n, UNKNOWN)
                if head == "/Indexed":
                    base = self.resolve_cs(obj[1], resources, depth + 1)
                    return ColourSpace(base.kind, base.names, True, base.profile)
                if head == "/Separation":
                    return ColourSpace("sep", (str(obj[1])[1:],))
                if head == "/DeviceN":
                    return ColourSpace("sep", tuple(str(n)[1:] for n in obj[1]))
                if head == "/Pattern":
                    return PATTERN
                if head in _DEVICE:
                    return _DEVICE[head]
        except Exception:
            pass
        return UNKNOWN

    # ---- events --------------------------------------------------------
    def _emit(self, kind, paint, st, bbox, resources, located, px=None, ppi=None, cs=None, colour=None, parts=None, ref=None,
              alpha=False, reach=None):
        if cs is None:
            cs, colour, pat = (st.fcs, st.fcol, st.fpat) if paint == "fill" else (st.scs, st.scol, st.spat)
            if cs.kind == "pattern":
                self._emit_pattern(pat, paint, st, bbox, resources, located)
                return
        overprint = st.op_fill if paint == "fill" else st.op_stroke
        width = None
        if paint == "stroke":
            # Line width is in user space: scale it by the current matrix.
            a, b, c, d = st.ctm[:4]
            width = st.lw * math.sqrt(abs(a * d - b * c))
        font = None
        if kind == "text" and st.font is not None:
            font = str(st.font.get("/BaseFont", "/(unnamed)"))[1:]
            if len(font) > 7 and font[6] == "+":
                font = font[7:]  # drop the subset tag
        event = Event(kind, paint, cs, colour, bbox if located else None, overprint, st.opm, px, ppi,
                      parts if located else None, self._loc(st, located), width, font, st.bm,
                      tuple((f.name, f.blend) for f in self._chain), ref)
        if kind == "text":
            a, b, c, d = mat_mul(st.tm, st.ctm)[:4]
            event.size = st.fsize * math.sqrt(abs(a * d - b * c))
        event.alpha = alpha
        _clip(event, st.clip, reach)
        self._append(event, st, resources)

    def _append(self, event, st, resources):
        event.opacity = st.ca if event.paint == "fill" else st.CA  # every event, gradients included
        event.smask = st.smask
        number = len(self.out.events)
        if self.detail is not None and (self.detail(event) if callable(self.detail) else number == self.detail):
            snap = Snapshot(event, st.copy(), resources, self._stream, self._idx, tuple(self._chain), number)
            self.snapshots.append(snap)
            self.snapshot = self.snapshot or snap
        self.out.events.append(event)

    def _loc(self, st, located):
        return (tuple(f.name for f in self._chain), self._idx, st.tr) if located else None

    def _emit_pattern(self, pat_name, paint, st, bbox, resources, located):
        try:
            pattern = resources["/Pattern"][str(pat_name)]
            ptype = int(pattern.get("/PatternType", 0))
            if ptype == 2:
                cs = self.resolve_cs(pattern["/Shading"].get("/ColorSpace"), resources)
                self._append(Event("shading", paint, cs, None, bbox if located else None,
                                   st.op_fill if paint == "fill" else st.op_stroke, st.opm,
                                   loc=self._loc(st, located), blend=st.bm,
                                   groups=tuple((f.name, f.blend) for f in self._chain)), st, resources)
                return
            if ptype == 1:
                # Tiling pattern: walk its cell; positions inside it are not page positions.
                sub = _State()
                sub.op_fill, sub.op_stroke, sub.opm = st.op_fill, st.op_stroke, st.opm
                idx = self._idx
                self._walk(pattern, pattern.get("/Resources", resources), sub, 1, located=False)
                self._idx = idx
                return
        except Exception:
            pass
        self.out.problems["pattern fill could not be read"] += 1

    # ---- main loop -----------------------------------------------------
    def run(self) -> PageContent:
        group = self.page.obj.get("/Group")
        if group is not None and bool(group.get("/K", False)):
            self._knockout += 1
        resources = self.page.obj.get("/Resources")
        if resources is None:
            try:
                resources = self.page.resources
            except Exception:
                resources = pikepdf.Dictionary()
        self._walk(self.page, resources, _State(), 0, located=True)
        return self.out

    def _walk(self, stream, resources, st, depth, located):
        try:
            instructions = pikepdf.parse_content_stream(stream)
        except Exception:
            self.out.problems["content stream could not be parsed"] += 1
            return
        outer, self._stream = self._stream, stream
        try:
            self._interpret(instructions, resources, st, depth, located)
        finally:
            self._stream = outer

    def _interpret(self, instructions, resources, st, depth, located):
        stack = []
        subs: list = []  # current path as a list of subpaths; points already in page space
        pending_clip = None  # W or W* seen: the path clips once it has been painted (or ended with n)

        def box_of(points):
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            return (min(xs), min(ys), max(xs), max(ys))

        def path_bbox():
            pts = [p for sub in subs for p in sub]
            return box_of(pts) if pts else None

        def path_parts():
            if len(subs) < 2 or len(subs) > 5000:
                return None
            return [box_of(sub) for sub in subs if sub]

        for idx, ins in enumerate(instructions):
            self._idx = idx
            op = str(ins.operator)
            operands = ins.operands
            try:
                if op == "q":
                    stack.append(st.copy())
                elif op == "Q":
                    if stack:
                        st = stack.pop()
                elif op == "cm":
                    m = _nums(operands)
                    if len(m) == 6:
                        st.ctm = mat_mul(m, st.ctm)
                # --- path construction
                elif op == "m":
                    v = _nums(operands)
                    subs.append([mat_apply(st.ctm, v[0], v[1])])
                elif op == "l":
                    v = _nums(operands)
                    if not subs:
                        subs.append([])
                    subs[-1].append(mat_apply(st.ctm, v[0], v[1]))
                elif op in ("c", "v", "y"):
                    v = _nums(operands)
                    if not subs:
                        subs.append([])
                    for i in range(0, len(v) - 1, 2):
                        subs[-1].append(mat_apply(st.ctm, v[i], v[i + 1]))
                elif op == "re":
                    x, y, w, h = _nums(operands)
                    subs.append([mat_apply(st.ctm, px_, py_) for px_, py_ in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))])
                # --- path painting
                elif op in ("f", "F", "f*", "S", "s", "B", "B*", "b", "b*"):
                    bb, parts = path_bbox(), path_parts()
                    mark = self._blend_begin(st, depth, idx, "path", bb, located)
                    if op not in ("S", "s"):
                        self._emit("path", "fill", st, bb, resources, located, parts=parts)
                    if op not in ("f", "F", "f*"):
                        self._emit("path", "stroke", st, bb, resources, located, parts=parts)
                    self._blend_end(mark)
                    subs = []
                    if pending_clip is not None:
                        st.clip, pending_clip = intersect(st.clip, pending_clip), None
                elif op == "n":
                    subs = []
                    if pending_clip is not None:
                        st.clip, pending_clip = intersect(st.clip, pending_clip), None
                elif op in ("W", "W*"):
                    pending_clip = path_bbox() or (0.0, 0.0, 0.0, 0.0)  # an empty path clips everything away
                # --- colour
                elif op == "g":
                    st.fcs, st.fcol, st.fpat, st.fdef = GRAY, _nums(operands), None, None
                elif op == "G":
                    st.scs, st.scol, st.spat, st.sdef = GRAY, _nums(operands), None, None
                elif op == "rg":
                    st.fcs, st.fcol, st.fpat, st.fdef = RGB, _nums(operands), None, None
                elif op == "RG":
                    st.scs, st.scol, st.spat, st.sdef = RGB, _nums(operands), None, None
                elif op == "k":
                    st.fcs, st.fcol, st.fpat, st.fdef = CMYK, _nums(operands), None, None
                elif op == "K":
                    st.scs, st.scol, st.spat, st.sdef = CMYK, _nums(operands), None, None
                elif op == "cs":
                    st.fdef = (operands[0], resources)
                    st.fcs, st.fcol, st.fpat = self.resolve_cs(operands[0], resources), None, None
                    if st.fcs.kind == "unknown":
                        self.out.problems["colour space could not be read"] += 1
                elif op == "CS":
                    st.sdef = (operands[0], resources)
                    st.scs, st.scol, st.spat = self.resolve_cs(operands[0], resources), None, None
                    if st.scs.kind == "unknown":
                        self.out.problems["colour space could not be read"] += 1
                elif op in ("sc", "scn"):
                    st.fcol = _nums(operands)
                    st.fpat = operands[-1] if operands and isinstance(operands[-1], pikepdf.Name) else None
                elif op in ("SC", "SCN"):
                    st.scol = _nums(operands)
                    st.spat = operands[-1] if operands and isinstance(operands[-1], pikepdf.Name) else None
                # --- graphics state
                elif op == "w":
                    st.lw = float(operands[0])
                elif op == "gs":
                    g = resources["/ExtGState"][str(operands[0])]
                    gs_name = str(operands[0])[1:]  # each once, most recently applied last
                    st.gs_names = tuple(n for n in st.gs_names if n != gs_name) + (gs_name,)
                    if "/SMask" in g:
                        st.smask = str(g["/SMask"]) != "/None"
                    if "/ca" in g:
                        st.ca = float(g["/ca"])
                    if "/CA" in g:
                        st.CA = float(g["/CA"])
                    if "/LW" in g:
                        st.lw = float(g["/LW"])
                    if "/OP" in g:
                        st.op_stroke = bool(g["/OP"])
                        if "/op" not in g:
                            st.op_fill = bool(g["/OP"])
                    if "/op" in g:
                        st.op_fill = bool(g["/op"])
                    if "/OPM" in g:
                        st.opm = int(g["/OPM"])
                    if "/BM" in g:
                        bm = g["/BM"]
                        if isinstance(bm, pikepdf.Array):
                            bm = bm[0] if len(bm) else pikepdf.Name("/Normal")
                        bm = str(bm)[1:]
                        st.bm = "Normal" if bm in ("Normal", "Compatible") else bm
                        if st.bm != "Normal":
                            self.out.blend_modes.add(bm)
                # --- text
                elif op == "BT":
                    st.tm = st.tlm = IDENTITY
                    self._text_run = None
                elif op == "ET":
                    self._text_run = None
                elif op == "Tf":
                    st.fsize = float(operands[1])
                    try:
                        st.font = resources["/Font"][str(operands[0])]
                    except Exception:
                        st.font = None
                        self.out.problems["font could not be found"] += 1
                elif op == "Tc":
                    st.tc = float(operands[0])
                elif op == "Tw":
                    st.tw = float(operands[0])
                elif op == "Tz":
                    st.th = float(operands[0]) / 100
                elif op == "Ts":
                    st.rise = float(operands[0])
                elif op == "TL":
                    st.leading = float(operands[0])
                elif op == "Tr":
                    st.tr = int(operands[0])
                elif op in ("Td", "TD"):
                    tx, ty = _nums(operands)
                    if op == "TD":
                        st.leading = -ty
                    st.tlm = mat_mul((1, 0, 0, 1, tx, ty), st.tlm)
                    st.tm = st.tlm
                elif op == "Tm":
                    m = _nums(operands)
                    if len(m) == 6:
                        st.tm = st.tlm = m
                elif op == "T*":
                    st.tlm = mat_mul((1, 0, 0, 1, 0, -st.leading), st.tlm)
                    st.tm = st.tlm
                elif op in ("Tj", "TJ", "'", '"'):
                    if op == '"':
                        st.tw, st.tc = float(operands[0]), float(operands[1])
                    if op in ("'", '"'):
                        st.tlm = mat_mul((1, 0, 0, 1, 0, -st.leading), st.tlm)
                        st.tm = st.tlm
                    mark = None if st.tr in (3, 7) else self._blend_begin(st, depth, idx, "text", None, located)
                    self._show_text(operands[-1], st, resources, located)
                    self._blend_end(mark)
                # --- shading, images, forms
                elif op == "sh":
                    sh = resources["/Shading"][str(operands[0])]
                    cs = self.resolve_cs(sh.get("/ColorSpace"), resources)
                    mark = self._blend_begin(st, depth, idx, "shading", None, located)
                    self._append(Event("shading", "fill", cs, None, None, st.op_fill, st.opm, loc=self._loc(st, located),
                                       blend=st.bm, groups=tuple((f.name, f.blend) for f in self._chain)), st, resources)
                    self.out.events[-1].clip = st.clip
                    self._blend_end(mark)
                elif op == "INLINE IMAGE":
                    mark = self._blend_begin(st, depth, idx, "image", self._image_geometry(st, 1, 1)[0], located)
                    self._inline_image(operands[0], st, resources, located)
                    self._blend_end(mark)
                elif op == "Do":
                    self._do(operands[0], st, resources, depth, located, idx)
            except Exception:
                self.out.problems[f"operator '{op}' could not be interpreted"] += 1

    def _show_text(self, operand, st, resources, located):
        fs = st.fsize or 1.0
        width = _advance(st, operand, self._metrics(st.font))  # in unscaled text space, or None when unknown
        if st.tr in (3, 7):
            if width is not None:
                st.tm = mat_mul((1, 0, 0, 1, width, 0), st.tm)
            return
        if st.font is not None:
            key = st.font.objgen if st.font.is_indirect else id(st.font)
            self.out.fonts.setdefault(key, st.font)
        m = mat_mul(st.tm, st.ctm)
        lo, hi = st.rise - 0.2 * fs, st.rise + 0.8 * fs  # roughly descender to cap height

        def box(length):
            corners = [mat_apply(m, x, y) for x, y in ((0, lo), (length, lo), (length, hi), (0, hi))]
            return (min(c[0] for c in corners), min(c[1] for c in corners),
                    max(c[0] for c in corners), max(c[1] for c in corners))

        if width is not None:
            # Measured from the font's glyph widths: the run's whole extent along
            # its baseline (the height is a fixed share of the size: no glyph outlines are read).
            bbox = reach = box(width if width > 0 else fs * 0.01)
        else:
            # No widths to read: a one-em box where the run starts, and for
            # deciding whether a clip hides it, the most it could reach (one em per byte).
            bbox = box(fs)
            reach = box(fs * max(1, _text_bytes(operand)))
        if st.tr in (0, 2, 4, 6):
            self._emit("text", "fill", st, bbox, resources, located, reach=reach)
        if st.tr in (1, 2, 5, 6):
            self._emit("text", "stroke", st, bbox, resources, located, reach=reach)
        if width is not None:
            st.tm = mat_mul((1, 0, 0, 1, width, 0), st.tm)  # the next run starts where this one ended

    def _metrics(self, font):
        if font is None:
            return None
        key = font.objgen if font.is_indirect and font.objgen != (0, 0) else id(font)
        if key not in self._fonts:
            try:
                self._fonts[key] = _font_metrics(font)
            except Exception:
                self._fonts[key] = None
        return self._fonts[key]

    def _image_geometry(self, st, w, h):
        corners = [mat_apply(st.ctm, x, y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
        bbox = (min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners))
        side_u = math.hypot(st.ctm[0], st.ctm[1])
        side_v = math.hypot(st.ctm[2], st.ctm[3])
        ppi = None
        if side_u > 1e-6 and side_v > 1e-6 and w and h:
            ppi = min(w / (side_u / 72.0), h / (side_v / 72.0))
        return bbox, ppi

    def _inline_image(self, img, st, resources, located):
        try:
            w, h = int(img.width), int(img.height)
            is_mask = bool(img.image_mask)
        except Exception:
            self.out.problems["inline image could not be read"] += 1
            return
        bbox, ppi = self._image_geometry(st, w, h)
        if is_mask:
            self._emit("imagemask", "fill", st, bbox, resources, located, px=(w, h), ppi=ppi)
            return
        try:
            cs = self.resolve_cs(pikepdf.Name(str(img.colorspace)), resources)
        except Exception:
            cs = UNKNOWN
        self._emit("image", "fill", st, bbox, resources, located, px=(w, h), ppi=ppi, cs=cs)

    def _do(self, name, st, resources, depth, located, idx):
        xobj = resources["/XObject"][str(name)]
        subtype = str(xobj.get("/Subtype"))
        if subtype == "/Image":
            w, h = int(xobj.get("/Width", 0)), int(xobj.get("/Height", 0))
            bbox, ppi = self._image_geometry(st, w, h)
            mark = self._blend_begin(st, depth, idx, "image", bbox, located)
            if bool(xobj.get("/ImageMask", False)):
                self._emit("imagemask", "fill", st, bbox, resources, located, px=(w, h), ppi=ppi)
            else:
                cs = self.resolve_cs(xobj.get("/ColorSpace"), resources)
                if cs.kind == "unknown":
                    self.out.problems["image colour space could not be read"] += 1
                self._emit("image", "fill", st, bbox, resources, located, px=(w, h), ppi=ppi, cs=cs,
                           ref=xobj.objgen if xobj.is_indirect else None,
                           alpha="/SMask" in xobj or int(xobj.get("/SMaskInData", 0)) > 0)
            self._blend_end(mark)
        elif subtype == "/Form":
            key = xobj.objgen
            if depth >= MAX_DEPTH or key in self._active:
                self.out.problems["nested form too deep to follow"] += 1
                return
            sub = st.copy()
            # A transparency group is blended as one object, and starts with
            # Normal inside. A plain form passes the blend mode on to each
            # object inside it, and each of those is recorded on its own.
            mark = None
            group = xobj.get("/Group")
            if group is not None:
                mark = self._blend_begin(st, depth, idx, "group", None, located)
                sub.bm = "Normal"
            frame = FormFrame(str(name)[1:], idx, xobj,
                              group is not None and bool(group.get("/I", False)),
                              group is not None and bool(group.get("/K", False)),
                              st.bm if group is not None else "Normal")
            m = xobj.get("/Matrix")
            if m is not None:
                mv = _nums(m)
                if len(mv) == 6:
                    sub.ctm = mat_mul(mv, st.ctm)
            fb = _nums(xobj.get("/BBox", []))
            if len(fb) == 4:  # a form is clipped to its bounding box
                corners = [mat_apply(sub.ctm, x, y) for x, y in ((fb[0], fb[1]), (fb[2], fb[1]), (fb[2], fb[3]), (fb[0], fb[3]))]
                sub.clip = intersect(sub.clip, (min(c[0] for c in corners), min(c[1] for c in corners),
                                                max(c[0] for c in corners), max(c[1] for c in corners)))
            self._active.add(key)
            self._chain.append(frame)
            self._knockout += frame.knockout
            try:
                self._walk(xobj, xobj.get("/Resources", resources), sub, depth + 1, located)
            finally:
                self._knockout -= frame.knockout
                self._chain.pop()
                self._active.discard(key)
                self._blend_end(mark)


def walk_page(page: pikepdf.Page) -> PageContent:
    return PageWalker(page).run()
