"""Downsample images placed at far more resolution than the press can use.

    preflight fix downsample-images FILE                          (over 450 ppi -> 300 ppi)
    preflight fix downsample-images FILE --max-ppi 200 --to-ppi 150   (large format)

Why: detail beyond what the plate or the print head can render is thrown away
by the RIP anyway, after it has been carried through every step. A 300 ppi
photo on a 2 m banner seen from across a room, or a 900 ppi picture shrunk
into a yearbook, makes files that are slow to send, open and RIP. Acrobat's
and PitStop's usual rule is the default here: images over 450 ppi are brought
to 300 ppi.

The resolution is the effective one, as placed. An image used several times is
judged by its largest placement (its lowest resolution), so no use of it drops
under the target. It is resized as a whole, keeping its proportions, with a
Lanczos filter (Pillow's), and stays in its own colour space. A JPEG is saved
again as JPEG at its own quality, anything else losslessly (Flate). The image
is replaced as an object, so every use gets the new pixels. A soft mask
(transparency) stays at full resolution, which PDF allows, so the outline stays
sharp; the colour is resampled weighted by it, so colour hidden under the
transparent part (often black) cannot bleed into the visible edge as a fringe.

Left and listed: inline images, indexed (palette) images, images that are not
8 bits per channel, stencil masks, images masked by colour key or by a mask
image, images whose soft mask holds a pre-blended colour (Matte), images in
spot, DeviceN or Lab colour, and any image the new version would not make
smaller.

Verified: every page paints the same objects in the same places; each
replaced image decodes at its new size; and each changed page looks as it did,
compared as the average colour of 0.7 mm cells. Cells, not pixels: a renderer
shrinking a fine image to screen size skips pixels and aliases thin lines
differently from a filtered one, which shows as pixel noise that is not in the
print. So this proves place, colour and tone, not sharpness.
"""
from __future__ import annotations

import io
import zlib
from collections import Counter

import numpy as np
import pikepdf
import pymupdf
from PIL import Image

from ..checks.base import Options
from ..content import walk_page
from .append import AppendError, append_update
from .base import Proposal
from .reduce_ink import _gray_or_rgb, _is_cmyk_space
from .rgb_to_cmyk import ADOBE_INVERTED, _is_jpeg, jpeg_quality

NAME = "downsample-images"
MAX_PPI, TO_PPI = 450.0, 300.0
MODES = {1: "L", 3: "RGB", 4: "CMYK"}


def _settings(max_ppi, to_ppi):
    top = MAX_PPI if max_ppi is None else float(max_ppi)
    to = TO_PPI if to_ppi is None else float(to_ppi)
    if not 72 <= to <= top:
        raise ValueError("--to-ppi must be at least 72 and no more than --max-ppi")
    return top, to


def _channels(space) -> int | None:
    """1, 3 or 4 for plain or ICC grey, RGB and CMYK; None for anything else."""
    if _is_cmyk_space(space):
        return 4
    if not _gray_or_rgb(space) or (isinstance(space, pikepdf.Array) and str(space[0]) == "/Indexed"):
        return None
    if isinstance(space, pikepdf.Array) and str(space[0]) == "/ICCBased":
        return int(space[1].get("/N"))
    return 1 if str(space if isinstance(space, pikepdf.Name) else space[0]) in ("/DeviceGray", "/CalGray") else 3


def _survey(pdf, top, to):
    """({objgen: (new width, new height, lowest ppi)}, {page: Counter(reason)}, {objgen: [pages]})."""
    uses, inline = {}, []
    for number, page in enumerate(pdf.pages, 1):
        unit = float(page.obj.get("/UserUnit", 1))
        for e in walk_page(page).events:
            if e.kind != "image" or e.ppi is None:
                continue
            e.ppi = e.ppi / unit
            if e.ref is None:
                if e.ppi > top:
                    inline.append(number)
                continue
            uses.setdefault(e.ref, []).append((number, e.ppi))
    targets, left, pages_of = {}, {}, {}

    def leave(pages, reason):
        for n in sorted(set(pages)):
            left.setdefault(n, Counter())[reason] += 1

    leave(inline, "inline image(s), which this fix does not change")
    for key, placed in uses.items():
        lowest = min(p for _, p in placed)
        if lowest <= top:
            continue
        pages = [n for n, _ in placed]
        d = pdf.get_object(key).stream_dict
        if bool(d.get("/ImageMask", False)):
            leave(pages, "stencil mask(s)")
            continue
        space = d.get("/ColorSpace")
        if isinstance(space, pikepdf.Array) and len(space) and str(space[0]) == "/Indexed":
            leave(pages, "indexed (palette) image(s)")
            continue
        if space is None or _channels(space) is None:
            leave(pages, "image(s) in spot, DeviceN or Lab colour, or with no colour space of their own")
            continue
        if int(d.get("/BitsPerComponent", 8)) != 8:
            leave(pages, "image(s) that are not 8 bits per channel")
            continue
        if isinstance(d.get("/Mask"), pikepdf.Array):
            leave(pages, "image(s) masked by colour key (resampling would change the mask)")
            continue
        if "/Mask" in d or "/SMaskInData" in d or ("/SMask" in d and "/Matte" in d.SMask):
            leave(pages, "image(s) with a mask image or a pre-blended soft mask, which this fix does not resample")
            continue
        scale = to / lowest
        w, h = int(d.Width), int(d.Height)
        targets[key] = (max(1, round(w * scale)), max(1, round(h * scale)), lowest)
        pages_of[key] = sorted(set(pages))
    return targets, left, pages_of


def _pixels(mu, img):
    """The image's own colour values. MuPDF folds a soft mask or mask into the decoded
    pixels (premultiplied), so an image that has one is decoded from a copy without it."""
    d = img.stream_dict
    if "/SMask" not in d and "/Mask" not in d and "/SMaskInData" not in d:
        pix = pymupdf.Pixmap(mu, img.objgen[0])
    else:
        with pikepdf.new() as scratch:
            copy = scratch.copy_foreign(img)
            for k in ("/SMask", "/Mask", "/SMaskInData"):
                if k in copy:
                    del copy[k]
            scratch.Root["/Img"] = scratch.make_indirect(copy)
            buf = io.BytesIO()
            scratch.save(buf)  # renumbers objects: the image is found again by the catalogue's key
        with pymupdf.open("pdf", buf.getvalue()) as doc:
            xref = int(doc.xref_get_key(doc.pdf_catalog(), "Img")[1].split()[0])
            pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        raise AppendError(f"image {img.objgen[0]} decodes with transparency that cannot be separated")
    return pix


def _resample_under_mask(mu, smask, pix, n, size):
    """Resample weighting each pixel by its opacity, so colour hidden under the transparent
    part of a soft mask (often black) does not bleed into the visible edge as a dark fringe.
    The soft mask itself is kept at full resolution, so the outline stays as sharp as it was."""
    alpha_pix = pymupdf.Pixmap(mu, smask.objgen[0])
    if alpha_pix.n != 1 or alpha_pix.alpha:
        raise AppendError(f"soft mask {smask.objgen[0]} is not a single grey channel")
    alpha = Image.frombuffer("L", (alpha_pix.width, alpha_pix.height), alpha_pix.samples, "raw", "L", 0, 1)
    if alpha.size != (pix.width, pix.height):
        alpha = alpha.resize((pix.width, pix.height), Image.LANCZOS)
    a = np.asarray(alpha, np.float32) / 255.0
    colour = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, n).astype(np.float32)
    weight = np.asarray(Image.fromarray(a, "F").resize(size, Image.LANCZOS))
    channels = []
    for c in range(n):
        num = np.asarray(Image.fromarray(colour[:, :, c] * a, "F").resize(size, Image.LANCZOS))
        plain = np.asarray(Image.fromarray(colour[:, :, c], "F").resize(size, Image.LANCZOS))
        channels.append(np.where(weight > 1e-3, num / np.maximum(weight, 1e-3), plain))  # fully hidden: any colour
    out = np.clip(np.rint(np.stack(channels, axis=2)), 0, 255).astype(np.uint8)
    return Image.fromarray(out[:, :, 0] if n == 1 else out, MODES[n])


def _build(pdf, mu, key, size):
    """The new image stream, or None if it would not be smaller."""
    img = pdf.get_object(key)
    d = img.stream_dict
    pix = _pixels(mu, img)
    n = _channels(d.ColorSpace)
    if pix.n != n:
        raise AppendError(f"image {key[0]} decodes with {pix.n} channels, not {n}")
    if "/SMask" in d:
        out = _resample_under_mask(mu, d.SMask, pix, n, size)
    else:
        src = Image.frombuffer(MODES[n], (pix.width, pix.height), pix.samples, "raw", MODES[n], 0, 1)
        out = src.resize(size, Image.LANCZOS)
        del src
    del pix
    jpeg = _is_jpeg(d)
    if jpeg:
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=jpeg_quality(img.read_raw_bytes()))
        data = buf.getvalue()
    else:
        data = zlib.compress(out.tobytes(), 6)
    if len(data) >= len(img.read_raw_bytes()):
        return None
    stream = pikepdf.Stream(pdf, data)
    for k in d.keys():
        if k not in ("/Length", "/Filter", "/DecodeParms", "/Decode", "/Width", "/Height"):
            stream[k] = d[k]
    stream["/Width"], stream["/Height"] = size
    stream["/Filter"] = pikepdf.Name.DCTDecode if jpeg else pikepdf.Name.FlateDecode
    if jpeg and n == 4:
        stream["/Decode"] = ADOBE_INVERTED  # Pillow's CMYK JPEG is stored inverted (Adobe's convention)
    return stream


def propose_downsample_images(path: str, options: Options | None = None, max_ppi=None, to_ppi=None, **_) -> Proposal:
    """What the fix would change. Reads only (no image is decoded)."""
    top, to = _settings(max_ppi, to_ppi)
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        targets, left, pages_of = _survey(pdf, top, to)
        per_page = {}
        for key, (w, h, lowest) in targets.items():
            d = pdf.get_object(key).stream_dict
            for n in pages_of[key]:
                per_page.setdefault(n, []).append((lowest, int(d.Width), int(d.Height), w, h))
    for page in sorted(per_page):
        items = sorted(per_page[page], reverse=True)
        top_item = items[0]
        text = (f"Downsamples {len(items)} image(s) placed over {top:g} ppi to {to:g} ppi; the highest is "
                f"{top_item[0]:.0f} ppi ({top_item[1]} × {top_item[2]} px, to {top_item[3]} × {top_item[4]} px).")
        if to < (options or Options()).min_ppi:
            text += (f" {to:g} ppi is under the Image resolution check's {(options or Options()).min_ppi:g} ppi, "
                     "so it will warn: fine for large format seen from a distance.")
        proposal.reasons.append((page, text))
    for page in sorted(left):
        for reason, n in left[page].items():
            proposal.skipped.append((page, f"{n} image(s) over {top:g} ppi left as they are: {reason}"))
    if targets:
        proposal.plan = {0: {"max_ppi": top, "to_ppi": to,
                             "targets": sorted([list(k), list(v[:2])] for k, v in targets.items())}}
    elif not left:
        proposal.skipped.append((1, f"no image is placed at over {top:g} ppi"))
    return proposal


CELL_MM, MAX_RENDER_PX = 0.7, 20_000_000


def _render(path, numbers):
    """Each page as the mean CMYK of ~0.7 mm cells, from a render of up to 144 dpi (fewer for huge pages)."""
    out = {}
    with pymupdf.open(path) as doc:
        for n in numbers:
            page = doc.load_page(n - 1)
            area_in = page.rect.width * page.rect.height / 72 / 72
            dpi = int(min(144, (MAX_RENDER_PX / max(area_in, 1e-6)) ** 0.5))
            k = max(1, round(dpi * CELL_MM / 25.4))
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csCMYK, alpha=False, annots=False)
            a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4)
            h, w = pix.height // k * k, pix.width // k * k
            out[n] = a[:h, :w].reshape(h // k, k, w // k, k, 4).mean(axis=(1, 3), dtype=np.float32)
            del pix, a
    return out


def _check(src, sizes, pages):
    def check(new_path):
        with pikepdf.open(src) as a, pikepdf.open(new_path) as b:
            for key, size in sizes.items():
                img = b.get_object(key)
                if (int(img.Width), int(img.Height)) != size:
                    raise AppendError(f"verification failed: image {key[0]} is not the new size")
            for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
                before, after = walk_page(pa).events, walk_page(pb).events
                if len(before) != len(after):
                    raise AppendError(f"verification failed: page {number} paints a different number of objects")
                for i, (x, y) in enumerate(zip(before, after)):
                    if (x.kind, x.paint, x.cs, x.colour, x.bbox, x.overprint, x.blend) != (
                            y.kind, y.paint, y.cs, y.colour, y.bbox, y.overprint, y.blend):
                        raise AppendError(f"verification failed: page {number}, object {i} changed")
        pymupdf.TOOLS.set_icc(False)
        with pymupdf.open(new_path) as doc:
            for key, size in sizes.items():
                pix = pymupdf.Pixmap(doc, key[0])
                if (pix.width, pix.height) != size:
                    raise AppendError(f"verification failed: image {key[0]} does not decode at its new size")
        before, after = _render(src, pages), _render(new_path, pages)
        for n in pages:
            diff = np.abs(before[n] - after[n]).max(axis=2)
            if before[n].shape != after[n].shape or (diff > 48).mean() > 0.001:
                raise AppendError(f"verification failed: page {n} does not look as it did")
    return check


def write_downsample_images(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan = proposal.plan[0]
    wanted = {tuple(k): tuple(s) for k, s in plan["targets"]}
    pymupdf.TOOLS.set_icc(False)
    with pikepdf.open(proposal.path) as pdf:
        targets, _, pages_of = _survey(pdf, plan["max_ppi"], plan["to_ppi"])
        if {k: v[:2] for k, v in targets.items()} != wanted:
            raise AppendError("the file changed between the proposal and the write")
        changes, sizes = {}, {}
        with pymupdf.open(proposal.path) as mu:
            for key, size in wanted.items():
                stream = _build(pdf, mu, key, size)
                if stream is not None:
                    changes[key] = stream
                    sizes[key] = size
        if not changes:
            raise AppendError("no image would get smaller; nothing was written")
        pages = sorted({n for k in changes for n in pages_of[k]})
        append_update(proposal.path, dst, changes, {}, extra_check=_check(proposal.path, sizes, pages))
