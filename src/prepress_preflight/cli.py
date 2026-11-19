"""Command line: preflight file.pdf [--json] [--trim WxH] [--bound]

Also: preflight fix NAME file.pdf [--write new.pdf]  (see fix_cli.py)
      preflight report file.pdf [--out report.html]   (an HTML report to keep or send)
"""
from __future__ import annotations

import argparse
import json
import os
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


def _pdfs(inputs) -> list[str]:
    """The PDFs named, and those directly inside any folders named, in order, once each."""
    out, seen = [], set()
    for item in inputs:
        found = ([os.path.join(item, n) for n in sorted(os.listdir(item), key=str.lower)
                  if n.lower().endswith(".pdf") and not n.startswith(".")] if os.path.isdir(item) else [item])
        for path in found:
            key = os.path.realpath(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
    return out


def _default_reports_folder(first_input: str) -> str:
    """'preflight reports <date time>': inside the folder given, or beside the first PDF."""
    import datetime
    where = os.path.abspath(first_input) if os.path.isdir(first_input) else os.path.dirname(os.path.abspath(first_input))
    return os.path.join(where, f"preflight reports {datetime.datetime.now():%Y-%m-%d %H.%M}")


def report_main(argv) -> int:
    """preflight report file.pdf [--out report.html]: check, and write the report as one HTML file.
    preflight report a.pdf b.pdf folder/ [--out-dir DIR]: a report for each, and a summary (index.html)."""
    from .report_html import build_index, default_report_path, report_name, write_report
    parser = argparse.ArgumentParser(prog="preflight report",
                                     description="Check PDFs and write the findings as HTML to keep or send. "
                                                 "With several files or a folder, each gets a report and a summary page "
                                                 "lists them all. The PDFs are only read.")
    parser.add_argument("inputs", nargs="+", metavar="PDF_OR_FOLDER", help="PDF files, or folders of them")
    parser.add_argument("--out", metavar="REPORT.html",
                        help="one file: where to write its report (default: '<name> preflight report.html' beside it)")
    parser.add_argument("--out-dir", metavar="FOLDER",
                        help="several files: a new folder for the reports and the summary (default: "
                             "'preflight reports <date time>' beside the first input)")
    add_check_options(parser)
    args = parser.parse_args(argv)
    options = options_from(args)
    files = _pdfs(args.inputs)
    if not files:
        print("preflight report: no PDF files found", file=sys.stderr)
        return 2
    single = len(files) == 1 and not any(os.path.isdir(i) for i in args.inputs) and not args.out_dir
    if single:
        try:
            result = run(files[0], options)
            dst = write_report(result, args.out or default_report_path(files[0]), options)
        except Exception as exc:
            print(f"preflight report: {exc}", file=sys.stderr)
            return 2
        print(f"{result.status}: report written to {dst}")
        return 1 if result.status == "FAIL" else 0
    if args.out:
        print("preflight report: --out is for one file; use --out-dir for several", file=sys.stderr)
        return 2
    folder = args.out_dir or _default_reports_folder(args.inputs[0])
    if os.path.exists(folder) and os.listdir(folder):
        print(f"preflight report: {folder} already exists and is not empty; choose a new folder", file=sys.stderr)
        return 2
    os.makedirs(folder, exist_ok=True)
    entries, worst = [], 0
    for path in files:
        try:
            result = run(path, options)
            dst = write_report(result, report_name(folder, path), options)
            entries.append({"path": path, "result": result, "report": os.path.basename(dst)})
            print(f"{result.status:5} {os.path.basename(path)}")
            worst = max(worst, 1 if result.status == "FAIL" else 0)
        except Exception as exc:  # listed in the summary as not checked, never skipped
            entries.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
            print(f"ERROR {os.path.basename(path)}: {exc}", file=sys.stderr)
            worst = 2
    index = os.path.join(folder, "index.html")
    with open(index, "x", encoding="utf-8") as f:
        f.write(build_index(entries, options))
    print(f"Summary of {len(files)} files: {index}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
