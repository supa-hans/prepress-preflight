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


def deliver(original: str, final: str, dst: str) -> None:
    """Copy the chain's result to `dst`, after checking it once more as a whole."""
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
        shutil.copyfile(final, tmp)
        if not _same_prefix(final, tmp, os.path.getsize(final)) or os.path.getsize(tmp) != os.path.getsize(final):
            raise AppendError("verification failed: the copy does not match")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
