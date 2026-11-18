"""The app in a native window, through PyWebView (optional: pip install -e ".[app]").

The window shows the same pages the browser would, served by the same local
server. What it adds is drag and drop with real file paths: a browser hides a
dropped file's location, but PyWebView passes it to Python, so a dropped PDF is
opened where it is, with no copy.

Closing the window stops the server and removes the app's temporary previews.
"""
from __future__ import annotations

import json
import threading


def handle_drop(event: dict, session) -> str | None:
    """Open the first PDF dropped on the window. Returns what to tell the user if
    nothing could be opened, or None when a file was opened."""
    files = (event.get("dataTransfer") or {}).get("files") or []
    paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
    pdfs = [p for p in paths if p.lower().endswith(".pdf")]
    if not pdfs:
        return "Drop a PDF file to open it."
    try:
        session.open(pdfs[0])
    except ValueError as exc:
        return str(exc)
    return None


def available() -> bool:
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


def run_window(server, url: str, session) -> None:
    import webview
    from webview.dom import DOMEventHandler

    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = webview.create_window("Preflight", url, width=1360, height=880, min_size=(760, 520))

    def on_drop(event):
        problem = handle_drop(event, session)
        if problem:
            window.evaluate_js(f"window.preflightNotice && window.preflightNotice({json.dumps(problem)})")
        else:
            window.evaluate_js("window.preflightRefresh && window.preflightRefresh()")

    def on_loaded():
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)

    window.events.loaded += on_loaded
    try:
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
        session.close()
