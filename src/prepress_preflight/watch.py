"""A watched folder: PDFs dropped in are checked (and fixed, if asked) on their own.

    preflight watch IN --out OUT [--fix @list | NAME[+NAME]] [--job NAME] [check options]

Every PDF directly inside IN gets a preflight report in OUT/reports. With
--fix, each file the fixes change also gets a fixed copy in OUT/fixed
("<name> fixed.pdf", verified as every fix is) and a report on that copy.

The PDFs in IN are only read: never moved, renamed or changed. A file is taken
once it has not been written to for --settle seconds (so a copy still arriving
is left until it is complete), and once only: what has been done is kept in
OUT/.preflight-watch.json by name, size and modification time, so a file
replaced with a new version is taken again. Each file's outcome is a line in
OUT/log.txt. A file that cannot be read or fixed is logged as an ERROR, never
skipped quietly, and the others carry on.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

from .engine import run
from .fixes import FIXES, ONE_FILE_ONLY
from .fixes.chain import deliver, final_file, parse_chain, run_chain
from .report_html import report_name, write_report

STATE = ".preflight-watch.json"


class Watcher:
    def __init__(self, in_dir: str, out_dir: str, options, steps=None, compact: bool = False, settle: float = 5.0):
        self.in_dir, self.out_dir = os.path.abspath(in_dir), os.path.abspath(out_dir)
        if not os.path.isdir(self.in_dir):
            raise ValueError(f"{in_dir} is not a folder")
        if self.out_dir == self.in_dir:
            raise ValueError("the output folder must not be the watched folder")
        self.options, self.steps, self.compact, self.settle = options, steps, compact, settle
        for sub in ("reports", "fixed") if steps else ("reports",):
            os.makedirs(os.path.join(self.out_dir, sub), exist_ok=True)
        self.state_path = os.path.join(self.out_dir, STATE)
        try:
            with open(self.state_path) as f:
                self.done = json.load(f)
        except (OSError, ValueError):
            self.done = {}

    def _save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.done, f, indent=1)
        os.replace(tmp, self.state_path)

    def _log(self, line: str):
        stamp = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
        with open(os.path.join(self.out_dir, "log.txt"), "a", encoding="utf-8") as f:
            f.write(f"{stamp}  {line}\n")
        print(f"{stamp}  {line}", flush=True)

    def waiting(self, now: float | None = None) -> list[str]:
        """The PDFs ready to take: new or replaced, and settled."""
        now = time.time() if now is None else now
        ready = []
        for name in sorted(os.listdir(self.in_dir), key=str.lower):
            path = os.path.join(self.in_dir, name)
            if name.startswith(".") or not name.lower().endswith(".pdf") or not os.path.isfile(path):
                continue
            st = os.stat(path)
            if self.done.get(name) == [st.st_size, st.st_mtime_ns]:
                continue
            if now - st.st_mtime < self.settle:
                continue  # still being written, or only just arrived
            ready.append(path)
        return ready

    def take(self, path: str) -> str:
        """Check (and fix) one PDF. Returns the log line."""
        name = os.path.basename(path)
        st = os.stat(path)
        line = name + ": "
        try:
            result = run(path, self.options)
            report = write_report(result, report_name(os.path.join(self.out_dir, "reports"), path), self.options)
            line += f"{result.status}, report {os.path.relpath(report, self.out_dir)}"
            if self.steps:
                work = tempfile.mkdtemp(prefix="preflight-watch-")
                try:
                    final = final_file(run_chain(path, self.steps, FIXES, work))
                    if final is None:
                        line += "; fixes: no change"
                    else:
                        dst = _unique(os.path.join(self.out_dir, "fixed"), os.path.splitext(name)[0] + " fixed.pdf")
                        deliver(path, final, dst, compact=self.compact)
                        fixed = run(dst, self.options)
                        write_report(fixed, report_name(os.path.join(self.out_dir, "reports"), dst), self.options)
                        line += f"; fixed copy {os.path.relpath(dst, self.out_dir)}: {fixed.status}"
                finally:
                    shutil.rmtree(work, ignore_errors=True)
        except Exception as exc:  # logged, never skipped quietly; the file is taken again if it is replaced
            line += f"ERROR {type(exc).__name__}: {exc}"
        self.done[name] = [st.st_size, st.st_mtime_ns]
        self._save()
        self._log(line)
        return line

    def scan(self) -> list[str]:
        return [self.take(path) for path in self.waiting()]


def _unique(folder: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    candidate, n = os.path.join(folder, filename), 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem} {n}{ext}")
        n += 1
    return candidate


def steps_for(spec: str, step_tokens: list[str]):
    """[(fix, Options, extra)] for --fix: a chain of names or @list, with the job's step options as defaults."""
    from .actions import _step_parser, load
    from .fix_cli import _settings
    listed = load(spec[1:]) if spec.startswith("@") else [(n, []) for n in parse_chain(spec, FIXES)]
    out = []
    for name, own in listed:
        if name in ONE_FILE_ONLY:
            raise ValueError(f"{name} changes one object of one file, so it cannot run on a watched folder")
        try:
            ns = _step_parser().parse_args(step_tokens + own)
        except (Exception, SystemExit) as exc:
            raise ValueError(f"options for {name}: {exc}") from None
        out.append((name, *_settings(ns)))
    return out


def main(argv) -> int:
    from .cli_common import add_check_options, options_from
    from .jobs import _step_tokens, expand
    parser = argparse.ArgumentParser(prog="preflight watch",
                                     description="Check (and fix) every PDF dropped into a folder. The PDFs are only read.")
    parser.add_argument("folder", help="the folder to watch")
    parser.add_argument("--out", required=True, metavar="FOLDER", help="where reports, fixed copies and the log go")
    parser.add_argument("--fix", metavar="NAME|@LIST", help="fixes to run on each file, e.g. @canva-press")
    parser.add_argument("--compact", action="store_true", help="write fixed copies compact (see preflight fix)")
    parser.add_argument("--every", type=float, default=5.0, metavar="SECONDS", help="how often to look (default 5)")
    parser.add_argument("--settle", type=float, default=5.0, metavar="SECONDS",
                        help="take a file once it has not changed for this long (default 5)")
    parser.add_argument("--once", action="store_true", help="take what is ready now, then stop")
    add_check_options(parser)
    try:
        argv, job = expand(list(argv))
        args = parser.parse_args(argv)
        options = options_from(args, job)
        steps = steps_for(args.fix, _step_tokens(argv)) if args.fix else None
        watcher = Watcher(args.folder, args.out, options, steps, args.compact, args.settle)
    except ValueError as exc:
        print(f"preflight watch: {exc}", file=sys.stderr)
        return 2
    if args.once:
        lines = watcher.scan()
        return 2 if any("ERROR" in line for line in lines) else 0
    print(f"Watching {watcher.in_dir}; results in {watcher.out_dir}. Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            watcher.scan()
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0
