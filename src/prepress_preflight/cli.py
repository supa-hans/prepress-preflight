"""Command line: preflight file.pdf [--json] [--trim WxH] [--bound]

Also: preflight fix NAME file.pdf [--write new.pdf]  (see fix_cli.py)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .checks import Options
from .engine import run
from .cli_common import parse_trim
from .report import render


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["fix"]:
        from .fix_cli import main as fix_main
        return fix_main(argv[1:])
    if argv[:1] == ["actions"]:
        from .actions import main as actions_main
        return actions_main(argv[1:])
    parser = argparse.ArgumentParser(prog="preflight", description="Read-only preflight checks for print PDFs.")
    parser.add_argument("files", nargs="+", help="PDF file(s) to check")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    parser.add_argument("--trim", type=parse_trim, metavar="WxH", help="expected trim size in mm, e.g. 90x55")
    parser.add_argument("--min-bleed", type=float, default=3.0, metavar="MM", help="minimum bleed (default 3)")
    parser.add_argument("--safe-margin", type=float, default=1.5, metavar="MM",
                        help="keep text and loose artwork this far inside the trim (default 1.5)")
    parser.add_argument("--ink-limit", type=float, default=400.0, metavar="PCT",
                        help="total ink limit in percent (default 400, a placeholder until the press limit is known)")
    parser.add_argument("--bound", action="store_true",
                        help="bound job: spine edges need no bleed (page 1 is a right-hand page)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    options = Options(expected_trim_mm=args.trim, min_bleed_mm=args.min_bleed, bound=args.bound,
                      ink_limit_pct=args.ink_limit, safe_margin_mm=args.safe_margin)
    results, errors = [], 0
    for path in args.files:
        try:
            results.append(run(path, options))
        except Exception as exc:
            errors += 1
            print(f"preflight: could not read {path}: {exc}", file=sys.stderr)
    if args.json:
        payload = [r.to_dict() for r in results]
        print(json.dumps(payload[0] if len(args.files) == 1 and payload else payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(render(r) for r in results), end="")
    if errors:
        return 2
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
