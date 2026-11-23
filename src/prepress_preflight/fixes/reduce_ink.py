"""Bring vector and text colours over the total-ink limit down to it.

Why: paper takes only so much ink before it stays wet, sets off onto the next
sheet or cracks when folded. Each press and stock has a limit (often 300% to
340% for coated, lower for uncoated). Colours built over it, such as the
"black" C100 M100 Y100 K100 (400%), must come down.

How each colour over the limit is brought down (colours within it are not
touched, so nothing else shifts):
  1. Grey is moved into black: equal parts of C, M and Y are taken out and the
     same amount of K is added, until the total fits or K reaches 100%. Each
     step removes two parts of ink for one and keeps the colour about as dark,
     which is what gray component replacement does.
  2. If it still does not fit, C, M and Y are scaled down together, which keeps
     their balance (the hue), leaving K as it is.
C100 M100 Y100 K100 at a 300% limit becomes C67 M67 Y67 K100.

The limit must be given (`--ink-limit`, 200 to 399): the press limit is the
printer's, and 400% is no limit at all.

Flat CMYK colours of shapes, lines, text and stencil masks: each object is
bracketed (see bracket.py), so nothing else takes the new colour.

CMYK images (DeviceCMYK or a 4-channel ICC space, 8 bits): every pixel over
the limit is reduced the same way; pixels within it keep their exact values.
The image keeps its compression. A JPEG is saved again at its own quality,
and since that adds a little noise, its pixels are brought 2% under the limit
so the noise cannot lift them back over. The image is replaced as an object,
so every place it is used gets the new pixels. An indexed image has its
palette reduced instead, exactly. Left and listed: colour-keyed images, inline
images, images in spot, DeviceN or Lab colour, and gradients.

Verified: every object in the file is unchanged except the targets, whose
colours are exactly the reduced ones; every replaced image decodes at the same
size with nothing over the limit.
"""
from __future__ import annotations

import io
import math
import zlib
from collections import Counter

import numpy as np
import pikepdf
import pymupdf
from PIL import Image

from ..checks.base import Options
from ..content import CMYK, walk_page
from .append import AppendError, append_update
from .base import Proposal
from .bracket import Plan, find_spots, known, recolour, write_plan
from .rgb_to_cmyk import ADOBE_INVERTED, _is_jpeg, jpeg_quality

NAME = "reduce-ink"
JPEG_MARGIN_PCT = 2.0
LABELS = {("path", "fill"): "shape(s)", ("path", "stroke"): "line(s)", ("text", "fill"): "text object(s)",
          ("text", "stroke"): "outlined text object(s)", ("imagemask", "fill"): "stencil mask(s)"}


def reduce(colour, limit_pct: float) -> tuple:
    """The colour brought within limit_pct total ink, or the colour itself if it is within."""
    c, m, y, k = (float(v) for v in colour)
    limit = limit_pct / 100
    if c + m + y + k <= limit + 1e-9:
        return tuple(colour)
    excess = c + m + y + k - limit
    x = max(0.0, min(excess / 2, c, m, y, 1 - k))
    c, m, y, k = c - x, m - x, y - x, k + x
    cmy = c + m + y
    if cmy + k > limit + 1e-9 and cmy > 0:
        s = max(0.0, limit - k) / cmy
        c, m, y = c * s, m * s, y * s
    # round down to 0.01% so the total cannot creep over the limit
    return tuple(math.floor(v * 10000 + 1e-6) / 10000 for v in (c, m, y, k))


def reduce_pixels(cmyk: np.ndarray, limit_pct: float) -> np.ndarray:
    """reduce() for an (h, w, 4) uint8 array; pixels within the limit are returned exactly."""
    v = cmyk.astype(np.float64) / 255
    limit = limit_pct / 100
    total = v.sum(axis=2)
    over = total > limit + 1e-9
    if not over.any():
        return cmyk
    c, m, y, k = (v[..., i][over] for i in range(4))
    x = np.clip(np.minimum.reduce([(total[over] - limit) / 2, c, m, y, 1 - k]), 0, None)
    c, m, y, k = c - x, m - x, y - x, k + x
    cmy = c + m + y
    s = np.where((cmy + k > limit + 1e-9) & (cmy > 0), np.clip(limit - k, 0, None) / np.where(cmy > 0, cmy, 1), 1.0)
    out = cmyk.copy()
    reduced = np.stack([c * s, m * s, y * s, k], axis=1)
    out[over] = np.floor(reduced * 255 + 1e-6).astype(np.uint8)  # down, so 8 bits cannot creep over
    return out


def _is_cmyk_space(space) -> bool:
    if space is None:
        return False
    if isinstance(space, pikepdf.Name):
        return str(space) == "/DeviceCMYK"
    return (isinstance(space, pikepdf.Array) and len(space) == 2 and str(space[0]) == "/ICCBased"
            and int(space[1].get("/N", 0)) == 4)


def _gray_or_rgb(space) -> bool:
    """Grey or RGB, plain, ICC or indexed: never over a CMYK ink limit (RGB is rgb-to-cmyk's business)."""
    if isinstance(space, pikepdf.Name):
        return str(space) in ("/DeviceGray", "/DeviceRGB", "/CalGray", "/CalRGB")
    if isinstance(space, pikepdf.Array) and len(space):
        head = str(space[0])
        if head == "/Indexed" and len(space) == 4:
            return _gray_or_rgb(space[1])
        if head in ("/CalGray", "/CalRGB"):
            return True
        if head == "/ICCBased":
            return int(space[1].get("/N", 0)) in (1, 3)
    return False


def _decode(mu, xref) -> np.ndarray:
    pix = pymupdf.Pixmap(mu, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n != 4:
        raise ValueError("not four channels")
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4)


def _images(pdf, path, limit, build=False):
    """({objgen: new stream or None}, {page: Counter(reason)}, {page: count}) for the CMYK images
    over the limit, the images left and why, and the images changed on each page."""
    uses, inline = {}, []
    for number, page in enumerate(pdf.pages, 1):
        for e in walk_page(page).events:
            if e.kind == "image" and e.ref is not None:
                uses.setdefault(e.ref, []).append(number)
            elif e.kind == "image" and e.cs == CMYK:
                inline.append(number)
    new, left, fixed = {}, {}, Counter()

    def leave(pages, reason):
        for n in pages:
            left.setdefault(n, Counter())[reason] += 1

    pymupdf.TOOLS.set_icc(False)
    mu = pymupdf.open(path)

    leave(inline, "CMYK inline image(s), which this fix does not change (not checked against the limit)")
    try:
        for key, pages in uses.items():
            img = pdf.get_object(key)
            d = img.stream_dict
            space = d.get("/ColorSpace")
            if bool(d.get("/ImageMask", False)) or space is None:
                continue
            if (isinstance(space, pikepdf.Array) and len(space) == 4 and str(space[0]) == "/Indexed"
                    and _is_cmyk_space(space[1])):
                hival = int(space[2])
                lookup = space[3]
                data = lookup.read_bytes() if isinstance(lookup, pikepdf.Stream) else bytes(lookup)
                if len(data) < 4 * (hival + 1):
                    leave(pages, "indexed image(s) whose palette is short")
                    continue
                palette = np.frombuffer(data[:4 * (hival + 1)], np.uint8).reshape(1, hival + 1, 4)
                if palette.astype(np.int32).sum(axis=2).max() / 2.55 <= limit + 0.5:
                    continue
                for n in set(pages):
                    fixed[n] += 1
                if not build:
                    new[key] = None
                    continue
                # the indexes are unchanged; decoded and compressed afresh so a damaged
                # original stream is not copied as it was
                stream = pikepdf.Stream(pdf, zlib.compress(img.read_bytes(), 6))
                for k in d.keys():
                    if k not in ("/Length", "/Filter", "/DecodeParms"):
                        stream[k] = d[k]
                stream["/Filter"] = pikepdf.Name.FlateDecode
                reduced = reduce_pixels(palette, limit)  # exact: the palette is not recompressed
                stream["/ColorSpace"] = pikepdf.Array([pikepdf.Name.Indexed, space[1], hival,
                                                       pikepdf.String(reduced.tobytes() + data[4 * (hival + 1):])])
                new[key] = stream
                continue
            if not _is_cmyk_space(space):
                if not _gray_or_rgb(space):
                    leave(pages, "image(s) in spot, DeviceN or Lab colour, which this fix does not change")
                continue
            if int(d.get("/BitsPerComponent", 8)) != 8:
                leave(pages, "CMYK image(s) that are not 8 bits per channel")
                continue
            try:
                pixels = _decode(mu, key[0])
            except Exception:
                leave(pages, "image(s) that could not be read")
                continue
            jpeg = _is_jpeg(d)
            target = limit - JPEG_MARGIN_PCT if jpeg else limit
            if pixels.astype(np.int32).sum(axis=2).max() / 2.55 <= limit + 0.5:
                continue
            if isinstance(d.get("/Mask"), pikepdf.Array):
                leave(pages, "image(s) masked by colour key (changing pixels would change the mask)")
                continue
            for n in set(pages):
                fixed[n] += 1
            if not build:
                new[key] = None
                continue
            out = reduce_pixels(pixels, target)
            if jpeg:
                buf = io.BytesIO()
                Image.frombytes("CMYK", (out.shape[1], out.shape[0]), out.tobytes()).save(
                    buf, "JPEG", quality=jpeg_quality(img.read_raw_bytes()))
                stream = pikepdf.Stream(pdf, buf.getvalue())
            else:
                stream = pikepdf.Stream(pdf, zlib.compress(out.tobytes(), 6))
            for k in d.keys():
                if k not in ("/Length", "/Filter", "/DecodeParms", "/Decode"):
                    stream[k] = d[k]
            stream["/Filter"] = pikepdf.Name.DCTDecode if jpeg else pikepdf.Name.FlateDecode
            if jpeg:
                stream["/Decode"] = ADOBE_INVERTED  # Pillow's CMYK JPEG is stored inverted (Adobe's convention)
            new[key] = stream
    finally:
        mu.close()
    return new, left, fixed


def _percent(colour) -> str:
    return " ".join(f"{ink}{round(v * 100)}" for ink, v in zip("CMYK", colour))


def _candidate(e) -> bool:
    return ((e.kind, e.paint) in LABELS and e.cs == CMYK and not e.cs.indexed and bool(e.colour)
            and len(e.colour) == 4)


def _limit(limit) -> float:
    if limit is None or float(limit) >= 400:
        raise ValueError("reduce-ink needs the press limit: --ink-limit, for example 300 (400% is no limit)")
    limit = float(limit)
    if limit < 200:
        raise ValueError("--ink-limit for reduce-ink must be 200 or more")
    return limit


def _analyse(pdf, limit) -> tuple[Plan, Counter, dict]:
    plan = Plan()
    colours: Counter = Counter()  # (old, new) -> count

    def flag(number, snap):
        return sum(snap.event.colour) * 100 > limit + 0.5

    for spot in find_spots(pdf, _candidate, flag, plan, every_use=True,
                           elsewhere="in a form that is also painted where it is within the limit"):
        new, ok = {}, True
        for side in {s.event.paint for _, s in spot.flagged}:
            old = known(spot, lambda s, side=side: tuple(s.event.colour) if s.event.paint == side else None)
            if old is None:
                ok = False
                break
            new[side] = reduce(old, limit)
        if not ok:
            plan.leave(spot, "in a form whose uses have different colours")
            continue
        if recolour(plan, spot, new, lambda e: LABELS[(e.kind, e.paint)]):
            for _, s in spot.flagged:
                colours[(tuple(s.event.colour), new[s.event.paint])] += 1
        else:
            plan.leave(spot, "in a form whose uses disagree on the colour to put back")
    plan.targets.sort()
    gradients = {}
    for number, page in enumerate(pdf.pages, 1):
        n = sum(1 for e in walk_page(page).events if e.kind == "shading")
        if n:
            gradients[number] = n
    return plan, colours, gradients


def propose_reduce_ink(path: str, options: Options | None = None, ink_limit=None, **_) -> Proposal:
    """What the fix would change. Reads only."""
    limit = _limit(ink_limit if ink_limit is not None else (options.ink_limit_pct if options else None))
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        plan, colours, gradients = _analyse(pdf, limit)
        images, images_left, images_fixed = _images(pdf, path, limit)
    for page in sorted(images_fixed):
        proposal.reasons.append((page, f"Brings the pixels over {limit:g}% total ink down to it in "
                                       f"{images_fixed[page]} CMYK image(s); pixels within the limit keep their values."))
    for page in sorted(images_left):
        for reason, n in images_left[page].items():
            proposal.skipped.append((page, f"{n} {reason} left as they are"))
    for page in sorted(plan.fixed):
        detail = ", ".join(f"{n} {label}" for label, n in plan.fixed[page].most_common())
        proposal.reasons.append((page, f"Brings colours over {limit:g}% total ink down to it: {detail}."))
    if colours:
        top = ", ".join(f"{_percent(old)} ({sum(old) * 100:.0f}%) to {_percent(new)} ({sum(new) * 100:.0f}%) x{n}"
                        for (old, new), n in colours.most_common(5))
        first = min(plan.fixed)
        proposal.reasons.append((first, f"Colours changed (most used first): {top}"
                                        + (f", and {len(colours) - 5} more" if len(colours) > 5 else "") + "."))
    for page in sorted(plan.left):
        for reason, n in plan.left[page].items():
            proposal.skipped.append((page, f"{n} object(s) over the limit left as they are: {reason}"))
    for page, n in sorted(gradients.items()):
        proposal.skipped.append((page, f"{n} gradient(s) not changed by this fix: if Total ink still finds this page "
                                       "over the limit afterwards, look at them"))
    if plan.targets or images:
        proposal.plan = {0: {"targets": [list(t) for t in plan.targets], "limit": limit,
                             "images": sorted(list(k) for k in images)}}
    elif not plan.left and not images_left:
        proposal.skipped.append((1, f"no flat CMYK colours over {limit:g}% total ink"))
    return proposal


def _check(src, targets, limit, images):
    wanted = {(p, n) for p, n, _ in targets}

    def check(new_path):
        pymupdf.TOOLS.set_icc(False)
        mu_a, mu_b = pymupdf.open(src), pymupdf.open(new_path)
        try:
            for objgen in images:
                a, b = _decode(mu_a, objgen[0]), _decode(mu_b, objgen[0])
                if a.shape != b.shape:
                    raise AppendError(f"verification failed: image {objgen[0]} changed size")
                if b.astype(np.int32).sum(axis=2).max() / 2.55 > limit + 0.5:
                    raise AppendError(f"verification failed: image {objgen[0]} is still over {limit:g}%")
        finally:
            mu_a.close()
            mu_b.close()
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
                before, after = walk_page(pa).events, walk_page(pb).events
                if len(before) != len(after):
                    raise AppendError(f"verification failed: page {number} paints a different number of objects")
                for i, (x, y) in enumerate(zip(before, after)):
                    if (x.kind, x.paint, x.bbox, x.overprint, x.opm, x.blend) != (y.kind, y.paint, y.bbox, y.overprint, y.opm, y.blend):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
                    if (number, i) in wanted:
                        if y.cs != CMYK or tuple(y.colour) != reduce(x.colour, limit) or sum(y.colour) * 100 > limit + 0.01:
                            raise AppendError(f"verification failed: page {number}, object {i} is not the reduced colour")
                    elif (x.cs, x.colour) != (y.cs, y.colour):
                        raise AppendError(f"verification failed: page {number}, object {i}'s colour changed")
    return check


def write_reduce_ink(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    targets = [tuple(t) for t in proposal.plan[0]["targets"]]
    limit = proposal.plan[0]["limit"]
    wanted_images = [tuple(k) for k in proposal.plan[0].get("images", [])]
    with pikepdf.open(proposal.path) as pdf:
        plan, _, _ = _analyse(pdf, limit)
        images, _, _ = _images(pdf, proposal.path, limit, build=True)
        if plan.targets != targets or sorted(images) != wanted_images:
            raise AppendError("the file changed between the proposal and the write")
        changes, additions = write_plan(pdf, plan) if plan.targets else ({}, {})
        changes.update(images)
        append_update(proposal.path, dst, changes, additions,
                      extra_check=_check(proposal.path, targets, limit, list(images)))
