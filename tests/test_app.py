"""The local app's server: access rules, the report it serves, page renders."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from prepress_preflight.app.server import serve

from conftest import BLEEDBOX, MEDIA, MM, TRIM, rect, registration_space


@pytest.fixture
def app():
    servers = []

    def start(path=None):
        server, url, session = serve(path, 0, False, key="testkey")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return url.split("/?")[0]
    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def get(base, path, key="testkey", headers=None, data=None):
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{base}{path}{sep}key={key}", headers=headers or {}, data=data,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req) as res:
        return res.status, res.headers.get("Content-Type"), res.read()


def wait_report(base):
    for _ in range(200):
        _, _, body = get(base, "/api/state")
        state = json.loads(body)
        if not state["busy"]:
            return state
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_wrong_or_missing_key_is_refused(app, make):
    base = app()
    for key in ("", "nope"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base, "/api/state", key=key)
        assert e.value.code == 403


def test_requests_from_another_site_are_refused(app):
    base = app()
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/state", headers={"Origin": "https://example.com"})
    assert e.value.code == 403
    assert get(base, "/api/state", headers={"Origin": base})[0] == 200


def test_requests_naming_another_host_are_refused(app):
    base = app()
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/state", headers={"Host": "evil.example"})
    assert e.value.code == 403


def test_the_page_carries_the_key(app):
    base = app()
    _, kind, body = get(base, "/")
    assert kind.startswith("text/html") and b'"testkey"' in body and b"__KEY__" not in body


def test_static_files_cannot_reach_outside_their_folder(app):
    base = app()
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/static/../server.py")
    assert e.value.code == 404
    assert get(base, "/static/app.js")[0] == 200


def test_report_gives_positions_on_the_rendered_page(app, make):
    # registration colour inside the trim, at a known place: x 100-150, y 100-150 in PDF points
    reg = {"/CS0": registration_space()}
    path = make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace=reg)
    state = wait_report(app(path))
    report = state["report"]
    assert report["status"] == "FAIL"
    finding = next(f for f in report["findings"] if f["check"] == "Registration inside trim")
    view = finding["locations"][0]["view"]
    height = MEDIA[3] - MEDIA[1]
    # the rendered page measures from its top-left corner
    assert view == pytest.approx([100, height - 150, 150, height - 100], abs=0.05)
    g = report["page_geometry"][0]
    assert g["trim"] == pytest.approx([TRIM[0], height - TRIM[3], TRIM[2], height - TRIM[1]], abs=0.05)
    assert g["bleed"] == pytest.approx([BLEEDBOX[0], height - BLEEDBOX[3], BLEEDBOX[2], height - BLEEDBOX[1]], abs=0.05)


def test_rotated_page_positions(app, make):
    reg = {"/CS0": registration_space()}
    path = make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace=reg, rotate=90)
    report = wait_report(app(path))["report"]
    g = report["page_geometry"][0]
    assert g["width"] == pytest.approx(MEDIA[3] - MEDIA[1], abs=0.05)  # turned: width and height swap
    view = next(f for f in report["findings"] if f["check"] == "Registration inside trim")["locations"][0]["view"]
    assert view == pytest.approx([100, 100, 150, 150], abs=0.05)


def test_page_render(app, make):
    base = app(make(b"0 0 0 1 k 100 100 50 50 re f"))
    wait_report(base)
    status, kind, body = get(base, "/api/page?n=1&w=300")
    assert status == 200 and kind == "image/png" and body[:4] == b"\x89PNG"
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/page?n=9&w=300")
    assert e.value.code == 400


def test_opening_something_that_is_not_a_pdf(app, tmp_path):
    base = app()
    not_pdf = tmp_path / "notes.txt"
    not_pdf.write_text("hello")
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/open", data=json.dumps({"path": str(not_pdf)}).encode())
    assert e.value.code == 400


def test_opening_a_file_never_changes_it(app, make):
    import hashlib
    path = make(b"0 0 0 1 k " + rect(MEDIA) + b"f")
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    base = app()
    get(base, "/api/open", data=json.dumps({"path": path}).encode())
    wait_report(base)
    get(base, "/api/page?n=1&w=500")
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before
