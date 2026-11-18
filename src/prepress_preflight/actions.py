"""Saved action lists: a chain of fixes kept under a name.

A list is a plain text file, one step per line: the fix's name and that step's
own options, as they would be typed after `preflight fix`. Lines starting with
# are comments; the first comment is shown as the list's description.

    # Canva files: bleed, clip and marks
    add-bleed --clip-to-bleed --marks

Lists live in ~/.config/prepress-preflight/actions/, or the folder named by
PREFLIGHT_ACTIONS. Run one with `preflight fix @name file.pdf`.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path

from .cli_common import add_step_options
from .fixes import FIXES

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def actions_dir() -> Path:
    return Path(os.environ.get("PREFLIGHT_ACTIONS") or Path.home() / ".config" / "prepress-preflight" / "actions")


def _path(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError(f"'{name}' is not a usable name: use lower-case letters, digits, - and _")
    return actions_dir() / f"{name}.txt"


def _step_parser():
    parser = argparse.ArgumentParser(prog="step", add_help=False, exit_on_error=False)
    add_step_options(parser)
    return parser


def parse_steps(text: str, source: str = "the list") -> list[tuple[str, list[str]]]:
    """[(fix name, its option tokens)] from the text of a list. Raises ValueError on any mistake."""
    steps = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = shlex.split(line)
        name, options = tokens[0], tokens[1:]
        if name not in FIXES:
            raise ValueError(f"{source}, line {number}: unknown fix '{name}'; choose from " + ", ".join(sorted(FIXES)))
        try:
            _, unknown = _step_parser().parse_known_args(options)
        except (argparse.ArgumentError, SystemExit) as exc:
            raise ValueError(f"{source}, line {number}: {exc}") from None
        if unknown:
            raise ValueError(f"{source}, line {number}: not an option for a step: " + " ".join(unknown))
        steps.append((name, options))
    if not steps:
        raise ValueError(f"{source} has no steps")
    return steps


def load(ref: str) -> list[tuple[str, list[str]]]:
    """Steps from a saved list by name, or from a file path."""
    path = Path(ref) if ("/" in ref or ref.endswith(".txt")) else _path(ref)
    if not path.exists():
        raise ValueError(f"no action list '{ref}' (looked for {path})")
    return parse_steps(path.read_text(), str(path))


def description(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.strip().startswith("#"):
            return line.strip().lstrip("#").strip()
    return ""


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="preflight actions", description="Manage saved action lists.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the saved action lists")
    show = sub.add_parser("show", help="print a saved list")
    show.add_argument("name")
    save = sub.add_parser("save", help="save a list: one quoted step per argument")
    save.add_argument("name")
    save.add_argument("steps", nargs="+", metavar="STEP", help='e.g. "trim-from-marks" "add-bleed --marks"')
    save.add_argument("--description", default="", help="a line describing what the list is for")
    save.add_argument("--replace", action="store_true", help="replace a list of the same name")
    delete = sub.add_parser("delete", help="delete a saved list")
    delete.add_argument("name")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            folder = actions_dir()
            lists = sorted(folder.glob("*.txt")) if folder.exists() else []
            if not lists:
                print(f"No saved action lists in {folder}")
            for path in lists:
                text = description(path)
                print(f"@{path.stem}" + (f"  {text}" if text else ""))
            return 0
        if args.command == "show":
            path = _path(args.name)
            if not path.exists():
                raise ValueError(f"no action list '{args.name}'")
            print(path.read_text(), end="")
            return 0
        if args.command == "save":
            path = _path(args.name)
            if path.exists() and not args.replace:
                raise ValueError(f"'{args.name}' already exists; add --replace to replace it")
            text = (f"# {args.description}\n" if args.description else "") + "\n".join(args.steps) + "\n"
            parse_steps(text, f"'{args.name}'")  # check it before saving
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(f"Saved @{args.name} ({path})")
            return 0
        if args.command == "delete":
            path = _path(args.name)
            if not path.exists():
                raise ValueError(f"no action list '{args.name}'")
            path.unlink()
            print(f"Deleted @{args.name}")
            return 0
    except ValueError as exc:
        print(f"preflight actions: {exc}", file=sys.stderr)
        return 2
    return 2
