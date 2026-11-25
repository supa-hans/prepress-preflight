"""The local app: a small web server on this computer, and the pages it serves.

`preflight app [file.pdf]` starts it and opens a browser window. It answers
only on 127.0.0.1, only to requests carrying the key it printed (so other web
pages open in the same browser cannot drive it), and only when the Host header
names this machine (so a website cannot reach it through DNS tricks). Nothing
is uploaded anywhere: files are read where they are on disk.

Inspect is read-only: it opens a file, runs the same checks as the command
line, and renders pages. Propose runs a fix's proposal and writes the result,
verified as always, to a private temporary folder so that before and after can
be compared; nothing is written anywhere the user keeps files, and the
temporary file is deleted when the next preview starts, another file is
opened, or the app stops.
"""
from __future__ import annotations

import dataclasses
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymupdf

import shlex
import shutil
import tempfile

from ..actions import _step_parser
from ..checks import Options
from ..document import device_matrix
from ..engine import run
from ..fix_cli import _settings
from ..jobs import job_options, list_jobs
from ..fixes import FIXES
from ..fixes.chain import deliver, run_chain
from ..model import FAIL, UNCHECKED, WARN
from ..objects import object_details, objects_at
from ..plates import ink_coverage, page_inks, render_plate
from ..content import walk_page
import pikepdf

STATIC = Path(__file__).parent / "static"
RECENT_MAX = 12


def _settings_text(o: Options) -> str:
    return (f"{o.min_bleed_mm:g} mm bleed, {o.safe_margin_mm:g} mm safe margin, {o.ink_limit_pct:g}% total ink"
            + (", process colours only" if o.cmyk_only else "") + (", bound" if o.bound else "")
            + (f", job size {o.expected_trim_mm[0]:g} × {o.expected_trim_mm[1]:g} mm" if o.expected_trim_mm else ""))


def recent_file() -> Path:
    """Where the list of recently opened files is kept: paths only, on this computer."""
    base = os.environ.get("PREFLIGHT_CONFIG") or Path.home() / ".config" / "prepress-preflight"
    return Path(base) / "recent.json"


def load_recent() -> list[dict]:
    try:
        paths = json.loads(recent_file().read_text())
    except (OSError, ValueError):
        return []
    return [{"path": p, "name": os.path.basename(p), "exists": os.path.isfile(p)}
            for p in paths if isinstance(p, str)][:RECENT_MAX]


def remember(path: str) -> None:
    try:
        old = json.loads(recent_file().read_text())
    except (OSError, ValueError):
        old = []
    paths = [path] + [p for p in old if isinstance(p, str) and p != path]
    try:
        recent_file().parent.mkdir(parents=True, exist_ok=True)
        recent_file().write_text(json.dumps(paths[:RECENT_MAX], indent=1))
    except OSError:
        pass  # a list that cannot be saved is not worth stopping for
MAX_PIXELS = 16_000_000  # the largest page render the app will make
pymupdf.TOOLS.set_icc(False)  # show the file's own CMYK values, as the checks do
# PyMuPDF is not safe to use from several threads at once; renders take turns.
# Re-entrant, so a function that takes it may call another that does too.
RENDER_LOCK = threading.RLock()


STEP_FIELDS = {"--trim": "expected_trim_mm", "--min-bleed": "min_bleed_mm", "--bound": "bound",
               "--ink-limit": "ink_limit_pct"}


def check_options(base: Options, tokens, step: Options) -> Options:
    """The options to check a fix's result with: the app's own (its job), with
    only what the fix's options say explicitly put on top, so before and after
    are judged alike."""
    typed = {t.split("=", 1)[0] for t in tokens}
    return dataclasses.replace(base, **{f: getattr(step, f) for flag, f in STEP_FIELDS.items() if flag in typed})


class Session:
    """The file that is open, its report, and cached page renders. One per app."""

    def __init__(self, options: Options):
        self.start_options = options  # as the app was started; choosing no job goes back to these
        self.options = options
        self.lock = threading.Lock()
        self.path: str | None = None
        self.report: dict | None = None
        self.error: str | None = None
        self.busy = False
        self._renders: dict = {}
        self.workdir = tempfile.mkdtemp(prefix="preflight-app-")
        self.preview: dict | None = None  # the current proposal and its effect
        self.preview_path: str | None = None
        self.preview_busy = False
        # The review queue: fixes kept, in order, each applied to the result of
        # the ones before. `base_path` is the result of the whole queue (the
        # original when it is empty); new previews are made on top of it.
        self.queue: list[dict] = []
        self.base_path: str | None = None
        self.base_report: dict | None = None
        self.exported: str | None = None
        self.export_error: str | None = None
        self.result = None  # the engine's result for the open file, for the report
        self.report_written: str | None = None

    def open(self, path: str) -> None:
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".pdf") or not os.path.isfile(path):
            raise ValueError("Choose a PDF file that exists")
        remember(path)
        with self.lock:
            self.path, self.report, self.error, self.busy = path, None, None, True
            self.result, self.report_written = None, None
            self._renders.clear()
            self._drop_preview()
            self._drop_queue()
        threading.Thread(target=self._check, args=(path,), daemon=True).start()

    def _check(self, path: str) -> None:
        try:
            with RENDER_LOCK:  # the checks render too
                result = run(path, self.options)
            report = _report(result)
        except Exception as exc:  # shown to the user; never a pass
            with self.lock:
                if self.path == path:
                    self.error, self.busy = f"The file could not be checked ({type(exc).__name__}: {exc})", False
            return
        with self.lock:
            if self.path == path:
                self.report, self.busy, self.result = report, False, result
                self.base_path, self.base_report = path, report

    def save_report(self, dst: str | None = None) -> str | None:
        """Write the open file's preflight report as HTML: to `dst`, or where the
        user chooses (Mac), or beside the PDF. None if the user cancelled."""
        from ..report_html import default_report_path, write_report
        with self.lock:
            path, result = self.path, self.result
        if path is None or result is None:
            raise ValueError("Open a file and wait for its check first")
        if not dst:
            if sys.platform == "darwin":
                dst = choose_save_name(path, "Save the preflight report as", " preflight report.html")
                if dst is None:
                    return None
            else:
                dst = default_report_path(path)
        with RENDER_LOCK:  # thumbnails are drawn with PyMuPDF
            written = write_report(result, dst, self.options)
        with self.lock:
            self.report_written = written
        return written

    def state(self) -> dict:
        with self.lock:
            return {"path": self.path, "name": os.path.basename(self.path) if self.path else None,
                    "busy": self.busy, "error": self.error, "report": self.report,
                    "preview": self.preview, "preview_busy": self.preview_busy,
                    "queue": [{k: v for k, v in step.items() if k != "output"} for step in self.queue],
                    "queue_report": self.base_report if self.queue else None,
                    "queue_effect": effect(self.report, self.base_report) if self.queue and self.base_report else None,
                    "exported": self.exported, "export_error": self.export_error,
                    "recent": load_recent() if self.path is None else [],
                    "job": self.options.job, "jobs": list_jobs(),
                    "settings": _settings_text(self.options)}

    def set_job(self, name: str | None) -> None:
        """Check with a saved job's settings (None: as the app was started). Re-checks the open file."""
        options = job_options(name) if name else self.start_options
        with self.lock:
            if self.preview_busy:
                raise ValueError("Something is already being worked on")
            if self.queue:
                raise ValueError("Changing the job checks the file again, which would clear the fixes kept in Review: "
                                 "export or remove them first")
            self.options = options
            path = self.path
        if path:
            self.open(path)

    # ---- propose ----------------------------------------------------------

    def _drop_queue(self):
        for step in self.queue:
            if step.get("output") and os.path.exists(step["output"]):
                os.remove(step["output"])
        self.queue, self.base_path, self.base_report = [], self.path, self.report
        self.exported = self.export_error = None

    def _drop_preview(self):
        if self.preview_path and os.path.exists(self.preview_path):
            os.remove(self.preview_path)
        self.preview, self.preview_path = None, None
        self._renders = {k: v for k, v in self._renders.items() if k[0] == self.path}

    def start_preview(self, fix: str, tokens: list[str]) -> None:
        if fix not in FIXES:
            raise ValueError(f"Unknown fix: {fix}")
        try:
            ns = _step_parser().parse_args(tokens)
        except (Exception, SystemExit) as exc:
            raise ValueError(f"Those options are not right: {exc}") from None
        step_options, extra = _settings(ns)
        options = check_options(self.options, tokens, step_options)
        with self.lock:
            if self.path is None or self.report is None:
                raise ValueError("Open a file and wait for its check first")
            if self.preview_busy:
                raise ValueError("Something is already being worked on")
            self._drop_preview()
            self.preview_busy = True
            base, report = self.base_path, self.base_report
        threading.Thread(target=self._preview, args=(self.path, base, report, fix, tokens, options, extra),
                         daemon=True).start()

    def _preview(self, original, path, before, fix, tokens, options, extra):
        propose, write = FIXES[fix]
        out = os.path.join(self.workdir, f"preview-{secrets.token_hex(4)}.pdf")
        result = {"fix": fix, "options": tokens, "reasons": [], "skipped": [], "changes": [], "empty": True,
                  "error": None, "report": None, "effect": None}
        try:
            with RENDER_LOCK:
                proposal = propose(path, options, **extra)
                result["reasons"] = [{"page": p, "text": t} for p, t in proposal.reasons]
                result["skipped"] = [{"page": p, "text": t} for p, t in proposal.skipped]
                result["changes"] = [{"page": c.page, "box": c.box, "before": c.before_mm, "after": c.after_mm}
                                     for c in proposal.changes]
                result["empty"] = proposal.empty
                if not proposal.empty:
                    write(proposal, out)  # verified, as every fix is
                    after = run(out, options)
            if not proposal.empty:
                result["report"] = _report(after)
                result["effect"] = effect(before, result["report"])
        except Exception as exc:  # shown to the user; nothing is kept
            result["error"] = f"{type(exc).__name__}: {exc}"
            if os.path.exists(out):
                os.remove(out)
            out = None
        with self.lock:
            if self.path == original and self.base_path == path:
                self.preview, self.preview_path, self.preview_busy = result, (out if not result["empty"] else None), False
            else:
                self.preview_busy = False
                if out and os.path.exists(out):
                    os.remove(out)

    # ---- review -----------------------------------------------------------

    def keep_preview(self) -> None:
        """Add the current preview to the review queue: its result becomes the base."""
        with self.lock:
            if self.preview_busy:
                raise ValueError("Something is already being worked on")
            if not self.preview or self.preview.get("empty") or self.preview.get("error") or not self.preview_path:
                raise ValueError("Preview a fix that changes something first")
            self.queue.append({"fix": self.preview["fix"], "options": self.preview["options"],
                               "reasons": self.preview["reasons"], "output": self.preview_path})
            self.base_path, self.base_report = self.preview_path, self.preview["report"]
            self.preview, self.preview_path = None, None
            self.exported = self.export_error = None

    def remove_step(self, index: int) -> None:
        """Take a fix out of the queue. The queue is run again from the original."""
        with self.lock:
            if self.preview_busy:
                raise ValueError("Something is already being worked on")
            if not 0 <= index < len(self.queue):
                raise ValueError("No such step")
            self._drop_preview()
            steps = [(q["fix"], q["options"]) for i, q in enumerate(self.queue) if i != index]
            self._drop_queue()
            if not steps:
                return
            self.preview_busy = True
            path = self.path
        threading.Thread(target=self._rebuild, args=(path, steps), daemon=True).start()

    def _rebuild(self, path, steps):
        error = None
        try:
            settings = []
            for fix, tokens in steps:
                step_options, extra = _settings(_step_parser().parse_args(tokens))
                settings.append((fix, check_options(self.options, tokens, step_options), extra))
            folder = tempfile.mkdtemp(dir=self.workdir)
            with RENDER_LOCK:
                done = run_chain(path, settings, FIXES, folder)
                outputs = [s.output for s in done if s.output]
                result = run(outputs[-1], settings[0][1]) if outputs else None
            report = _report(result) if result else None
        except Exception as exc:
            error = f"The review list could not be rebuilt ({type(exc).__name__}: {exc}); it has been cleared"
            done, report = [], None
        with self.lock:
            self.preview_busy = False
            if self.path != path:
                return
            if error:
                self.export_error = error
                return
            for step, (_, tokens) in zip(done, steps):  # in the same order as asked
                if step.output:
                    self.queue.append({"fix": step.name, "options": tokens,
                                       "reasons": [{"page": p, "text": t} for p, t in step.proposal.reasons],
                                       "output": step.output})
            if self.queue:
                self.base_path, self.base_report = self.queue[-1]["output"], report

    # ---- export -----------------------------------------------------------

    def export(self, dst: str, compact: bool = False) -> str:
        """Write the queue's result to a new file. Only after the user confirmed it."""
        with self.lock:
            if self.preview_busy:
                raise ValueError("Something is already being worked on")
            if not self.queue:
                raise ValueError("There is nothing in the review list to export")
            original, final = self.path, self.base_path
        dst = os.path.abspath(os.path.expanduser(dst))
        if not dst.lower().endswith(".pdf"):
            dst += ".pdf"
        if os.path.exists(dst):
            raise ValueError(f"{os.path.basename(dst)} already exists; choose a new name")
        if not os.path.isdir(os.path.dirname(dst)):
            raise ValueError("That folder does not exist")
        with RENDER_LOCK:
            deliver(original, final, dst, compact=compact)  # checks the original's bytes are intact first
        with self.lock:
            self.exported, self.export_error = dst, None
        return dst

    def close(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _file(self, which: str) -> str:
        path = {"after": self.preview_path, "base": self.base_path}.get(which, self.path)
        if path is None:
            raise ValueError("No file is open" if which != "after" else "There is no preview")
        return path

    def object_details(self, number: int, obj: int, which: str = "before") -> dict:
        """Everything known about one object on a page (its number from `objects`)."""
        path = self._file(which)
        with RENDER_LOCK, pikepdf.open(path) as pdf, pymupdf.open(path) as doc:
            if not 1 <= number <= len(pdf.pages):
                raise ValueError("No such page")
            # measure from the same trim the page shows: inferred from crop marks when the report says so
            report = {"after": (self.preview or {}).get("report"), "base": self.base_report}.get(which, self.report)
            g = next((g for g in (report or {}).get("page_geometry", []) if g["page"] == number), None)
            trim = (g["trim"], g["trim_inferred"]) if g and g["trim"] else None
            return object_details(pdf.pages[number - 1], doc.load_page(number - 1), obj, trim=trim)

    def objects(self, number: int, x: float, y: float, which: str = "before") -> list[dict]:
        """The objects under a point on the rendered page, topmost first."""
        path = self._file(which)
        key = ("content", path, number)
        with RENDER_LOCK, pikepdf.open(path) as pdf, pymupdf.open(path) as doc:
            if not 1 <= number <= len(pdf.pages):
                raise ValueError("No such page")
            content = self._renders.get(key)
            if content is None:
                content = walk_page(pdf.pages[number - 1])
                self._renders[key] = content
            return objects_at(pdf.pages[number - 1], doc.load_page(number - 1), x, y, content=content)

    def inks(self, number: int, which: str = "before") -> list[dict]:
        """The inks of one page, with how much of it each covers."""
        path = self._file(which)
        with RENDER_LOCK, pymupdf.open(path) as doc:
            if not 1 <= number <= doc.page_count:
                raise ValueError("No such page")
            page = doc.load_page(number - 1)
            coverage = ink_coverage(page)
            return [{"name": i.name, "process": i.process, "printing": i.printing,
                     "coverage": coverage.get(i.name, 0.0)} for i in page_inks(page)]

    def render_plate(self, number: int, width: int, ink: str, which: str = "before") -> bytes:
        path = self._file(which)
        key = (path, number, width, "plate", ink)
        if key in self._renders:
            return self._renders[key]
        with RENDER_LOCK, pymupdf.open(path) as doc:
            if not 1 <= number <= doc.page_count:
                raise ValueError("No such page")
            page = doc.load_page(number - 1)
            png = render_plate(page, ink, width / page.rect.width)
        if len(self._renders) > 64:
            self._renders.clear()
        self._renders[key] = png
        return png

    def render(self, number: int, width: int, which: str = "before") -> bytes:
        """PNG of a page, `width` pixels wide (capped), cached. `which` is
        "before" (the file as it is), "base" (after the review queue) or
        "after" (the current preview)."""
        path = {"after": self.preview_path, "base": self.base_path}.get(which, self.path)
        if path is None:
            raise ValueError("No file is open" if which != "after" else "There is no preview")
        key = (path, number, width)
        if key in self._renders:
            return self._renders[key]
        with RENDER_LOCK, pymupdf.open(path) as doc:
            if not 1 <= number <= doc.page_count:
                raise ValueError("No such page")
            page = doc.load_page(number - 1)
            zoom = width / page.rect.width
            if page.rect.width * page.rect.height * zoom * zoom > MAX_PIXELS:
                zoom = (MAX_PIXELS / (page.rect.width * page.rect.height)) ** 0.5
            png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=False).tobytes("png")
        if len(self._renders) > 64:
            self._renders.clear()
        self._renders[key] = png
        return png


def _report(result) -> dict:
    """The engine's result, with every position given on the rendered page as
    well: points from the page's top-left corner, after any rotation."""
    data = result.to_dict()
    with RENDER_LOCK, pymupdf.open(result.path) as doc:
        pages = []
        to_view = {}
        for g in result.geometry:
            page = doc.load_page(g["page"] - 1)
            m = device_matrix(page)
            to_view[g["page"]] = m

            def view(box, m=m):
                if box is None:
                    return None
                r = (pymupdf.Rect(box) * m).normalize()
                return [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
            pages.append({"page": g["page"], "width": round(page.rect.width, 2), "height": round(page.rect.height, 2),
                          "media": view(g["media"]), "trim": view(g["trim"]),
                          "bleed": view(g["bleed"]) if g["has_bleed"] else None,
                          "declared_trim": view(g["declared_trim"]), "trim_inferred": g["trim_inferred"]})
        for finding in data["findings"]:
            for loc in finding["locations"]:
                m = to_view.get(loc["page"])
                if m is not None:
                    r = (pymupdf.Rect(loc["bbox"]) * m).normalize()
                    loc["view"] = [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
    data["page_geometry"] = pages
    data["suggestions"] = suggest(data)
    return data


NOTES_SHOWN = {"Spot colours", "Trim marks", "Page size"}


def effect(before: dict, after: dict) -> dict:
    """What a fix changes in the findings: gone, new, and changed (same check and
    severity, different figures). Fails, warnings and could-not-checks, plus the
    notes a fix may be meant to change."""
    def summary(report):
        return {(f["check"], f["severity"], f["message"]) for f in report["findings"]
                if f["severity"] in (FAIL, WARN, UNCHECKED) or f["check"] in NOTES_SHOWN}
    old, new = summary(before), summary(after)
    gone, added, changed = sorted(old - new), sorted(new - old), []
    for item in list(gone):
        twin = next((a for a in added if a[:2] == item[:2]), None)
        if twin is not None:
            gone.remove(item)
            added.remove(twin)
            changed.append({"check": item[0], "severity": item[1], "before": item[2], "after": twin[2]})
    as_dicts = lambda items: [{"check": c, "severity": s, "message": m} for c, s, m in items]
    return {"status_before": before["status"], "status_after": after["status"],
            "gone": as_dicts(gone), "new": as_dicts(added), "changed": changed}


def suggest(report: dict) -> list[list[str]]:
    """For each finding, the fixes that address it."""
    out = []
    for f in report["findings"]:
        check, sev, msg = f["check"], f["severity"], f["message"]
        fixes = []
        if check == "Trim marks" and sev == WARN:
            fixes.append("trim-from-marks")
        elif check == "Bleed artwork" and sev == FAIL:
            fixes.append("add-bleed")
        elif check == "Bleed box" and sev in (FAIL, WARN):
            fixes += ["add-bleed", "set-boxes"]
        elif check == "Spot colours" and sev in (FAIL, "note") and "Non-printing" not in msg:
            fixes.append("spot-to-cmyk")
        elif check == "Spot colours" and "spelt more than one way" in msg:
            fixes.append("rename-spot")
        elif check == "Registration inside trim" and sev == FAIL:
            fixes.append("registration-to-black")
        elif check == "RGB":
            fixes.append("rgb-to-cmyk")
        elif check == "CMYK profiles":
            fixes.append("untag-cmyk")
        elif check == "Hairlines" and sev == WARN:
            fixes.append("thicken-hairlines")
        elif check == "White overprint":
            fixes.append("knock-out-white")
        elif check == "Total ink" and sev == FAIL:
            fixes.append("reduce-ink")
        elif check == "Small text inks" and sev == WARN:
            fixes.append("rich-black-text")
        elif check == "Annotations" and sev == WARN:
            fixes += ["flatten-annotations", "remove-annotations"]
        elif check == "Annotations":
            fixes.append("remove-annotations")
        elif check == "Image resolution" and "Downsampling" in msg:
            fixes.append("downsample-images")
        elif check == "Dielines" and sev == WARN:
            fixes.append("overprint-spot")
        elif check == "Layers":
            fixes.append("remove-layer")
        elif check == "Black overprint":
            fixes.append("overprint-black")
        elif check == "Page size" and sev == WARN and "does not match the expected" in msg:
            fixes.append("scale")
        out.append(fixes)
    return out


def choose_save_name(original: str, prompt: str = "Save the fixed copy as", suffix: str = " fixed.pdf") -> str | None:
    """The Mac's own save dialog, through AppleScript. None if cancelled."""
    if sys.platform != "darwin":
        return None
    stem = os.path.splitext(os.path.basename(original))[0].replace('"', "")
    folder = os.path.dirname(original).replace('"', "")
    script = (f'POSIX path of (choose file name with prompt "{prompt}" '
              f'default name "{stem}{suffix}" default location (POSIX file "{folder}"))')
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return done.stdout.strip() or None


def choose_file() -> str | None:
    """The Mac's own file chooser, through AppleScript. None if cancelled."""
    if sys.platform != "darwin":
        return None
    script = 'POSIX path of (choose file with prompt "Choose a PDF to check" of type {"com.adobe.pdf"})'
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    path = done.stdout.strip()
    return path or None


def make_handler(session: Session, key: str, port_ref: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "preflight"

        def log_message(self, *args):  # quiet
            pass

        def _send(self, status, body: bytes, kind: str):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _allowed(self, query) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return False
            # A browser names the page a request comes from. Only the app's own
            # pages may call it, so a website open in another tab cannot.
            origin = self.headers.get("Origin")
            if origin is not None and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                return False
            given = (query.get("key") or [""])[0] or self.headers.get("X-Preflight-Key", "")
            return secrets.compare_digest(given, key)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self._allowed(query):
                return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            try:
                if url.path in ("/", "/index.html"):
                    page = (STATIC / "index.html").read_text().replace("__KEY__", key)
                    return self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
                if url.path.startswith("/static/"):
                    name = url.path[len("/static/"):]
                    target = (STATIC / name).resolve()
                    if STATIC.resolve() not in target.parents or not target.is_file():
                        return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
                    kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    return self._send(HTTPStatus.OK, target.read_bytes(), kind)
                if url.path == "/api/state":
                    return self._json(HTTPStatus.OK, session.state())
                if url.path == "/api/page":
                    number = int((query.get("n") or ["1"])[0])
                    width = max(64, min(4000, int((query.get("w") or ["800"])[0])))
                    which = (query.get("which") or ["before"])[0]
                    plate = (query.get("plate") or [""])[0]
                    if plate:
                        return self._send(HTTPStatus.OK, session.render_plate(number, width, plate, which), "image/png")
                    return self._send(HTTPStatus.OK, session.render(number, width, which), "image/png")
                if url.path == "/api/objects":
                    number = int((query.get("n") or ["1"])[0])
                    x, y = float((query.get("x") or ["0"])[0]), float((query.get("y") or ["0"])[0])
                    which = (query.get("which") or ["before"])[0]
                    return self._json(HTTPStatus.OK, {"objects": session.objects(number, x, y, which)})
                if url.path == "/api/object":
                    number = int((query.get("n") or ["1"])[0])
                    obj = int((query.get("id") or ["-1"])[0])
                    which = (query.get("which") or ["before"])[0]
                    return self._json(HTTPStatus.OK, {"object": session.object_details(number, obj, which)})
                if url.path == "/api/inks":
                    number = int((query.get("n") or ["1"])[0])
                    which = (query.get("which") or ["before"])[0]
                    return self._json(HTTPStatus.OK, {"inks": session.inks(number, which)})
                if url.path == "/api/recent":
                    return self._json(HTTPStatus.OK, {"recent": load_recent()})
                if url.path == "/api/fixes":
                    return self._json(HTTPStatus.OK, {"fixes": sorted(FIXES)})
                return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            except ValueError as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self._allowed(query):
                return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Bad request"})
            try:
                if url.path == "/api/open":
                    path = body.get("path")
                    if body.get("choose"):
                        path = choose_file()
                        if path is None:
                            return self._json(HTTPStatus.OK, session.state())
                    if not path:
                        raise ValueError("Choose a PDF file")
                    session.open(path)
                    return self._json(HTTPStatus.OK, session.state())
                if url.path == "/api/job":
                    session.set_job(body.get("name") or None)
                    return self._json(HTTPStatus.OK, session.state())
                if url.path == "/api/keep":
                    session.keep_preview()
                    return self._json(HTTPStatus.OK, session.state())
                if url.path == "/api/remove":
                    session.remove_step(int(body.get("index", -1)))
                    return self._json(HTTPStatus.OK, session.state())
                if url.path == "/api/save-name":
                    state = session.state()
                    if not state["path"]:
                        raise ValueError("No file is open")
                    return self._json(HTTPStatus.OK, {"path": choose_save_name(state["path"])})
                if url.path == "/api/export":
                    if body.get("confirm") is not True:
                        raise ValueError("Exporting needs a confirmation")
                    written = session.export(str(body.get("path") or ""), compact=body.get("compact") is True)
                    sizes = {"size": os.path.getsize(written), "original_size": os.path.getsize(session.path)}
                    return self._json(HTTPStatus.OK, {"written": written, **sizes, **session.state()})
                if url.path == "/api/report":
                    written = session.save_report(str(body.get("path") or "") or None)
                    return self._json(HTTPStatus.OK, {"written": written})
                if url.path == "/api/reveal":
                    state = session.state()
                    target = session.report_written if body.get("what") == "report" else state["exported"]
                    if target and sys.platform == "darwin":
                        subprocess.run(["open", "-R", target], check=False)
                    return self._json(HTTPStatus.OK, state)
                if url.path == "/api/preview":
                    fix = body.get("fix", "")
                    tokens = body.get("options", "")
                    tokens = shlex.split(tokens) if isinstance(tokens, str) else [str(t) for t in tokens]
                    session.start_preview(fix, tokens)
                    return self._json(HTTPStatus.OK, session.state())
                return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            except ValueError as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def serve(path: str | None = None, port: int = 0, open_browser: bool = True, options: Options | None = None,
          key: str | None = None):
    """Start the app. Returns (server, url); the server runs until shut down."""
    session = Session(options or Options())
    key = key or secrets.token_urlsafe(18)
    port_ref: dict = {}
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session, key, port_ref))
    url = f"http://127.0.0.1:{server.server_address[1]}/?key={key}"
    if path:
        session.open(path)
    if open_browser:
        webbrowser.open(url)
    return server, url, session


def main(argv) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="preflight app", description="Open the preflight app in your browser.")
    parser.add_argument("file", nargs="?", help="a PDF to open straight away")
    parser.add_argument("--port", type=int, default=0, help="port to use (default: any free one)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--browser", action="store_true",
                        help="use the web browser even if the native window is available")
    parser.add_argument("--key", help=argparse.SUPPRESS)  # fixed key, for development only
    from ..cli_common import add_check_options, options_from
    from ..jobs import expand
    add_check_options(parser)
    try:
        argv, job = expand(list(argv))
    except ValueError as exc:
        print(f"preflight app: {exc}", file=sys.stderr)
        return 2
    args = parser.parse_args(argv)
    from .window import available, run_window
    native = available() and not args.browser and not args.no_browser
    try:
        server, url, session = serve(args.file, args.port, not (args.no_browser or native),
                                     options=options_from(args, job), key=args.key)
    except ValueError as exc:
        print(f"preflight app: {exc}", file=sys.stderr)
        return 2
    if native:
        print("Preflight is open in its own window. Close the window to stop it.")
        run_window(server, url, session)
        return 0
    print(f"Preflight is open at {url}")
    print("It runs on this computer only. Press Ctrl+C to stop it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        session.close()
    return 0
