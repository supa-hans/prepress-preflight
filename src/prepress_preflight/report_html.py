"""A preflight report as one self-contained HTML file, to keep or send to a client.

Everything is inside the file: styles inline, page thumbnails as embedded
PNGs with the problem areas marked. It reads the PDF only; the report is the
one file written. Thumbnails are drawn with colour management off, as the app
shows pages, so colours on screen are approximate; the report says so.
"""
from __future__ import annotations

import base64
import datetime
import html
import os

import pymupdf

from . import __version__
from .document import device_matrix
from .model import FAIL, NOTE, UNCHECKED, WARN
from .report import page_list

pymupdf.TOOLS.set_icc(False)

ORDER = {FAIL: 0, UNCHECKED: 1, WARN: 2, NOTE: 3}
LABEL = {FAIL: "Fail", UNCHECKED: "Could not check", WARN: "Warning", NOTE: "Note"}
TRIM, BLEED, HIT = "#185fa5", "#c46a1a", "#e24b4a"
MAX_IMAGES = 24  # page thumbnails in one report
THUMB_PX = 560

# Why each check matters, for a reader who is not a pre-press operator.
WHY = {
    "Page size": "The trimmed size must match the job, or the print is made at the wrong size.",
    "Bleed box": "Artwork that reaches the edge must carry on past it, so no white shows when the sheet is cut.",
    "Bleed artwork": "Artwork that reaches the edge must carry on past it, so no white shows when the sheet is cut.",
    "RGB": "Print uses CMYK inks. RGB colours are converted at print time and can shift, most of all bright colours.",
    "White overprint": "White set to overprint does not print at all: it disappears and what is beneath shows through.",
    "Registration inside trim": "Registration colour prints on every plate. In artwork, black text or rules in it "
                                "come out as a build of all inks that never lines up perfectly.",
    "Blend modes": "Listed for information. Blend modes are common and normally print as expected.",
    "Blend over same ink": "A Multiply (or similar) object over the same ink can print far darker than it looks "
                           "on screen if the printer's system does not handle it as intended.",
    "Total ink": "Too much ink in one place can smudge, set off or dry badly.",
    "Image resolution": "Low resolution images can print soft or pixelated.",
    "Safe margin": "Text or important artwork too close to the edge risks being cut off, as cutting can move slightly.",
    "Hairlines": "Very thin lines can break up or vanish in print.",
    "Font embedding": "A font that is not embedded can be replaced by another when printed.",
    "Trim marks": "The crop marks show where the sheet will be cut; when they disagree with the file's trim size, "
                  "one of them is wrong.",
    "Dielines": "Cutting and creasing lines must be set to overprint and kept on their own colour, or they print "
                "or knock out the artwork beneath.",
    "Spot colours": "Each spot colour is an extra ink. Unexpected or duplicated ones add cost or print on the wrong plate.",
    "Annotations": "Comments or stamps set to print will appear on the printed sheet.",
    "Layers": "A layer that is switched off may or may not print, depending on how the file is processed.",
    "Security": "Restrictions on the file can stop it being processed for print.",
    "Output intent": "Says which print condition the colours were prepared for.",
    "Reading the file": "Part of the file could not be read, so it could not be fully checked.",
}

CSS = """
:root { --fg:#1f1e1d; --muted:#6b6a66; --line:#d9d7d0; --bg:#faf9f5; --panel:#fff;
  --fail:#a32d2d; --fail-bg:#fcebeb; --warn:#854f0b; --warn-bg:#faeeda; --note:#5f5e5a; --note-bg:#f1efe8;
  --pass:#2b6b3a; --pass-bg:#e5f2e8; --trim:#185fa5; --bleed:#c46a1a; --hit:#e24b4a; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 32px 20px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; overflow-wrap: anywhere; }
h2 { font-size: 16px; margin: 32px 0 10px; }
.meta { color: var(--muted); font-size: 13px; }
.status { display: inline-block; padding: 2px 10px; border-radius: 6px; font-weight: 600; margin-right: 8px; }
.status.FAIL { background: var(--fail-bg); color: var(--fail); }
.status.WARN { background: var(--warn-bg); color: var(--warn); }
.status.PASS { background: var(--pass-bg); color: var(--pass); }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; margin: 14px 0 0; font-size: 13px; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; }
.finding { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; margin: 8px 0; }
.finding .head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.sev { font-size: 12px; padding: 1px 8px; border-radius: 6px; white-space: nowrap; }
.sev.fail { background: var(--fail-bg); color: var(--fail); }
.sev.unchecked, .sev.warn { background: var(--warn-bg); color: var(--warn); }
.sev.note { background: var(--note-bg); color: var(--note); }
.check { font-weight: 600; }
.num { color: var(--hit); font-weight: 600; }
.why { color: var(--muted); font-size: 13px; margin-top: 2px; }
.where { color: var(--muted); font-size: 12px; margin-top: 2px; }
.pages { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
figure { margin: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
figure .sheet { position: relative; line-height: 0; }
figure img, figure svg { width: 100%; height: auto; }
figure svg { position: absolute; inset: 0; height: 100%; }
figcaption { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.4; }
.legend { font-size: 12px; color: var(--muted); display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }
.legend i { display: inline-block; width: 14px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 4px; }
.passed { font-size: 13px; color: var(--muted); }
footer { margin-top: 36px; font-size: 12px; color: var(--muted); border-top: 1px solid var(--line); padding-top: 12px; }
@media print { body { background: #fff; } main { padding: 0; } .finding, figure { break-inside: avoid; } }
"""


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _view_box(box, m):
    r = (pymupdf.Rect(box) * m).normalize()
    return r.x0, r.y0, r.x1, r.y1


def _thumbnail(page, marks, geometry) -> str:
    """A page picture with the trim, bleed and numbered problem areas drawn on it."""
    m = device_matrix(page)
    w, h = page.rect.width, page.rect.height
    zoom = min(THUMB_PX / max(w, 1e-6), 3.0)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=False)
    jpg = base64.b64encode(pix.tobytes("jpg", jpg_quality=82)).decode()  # JPEG keeps a report small enough to email
    stroke = max(w, h) / 300
    shapes = []
    # SVG attributes take plain colours, not CSS variables
    for key, colour, dash in (("bleed", BLEED, f"{stroke * 4} {stroke * 3}"), ("trim", TRIM, "")):
        box = geometry.get(key)
        if box and not (key == "bleed" and not geometry.get("has_bleed")):
            x0, y0, x1, y1 = _view_box(box, m)
            shapes.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" fill="none" '
                          f'stroke="{colour}" stroke-width="{stroke:.2f}" stroke-dasharray="{dash}"/>')
    pad = stroke * 3
    for number, bbox in marks:
        x0, y0, x1, y1 = _view_box(bbox, m)
        # a tint helps a small mark stand out, but over a large area it changes the artwork's colour
        tint = "0.15" if (x1 - x0) * (y1 - y0) < 0.02 * w * h else "0"
        shapes.append(f'<rect x="{x0 - pad:.1f}" y="{y0 - pad:.1f}" width="{x1 - x0 + 2 * pad:.1f}" '
                      f'height="{y1 - y0 + 2 * pad:.1f}" fill="{HIT}" fill-opacity="{tint}" stroke="{HIT}" '
                      f'stroke-width="{stroke:.2f}"><title>Finding {number}</title></rect>')
    labelled = []
    for number, bbox in marks:
        if number in labelled:
            continue
        labelled.append(number)
        x0, y0, _, _ = _view_box(bbox, m)
        size = max(w, h) / 28
        shapes.append(f'<text x="{max(x0 - pad, 0):.1f}" y="{max(y0 - pad, size):.1f}" font-size="{size:.1f}" '
                      f'font-weight="700" fill="{HIT}" stroke="#fff" stroke-width="{size / 8:.1f}" '
                      f'paint-order="stroke">{number}</text>')
    return (f'<div class="sheet"><img alt="" src="data:image/jpeg;base64,{jpg}">'
            f'<svg viewBox="0 0 {w:.1f} {h:.1f}" preserveAspectRatio="none" aria-hidden="true">{"".join(shapes)}</svg></div>')


def _size_text(geometry) -> str:
    sizes = []
    for g in geometry:
        x0, y0, x1, y1 = g["trim"]
        size = f"{(x1 - x0) * 25.4 / 72:.1f} × {(y1 - y0) * 25.4 / 72:.1f} mm"
        if size not in sizes:
            sizes.append(size)
    text = ", ".join(sizes[:4]) + (" and others" if len(sizes) > 4 else "")
    if any(g.get("trim_inferred") for g in geometry):
        text += " (taken from the crop marks)"
    return text


def build_html(result, options=None, now: datetime.datetime | None = None) -> str:
    """The report for one checked file (`engine.run` result)."""
    now = now or datetime.datetime.now()
    name = os.path.basename(result.path)
    findings = sorted(result.findings, key=lambda f: ORDER.get(f.severity, 9))
    numbered = {id(f): i + 1 for i, f in enumerate(findings)}
    counts = []
    for sev, label in ((FAIL, "fail"), (WARN, "warning"), (UNCHECKED, "could not check")):
        n = result.count(sev)
        if n:
            counts.append(f"{n} {label}{'s' if n != 1 and sev != UNCHECKED else ''}")

    facts = [("Pages", result.page_count), ("Trim size", _size_text(result.geometry))]
    if options is not None:
        facts.append(("Checked for", f"{options.min_bleed_mm:g} mm bleed, {options.safe_margin_mm:g} mm safe margin, "
                                     f"{options.ink_limit_pct:g}% total ink"
                      + (", process colours only" if options.cmyk_only else "")
                      + (", bound (no bleed at the spine)" if options.bound else "")
                      + (f", job size {options.expected_trim_mm[0]:g} × {options.expected_trim_mm[1]:g} mm"
                         if options.expected_trim_mm else "")))
    facts.append(("Checked on", f"{now.day} {now:%B %Y, %H:%M}"))

    out = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
           f"<title>Preflight report: {_esc(name)}</title><style>{CSS}</style></head><body><main>",
           f"<h1>{_esc(name)}</h1>",
           f"<p><span class=\"status {result.status}\">{result.status}</span>"
           f"<span class=\"meta\">{_esc(', '.join(counts) or 'nothing to fix')}</span></p>",
           "<dl class=\"facts\">" + "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in facts) + "</dl>"]

    if findings:
        out.append("<h2>Findings</h2>")
        for f in findings:
            where = page_list(f.pages, result.page_count) if result.page_count > 1 else ""
            located = bool(f.locations)
            out.append(
                f"<div class=\"finding\"><div class=\"head\"><span class=\"num\">{numbered[id(f)]}</span>"
                f"<span class=\"sev {f.severity}\">{LABEL.get(f.severity, f.severity)}</span>"
                f"<span class=\"check\">{_esc(f.check)}</span></div><div>{_esc(f.message)}</div>"
                + (f"<div class=\"why\">{_esc(WHY[f.check])}</div>" if f.check in WHY and f.severity != NOTE else "")
                + (f"<div class=\"where\">{_esc(' · '.join(x for x in (where.capitalize(), 'marked on the page' if located else '') if x))}</div>"
                   if where or located else "") + "</div>")

    # pictures of the pages with something marked on them, fails first
    marks_by_page: dict[int, list] = {}
    for f in findings:
        if f.severity == NOTE:
            continue
        for page, bbox in f.locations[:60]:
            marks_by_page.setdefault(page, []).append((numbered[id(f)], bbox))
    if marks_by_page:
        pages = sorted(marks_by_page, key=lambda p: (min(n for n, _ in marks_by_page[p]), p))
        shown = sorted(pages[:MAX_IMAGES])
        geometry = {g["page"]: g for g in result.geometry}
        out.append("<h2>Where on the page</h2>")
        out.append("<div class=\"legend\"><span><i style=\"border-color:var(--trim)\"></i>Trim</span>"
                   "<span><i style=\"border-color:var(--bleed);border-top-style:dashed\"></i>Bleed</span>"
                   "<span><i style=\"border-color:var(--hit)\"></i>Finding (numbered as above)</span></div>")
        out.append("<div class=\"pages\">")
        with pymupdf.open(result.path) as doc:
            for p in shown:
                numbers = sorted({n for n, _ in marks_by_page[p]})
                out.append(f"<figure>{_thumbnail(doc.load_page(p - 1), marks_by_page[p], geometry[p])}"
                           f"<figcaption>Page {p} · finding{'s' if len(numbers) > 1 else ''} "
                           f"{', '.join(str(n) for n in numbers)}</figcaption></figure>")
        out.append("</div>")
        if len(pages) > len(shown):
            out.append(f"<p class=\"meta\">{len(pages) - len(shown)} more page(s) have marked findings; "
                       f"the first {len(shown)} are shown.</p>")

    if result.passed_checks:
        out.append("<h2>Passed</h2>")
        out.append(f"<p class=\"passed\">{_esc(', '.join(result.passed_checks))}</p>")
    out.append(f"<footer>Checked with prepress-preflight {__version__}. The PDF was read only, not changed. "
               "Page pictures are drawn without colour management, so colours on screen are approximate.</footer>")
    out.append("</main></body></html>")
    return "\n".join(out)


def default_report_path(pdf_path: str) -> str:
    """'<name> preflight report.html' beside the PDF, never an existing file."""
    folder = os.path.dirname(os.path.abspath(pdf_path))
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    candidate = os.path.join(folder, f"{stem} preflight report.html")
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem} preflight report {n}.html")
        n += 1
    return candidate


def write_report(result, dst: str, options=None) -> str:
    """Write the report to a new file. Refuses to overwrite anything, the PDF above all."""
    if os.path.exists(dst):
        raise ValueError(f"{dst} already exists; choose a new name")
    if os.path.splitext(dst)[1].lower() not in (".html", ".htm"):
        raise ValueError("the report is an HTML file: give it a name ending in .html")
    with open(dst, "x", encoding="utf-8") as f:
        f.write(build_html(result, options))
    return dst
