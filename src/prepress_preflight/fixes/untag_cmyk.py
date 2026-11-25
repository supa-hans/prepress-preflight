"""Untag CMYK: replace ICC-based CMYK colour spaces with plain DeviceCMYK.

    preflight fix untag-cmyk FILE --write UNTAGGED.pdf

Why: CMYK tagged with a profile (ICCBased, 4 components) tells a
colour-managing RIP which press the numbers were made for, and a RIP set to
manage colour converts them to its own press profile when the two differ. The
numbers then change: a K-only black can print in four inks. Untagged, the
numbers go to the plates as they are.

What changes: every place a colour space is named for painting, when it is
ICC-based CMYK (or an indexed space built on one) becomes DeviceCMYK:

  * colour spaces in resources (pages, forms, patterns, Type 3 fonts,
    annotation appearances),
  * images and gradients (shadings) that name their own colour space.

Left as they are: the alternate space of spot colours (it only says how a spot
would be simulated), transparency group spaces, and the output intent's
profile. Image data, content streams and colour values are not touched.

Verified: no painted object is left in tagged CMYK, and every page's separated
plates render identically before and after (with colour management off, as
all measuring here is done).
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pikepdf
import pymupdf

from ..checks.base import Options
from ..checks.cmyk_profiles import profile_name
from ..content import walk_page
from ..plates import render_channels
from .append import AppendError, append_update
from .base import Proposal

NAME = "untag-cmyk"
DEVICE_CMYK = pikepdf.Name.DeviceCMYK
SKIP_KEYS = {"/Group", "/OutputIntents", "/Parent", "/P"}  # group spaces and back links are not followed


def _icc_cmyk(value):
    """The profile stream when `value` is [/ICCBased <N 4 profile>], else None."""
    if isinstance(value, pikepdf.Array) and len(value) == 2 and str(value[0]) == "/ICCBased":
        profile = value[1]
        if isinstance(profile, pikepdf.Stream) and int(profile.get("/N", 0)) == 4:
            return profile
    return None


def _untag_space(value, profiles: Counter):
    """A colour space with ICC CMYK replaced (itself, or an indexed space's base); None when nothing changes."""
    profile = _icc_cmyk(value)
    if profile is not None:
        profiles[profile.objgen] += 1
        return DEVICE_CMYK
    if isinstance(value, pikepdf.Array) and len(value) == 4 and str(value[0]) == "/Indexed":
        profile = _icc_cmyk(value[1])
        if profile is not None and not value.is_indirect:
            profiles[profile.objgen] += 1
            return pikepdf.Array([value[0], DEVICE_CMYK, value[2], value[3]])
    return None


def _walk(obj, profiles: Counter, counts: Counter) -> bool:
    """Untag colour spaces inside one indirect object's own (direct) structure. True when anything changed."""
    changed = False
    if isinstance(obj, pikepdf.Stream):
        items = obj.stream_dict
    elif isinstance(obj, pikepdf.Dictionary):
        items = obj
    elif isinstance(obj, pikepdf.Array):
        for i, v in enumerate(obj):
            if not (isinstance(v, (pikepdf.Dictionary, pikepdf.Array)) and v.is_indirect):
                if isinstance(v, (pikepdf.Dictionary, pikepdf.Array)):
                    changed |= _walk(v, profiles, counts)
        return changed
    else:
        return False
    for key in list(items.keys()):
        if key in SKIP_KEYS:
            continue
        value = items[key]
        if key == "/ColorSpace":
            if isinstance(value, pikepdf.Dictionary) and not isinstance(value, pikepdf.Stream):
                if value.is_indirect:
                    continue  # an indirect colour-space dictionary is its own holder (see _survey)
                for name in list(value.keys()):
                    new = _untag_space(value[name], profiles)
                    if new is not None:
                        value[name] = new
                        counts["resource"] += 1
                        changed = True
                continue
            kind = "image" if obj.get("/Subtype") == "/Image" else "gradient" if "/ShadingType" in obj else None
            new = _untag_space(value, profiles) if kind else None  # elsewhere (DeviceN process colours) left as is
            if new is not None:
                items[key] = new
                counts[kind] += 1
                changed = True
            continue
        if isinstance(value, (pikepdf.Dictionary, pikepdf.Array)) and not value.is_indirect:
            changed |= _walk(value, profiles, counts)
    return changed


def _survey(pdf):
    """[(objgen, holder)] whose colour spaces change, the profiles involved, and what kind of places.
    The holders are changed in memory: the caller never saves `pdf`."""
    cs_dicts = _find_cs_dicts(pdf)
    holders, profiles, counts = [], Counter(), Counter()
    for obj in pdf.objects:
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)) or not obj.is_indirect:
            continue
        if isinstance(obj, pikepdf.Array):
            # An indirect indexed space built on ICC CMYK is its own holder.
            if len(obj) == 4 and str(obj[0]) == "/Indexed" and _icc_cmyk(obj[1]) is not None:
                profiles[_icc_cmyk(obj[1]).objgen] += 1
                obj[1] = DEVICE_CMYK
                counts["indexed"] += 1
                holders.append((obj.objgen, obj))
            continue
        if obj.get("/Type") == "/OutputIntent":
            continue
        # Indirect resources /ColorSpace dictionaries: every value is a colour space.
        if obj.objgen in cs_dicts:
            changed = False
            for name in list(obj.keys()):
                new = _untag_space(obj[name], profiles)
                if new is not None:
                    obj[name] = new
                    counts["resource"] += 1
                    changed = True
            if changed:
                holders.append((obj.objgen, obj))
            continue
        if _walk(obj, profiles, counts):
            holders.append((obj.objgen, obj))
    return holders, profiles, counts


def _find_cs_dicts(pdf) -> set:
    """Object numbers of indirect dictionaries referenced as a /ColorSpace resource dictionary."""
    out = set()
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary) and not isinstance(obj, pikepdf.Stream):
            cs = obj.get("/ColorSpace")
            if isinstance(cs, pikepdf.Dictionary) and not isinstance(cs, pikepdf.Stream) and cs.is_indirect:
                out.add(cs.objgen)
    return out


def propose_untag_cmyk(path: str, options: Options | None = None, **_) -> Proposal:
    """What the fix would change. Reads only (changes are made to an in-memory copy and discarded)."""
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        holders, profiles, counts = _survey(pdf)
        names = sorted({profile_name(pdf, og) for og in profiles})
    if not holders:
        proposal.skipped.append((1, "nothing painted in CMYK is tagged with a profile"))
        return proposal
    where = []
    if counts["resource"] or counts["indexed"]:
        where.append(f"{counts['resource'] + counts['indexed']} colour space(s) named in resources or palettes")
    if counts["image"]:
        where.append(f"{counts['image']} image(s)")
    if counts["gradient"]:
        where.append(f"{counts['gradient']} gradient(s)")
    proposal.reasons.append((1, f"Removes the profile tag ({', '.join(f'“{n}”' for n in names)}) from CMYK: "
                                f"{', '.join(where)}. Every CMYK number stays exactly as it is; a colour-managing "
                                "RIP then prints them as they are instead of converting them to its own press."))
    proposal.plan = {0: {"objects": sorted(list(og) for og, _ in holders)}}
    return proposal


def _plates(path):
    pymupdf.TOOLS.set_icc(False)
    with pymupdf.open(path) as doc:
        return [render_channels(page, 36 / 72) for page in doc]


def _check(src):
    def check(new_path):
        with pikepdf.open(new_path) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                if any(e.cs.kind == "cmyk" and e.cs.profile is not None for e in walk_page(page).events):
                    raise AppendError(f"verification failed: page {number} still paints tagged CMYK")
        before, after = _plates(src), _plates(new_path)
        for number, ((na, a), (nb, b)) in enumerate(zip(before, after), 1):
            if na != nb or a.shape != b.shape or not np.array_equal(a, b):
                raise AppendError(f"verification failed: page {number}'s plates do not render the same")
    return check


def write_untag_cmyk(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    with pikepdf.open(proposal.path) as pdf:
        holders, _, _ = _survey(pdf)
        if sorted(list(og) for og, _ in holders) != proposal.plan[0]["objects"]:
            raise AppendError("the file changed between the proposal and the write")
        append_update(proposal.path, dst, {og: obj for og, obj in holders}, {}, extra_check=_check(proposal.path))
