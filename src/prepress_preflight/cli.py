"""Command line: preflight file.pdf [--json] [--trim WxH] [--bound]

Also: preflight fix NAME file.pdf [--write new.pdf]  (see fix_cli.py)
      preflight report file.pdf [--out report.html]   (an HTML report to keep or send)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .engine import run
from .cli_common import add_check_options, options_from
from .report import render


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["fix"]:
        from .fix_cli import main as fix_main
        return fix_main(argv[1:])
    if argv[:1] == ["app"]:
        from .app.server import main as app_main
        return app_main(argv[1:])
    if argv[:1] == ["report"]:
        return report_main(argv[1:])
    if argv[:1] == ["actions"]:
        from .actions import main as actions_main
        return actions_main(argv[1:])
    parser = argparse.ArgumentParser(prog="preflight", description="Read-only preflight checks for print PDFs.")
    parser.add_argument("files", nargs="+", help="PDF file(s) to check")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    add_check_options(parser)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    options = options_from(args)
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


def report_main(argv) -> int:
    """preflight report file.pdf [--out report.html]: check, and write the report as one HTML file."""
    from .report_html import default_report_path, write_report
    parser = argparse.ArgumentParser(prog="preflight report",
                                     description="Check a PDF and write the findings as one HTML file to keep or send. "
                                                 "The PDF is only read.")
    parser.add_argument("file", help="the PDF to check")
    parser.add_argument("--out", metavar="REPORT.html",
                        help="where to write the report (default: '<name> preflight report.html' beside the PDF)")
    add_check_options(parser)
    args = parser.parse_args(argv)
    options = options_from(args)
    try:
        result = run(args.file, options)
        dst = write_report(result, args.out or default_report_path(args.file), options)
    except Exception as exc:
        print(f"preflight report: {exc}", file=sys.stderr)
        return 2
    print(f"{result.status}: report written to {dst}")
    return 1 if result.status == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
