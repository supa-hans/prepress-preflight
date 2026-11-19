"""Several fixes in a row: an action list.

Each step is proposed on the result of the step before, and written (and
verified, as every fix is) to a temporary file. Nothing reaches the user's
folder until the whole chain has been shown and confirmed. Because every fix
appends to the file it is given, the final file is still the original's bytes,
unchanged, followed by each step's update in turn; that is checked again at the
end, together with the file's structure.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .append import AppendError, _same_prefix, syntax_problems


@dataclass
class Step:
    name: str
    proposal: object
    output: str | None  # the temporary file this step wrote, or None if it changed nothing


def parse_chain(text: str, known) -> list[str]:
    names = [n.strip() for n in text.split("+") if n.strip()]
    unknown = [n for n in names if n not in known]
    if not names or unknown:
        raise ValueError("unknown fix: " + ", ".join(unknown or [text]) + "; choose from " + ", ".join(sorted(known)))
    return names


def run_chain(path: str, settings, fixes, workdir: str) -> list[Step]:
    """Propose and write each step into `workdir`. Raises on the first failure.

    settings: [(fix name, Options, extra keyword options)] in order.
    """
    steps, current = [], path
    for i, (name, options, extra) in enumerate(settings, 1):
        propose, write = fixes[name]
        proposal = propose(current, options, **extra)
        output = None
        if not proposal.empty:
            output = os.path.join(workdir, f"step{i}.pdf")
            try:
                write(proposal, output)
            except Exception as exc:
                raise AppendError(f"step {i} ({name}): {exc}") from exc
            current = output
        steps.append(Step(name, proposal, output))
    return steps


def final_file(steps: list[Step]) -> str | None:
    outputs = [s.output for s in steps if s.output]
    return outputs[-1] if outputs else None


def deliver(original: str, final: str, dst: str, compact: bool = False) -> None:
    """Copy the chain's result to `dst`, after checking it once more as a whole.

    compact: write it as a fresh file holding only the objects still in use, so
    what the fixes replaced (RGB images after a conversion, say) is dropped. The
    stream data is copied byte for byte. The original's bytes are then no longer
    at the start, so the compact file is checked against the verified result
    instead: same pages, same objects painted, same rendering.
    """
    if os.path.exists(dst):
        raise AppendError(f"{dst} already exists; choose a new file name")
    if os.path.realpath(dst) == os.path.realpath(original):
        raise AppendError("the new file must not be the input file")
    if not _same_prefix(original, final, os.path.getsize(original)):
        raise AppendError("verification failed: the original bytes were not preserved through the chain")
    new = sorted(syntax_problems(final) - syntax_problems(original))
    if new:
        raise AppendError("verification failed: the result has structural problems: " + "; ".join(new[:3]))
    tmp = dst + ".partial"
    try:
        if compact:
            write_compact(final, tmp)
            verify_compact(final, tmp)
        else:
            shutil.copyfile(final, tmp)
            if not _same_prefix(final, tmp, os.path.getsize(final)) or os.path.getsize(tmp) != os.path.getsize(final):
                raise AppendError("verification failed: the copy does not match")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---- compact delivery -------------------------------------------------------------

COMPACT_DPI = 36  # resolution of the render comparison
RENDER_TOLERANCE = 8  # per channel, 0-255: MuPDF varies by a few levels between runs


def write_compact(src: str, dst: str) -> None:
    """A fresh copy of `src` with only the objects in use. Streams are copied as
    they are (not decoded and re-encoded)."""
    import pikepdf

    with pikepdf.open(src) as pdf:
        pdf.save(dst, stream_decode_level=pikepdf.StreamDecodeLevel.none,
                 object_stream_mode=pikepdf.ObjectStreamMode.preserve, linearize=False)


def verify_compact(full: str, compact: str) -> None:
    """The compact file must be the verified result, page for page: the same
    boxes, the same objects painted, the same rendering, and no new structural
    problems."""
    import numpy as np
    import pikepdf
    import pymupdf

    from ..content import walk_page

    new = sorted(syntax_problems(compact) - syntax_problems(full))
    if new:
        raise AppendError("verification failed: the compact file has structural problems: " + "; ".join(new[:3]))
    with pikepdf.open(full) as a, pikepdf.open(compact) as b:
        if len(a.pages) != len(b.pages):
            raise AppendError("verification failed: the compact file has a different number of pages")
        for number, (pa, pb) in enumerate(zip(a.pages, b.pages), 1):
            for box in ("/MediaBox", "/CropBox", "/TrimBox", "/BleedBox", "/Rotate", "/UserUnit"):
                if repr(pa.obj.get(box)) != repr(pb.obj.get(box)):
                    raise AppendError(f"verification failed: page {number}'s {box[1:]} differs in the compact file")
            ea, eb = walk_page(pa).events, walk_page(pb).events
            key = lambda e: (e.kind, e.paint, e.cs, e.colour, e.bbox, e.overprint, e.opm, e.blend, e.width, e.font, e.px)
            if [key(e) for e in ea] != [key(e) for e in eb]:
                raise AppendError(f"verification failed: page {number} paints different objects in the compact file")
    with pymupdf.open(full) as da, pymupdf.open(compact) as db:
        for i in range(len(da)):
            ra = da.load_page(i).get_pixmap(dpi=COMPACT_DPI, alpha=False, colorspace=pymupdf.csCMYK)
            rb = db.load_page(i).get_pixmap(dpi=COMPACT_DPI, alpha=False, colorspace=pymupdf.csCMYK)
            if (ra.width, ra.height) != (rb.width, rb.height):
                raise AppendError(f"verification failed: page {i + 1} renders at a different size in the compact file")
            diff = np.abs(np.frombuffer(ra.samples, np.uint8).astype(int) - np.frombuffer(rb.samples, np.uint8).astype(int))
            if diff.size and diff.max() > RENDER_TOLERANCE:
                raise AppendError(f"verification failed: page {i + 1} renders differently in the compact file")
