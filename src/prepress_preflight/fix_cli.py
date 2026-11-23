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
from .fixes import FIXES, ONE_FILE_ONLY
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
    options = Options(min_bleed_mm=ns.min_bleed, expected_trim_mm=ns.trim, bound=ns.bound,
                      ink_limit_pct=ns.ink_limit if ns.ink_limit is not None else Options.ink_limit_pct)
    extra = dict(ink_limit=ns.ink_limit, method=ns.method, clip=ns.clip_to_bleed, marks=ns.marks, mode=ns.mode, spots=ns.spot,
                 target=ns.target, overprint=ns.overprint, expect=ns.expect, profile=ns.profile,
                 near_black=ns.near_black_to_k, replace=ns.replace, cmyk=ns.cmyk,
                 max_text=ns.max_text, max_line=ns.max_line, layer=ns.layer)
    return options, extra


def _step_settings(listed, args, argv):
    """(fix, Options, extra) for each step. Options typed on the command line are
    the job's defaults; a saved list's own options for a step come after and win."""
    # The job's options: everything typed except the fix name, the file, and
    # the options about writing, which belong to the whole run.
    job, skip_next, positionals = [], False, [args.name, *args.files]
    for token in argv:
        if skip_next:
            skip_next = False
        elif token in ("--write", "--out-dir"):
            skip_next = True
        elif token.startswith(("--write=", "--out-dir=")):
            continue
        elif token in ("--yes", "--no-preview", "--compact"):
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


def _batch(args, files, steps_in) -> int:
    """The same fixes on several PDFs: propose for all, one confirmation, a new file for each that changes."""
    if not files:
        print("preflight fix: no PDF files found", file=sys.stderr)
        return 2
    if args.write:
        print("preflight fix: --write is for one PDF; use --out-dir for several", file=sys.stderr)
        return 2
    single_object = sorted({name for name, _, _ in steps_in if name in ONE_FILE_ONLY})
    if single_object:
        print(f"preflight fix: {', '.join(single_object)} changes one object of one file, so it cannot run on several",
              file=sys.stderr)
        return 2
    if args.out_dir and os.path.exists(args.out_dir) and (not os.path.isdir(args.out_dir) or os.listdir(args.out_dir)):
        print(f"preflight fix: {args.out_dir} already exists and is not empty; choose a new folder", file=sys.stderr)
        return 2
    names = " + ".join(name for name, _, _ in steps_in)
    print(f"FIX  {names}  on {len(files)} PDF(s)\n")
    workdir = tempfile.mkdtemp(prefix="preflight-fix-")
    results, errors = [], 0  # (path, final temp file or None, message)
    try:
        for i, path in enumerate(files, 1):
            label = os.path.basename(path)
            try:
                folder = os.path.join(workdir, str(i))
                os.makedirs(folder)
                steps = run_chain(path, steps_in, FIXES, folder)
                final = final_file(steps)
                pages = sorted({page for s in steps for page, _ in s.proposal.reasons})
                left = sum(len(s.proposal.skipped) for s in steps if not s.proposal.empty)
                if final is None:
                    message = "no change"
                else:
                    total = _page_count(path)
                    where = "the file" if pages == [0] else page_list([p for p in pages if p], total)
                    message = f"changes {where}" + (f"; {left} thing(s) left as they are" if left else "")
                    if not args.no_preview:
                        options = steps_in[0][1]
                        message += f"; preflight {run(path, options).status} -> {run(final, options).status}"
            except Exception as exc:  # reported, never skipped quietly; the other files carry on
                errors += 1
                results.append((path, None, f"ERROR {type(exc).__name__}: {exc}"))
                print(f"  ERROR    {label}: {exc}")
                continue
            results.append((path, final, message))
            print(f"  {'CHANGE' if final else 'same':8} {label}: {message}")
        to_write = [(p, f) for p, f, _ in results if f]
        print(f"\n{len(to_write)} of {len(files)} would change" + (f"; {errors} could not be fixed" if errors else "")
              + ". For the details of one, run the fix on that file alone.")
        if not to_write:
            return 2 if errors else 0
        if not args.out_dir:
            print("Nothing written. To write the fixed copies: add --out-dir FOLDER")
            return 2 if errors else 0
        if not args.yes:
            if not sys.stdin.isatty():
                print("preflight fix: confirmation needed; run it in a terminal, or add --yes", file=sys.stderr)
                return 2
            answer = input(f"\nWrite {len(to_write)} fixed cop{'y' if len(to_write) == 1 else 'ies'} into {args.out_dir}? "
                           "The originals are not changed. Type yes: ")
            if answer.strip().lower() != "yes":
                print("Nothing written.")
                return 1
        os.makedirs(args.out_dir, exist_ok=True)
        written = 0
        for path, final in to_write:
            stem = os.path.splitext(os.path.basename(path))[0]
            dst = os.path.join(args.out_dir, f"{stem} fixed.pdf")
            n = 2
            while os.path.exists(dst):  # two inputs of the same name from different folders
                dst = os.path.join(args.out_dir, f"{stem} fixed {n}.pdf")
                n += 1
            try:
                deliver(path, final, dst, compact=args.compact)
                written += 1
                print(f"  written  {dst}")
            except Exception as exc:
                errors += 1
                print(f"  ERROR    {os.path.basename(path)}: nothing written: {exc}", file=sys.stderr)
        print(f"Written and verified: {written} file(s) in {args.out_dir}")
        return 2 if errors else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="preflight fix",
                                     description="Propose a fix, or several joined with +, and write the result to a "
                                                 "new file only when asked.")
    parser.add_argument("name", help="which fix: " + ", ".join(sorted(FIXES)) +
                                     "; join several with + to run them in order, e.g. trim-from-marks+add-bleed; "
                                     "or @name for a saved action list (see: preflight actions)")
    parser.add_argument("files", nargs="+", metavar="FILE_OR_FOLDER",
                        help="the PDF to fix, or several, or folders of them; they are never modified")
    parser.add_argument("--write", metavar="NEW.pdf", help="write the fixed copy to this new file (one PDF)")
    parser.add_argument("--out-dir", metavar="FOLDER",
                        help="several PDFs: write each fixed copy into this new (or empty) folder, as '<name> fixed.pdf'")
    parser.add_argument("--compact", action="store_true",
                        help="write the fixed copy as a fresh file without what the fixes replaced (smaller; the "
                             "original's bytes are then not kept at its start, and it is checked page by page instead)")
    parser.add_argument("--yes", action="store_true", help="confirm without being asked (for scripts)")
    add_step_options(parser)
    parser.add_argument("--no-preview", action="store_true", help="skip running preflight before and after")
    parser.add_argument("--job", metavar="NAME", help="use a saved job's --trim, --min-bleed, --bound and --ink-limit "
                                                     "(see: preflight jobs); options typed as well win")
    from .jobs import expand
    try:
        argv, _ = expand(list(argv), step_only=True)
    except ValueError as exc:
        print(f"preflight fix: {exc}", file=sys.stderr)
        return 2
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
    from .cli import _pdfs
    files = _pdfs(args.files)
    if len(files) > 1 or args.out_dir or any(os.path.isdir(f) for f in args.files):
        return _batch(args, files, steps_in)
    args.file = files[0]
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
            deliver(args.file, final, args.write, compact=args.compact)
        except Exception as exc:
            print(f"preflight fix: nothing written: {exc}", file=sys.stderr)
            return 2
        mb = lambda p: f"{os.path.getsize(p) / 1e6:.1f} MB"
        print(f"Written and verified: {args.write} ({mb(args.write)}; the original is {mb(args.file)})")
        if args.compact:
            print("  Compact: only the objects in use were written, and every page was checked against the full result.")
        else:
            print("  The original's bytes are unchanged at the start of the new file; the changes were added after them.")
            if os.path.getsize(args.write) > 1.25 * os.path.getsize(args.file):
                print("  It grew because the replaced originals are kept; --compact writes a smaller file without them.")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
