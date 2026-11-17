"""Command line: preflight file.pdf [--json] [--trim WxH] [--bound]"""
from __future__ import annotations

import argparse
import json
import re
import sys

from . import __version__
from .checks import Options
from .engine import run
from .report import render


def parse_trim(text: str) -> tuple[float, float]:
    m = re.fullmatch(r"\s*([\d.]+)\s*[xX×]\s*([\d.]+)\s*(mm)?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT in millimetres, for example 90x55")
    return float(m.group(1)), float(m.group(2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="preflight", description="Read-only preflight checks for print PDFs.")
    parser.add_argument("files", nargs="+", help="PDF file(s) to check")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    parser.add_argument("--trim", type=parse_trim, metavar="WxH", help="expected trim size in mm, e.g. 90x55")
    parser.add_argument("--min-bleed", type=float, default=3.0, metavar="MM", help="minimum bleed (default 3)")
    parser.add_argument("--ink-limit", type=float, default=400.0, metavar="PCT",
                        help="total ink limit in percent (default 400, a placeholder until the press limit is known)")
    parser.add_argument("--bound", action="store_true",
                        help="bound job: spine edges need no bleed (page 1 is a right-hand page)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    options = Options(expected_trim_mm=args.trim, min_bleed_mm=args.min_bleed, bound=args.bound,
                      ink_limit_pct=args.ink_limit)
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
