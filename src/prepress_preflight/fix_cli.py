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
from .actions import _step_parser, load
from .cli_common import add_step_options
from .engine import run
from .fixes import FIXES
from .fixes.chain import deliver, final_file, parse_chain, run_chain
from .model import FAIL, UNCHECKED, WARN
from .report import page_list


# Notes that a fix can be meant to change, so the preview shows them too.
NOTES_SHOWN = {"Spot colours", "Trim marks", "Page size"}


def _summary(result) -> set:
    return {(f.check, f.severity, f.message) for f in result.findings
            if f.severity in (FAIL, WARN, UNCHECKED) or f.check in NOTES_SHOWN}


HEADERS = {
    "spot-to-cmyk": "Proposed changes (the colour instructions for these spots are rewritten in CMYK; nothing else in the "
                    "artwork changes):",
    "rename-spot": "Proposed changes (only the spot colour definitions change; the artwork is not touched):",
    "scale": "Proposed changes (the artwork is scaled as a whole, not edited; page boxes and link positions scale with it):",
    "add-bleed": "Proposed changes (the original artwork inside the trim is not changed; bleed is added beneath it, "
                 "outside the trim, and any clip or trim marks asked for):",
    "add-marks": "Proposed changes (the artwork is not changed; trim marks are added outside the bleed box, "
                 "replacing any existing ones, and the clip if asked for):",
}


def _grouped(items, total):
    """Collapse identical lines that differ only by page: {text: [pages]} in first-seen order."""
    groups: dict = {}
    for page, text in items:
        groups.setdefault(text, []).append(page)
    return [(page_list(pages, total), text) for text, pages in groups.items()]


def _print_proposal(proposal, total, label=None) -> None:
    print(f"FIX  {proposal.fix}  {label or os.path.basename(proposal.path)}")
    for where, reason in _grouped(proposal.reasons, total):
        print(f"  {where}: {reason}")
    for where, why in _grouped(proposal.skipped, total):
        print(f"  {where}: left alone, {why}")
    if proposal.empty:
        print("\nNothing to change.")
        return
    print("\n" + HEADERS.get(proposal.fix, "Proposed changes (nothing else in the file is touched):"))
    lines = []
    for c in proposal.changes:
        old = "not set" if c.before is None else f"{c.before_mm}  {c.before}"
        lines.append((c.page, f"{c.box}\n      before: {old}\n      after:  {c.after_mm}  {c.after}"))
    for where, text in _grouped(lines, total):
        print(f"  {where}  {text}")


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


def _page_count(path) -> int:
    import pikepdf
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def _settings(ns):
    options = Options(min_bleed_mm=ns.min_bleed, expected_trim_mm=ns.trim, bound=ns.bound)
    extra = dict(method=ns.method, clip=ns.clip_to_bleed, marks=ns.marks, mode=ns.mode, spots=ns.spot,
                 target=ns.target, overprint=ns.overprint, expect=ns.expect, profile=ns.profile,
                 near_black=ns.near_black_to_k)
    return options, extra


def _step_settings(listed, args, argv):
    """(fix, Options, extra) for each step. Options typed on the command line are
    the job's defaults; a saved list's own options for a step come after and win."""
    # The job's options: everything typed except the fix name, the file, and
    # the options about writing, which belong to the whole run.
    job, skip_next, positionals = [], False, [args.name, args.file]
    for token in argv:
        if skip_next:
            skip_next = False
        elif token == "--write":
            skip_next = True
        elif token.startswith("--write="):
            continue
        elif token in ("--yes", "--no-preview"):
            continue
        elif token in positionals:
            positionals.remove(token)
        else:
            job.append(token)
    settings = []
    for name, own in listed:
        try:
            ns = _step_parser().parse_args(job + own)
        except (Exception, SystemExit) as exc:
            raise ValueError(f"options for {name}: {exc}") from None
        settings.append((name, *_settings(ns)))
    return settings


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="preflight fix",
                                     description="Propose a fix, or several joined with +, and write the result to a "
                                                 "new file only when asked.")
    parser.add_argument("name", help="which fix: " + ", ".join(sorted(FIXES)) +
                                     "; join several with + to run them in order, e.g. trim-from-marks+add-bleed; "
                                     "or @name for a saved action list (see: preflight actions)")
    parser.add_argument("file", help="the PDF to fix; it is never modified")
    parser.add_argument("--write", metavar="NEW.pdf", help="write the fixed copy to this new file")
    parser.add_argument("--yes", action="store_true", help="confirm without being asked (for scripts)")
    add_step_options(parser)
    parser.add_argument("--no-preview", action="store_true", help="skip running preflight before and after")
    args = parser.parse_args(argv)

    try:
        if args.name.startswith("@"):
            listed = load(args.name[1:])
        else:
            listed = [(name, []) for name in parse_chain(args.name, FIXES)]
        steps_in = _step_settings(listed, args, argv)
    except ValueError as exc:
        print(f"preflight fix: {exc}", file=sys.stderr)
        return 2
    if args.write and os.path.exists(args.write):
        print(f"preflight fix: {args.write} already exists; choose a new file name", file=sys.stderr)
        return 2
    try:
        total = _page_count(args.file)
    except Exception as exc:
        print(f"preflight fix: could not read {args.file}: {exc}", file=sys.stderr)
        return 2
    workdir = tempfile.mkdtemp(prefix="preflight-fix-")
    try:
        try:
            steps = run_chain(args.file, steps_in, FIXES, workdir)
        except Exception as exc:
            print(f"preflight fix: {exc}; nothing was written", file=sys.stderr)
            return 2
        for i, step in enumerate(steps, 1):
            if len(steps) > 1:
                print(("\n" if i > 1 else "") + f"STEP {i} of {len(steps)}" + (", on the result of the steps before" if i > 1 else ""))
            _print_proposal(step.proposal, total, os.path.basename(args.file))
        final = final_file(steps)
        if final is None:
            return 0
        if not args.no_preview:
            try:
                options = steps_in[0][1]
                _print_effect(run(args.file, options), run(final, options))
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
            what = "these changes" if len(steps) == 1 else f"all {len(steps)} steps"
            answer = input(f"\nWrite {what} to the new file {args.write}? The original is not changed. Type yes: ")
            if answer.strip().lower() != "yes":
                print("Nothing written.")
                return 1
        try:
            deliver(args.file, final, args.write)
        except Exception as exc:
            print(f"preflight fix: nothing written: {exc}", file=sys.stderr)
            return 2
        print(f"Written and verified: {args.write}")
        print("  The original's bytes are unchanged at the start of the new file; the changes were added after them.")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
