import json
import re

import pytest
from fastapi.testclient import TestClient

from runtime.api import create_app
from runtime.export import resolve_validator
from synth import T0, drop_cn0, make_sda, make_tele, ramp_xte, run_engine

MIN = 60


@pytest.fixture(scope="module")
def eng(cfg, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T1", T0 + 30 * MIN, T0 + 50 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 30 * MIN, T0 + 50 * MIN, 9.0)
    ramp_xte(tele, "T3", T0 + 70 * MIN, T0 + 100 * MIN, 0.005)
    from pathlib import Path
    validator = resolve_validator(cfg, None, Path(__file__).resolve().parent.parent)
    e = run_engine(cfg, tele, make_sda(cfg), out_dir=out, write_outputs=True, validator=validator)
    e.out = out
    return e


@pytest.fixture(scope="module")
def client(eng):
    return TestClient(create_app(eng))


def test_health_status(client):
    h = client.get("/health").json()
    assert h["ok"] is True and "data_time" in h and 0 <= h["progress"] <= 1
    s = client.get("/status").json()
    assert set(s["tractors"]) == {"T1", "T2", "T3"}
    for tr in s["tractors"].values():
        assert {"working", "present", "explanation", "top_bf"} <= set(tr)
    assert s["last_watchdog"] is not None


def test_alarms_and_filters(client):
    al = client.get("/alarms").json()
    assert al and {"alarm_id", "class", "state", "start", "end", "tractors", "confidence"} <= set(al[0])
    conf = client.get("/alarms", params={"state": "CONFIRMED"}).json()
    assert conf and all(a["state"] == "CONFIRMED" for a in conf)
    jam = client.get("/alarms", params={"cls": "JAMMING", "tractor": "T1"}).json()
    assert jam and all(a["class"] == "JAMMING" and "T1" in a["tractors"] for a in jam)
    one = client.get(f"/alarms/{conf[0]['alarm_id']}").json()
    assert {"history", "evidence", "records"} <= set(one)
    assert client.get("/alarms/nope").status_code == 404


def test_watchdog_runs(client):
    runs = client.get("/watchdog").json()
    assert 1 <= len(runs) <= 20 and {"t_data", "alarms", "actions"} <= set(runs[-1])


def test_submission_matches_export(client, eng):
    api_sub = client.get("/submission").json()
    file_sub = json.loads((eng.out / "submission.json").read_text())
    assert api_sub == file_sub
    assert file_sub["detections"]


def test_evidence_strings(eng):
    sub = json.loads((eng.out / "submission.json").read_text())
    for d in sub["detections"]:
        ev = d["evidence"]
        assert 2 <= len(ev) <= 6          # 5 + the v2.2 confidence line
        joined = " ".join(ev)
        assert re.search(r"\d\d:\d\d", joined), ev
        assert re.search(r"\bT[123]\b", joined), ev
        assert "@" not in joined


def test_stream_and_ui(client, eng):
    eng.stop_event.set()
    with client.stream("GET", "/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        first = next(r.iter_lines())
        assert "hello" in first
    eng.stop_event.clear()
    ui = client.get("/ui")
    assert ui.status_code == 200 and "<html" in ui.text and "/status" in ui.text
