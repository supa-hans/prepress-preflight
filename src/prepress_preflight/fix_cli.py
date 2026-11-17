"""Command line for fixes: preflight fix NAME file.pdf [--write new.pdf] [--yes]

Without --write it only shows the proposal and its effect. With --write it
asks for confirmation (or takes --yes), writes the new file, verifies it, and
never touches the original.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

from .checks import Options
from .cli_common import parse_trim
from .engine import run
from .fixes import FIXES
from .model import FAIL, UNCHECKED, WARN


def _summary(result) -> set:
    return {(f.check, f.severity, f.message) for f in result.findings if f.severity in (FAIL, WARN, UNCHECKED)}


def _preview(proposal, options, write) -> tuple:
    """Preflight before, and after on a temporary copy that is deleted again."""
    before = run(proposal.path, options)
    folder = tempfile.mkdtemp(prefix="preflight-preview-")
    try:
        trial = os.path.join(folder, "preview.pdf")
        write(proposal, trial)
        after = run(trial, options)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return before, after


def _print_proposal(proposal) -> None:
    print(f"FIX  {proposal.fix}  {os.path.basename(proposal.path)}")
    for page, reason in proposal.reasons:
        print(f"  page {page}: {reason}")
    for page, why in proposal.skipped:
        print(f"  page {page}: left alone, {why}")
    if proposal.empty:
        print("\nNothing to change.")
        return
    print("\nProposed changes (nothing else in the file is touched):")
    for c in proposal.changes:
        old = "not set" if c.before is None else f"{c.before_mm}  {c.before}"
        print(f"  page {c.page}  {c.box}\n      before: {old}\n      after:  {c.after_mm}  {c.after}")


def _print_effect(before, after) -> None:
    old, new = _summary(before), _summary(after)
    print(f"\nEffect on preflight: {before.status} -> {after.status}")
    gone, added = sorted(old - new), sorted(new - old)
    # A finding still there with different figures is shown as changed, not as gone and new.
    for check, severity, message in gone:
        twin = next((m for c, s, m in added if (c, s) == (check, severity)), None)
        if twin is not None:
            added.remove((check, severity, twin))
            print(f"  changed: {severity:9s} {check}: {message}\n           now: {twin}")
        else:
            print(f"  gone:    {severity:9s} {check}: {message}")
    for check, severity, message in added:
        print(f"  new:     {severity:9s} {check}: {message}")
    if old == new:
        print("  no change to fails or warnings")


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="preflight fix",
                                     description="Propose a fix, and write it to a new file only when asked.")
    parser.add_argument("name", choices=sorted(FIXES), help="which fix")
    parser.add_argument("file", help="the PDF to fix; it is never modified")
    parser.add_argument("--write", metavar="NEW.pdf", help="write the fixed copy to this new file")
    parser.add_argument("--yes", action="store_true", help="confirm without being asked (for scripts)")
    parser.add_argument("--trim", type=parse_trim, metavar="WxH", help="set-boxes: the job's trim size in mm, e.g. 850x2000")
    parser.add_argument("--min-bleed", type=float, default=3.0, metavar="MM", help="bleed to give the new bleed box (default 3)")
    parser.add_argument("--no-preview", action="store_true", help="skip running preflight before and after")
    args = parser.parse_args(argv)

    options = Options(min_bleed_mm=args.min_bleed, expected_trim_mm=args.trim)
    propose, write = FIXES[args.name]
    try:
        proposal = propose(args.file, options)
    except Exception as exc:
        print(f"preflight fix: could not read {args.file}: {exc}", file=sys.stderr)
        return 2
    _print_proposal(proposal)
    if proposal.empty:
        return 0
    if args.write and os.path.exists(args.write):
        print(f"\npreflight fix: {args.write} already exists; choose a new file name", file=sys.stderr)
        return 2
    if not args.no_preview:
        try:
            _print_effect(*_preview(proposal, options, write))
        except Exception as exc:
            print(f"\npreflight fix: the preview failed ({exc}); nothing was written", file=sys.stderr)
            return 2
    if not args.write:
        print("\nNothing written. To write the fixed copy: add --write NEW.pdf")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("\npreflight fix: confirmation needed; run it in a terminal, or add --yes", file=sys.stderr)
            return 2
        answer = input(f"\nWrite these changes to the new file {args.write}? The original is not changed. Type yes: ")
        if answer.strip().lower() != "yes":
            print("Nothing written.")
            return 1
    try:
        write(proposal, args.write)
    except Exception as exc:
        print(f"preflight fix: nothing written: {exc}", file=sys.stderr)
        return 2
    print(f"Written and verified: {args.write}")
    print("  The original's bytes are unchanged at the start of the new file; only the changed page")
    print("  dictionaries were added after them.")
    return 0
