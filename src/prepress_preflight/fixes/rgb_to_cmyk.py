"""Convert RGB colour to CMYK through a colour profile.

Owner's defaults (2026-11-19): the target is Coated FOGRA39 (Adobe's
CoatedFOGRA39.icc), overridable with --profile; pure RGB black (0, 0, 0) text
and vector objects become K100 only, not a four-colour black that never
registers cleanly on small type. Images and gradients follow the profile.

Technical choices (mine, recorded in STATUS.md):
  * Relative colorimetric with black point compensation, through LittleCMS
    (Pillow's ImageCms), for everything.
  * Source: DeviceRGB is taken as sRGB; RGB with an embedded ICC profile uses
    that profile; CalRGB is taken as sRGB (an approximation, said so).
  * Vector and text colours: each rg/RG and each colour in an RGB colour space
    is replaced in the content by its CMYK value (k/K, or DeviceCMYK sc).
  * Images: decoded, converted pixel by pixel, written back as 8-bit CMYK in
    the compression they came in: JPEG photos as CMYK JPEG at the source's own
    quality (estimated from its quantisation tables, kept within 75-95;
    lossless Flate made the 50 MB yearbook 245 MB), everything else Flate.
    Pillow writes CMYK JPEG in Adobe's inverted form, so those images carry
    /Decode [1 0 1 0 1 0 1 0], the usual convention (checked to read back
    right in MuPDF and in macOS's own renderer). Indexed images keep their
    pixels and get a converted palette.
  * Gradients (axial and radial): resampled at 256 steps into CMYK.
  * Transparency groups blended in RGB are switched to blend in CMYK.
  * Soft masks and annotation appearances are not touched: they are not
    printed colour.

Left as they are, and listed: inline images, images that are not 8-bit or
carry a Decode array or colour-key mask, mesh and function-based gradients,
RGB inside patterns' base spaces, and anything whose embedded profile cannot
be read. The RGB check still reports what is left.
"""
from __future__ import annotations

import io
import os
import zlib
from collections import Counter, defaultdict

import pikepdf
from PIL import Image, ImageCms

from ..checks.base import Options
from ..content import walk_page
from .append import AppendError, append_update
from .base import Proposal
from .spot_to_cmyk import SAMPLES, _flate, _num
from ..functions import Function

NAME = "rgb-to-cmyk"
ADOBE = "/Library/Application Support/Adobe/Color/Profiles"
PROFILES = {  # short names for --profile
    "fogra39": [f"{ADOBE}/Recommended/CoatedFOGRA39.icc", f"{ADOBE}/CoatedFOGRA39.icc"],
    "gracol2006": [f"{ADOBE}/Recommended/CoatedGRACoL2006.icc", f"{ADOBE}/CoatedGRACoL2006.icc"],
    "swop": [f"{ADOBE}/Recommended/USWebCoatedSWOP.icc"],
}
DEFAULT_PROFILE = "fogra39"
NEAR_BLACK = 0.1  # near-black: no channel above this...
NEUTRAL = 0.02  # ...and the channels this close to each other (a navy-black is not near-black)


def is_near_black(rgb) -> bool:
    values = [float(v) for v in list(rgb)[:3]]
    return len(values) == 3 and any(values) and max(values) <= NEAR_BLACK and max(values) - min(values) <= NEUTRAL


def find_profile(name: str | None) -> str:
    """The path of the destination profile: a short name, or a path to an .icc file."""
    name = name or DEFAULT_PROFILE
    candidates = PROFILES.get(name.lower().replace(" ", ""), [os.path.expanduser(name)])
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise ValueError(f'colour profile "{name}" not found; give the path to a CMYK .icc file with --profile')


# ---- the colour engine -----------------------------------------------------------

class Engine:
    """RGB -> CMYK through LittleCMS: relative colorimetric, black point compensation."""

    def __init__(self, profile_path: str):
        try:
            self.dst = ImageCms.getOpenProfile(profile_path)
        except Exception as exc:
            raise ValueError(f"the colour profile could not be read: {exc}") from None
        if self.dst.profile.xcolor_space.strip() != "CMYK":
            raise ValueError("the colour profile is not a CMYK profile")
        self.name = (self.dst.profile.profile_description or os.path.basename(profile_path)).strip()
        self.path = profile_path
        self.srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        self._sources: dict = {}
        self._transforms: dict = {}
        self._colours: dict = {}

    def source(self, key):
        """A source profile: "srgb", or ("icc", ICC stream)."""
        if key == "srgb":
            return self.srgb
        objgen = key[1].objgen
        if objgen not in self._sources:
            self._sources[objgen] = ImageCms.ImageCmsProfile(io.BytesIO(key[1].read_bytes()))
        return self._sources[objgen]

    def _transform(self, key):
        k = "srgb" if key == "srgb" else key[1].objgen
        if k not in self._transforms:
            self._transforms[k] = ImageCms.buildTransform(
                self.source(key), self.dst, "RGB", "CMYK",
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        return self._transforms[k]

    def colour(self, rgb, key="srgb", black_to_k=False, near_black=False) -> tuple:
        """One colour, values 0..1. With black_to_k, pure black becomes K100 only;
        with near_black as well, so does near-black (the owner's option, off by default)."""
        values = [min(max(float(v), 0.0), 1.0) for v in list(rgb)[:3]] + [0.0] * (3 - len(rgb[:3]))
        if black_to_k and (all(v <= 1e-6 for v in values) or (near_black and is_near_black(values))):
            return (0.0, 0.0, 0.0, 1.0)
        rgb8 = tuple(int(round(v * 255)) for v in values)
        k = ("srgb" if key == "srgb" else key[1].objgen, rgb8)
        if k not in self._colours:
            img, out = Image.new("RGB", (1, 1), rgb8), Image.new("CMYK", (1, 1))
            # the core transform: applyTransform would also copy the profile into every 1-pixel result
            self._transform(key).transform.apply(img.getim(), out.getim())
            self._colours[k] = tuple(round(v / 255, 4) for v in out.getpixel((0, 0)))
        return self._colours[k]

    def image(self, img: Image.Image, key="srgb") -> Image.Image:
        return ImageCms.applyTransform(img.convert("RGB"), self._transform(key))


def rgb_source(space, resources=None):
    """"srgb" or ("icc", stream) when a colour space is RGB, else None."""
    if isinstance(space, pikepdf.Name):
        name = str(space)
        if name in ("/DeviceRGB", "/RGB"):
            return "srgb"
        spaces = resources.get("/ColorSpace") if isinstance(resources, pikepdf.Dictionary) else None
        if spaces is not None and name in spaces:
            return rgb_source(spaces[name], None)
        return None
    if isinstance(space, pikepdf.Array) and len(space):
        head = str(space[0])
        if head == "/ICCBased" and int(space[1].get("/N", 0)) == 3:
            return ("icc", space[1])
        if head == "/CalRGB":
            return "srgb"
        if head in ("/DeviceRGB", "/RGB") and len(space) == 1:
            return "srgb"
    return None


# ---- content streams ---------------------------------------------------------------

def _ins(op, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(op))


class Rewriter:
    def __init__(self, engine: Engine, near_black: bool = False):
        self.engine = engine
        self.near_black = near_black
        self.left: Counter = Counter()
        self.colours = 0  # colour settings converted

    def rewrite(self, instructions, resources):
        out, changed = [], False
        fill = stroke = None  # the RGB source of the current fill/stroke colour space
        stack = []
        to_cmyk = lambda values, src: [_num(v) for v in self.engine.colour(values, src, black_to_k=True,
                                                                             near_black=self.near_black)]
        for ins in instructions:
            op, ops = str(ins.operator), ins.operands
            if op == "q":
                stack.append((fill, stroke))
            elif op == "Q" and stack:
                fill, stroke = stack.pop()
            elif op in ("rg", "RG"):
                values = [float(v) for v in ops]
                out.append(_ins("k" if op == "rg" else "K", *to_cmyk(values, "srgb")))
                if op == "rg":
                    fill = None
                else:
                    stroke = None
                self.colours += 1
                changed = True
                continue
            elif op in ("g", "k"):
                fill = None
            elif op in ("G", "K"):
                stroke = None
            elif op in ("cs", "CS"):
                src = rgb_source(ops[0], resources)
                if op == "cs":
                    fill = src
                else:
                    stroke = src
                if src is not None:
                    # an RGB colour space starts at black: K100 under the black rule
                    out += [_ins(op, pikepdf.Name.DeviceCMYK), _ins("sc" if op == "cs" else "SC", *to_cmyk([0, 0, 0], src))]
                    changed = True
                    continue
            elif op in ("sc", "scn", "SC", "SCN"):
                src = fill if op in ("sc", "scn") else stroke
                if src is not None:
                    values = [float(v) for v in ops if not isinstance(v, pikepdf.Name)]
                    out.append(_ins("sc" if op in ("sc", "scn") else "SC", *to_cmyk(values, src)))
                    self.colours += 1
                    changed = True
                    continue
            elif op == "INLINE IMAGE":
                try:
                    cs = ops[0].obj.get("/ColorSpace", ops[0].obj.get("/CS"))
                    if cs is not None and rgb_source(cs, resources) is not None:
                        self.left["inline image(s)"] += 1
                except Exception:
                    pass
            out.append(ins)
        return out, changed


# ---- images, gradients ---------------------------------------------------------------

JPEG_QUALITY = 90  # when the source's own quality cannot be read
STANDARD_LUMA_MEAN = 57.625  # mean of the JPEG standard's luminance table (quality 50)


def jpeg_quality(data: bytes) -> int:
    """The quality a JPEG was saved at, estimated from its luminance table (IJG scaling)."""
    try:
        tables = Image.open(io.BytesIO(data)).quantization
        mean = sum(tables[0]) / len(tables[0])
    except Exception:
        return JPEG_QUALITY
    scale = mean / STANDARD_LUMA_MEAN * 100
    quality = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return int(round(min(max(quality, 75), 95)))
ADOBE_INVERTED = [1, 0, 1, 0, 1, 0, 1, 0]


def _is_jpeg(d) -> bool:
    f = d.get("/Filter")
    names = [str(x) for x in f] if isinstance(f, pikepdf.Array) else [str(f)] if f is not None else []
    return names == ["/DCTDecode"]


def _convert_image(pdf, img, engine, dry=False):
    """(new image stream, None) or (None, reason it is left). With dry, only
    whether it can be converted: (True, None)."""
    d = img.stream_dict
    space = d.get("/ColorSpace")
    if bool(d.get("/ImageMask", False)) or space is None:
        return None, None
    if isinstance(space, pikepdf.Array) and len(space) == 4 and str(space[0]) == "/Indexed":
        src = rgb_source(space[1])
        if src is None:
            return None, None
        hival = int(space[2])
        lookup = space[3]
        data = lookup.read_bytes() if isinstance(lookup, pikepdf.Stream) else bytes(lookup)
        if len(data) < 3 * (hival + 1):
            return None, "an indexed image whose palette is short"
        if dry:
            return True, None
        palette = bytearray()
        for i in range(hival + 1):
            rgb = [data[3 * i + j] / 255 for j in range(3)]
            palette += bytes(int(round(v * 255)) for v in engine.colour(rgb, src))
        new = pikepdf.Stream(pdf, img.read_raw_bytes())
        for k in d.keys():
            if k != "/Length":
                new[k] = d[k]
        new["/ColorSpace"] = pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceCMYK, hival, pikepdf.String(bytes(palette))])
        return new, None
    src = rgb_source(space)
    if src is None:
        return None, None
    if int(d.get("/BitsPerComponent", 8)) != 8:
        return None, "image(s) that are not 8 bits per channel"
    if "/Decode" in d or isinstance(d.get("/Mask"), pikepdf.Array):
        return None, "image(s) with a decode array or colour-key mask"
    if dry:
        return True, None
    try:
        pil = pikepdf.PdfImage(img).as_pil_image()
        if pil.mode not in ("RGB",):
            pil = pil.convert("RGB")
        cmyk = engine.image(pil, src)
    except Exception as exc:
        return None, f"image(s) that could not be read ({type(exc).__name__})"
    if _is_jpeg(d):
        buf = io.BytesIO()
        cmyk.save(buf, "JPEG", quality=jpeg_quality(img.read_raw_bytes()))
        new = pikepdf.Stream(pdf, buf.getvalue())
    else:
        new = pikepdf.Stream(pdf, zlib.compress(cmyk.tobytes(), 6))
    for k in d.keys():
        if k not in ("/Length", "/Filter", "/DecodeParms", "/ColorSpace", "/BitsPerComponent", "/Decode"):
            new[k] = d[k]
    new["/ColorSpace"] = pikepdf.Name.DeviceCMYK
    new["/BitsPerComponent"] = 8
    if _is_jpeg(d):
        new["/Filter"] = pikepdf.Name.DCTDecode
        new["/Decode"] = ADOBE_INVERTED  # Pillow's CMYK JPEG is stored inverted (Adobe's convention)
    else:
        new["/Filter"] = pikepdf.Name.FlateDecode
    return new, None


def _sampled(pdf, fn, src, engine, domain):
    try:
        parts = [Function(f) for f in fn] if isinstance(fn, pikepdf.Array) else [Function(fn)]
    except Exception:
        return None
    d0, d1 = domain
    data = bytearray()
    for i in range(SAMPLES):
        t = d0 + (d1 - d0) * i / (SAMPLES - 1)
        rgb = [v for part in parts for v in part([t])]
        for v in engine.colour(rgb, src):
            data += int(round(min(max(v, 0.0), 1.0) * 65535)).to_bytes(2, "big")
    stream = pikepdf.Stream(pdf, bytes(data))
    stream["/FunctionType"] = 0
    stream["/Domain"] = [d0, d1]
    stream["/Range"] = [0, 1, 0, 1, 0, 1, 0, 1]
    stream["/Size"] = [SAMPLES]
    stream["/BitsPerSample"] = 16
    return pdf.make_indirect(stream)


def _convert_shading(pdf, sh, engine, created):
    """(new shading dictionary, None) or (None, reason) for an RGB shading; (None, None) if not RGB."""
    src = rgb_source(sh.get("/ColorSpace"))
    if src is None:
        return None, None
    kind = int(sh.get("/ShadingType", 0))
    if kind not in (2, 3) or sh.get("/Function") is None:
        return None, "gradient(s) of a kind this fix does not convert (mesh or function-based)"
    domain = [float(v) for v in sh.get("/Domain", [0, 1])]
    fn = _sampled(pdf, sh.Function, src, engine, domain)
    if fn is None:
        return None, "gradient(s) whose colour function could not be read"
    created.append(fn)
    new = pikepdf.Dictionary({k: sh[k] for k in sh.keys()})
    new["/ColorSpace"] = pikepdf.Name.DeviceCMYK
    new["/Function"] = fn
    if "/Background" in sh:
        new["/Background"] = [_num(v) for v in engine.colour([float(v) for v in sh.Background], src)]
    return new, None


def _group_is_rgb(obj) -> bool:
    group = obj.get("/Group")
    return isinstance(group, pikepdf.Dictionary) and rgb_source(group.get("/CS")) is not None


def _cmyk_group(group):
    new = pikepdf.Dictionary({k: group[k] for k in group.keys()})
    new["/CS"] = pikepdf.Name.DeviceCMYK
    return new


# ---- planning ------------------------------------------------------------------------

class _Plan:
    def __init__(self):
        self.pages = {}  # page number -> new instructions or None (group only)
        self.streams = {}  # objgen -> new instructions (forms, tiling patterns), or None (group only)
        self.images = {}  # objgen -> new image stream
        self.shadings = {}  # objgen -> new shading dictionary (indirect dictionaries)
        self.patterns = {}  # objgen -> new shading-pattern dictionary (direct shading inside)
        self.created = []
        self.left: Counter = Counter()


def _excluded(pdf) -> set:
    """Streams that are not printed colour: soft-mask groups and annotation appearances."""
    out = set()
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary) and "/SMask" in obj and isinstance(obj.get("/SMask"), pikepdf.Dictionary):
            g = obj.SMask.get("/G")
            if isinstance(g, pikepdf.Stream):
                out.add(g.objgen)
    for page in pdf.pages:
        for annot in page.obj.get("/Annots", []):
            ap = annot.get("/AP") if isinstance(annot, pikepdf.Dictionary) else None
            if not isinstance(ap, pikepdf.Dictionary):
                continue
            for key in ap.keys():
                v = ap[key]
                for s in ([v] if isinstance(v, pikepdf.Stream) else [v[k] for k in v.keys()] if isinstance(v, pikepdf.Dictionary) else []):
                    if isinstance(s, pikepdf.Stream):
                        out.add(s.objgen)
    return out


def _plan(pdf, engine, dry=False, near_black=False) -> tuple[_Plan, Rewriter]:
    plan, rw = _Plan(), Rewriter(engine, near_black)
    skip = _excluded(pdf)
    for number, page in enumerate(pdf.pages, 1):
        new, changed = rw.rewrite(list(pikepdf.parse_content_stream(page)), page.resources)
        if changed or _group_is_rgb(page.obj):
            plan.pages[number] = new if changed else None
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream):
            d = obj.stream_dict
            sub = str(d.get("/Subtype"))
            if sub == "/Image":
                new, why = _convert_image(pdf, obj, engine, dry)
                if new is not None:
                    plan.images[obj.objgen] = new
                elif why:
                    plan.left[why] += 1
                continue
            if obj.objgen in skip:
                continue
            if sub == "/Form" or int(d.get("/PatternType", 0)) == 1:
                try:
                    instructions = list(pikepdf.parse_content_stream(obj))
                except Exception:
                    continue
                res = d.get("/Resources")
                new, changed = rw.rewrite(instructions, res)
                if changed or (sub == "/Form" and _group_is_rgb(d)):
                    plan.streams[obj.objgen] = new if changed else None
            elif "/ShadingType" in d:  # mesh gradients are streams
                if rgb_source(d.get("/ColorSpace")) is not None:
                    plan.left["gradient(s) of a kind this fix does not convert (mesh or function-based)"] += 1
        elif isinstance(obj, pikepdf.Dictionary):
            if "/ShadingType" in obj:
                new, why = _convert_shading(pdf, obj, engine, plan.created)
                if new is not None:
                    plan.shadings[obj.objgen] = new
                elif why:
                    plan.left[why] += 1
            elif int(obj.get("/PatternType", 0)) == 2 and isinstance(obj.get("/Shading"), pikepdf.Dictionary) \
                    and not obj.Shading.is_indirect:
                new, why = _convert_shading(pdf, obj.Shading, engine, plan.created)
                if new is not None:
                    copy = pikepdf.Dictionary({k: obj[k] for k in obj.keys()})
                    copy["/Shading"] = new
                    plan.patterns[obj.objgen] = copy
                elif why:
                    plan.left[why] += 1
    # gradients written directly inside a resources dictionary are not separate objects
    owners = [p.obj.get("/Resources") for p in pdf.pages] + \
             [o.stream_dict.get("/Resources") for o in pdf.objects if isinstance(o, pikepdf.Stream)]
    seen = set()
    for res in owners:
        if not isinstance(res, pikepdf.Dictionary) or (res.is_indirect and res.objgen in seen):
            continue
        if res.is_indirect:
            seen.add(res.objgen)
        for key in ("/Shading", "/Pattern"):
            group = res.get(key)
            if not isinstance(group, pikepdf.Dictionary):
                continue
            for name in group.keys():
                item = group[name]
                sh = item.get("/Shading") if key == "/Pattern" and isinstance(item, pikepdf.Dictionary) else item
                if isinstance(item, pikepdf.Dictionary) and not item.is_indirect and isinstance(sh, pikepdf.Dictionary) \
                        and rgb_source(sh.get("/ColorSpace")) is not None:
                    plan.left["gradient(s) written inside a resources dictionary"] += 1
    plan.left.update(rw.left)
    return plan, rw


# ---- proposal ---------------------------------------------------------------------------

def _rgb_events(pdf) -> dict:
    """{page: Counter(kind)} of RGB objects, as the RGB check counts them."""
    out = {}
    labels = {"path": "vector", "text": "text", "image": "image", "shading": "gradient"}
    for number, page in enumerate(pdf.pages, 1):
        c = Counter(labels.get(e.kind, e.kind) for e in walk_page(page).events if e.cs.kind == "rgb")
        if c:
            out[number] = c
    return out


def _counts(c: Counter) -> str:
    order = ["text", "vector", "image", "gradient"]
    return ", ".join(f"{c[k]} {k}" for k in order if c.get(k))


def propose_rgb_to_cmyk(path: str, options: Options | None = None, profile=None, near_black=False, **_) -> Proposal:
    """What the fix would change. Reads only."""
    engine = Engine(find_profile(profile))
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        before = _rgb_events(pdf)
        plan, rw = _plan(pdf, engine, dry=True, near_black=near_black)  # images only checked here; the write converts them
        vectors = [e for page in pdf.pages for e in walk_page(page).events
                   if e.cs.kind == "rgb" and e.kind in ("path", "text") and e.colour]
        blacks = sum(1 for e in vectors if not any(e.colour))
        # near blacks are not pure black, so they follow the profile: a four-colour black
        near = [e for e in vectors if is_near_black(e.colour)]
    anything = plan.pages or plan.streams or plan.images or plan.shadings or plan.patterns
    for page in sorted(before):
        text = f"Converts RGB to CMYK ({engine.name}): {_counts(before[page])} object(s)."
        proposal.reasons.append((page, text))
    if blacks:
        proposal.reasons.append((min(before) if before else 1, f"{blacks} pure RGB black text or vector object(s) become "
                                                               "K100 only, not a four-colour black."))
    if near and near_black:
        proposal.reasons.append((min(before) if before else 1, f"{len(near)} near-black RGB text or vector object(s) "
                                 f"(neutral, no channel above {NEAR_BLACK * 100:.0f}%) also become K100 only, "
                                 "as asked (--near-black-to-k)."))
    elif near:
        ink = round(sum(engine.colour(near[0].colour)) * 100)
        proposal.reasons.append((min(before) if before else 1, f"{len(near)} near-black RGB text or vector object(s) "
                                 f"(neutral, no channel above {NEAR_BLACK * 100:.0f}%) are not pure black, so they follow "
                                 f"the profile and become a four-colour black (about {ink}% ink). "
                                 "--near-black-to-k makes them K100 too."))
    for why, n in plan.left.items():
        proposal.skipped.append((1, f"{n} {why} left in RGB"))
    if anything:
        proposal.plan = {0: {"profile": engine.path, "near_black": near_black}}
    elif not plan.left:
        proposal.skipped.append((1, "there is no RGB colour to convert"))
    return proposal


# ---- writing ------------------------------------------------------------------------------

def _check(src, engine, near_black=False):
    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
                before, after = walk_page(pa).events, walk_page(pb).events
                if len(before) != len(after):
                    raise AppendError(f"verification failed: page {number} paints a different number of objects")
                for i, (x, y) in enumerate(zip(before, after)):
                    if (x.kind, x.paint, x.bbox, x.overprint, x.opm, x.blend) != (y.kind, y.paint, y.bbox, y.overprint, y.opm, y.blend):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if x.cs.kind != "rgb":
                        if (x.cs, x.colour) != (y.cs, y.colour):
                            raise AppendError(f"verification failed: page {number}, object {i} was not RGB but changed colour")
                    elif x.kind in ("path", "text") and x.colour is not None:
                        # every RGB text and vector colour must be converted; pure black exactly to K100
                        if y.cs.kind != "cmyk":
                            raise AppendError(f"verification failed: page {number}, object {i} is still RGB")
                        black = not any(x.colour) or (near_black and is_near_black(x.colour))
                        if black and tuple(y.colour) != (0, 0, 0, 1):
                            raise AppendError(f"verification failed: page {number}, object {i}'s black is not K100")
    return check


def write_rgb_to_cmyk(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    engine = Engine(proposal.plan[0]["profile"])
    near_black = proposal.plan[0].get("near_black", False)
    with pikepdf.open(proposal.path) as pdf:
        plan, _ = _plan(pdf, engine, near_black=near_black)
        changes, additions = {}, {}
        for fn in plan.created:
            additions[fn.objgen] = fn
        for number, instructions in plan.pages.items():
            page = pdf.pages[number - 1].obj
            copy = pikepdf.Dictionary({k: page[k] for k in page.keys()})
            if instructions is not None:
                content = pdf.make_indirect(_flate(pdf, pikepdf.unparse_content_stream(instructions)))
                additions[content.objgen] = content
                copy["/Contents"] = content
            if _group_is_rgb(page):
                copy["/Group"] = _cmyk_group(page.Group)
            changes[page.objgen] = copy
        for objgen, instructions in plan.streams.items():
            old = pdf.get_object(objgen)
            d = old.stream_dict
            if instructions is not None:
                new = _flate(pdf, pikepdf.unparse_content_stream(instructions))
                skip = ("/Length", "/Filter", "/DecodeParms")
            else:
                new = pikepdf.Stream(pdf, old.read_raw_bytes())
                skip = ("/Length",)
            for k in d.keys():
                if k not in skip:
                    new[k] = d[k]
            if _group_is_rgb(d):
                new["/Group"] = _cmyk_group(d.Group)
            changes[objgen] = new
        changes.update(plan.images)
        changes.update(plan.shadings)
        changes.update(plan.patterns)
        append_update(proposal.path, dst, changes, additions, extra_check=_check(proposal.path, engine, near_black))
