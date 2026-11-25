"""The local app's server: access rules, the report it serves, page renders."""
import json
import os
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


# --- propose
def wait_preview(base):
    for _ in range(400):
        state = json.loads(get(base, "/api/state")[2])
        if not state["preview_busy"]:
            return state
        time.sleep(0.05)
    raise AssertionError("preview never finished")


def no_bleed(make):
    from conftest import TRIM
    return make(b"0 0 0 1 k " + rect(TRIM) + b"f", media=TRIM, trim=None, bleed=None)


def test_preview_shows_a_fix_before_and_after(app, make):
    import hashlib
    path = no_bleed(make)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    base = app(path)
    report = wait_report(base)["report"]
    i = next(i for i, f in enumerate(report["findings"]) if f["check"] == "Bleed artwork")
    assert "add-bleed" in report["suggestions"][i]
    get(base, "/api/preview", data=json.dumps({"fix": "add-bleed", "options": ["--marks"]}).encode())
    preview = wait_preview(base)["preview"]
    assert preview["error"] is None and not preview["empty"]
    assert preview["effect"]["status_before"] == "FAIL"
    assert any(f["check"] == "Bleed artwork" for f in preview["effect"]["gone"])
    status, kind, body = get(base, "/api/page?n=1&w=300&which=after")
    assert kind == "image/png" and body[:4] == b"\x89PNG"
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before  # never the user's file


def test_preview_files_stay_in_a_private_temporary_folder(app, make, tmp_path):
    path = no_bleed(make)
    folder_before = set(os.listdir(os.path.dirname(path)))
    base = app(path)
    wait_report(base)
    get(base, "/api/preview", data=json.dumps({"fix": "add-bleed", "options": []}).encode())
    wait_preview(base)
    assert set(os.listdir(os.path.dirname(path))) == folder_before


def test_preview_with_nothing_to_change(app, make):
    base = app(make(b"0 0 0 1 k 100 100 50 50 re f"))
    wait_report(base)
    get(base, "/api/preview", data=json.dumps({"fix": "trim-from-marks", "options": []}).encode())
    preview = wait_preview(base)["preview"]
    assert preview["empty"] and preview["effect"] is None
    with pytest.raises(urllib.error.HTTPError):
        get(base, "/api/page?n=1&w=300&which=after")


def test_preview_rejects_bad_options_and_unknown_fixes(app, make):
    base = app(make(b"0 0 0 1 k 100 100 50 50 re f"))
    wait_report(base)
    for body in ({"fix": "scale", "options": ["--trim", "big"]}, {"fix": "make-it-nice", "options": []}):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base, "/api/preview", data=json.dumps(body).encode())
        assert e.value.code == 400


def test_a_failing_fix_is_reported_not_hidden(app, make, monkeypatch):
    import prepress_preflight.fixes as fixes
    from prepress_preflight.fixes.append import AppendError
    propose, _ = fixes.FIXES["add-bleed"]

    def broken(p, dst):
        raise AppendError("verification failed: test")
    monkeypatch.setitem(fixes.FIXES, "add-bleed", (propose, broken))
    base = app(no_bleed(make))
    wait_report(base)
    get(base, "/api/preview", data=json.dumps({"fix": "add-bleed", "options": []}).encode())
    preview = wait_preview(base)["preview"]
    assert "verification failed" in preview["error"]


# --- review and export
def post(base, path, body):
    return json.loads(get(base, path, data=json.dumps(body).encode())[2])


def banner_like(make):
    from test_fixes import banner
    return banner(make)


def test_review_and_export_a_chain(app, make, tmp_path):
    import hashlib
    from prepress_preflight.engine import run
    src = banner_like(make)  # wrong trim box, marks inside it
    original = hashlib.sha256(open(src, "rb").read()).hexdigest()
    base = app(src)
    wait_report(base)
    post(base, "/api/preview", {"fix": "trim-from-marks", "options": []})
    wait_preview(base)
    state = post(base, "/api/keep", {})
    assert [q["fix"] for q in state["queue"]] == ["trim-from-marks"]
    # the next preview is made on top of the kept fix
    post(base, "/api/preview", {"fix": "add-marks", "options": ["--clip-to-bleed"]})
    preview = wait_preview(base)["preview"]
    assert preview["error"] is None and not preview["empty"]
    state = post(base, "/api/keep", {})
    assert [q["fix"] for q in state["queue"]] == ["trim-from-marks", "add-marks"]
    assert state["queue_effect"]["status_after"] in ("PASS", "WARN")
    dst = tmp_path / "out" / "banner fixed.pdf"
    dst.parent.mkdir()
    state = post(base, "/api/export", {"path": str(dst), "confirm": True})
    assert state["written"] == str(dst) and dst.exists()
    assert hashlib.sha256(open(src, "rb").read()).hexdigest() == original
    with open(dst, "rb") as f:
        assert hashlib.sha256(f.read(os.path.getsize(src))).hexdigest() == original
    assert [f for f in run(str(dst)).findings if f.check == "Trim marks" and "agree" in f.message]


def test_export_needs_a_confirmation_and_a_new_name(app, make, tmp_path):
    base = app(no_bleed(make))
    wait_report(base)
    post(base, "/api/preview", {"fix": "add-bleed", "options": []})
    wait_preview(base)
    post(base, "/api/keep", {})
    dst = tmp_path / "out.pdf"
    for body in ({"path": str(dst)}, {"path": str(dst), "confirm": "yes"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            post(base, "/api/export", body)
        assert e.value.code == 400
    assert not dst.exists()
    dst.write_bytes(b"keep me")
    with pytest.raises(urllib.error.HTTPError):
        post(base, "/api/export", {"path": str(dst), "confirm": True})
    assert dst.read_bytes() == b"keep me"


def test_export_with_nothing_kept_is_refused(app, make, tmp_path):
    base = app(no_bleed(make))
    wait_report(base)
    with pytest.raises(urllib.error.HTTPError):
        post(base, "/api/export", {"path": str(tmp_path / "out.pdf"), "confirm": True})


def test_removing_a_step_rebuilds_the_rest(app, make):
    base = app(banner_like(make))
    wait_report(base)
    for fix, opts in (("trim-from-marks", []), ("add-marks", ["--clip-to-bleed"])):
        post(base, "/api/preview", {"fix": fix, "options": opts})
        wait_preview(base)
        post(base, "/api/keep", {})
    post(base, "/api/remove", {"index": 1})
    state = wait_preview(base)
    assert [q["fix"] for q in state["queue"]] == ["trim-from-marks"]
    post(base, "/api/remove", {"index": 0})
    state = wait_preview(base)
    assert state["queue"] == [] and state["queue_effect"] is None


def test_keep_needs_a_preview_that_changes_something(app, make):
    base = app(make(b"0 0 0 1 k 100 100 50 50 re f"))
    wait_report(base)
    with pytest.raises(urllib.error.HTTPError):
        post(base, "/api/keep", {})


def test_inks_and_plate_renders(app, make):
    from test_spots import spot
    base = app(make(b"/CS0 cs 1 scn 100 100 50 50 re f", colorspace={"/CS0": spot("PANTONE 185 C")}))
    wait_report(base)
    inks = json.loads(get(base, "/api/inks?n=1")[2])["inks"]
    assert [i["name"] for i in inks][-1] == "PANTONE 185 C" and inks[-1]["coverage"] > 0
    status, kind, body = get(base, "/api/page?n=1&w=300&plate=PANTONE%20185%20C")
    assert kind == "image/png" and body[:4] == b"\x89PNG"
    with pytest.raises(urllib.error.HTTPError):
        get(base, "/api/page?n=1&w=300&plate=Nope")


def test_recent_files_are_remembered_most_recent_first(app, make, tmp_path):
    a, b = make(b"0 0 0 1 k 100 100 50 50 re f", name="a.pdf"), make(b"0 0 0 1 k 100 100 50 50 re f", name="b.pdf")
    base = app()
    for path in (a, b, a):
        post(base, "/api/open", {"path": path})
        wait_report(base)
    recent = json.loads(get(base, "/api/recent")[2])["recent"]
    assert [r["name"] for r in recent] == ["a.pdf", "b.pdf"]
    assert all(r["exists"] for r in recent)
    os.remove(b)
    assert [r["exists"] for r in json.loads(get(base, "/api/recent")[2])["recent"]] == [True, False]


def test_recent_files_stay_out_of_the_real_home_folder(app, make):
    import pathlib
    from prepress_preflight.app.server import recent_file
    assert pathlib.Path.home() not in recent_file().parents


def test_a_dropped_pdf_is_opened_where_it_is(make):
    from prepress_preflight.app.server import Session
    from prepress_preflight.app.window import handle_drop
    from prepress_preflight.checks import Options
    session = Session(Options())
    path = make(b"0 0 0 1 k 100 100 50 50 re f")
    try:
        assert handle_drop({"dataTransfer": {"files": [{"name": "t.pdf", "pywebviewFullPath": path}]}}, session) is None
        assert session.state()["path"] == path
        assert handle_drop({"dataTransfer": {"files": [{"name": "a.jpg", "pywebviewFullPath": "/x/a.jpg"}]}}, session)
        assert handle_drop({"dataTransfer": {"files": []}}, session)
        missing = {"dataTransfer": {"files": [{"name": "gone.pdf", "pywebviewFullPath": "/nowhere/gone.pdf"}]}}
        assert "exists" in handle_drop(missing, session)
    finally:
        session.close()


def test_objects_under_a_point(app, make):
    base = app(make(b"0 0 0 1 k 100 100 100 100 re f 1 0 0 0 k 150 150 100 100 re f"))
    wait_report(base)
    height = MEDIA[3] - MEDIA[1]
    _, kind, body = get(base, f"/api/objects?n=1&x=175&y={height - 175}")
    assert kind.startswith("application/json")
    assert [o["colour"] for o in json.loads(body)["objects"]] == ["C100 M0 Y0 K0", "C0 M0 Y0 K100"]
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/objects?n=5&x=1&y=1")
    assert e.value.code == 400


def test_object_details(app, make):
    base = app(make(b"0 0 0 1 k 100 100 100 100 re f"))
    wait_report(base)
    height = MEDIA[3] - MEDIA[1]
    hit = json.loads(get(base, f"/api/objects?n=1&x=150&y={height - 150}")[2])["objects"][0]
    d = json.loads(get(base, f"/api/object?n=1&id={hit['id']}")[2])["object"]
    assert d["colour"] == "C0 M0 Y0 K100" and d["source"]["excerpt"][-1] == "f"
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/object?n=1&id=99")
    assert e.value.code == 400



def test_object_position_uses_the_trim_from_crop_marks(app, make):
    # the banner case: the trim box is the whole page, the crop marks show the real trim
    from test_marks_and_dielines import crop_marks
    box = (TRIM[0] + 10 * MM, TRIM[1] + 10 * MM, TRIM[0] + 30 * MM, TRIM[1] + 20 * MM)
    content = crop_marks(TRIM, offset=1 * MM, length=6 * MM) + b"0 0 0 1 k " + rect(box) + b"f"
    base = app(make(content, colorspace={"/CS0": registration_space()}, trim=MEDIA, bleed=MEDIA))
    wait_report(base)
    height = MEDIA[3] - MEDIA[1]
    hit = json.loads(get(base, f"/api/objects?n=1&x={box[0] + 5}&y={height - box[1] - 5}")[2])["objects"][0]
    place = json.loads(get(base, f"/api/object?n=1&id={hit['id']}")[2])["object"]["place"]
    assert place["trim"] == "inferred from the crop marks"
    # 10 mm in from the trim's left; 60 - 20 = 40 mm down from its top
    assert place["from_trim_left_mm"] == pytest.approx(10, abs=0.05)
    assert place["from_trim_top_mm"] == pytest.approx(40, abs=0.05)


def test_knock_out_an_object_chosen_in_the_inspector(app, make, tmp_path):
    import pikepdf
    from prepress_preflight.content import walk_page
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
    base = app(make(b"0 0 0 1 k 100 100 100 100 re f /GS0 gs 0 0 0 0 k 120 120 20 20 re f", extgstate=gs))
    wait_report(base)
    height = MEDIA[3] - MEDIA[1]
    hit = json.loads(get(base, f"/api/objects?n=1&x=130&y={height - 130}")[2])["objects"][0]
    d = json.loads(get(base, f"/api/object?n=1&id={hit['id']}")[2])["object"]
    assert d["colour"] == "C0 M0 Y0 K0" and d["overprint_now"] and d["editable"]
    post(base, "/api/preview", {"fix": "object-overprint",
                                "options": ["--object", f"1:{d['id']}", "--overprint", "off", "--expect", d["fingerprint"]]})
    preview = wait_preview(base)["preview"]
    assert preview["error"] is None and "knock out" in preview["reasons"][0]["text"]
    post(base, "/api/keep", {})
    dst = tmp_path / "out.pdf"
    post(base, "/api/export", {"path": str(dst), "confirm": True})
    with pikepdf.open(dst) as pdf:
        assert [e.overprint for e in walk_page(pdf.pages[0]).events] == [False, False]


def test_white_overprint_suggests_knocking_it_out(app, make):
    import pikepdf
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
    report = wait_report(app(make(b"/GS0 gs 0 0 0 0 k 120 120 20 20 re f", extgstate=gs)))["report"]
    i = next(i for i, f in enumerate(report["findings"]) if f["check"] == "White overprint")
    assert report["suggestions"][i] == ["knock-out-white"]


def test_save_report(app, make, tmp_path):
    import pikepdf
    gs = {"/GS0": pikepdf.Dictionary(OP=True, op=True, OPM=1)}
    base = app(make(b"/GS0 gs 0 0 0 0 k 120 120 20 20 re f", extgstate=gs))
    wait_report(base)
    dst = tmp_path / "job report.html"  # given, so no save dialog opens
    assert post(base, "/api/report", {"path": str(dst)})["written"] == str(dst)
    assert "White overprint" in dst.read_text()
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/report", {"path": str(dst)})
    assert e.value.code == 400  # never overwritten


def test_export_compact(app, make, tmp_path):
    src = banner_like(make)
    base = app(src)
    wait_report(base)
    post(base, "/api/preview", {"fix": "trim-from-marks", "options": []})
    wait_preview(base)
    post(base, "/api/keep", {})
    dst = tmp_path / "small.pdf"
    r = post(base, "/api/export", {"path": str(dst), "confirm": True, "compact": True})
    assert r["written"] == str(dst) and r["size"] == os.path.getsize(dst) and r["original_size"] == os.path.getsize(src)


def test_remove_an_object_chosen_in_the_inspector(app, make, tmp_path):
    import pikepdf
    from prepress_preflight.content import walk_page
    base = app(make(b"0 0 0 1 k 100 100 100 100 re f 1 0 0 0 k 120 120 20 20 re f"))
    wait_report(base)
    height = MEDIA[3] - MEDIA[1]
    hit = json.loads(get(base, f"/api/objects?n=1&x=130&y={height - 130}")[2])["objects"][0]
    d = json.loads(get(base, f"/api/object?n=1&id={hit['id']}")[2])["object"]
    post(base, "/api/preview", {"fix": "remove-object", "options": ["--object", f"1:{d['id']}", "--expect", d["fingerprint"]]})
    preview = wait_preview(base)["preview"]
    assert preview["error"] is None and "Removes the filled shape in C100 M0 Y0 K0" in preview["reasons"][0]["text"]
    post(base, "/api/keep", {})
    dst = tmp_path / "out.pdf"
    post(base, "/api/export", {"path": str(dst), "confirm": True})
    with pikepdf.open(dst) as pdf:
        assert [e.colour for e in walk_page(pdf.pages[0]).events] == [(0.0, 0.0, 0.0, 1.0)]


def test_recolour_an_object_chosen_in_the_inspector(app, make, tmp_path):
    import pikepdf
    from prepress_preflight.content import walk_page
    base = app(make(b"0 0 0 1 k 100 100 100 100 re f 1 0 0 0 k 120 120 20 20 re f"))
    wait_report(base)
    height = MEDIA[3] - MEDIA[1]
    hit = json.loads(get(base, f"/api/objects?n=1&x=130&y={height - 130}")[2])["objects"][0]
    d = json.loads(get(base, f"/api/object?n=1&id={hit['id']}")[2])["object"]
    assert d["recolourable"]
    post(base, "/api/preview", {"fix": "recolour-object",
                                "options": ["--object", f"1:{d['id']}", "--cmyk", "0,100,0,0", "--expect", d["fingerprint"]]})
    preview = wait_preview(base)["preview"]
    assert preview["error"] is None and "to C0 M100 Y0 K0" in preview["reasons"][0]["text"]
    post(base, "/api/keep", {})
    dst = tmp_path / "out.pdf"
    post(base, "/api/export", {"path": str(dst), "confirm": True})
    with pikepdf.open(dst) as pdf:
        assert [e.colour for e in walk_page(pdf.pages[0]).events] == [(0.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 0.0)]


def test_black_that_knocks_out_suggests_overprinting_it(app, make):
    report = wait_report(app(make(b"0 0 0 1 K 0.5 w 60 60 m 300 60 l S")))["report"]
    i = next(i for i, f in enumerate(report["findings"]) if f["check"] == "Black overprint")
    assert report["suggestions"][i] == ["overprint-black"]


def test_small_rich_black_text_suggests_making_it_k(app, make):
    import pikepdf
    font = lambda pdf: pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    report = wait_report(app(make(b"BT /F1 7 Tf 0.75 0.68 0.67 0.9 k 100 100 Td (x) Tj ET", font={"/F1": font})))["report"]
    i = next(i for i, f in enumerate(report["findings"]) if f["check"] == "Small text inks")
    assert report["suggestions"][i] == ["rich-black-text"]


def test_total_ink_fail_suggests_reducing_it(app, make):
    report = wait_report(app(make(b"1 1 1 1 k 100 100 100 100 re f")))["report"]
    i = next(i for i, f in enumerate(report["findings"]) if f["check"] == "Total ink" and f["severity"] == "fail")
    assert report["suggestions"][i] == ["reduce-ink"]


def test_choosing_a_job_checks_again_with_its_settings(app, make, tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "press.txt").write_text("# Coated sheetfed\n--ink-limit 300 --safe-margin 3\n")
    monkeypatch.setenv("PREFLIGHT_JOBS", str(jobs))
    base = app(make(b"0.9 0.9 0.9 0.2 k 100 100 100 100 re f"))  # 290%: fine at 300 and at 400
    state = wait_report(base)
    assert state["job"] is None and [j["name"] for j in state["jobs"]] == ["press"]
    assert "400% total ink" in state["settings"]
    state = post(base, "/api/job", {"name": "press"})
    state = wait_report(base)
    assert state["job"] == "press" and "300% total ink" in state["settings"] and "3 mm safe margin" in state["settings"]
    post(base, "/api/job", {"name": ""})
    assert "400% total ink" in wait_report(base)["settings"]


def test_previews_are_judged_with_the_jobs_settings(app, make, tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "press.txt").write_text("--ink-limit 300\n")
    monkeypatch.setenv("PREFLIGHT_JOBS", str(jobs))
    base = app(make(b"1 1 1 1 k 100 100 100 100 re f 0 0 0 1 K 0.5 w 60 60 m 300 60 l S"))
    wait_report(base)
    post(base, "/api/job", {"name": "press"})
    before = [f for f in wait_report(base)["report"]["findings"] if f["check"] == "Total ink" and f["severity"] == "fail"]
    assert before
    # an unrelated fix must not make the 300% Total ink fail look "gone" by checking at 400%
    post(base, "/api/preview", {"fix": "overprint-black", "options": []})
    effect = wait_preview(base)["preview"]["effect"]
    assert not any("Total ink" in str(effect[k]) for k in ("gone", "new", "changed")), effect
    # reduce-ink with no limit typed takes the job's
    post(base, "/api/preview", {"fix": "reduce-ink", "options": []})
    preview = wait_preview(base)["preview"]
    assert preview["error"] is None and "over 300% total ink" in preview["reasons"][0]["text"]
    post(base, "/api/keep", {})
    with pytest.raises(urllib.error.HTTPError) as err:
        post(base, "/api/job", {"name": ""})
    assert "Review" in json.loads(err.value.read())["error"]


def test_annotations_suggest_flattening_or_removing(app, make, tmp_path):
    import pikepdf
    path = make(b"0 0 0 1 k 30 30 20 20 re f")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj["/Annots"] = pikepdf.Array([
            pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Stamp, Rect=[100, 100, 150, 150], F=4)),
            pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text, Rect=[200, 100, 220, 120], F=0))])
        pdf.save(path)
    report = wait_report(app(path))["report"]
    got = {f["severity"]: s for f, s in zip(report["findings"], report["suggestions"]) if f["check"] == "Annotations"}
    assert got == {"warn": ["flatten-annotations", "remove-annotations"], "note": ["remove-annotations"]}


def test_layers_suggest_removing_one(app, make):
    import pikepdf
    path = make(b"0 0 0 1 k 30 30 20 20 re f")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        g = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("Template")))
        pdf.Root["/OCProperties"] = pikepdf.Dictionary(OCGs=[g], D=pikepdf.Dictionary(OFF=[g]))
        pdf.save(path)
    report = wait_report(app(path))["report"]
    got = [s for f, s in zip(report["findings"], report["suggestions"]) if f["check"] == "Layers"]
    assert got == [["remove-layer"]]


def test_knocking_out_dieline_suggests_overprinting_it(app, make):
    import pikepdf
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1)
    die = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Dieline"), pikepdf.Name.DeviceCMYK, tint])
    report = wait_report(app(make(b"/Die CS 1 SCN 60 60 180 80 re S", colorspace={"/Die": die})))["report"]
    got = [s for f, s in zip(report["findings"], report["suggestions"]) if f["check"] == "Dielines" and f["severity"] == "warn"]
    assert got == [["overprint-spot"]]


def test_images_over_450_ppi_suggest_downsampling(app, make):
    import zlib
    import pikepdf

    def image(pdf):
        return pdf.make_stream(zlib.compress(bytes(600 * 600)), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=600, Height=600, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8,
                               Filter=pikepdf.Name.FlateDecode)
    report = wait_report(app(make(b"q 72 0 0 72 60 60 cm /Im0 Do Q", xobject={"/Im0": image})))["report"]
    got = [s for f, s in zip(report["findings"], report["suggestions"]) if f["check"] == "Image resolution"]
    assert got == [["downsample-images"]]


def test_tagged_cmyk_suggests_untagging(app, make):
    import pikepdf

    def cs(pdf):
        return pikepdf.Array([pikepdf.Name.ICCBased, pdf.make_stream(b"x", N=4, Alternate=pikepdf.Name.DeviceCMYK)])
    path = make(b"/CS0 cs 0 0 0 1 scn 60 60 40 40 re f")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].resources["/ColorSpace"] = pikepdf.Dictionary({"/CS0": cs(pdf)})
        pdf.save(path)
    report = wait_report(app(path))["report"]
    got = [s for f, s in zip(report["findings"], report["suggestions"]) if f["check"] == "CMYK profiles"]
    assert got == [["untag-cmyk"]]
