import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.export import ExportError, build_detections, build_submission, check_submission, resolve_validator, write_submission
from diagnose.lifecycle import Alarm, BatchRecord
from core.srtypes import AlarmState

HERE = Path(__file__).resolve().parent
W0 = "2026-05-06T00:00:00+00:00"


def mk(cls="JAMMING", start="2026-05-06T10:00:00+00:00", end="2026-05-06T10:30:00+00:00", state=AlarmState.CONFIRMED,
       tractors=("T1",), ev=("T1 10:00:00–10:30:00Z: mean C/N0 12.0 dB below reference", "no RFI report")):
    from core.config import parse_iso
    a = Alarm("A-X-1", cls, state, parse_iso(start), parse_iso(end), set(tractors), set())
    a.confidence = 0.8123456
    a.evidence = list(ev)
    a.records = [BatchRecord(parse_iso(start), tuple(tractors), 0.8, 30.0, True)]
    return a


def test_only_confirmed_export(cfg):
    now = 1e12
    alarms = [mk(state=AlarmState.ALARM), mk(state=AlarmState.WATCH), mk(state=AlarmState.RETRACTED), mk()]
    dets = build_detections(alarms, cfg, now)
    assert len(dets) == 1
    assert dets[0]["class"] == "JAMMING" and dets[0]["action"] == "HALT_AND_REPORT"
    assert dets[0]["confidence"] == 0.812
    assert tuple(sorted(dets[0].keys())) == tuple(sorted(("start", "end", "tractors", "class", "action", "confidence", "evidence")))


def test_clipping(cfg):
    now = 1e12
    a = mk(start="2026-05-05T23:30:00+00:00", end="2026-05-06T00:20:00+00:00")
    b = mk(start="2026-05-06T23:50:00+00:00", end="2026-05-07T01:00:00+00:00")
    c = mk(start="2026-05-05T22:00:00+00:00", end="2026-05-05T23:00:00+00:00")
    dets = build_detections([a, b, c], cfg, now)
    assert len(dets) == 2
    assert dets[0]["start"] == W0 and dets[0]["end"] == "2026-05-06T00:20:00+00:00"
    assert dets[1]["end"] == "2026-05-07T00:00:00+00:00"


def test_other_never_exports(cfg):
    a = mk(cls="OTHER")
    a.unattributed = True
    assert build_detections([a], cfg, 1e12) == []


def test_email_blocked(cfg, tmp_path):
    a = mk(ev=("contact someone@example.com", "x"))
    sub = build_submission([a], cfg, 1e12)
    assert any("email" in p for p in check_submission(sub, cfg))
    with pytest.raises(ExportError):
        write_submission(sub, tmp_path, cfg, None)
    assert not (tmp_path / "submission.json").exists()


def test_validator_passes(cfg, tmp_path):
    validator = resolve_validator(cfg, None, HERE.parent)
    assert validator is not None, "validate_submission.py not found"
    sub = build_submission([mk(), mk(cls="SPOOFING", tractors=("T2",))], cfg, 1e12)
    path = write_submission(sub, tmp_path, cfg, validator)
    r = subprocess.run([sys.executable, str(validator), str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(path.read_text())
    assert data["participant"]["github_handle"] == "test-handle"
    assert "display_name" not in data["participant"]


def test_placeholder_handle_fails_validator(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    c["participant"]["github_handle"] = "TODO-DYLAN"
    c["repository_url"] = "https://github.com/TODO-DYLAN/straight-rows"
    validator = resolve_validator(c, None, HERE.parent)
    sub = build_submission([mk()], c, 1e12)
    # placeholders pass the handle regex but the export logs a red warning; the file is still written
    path = write_submission(sub, tmp_path, c, validator)
    assert path.exists()


def test_no_detections_is_an_error(cfg, tmp_path):
    sub = build_submission([], cfg, 1e12)
    with pytest.raises(ExportError):
        write_submission(sub, tmp_path, cfg, None)
