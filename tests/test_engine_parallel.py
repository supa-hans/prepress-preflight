"""Large files run their slow checks in worker processes; the report must be exactly the same."""
import json

from prepress_preflight import engine


def test_parallel_and_serial_reports_are_identical(make, monkeypatch):
    path = make(b"1 0.7 0 0 k 0 0 400 400 re f 0 0 0 0 k 0.3 w 70 90 m 290 90 l S /GS0 gs 0 0 0 0 k 50 50 20 20 re f",
                extgstate={"/GS0": {"/OP": True, "/op": True, "/OPM": 1}})
    monkeypatch.setenv("PREFLIGHT_WORKERS", "1")
    serial = engine.run(path)
    monkeypatch.setenv("PREFLIGHT_WORKERS", "0")
    monkeypatch.setattr(engine, "PARALLEL_BYTES", 0)
    parallel = engine.run(path)
    assert engine._pool is not None  # the slow checks went to worker processes
    assert serial.findings and serial.status == "FAIL"
    assert json.dumps(serial.to_dict()) == json.dumps(parallel.to_dict()) and serial.geometry == parallel.geometry


def test_the_groups_name_real_checks():
    names = {cls.name for cls in engine.ALL_CHECKS}
    grouped = [n for g in engine.GROUPS for n in g]
    assert set(grouped) <= names and len(grouped) == len(set(grouped))
