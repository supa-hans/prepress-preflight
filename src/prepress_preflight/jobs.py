"""Saved job settings: the check options for a kind of job, kept under a name.

A job is a plain text file holding the options as they would be typed after
`preflight`, on one or more lines. Lines starting with # are comments; the
first is shown as the job's description.

    # Coated sheetfed, 320% press limit
    --ink-limit 320 --safe-margin 3

Jobs live in ~/.config/prepress-preflight/jobs/, or the folder named by
PREFLIGHT_JOBS. Use one with `--job NAME` on preflight, preflight report,
preflight app and preflight fix. The job's options come first, so anything
typed on the command line as well wins.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# The check options a fix step also takes, and how many values each has.
STEP_OPTIONS = {"--trim": 1, "--min-bleed": 1, "--ink-limit": 1, "--bound": 0}


def jobs_dir() -> Path:
    return Path(os.environ.get("PREFLIGHT_JOBS") or Path.home() / ".config" / "prepress-preflight" / "jobs")


def _path(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError(f"'{name}' is not a usable name: use lower-case letters, digits, - and _")
    return jobs_dir() / f"{name}.txt"


def _parser():
    from .cli_common import add_check_options
    parser = argparse.ArgumentParser(prog="job", add_help=False, exit_on_error=False)
    add_check_options(parser)
    return parser


def parse_job(text: str, source: str = "the job") -> list[str]:
    """The option tokens in a job's text. Raises ValueError on any mistake."""
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tokens += shlex.split(line)
    if not tokens:
        raise ValueError(f"{source} sets no options")
    if "--job" in tokens or any(t.startswith("--job=") for t in tokens):
        raise ValueError(f"{source}: a job cannot name another job")
    try:
        _, unknown = _parser().parse_known_args(tokens)
    except (argparse.ArgumentError, SystemExit) as exc:
        raise ValueError(f"{source}: {exc}") from None
    if unknown:
        raise ValueError(f"{source}: not a check option: " + " ".join(unknown))
    return tokens


def load(name: str) -> list[str]:
    path = _path(name)
    if not path.exists():
        raise ValueError(f"no job '{name}' (looked for {path}); see: preflight jobs list")
    return parse_job(path.read_text(), str(path))


def description(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.strip().startswith("#"):
            return line.strip().lstrip("#").strip()
    return ""


def expand(argv: list[str], step_only: bool = False) -> tuple[list[str], str | None]:
    """argv with `--job NAME` replaced by the job's options, put first so typed ones win.
    step_only: keep only the options a fix step takes. Returns (argv, job name or None)."""
    out, name, i = [], None, 0
    while i < len(argv):
        token = argv[i]
        if token == "--job":
            if i + 1 >= len(argv):
                raise ValueError("--job needs a name")
            if name is not None:
                raise ValueError("give --job once")
            name = argv[i + 1]
            i += 2
            continue
        if token.startswith("--job="):
            if name is not None:
                raise ValueError("give --job once")
            name = token.split("=", 1)[1]
            i += 1
            continue
        out.append(token)
        i += 1
    if name is None:
        return out, None
    tokens = load(name)
    if step_only:
        tokens = _step_tokens(tokens)
    return tokens + out, name


def _step_tokens(tokens: list[str]) -> list[str]:
    kept, i = [], 0
    while i < len(tokens):
        flag = tokens[i].split("=", 1)[0]
        if flag in STEP_OPTIONS:
            n = 0 if "=" in tokens[i] else STEP_OPTIONS[flag]
            kept += tokens[i:i + 1 + n]
            i += 1 + n
        else:
            i += 1 + (1 if i + 1 < len(tokens) and not tokens[i + 1].startswith("--") else 0)
    return kept


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="preflight jobs", description="Manage saved job settings.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the saved jobs")
    show = sub.add_parser("show", help="print a saved job")
    show.add_argument("name")
    save = sub.add_parser("save", help='save a job, e.g. preflight jobs save coated -- --ink-limit 320 --safe-margin 3')
    save.add_argument("name")
    save.add_argument("--description", default="", help="a line describing the job")
    save.add_argument("--replace", action="store_true", help="replace a job of the same name")
    delete = sub.add_parser("delete", help="delete a saved job")
    delete.add_argument("name")
    argv = list(argv)
    options = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            folder = jobs_dir()
            jobs = sorted(folder.glob("*.txt")) if folder.exists() else []
            if not jobs:
                print(f"No saved jobs in {folder}")
            for path in jobs:
                text = description(path)
                tokens = " ".join(parse_job(path.read_text(), str(path)))
                print(f"{path.stem}: {tokens}" + (f"  ({text})" if text else ""))
            return 0
        if args.command == "show":
            path = _path(args.name)
            if not path.exists():
                raise ValueError(f"no job '{args.name}'")
            print(path.read_text(), end="")
            return 0
        if args.command == "save":
            path = _path(args.name)
            if path.exists() and not args.replace:
                raise ValueError(f"'{args.name}' already exists; add --replace to replace it")
            if not options:
                raise ValueError("give the options after --, e.g. preflight jobs save coated -- --ink-limit 320")
            text = (f"# {args.description}\n" if args.description else "") + shlex.join(options) + "\n"
            parse_job(text, f"'{args.name}'")  # check it before saving
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(f"Saved job {args.name} ({path})")
            return 0
        if args.command == "delete":
            path = _path(args.name)
            if not path.exists():
                raise ValueError(f"no job '{args.name}'")
            path.unlink()
            print(f"Deleted job {args.name}")
            return 0
    except ValueError as exc:
        print(f"preflight jobs: {exc}", file=sys.stderr)
        return 2
    return 2
