"""Set the file's output intent: the print condition its CMYK was made for.

After rgb-to-cmyk converts to Coated FOGRA39, the file's CMYK numbers mean
"FOGRA39"; an output intent says so to a RIP or a PDF/X workflow. This embeds
the profile (by default the same FOGRA39 profile; --profile as for
rgb-to-cmyk) with the condition's identifier from the ICC registry. It does not
claim the file is PDF/X, which needs much more than an output intent.

Only the document catalogue changes (and the profile is added). A file that
already states an output intent is left alone, and said so, unless --replace
is given.
"""
from __future__ import annotations

import os
import zlib

import pikepdf

from ..checks.base import Options
from .append import AppendError, append_update
from .base import Proposal
from .rgb_to_cmyk import Engine, find_profile

NAME = "set-output-intent"
REGISTRY = "http://www.color.org"
# Identifiers as the ICC characterization registry and Adobe's PDF/X settings give them.
KNOWN = {
    "CoatedFOGRA39.icc": ("FOGRA39", "Coated FOGRA39 (ISO 12647-2:2004)"),
    "CoatedGRACoL2006.icc": ("CGATS TR 006", "Coated GRACoL 2006 (ISO 12647-2:2004)"),
    "USWebCoatedSWOP.icc": ("CGATS TR 001", "U.S. Web Coated (SWOP) v2"),
}


def _condition(profile_path: str, description: str) -> tuple[str, str]:
    known = KNOWN.get(os.path.basename(profile_path))
    return known if known else ("Custom", description)


def _current(pdf) -> list[str]:
    out = []
    for intent in pdf.Root.get("/OutputIntents", []):
        ident = str(intent.get("/OutputConditionIdentifier", "")).strip()
        info = str(intent.get("/Info", "")).strip()
        out.append(info or ident or "unnamed")
    return out


def propose_set_output_intent(path: str, options: Options | None = None, profile=None, replace=False, **_) -> Proposal:
    """What the fix would change. Reads only."""
    profile_path = find_profile(profile)
    engine = Engine(profile_path)  # also checks it is a CMYK profile that can be read
    ident, info = _condition(profile_path, engine.name)
    proposal = Proposal(NAME, path)
    with pikepdf.open(path) as pdf:
        current = _current(pdf)
    if current and not replace:
        proposal.skipped.append((1, f"the file already states an output intent ({'; '.join(current)}); "
                                    "--replace sets the new one instead"))
        return proposal
    if current and any(info == c or ident == c for c in current) and len(current) == 1:
        proposal.skipped.append((1, f"the file already states {current[0]}"))
        return proposal
    text = f"Sets the output intent to {info} ({ident}), with the profile embedded."
    if current:
        text += f" Replaces: {'; '.join(current)}."
    proposal.reasons.append((1, text + " Only the document catalogue changes."))
    proposal.plan = {0: {"profile": profile_path, "identifier": ident, "info": info}}
    return proposal


def _check(identifier, data):
    def check(new_path):
        with pikepdf.open(new_path) as pdf:
            intents = list(pdf.Root.get("/OutputIntents", []))
            if len(intents) != 1 or str(intents[0].get("/OutputConditionIdentifier")) != identifier:
                raise AppendError("verification failed: the output intent is not the one set")
            if intents[0].DestOutputProfile.read_bytes() != data:
                raise AppendError("verification failed: the embedded profile is not the one chosen")
    return check


def write_set_output_intent(proposal: Proposal, dst: str) -> None:
    """Write the proposal to a new file `dst`. The original is never written to."""
    if proposal.empty:
        raise ValueError("nothing to change")
    plan = proposal.plan[0]
    with open(plan["profile"], "rb") as f:
        data = f.read()
    with pikepdf.open(proposal.path) as pdf:
        stream = pikepdf.Stream(pdf, zlib.compress(data, 9))
        stream["/Filter"] = pikepdf.Name.FlateDecode
        stream["/N"] = 4
        stream = pdf.make_indirect(stream)
        intent = pikepdf.Dictionary(Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFX,
                                    OutputConditionIdentifier=pikepdf.String(plan["identifier"]),
                                    Info=pikepdf.String(plan["info"]), RegistryName=pikepdf.String(REGISTRY),
                                    DestOutputProfile=stream)
        root = pdf.Root
        copy = pikepdf.Dictionary({k: root[k] for k in root.keys()})
        copy["/OutputIntents"] = pikepdf.Array([intent])
        append_update(proposal.path, dst, {root.objgen: copy}, {stream.objgen: stream},
                      extra_check=_check(plan["identifier"], data))
