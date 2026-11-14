import copy
import json

import pytest

from runtime.export import build_submission
from diagnose.lifecycle import Alarm, BatchRecord
from core.srtypes import AlarmState
from synth import T0, drop_cn0, link_down, make_sda, make_tele, oscillate_cn0, ramp_xte, run_engine

MIN = 60


def confirmed(eng, cls=None):
    return [a for a in eng.lifecycle.alarms.values() if a.state == AlarmState.CONFIRMED and (cls is None or a.cls == cls)]


def test_two_tractor_jamming_is_one_alarm(cfg):
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 60 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 42 * MIN, T0 + 58 * MIN, 9.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    jam = confirmed(eng, "JAMMING")
    assert len(jam) == 1
    assert jam[0].tractors == {"T1", "T2"}
    assert abs(jam[0].start - (T0 + 40 * MIN)) <= 30 and abs(jam[0].end - (T0 + 60 * MIN)) <= 30


def test_one_batch_blip_not_sustained(cfg):
    tele = make_tele(50 * MIN)
    eng = run_engine(cfg, tele, make_sda(cfg))
    lc = eng.lifecycle
    a = lc.create("JAMMING", T0 + 40 * MIN, T0 + 40 * MIN, {"T1"}, AlarmState.ALARM, 50.0, end=T0 + 40 * MIN + 30)
    a.records = [BatchRecord(T0 + 40 * MIN, ("T1",), 0.6, 50.0, True)]
    eng.watchdog.run(T0 + 45 * MIN)
    assert a.state == AlarmState.RETRACTED and a.reason == "NOT_SUSTAINED"


def test_step_onset_relocalised(cfg):
    tele = make_tele(2 * 3600)
    t_on = T0 + 40 * MIN + 17
    drop_cn0(tele, "T1", t_on, T0 + 60 * MIN + 11, 18.0)
    drop_cn0(tele, "T2", t_on, T0 + 60 * MIN + 11, 9.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    jam = confirmed(eng, "JAMMING")
    assert len(jam) == 1
    assert abs(jam[0].start - t_on) <= 30
    assert abs(jam[0].end - (T0 + 60 * MIN + 11)) <= 30
    assert "start" in jam[0].relocalised or abs(jam[0].start - t_on) <= 5


def test_ramp_onset_hinge(cfg):
    tele = make_tele(2 * 3600)
    t_on = T0 + 40 * MIN + 17
    ramp_xte(tele, "T3", t_on, T0 + 65 * MIN, 0.004)
    eng = run_engine(cfg, tele, make_sda(cfg))
    sp = confirmed(eng, "SPOOFING")
    assert len(sp) == 1 and sp[0].tractors == {"T3"}
    assert abs(sp[0].start - t_on) <= 60


def test_hindsight_reclassification(cfg):
    """A mislabelled alarm over a span whose findings say SPACE_WEATHER is reclassified by the watchdog."""
    tele = make_tele(2 * 3600)
    for tr in tele.tractors:
        drop_cn0(tele, tr, T0 + 40 * MIN, T0 + 60 * MIN, 15.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    lc = eng.lifecycle
    sw = [a for a in lc.alarms.values() if a.cls == "SPACE_WEATHER" and a.state == AlarmState.CONFIRMED]
    assert len(sw) == 1
    bogus = lc.create("JAMMING", T0 + 61 * MIN, T0 + 40 * MIN, {"T1", "T2", "T3"}, AlarmState.ALARM, 50.0, end=T0 + 60 * MIN)
    bogus.records = [BatchRecord(r.t0, r.tractors, 0.5, 50.0, True, r.findings) for r in sw[0].records]
    run = eng.watchdog.run(T0 + 65 * MIN)
    assert bogus.state == AlarmState.RETRACTED and bogus.reason in ("RECLASSIFIED", "EXPLAINED_BY_OTHER_CAUSE")
    assert any("JAMMING" in x and ("->" in x or "explained" in x) for x in run.actions)


def test_late_rfi_strengthens_jamming(cfg):
    """A two-tractor unequal drop with a late RFI report: the report becomes evidence with hindsight (F10)."""
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 60 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 9.0)
    sda = make_sda(cfg, rfi=[{"id": "RFI-L", "t_event": T0 + 41 * MIN, "t_avail": T0 + 71 * MIN}])
    eng = run_engine(cfg, tele, sda)
    jam = confirmed(eng, "JAMMING")
    assert len(jam) == 1 and jam[0].tractors == {"T1", "T2"}
    assert any("RFI-L" in e for e in jam[0].evidence)


def _scenario():
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T1", T0 + 30 * MIN, T0 + 50 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 30 * MIN, T0 + 50 * MIN, 9.0)
    oscillate_cn0(tele, "T1", T0 + 30 * MIN, T0 + 50 * MIN)
    ramp_xte(tele, "T3", T0 + 70 * MIN, T0 + 100 * MIN, 0.005)
    return tele


def _events_before(eng, t_cut):
    return [(h["t_data"], h["alarm_id"], h["from"], h["to"], h["reason"], tuple(h["tractors"]))
            for a in eng.lifecycle.alarms.values() for h in a.history if h["t_data"] < t_cut]


def test_causality_truncation(cfg):
    tele = _scenario()
    sda = make_sda(cfg)
    full = run_engine(cfg, tele, sda)
    t_cut = T0 + 80 * MIN
    part = run_engine(cfg, tele.truncate(t_cut), sda)
    assert sorted(_events_before(full, t_cut)) == sorted(_events_before(part, t_cut))
    assert len(_events_before(full, t_cut)) > 5


def test_determinism_batch_vs_live(cfg):
    tele = _scenario()
    sda = make_sda(cfg)
    a = run_engine(cfg, tele, sda, speed=None)
    b = run_engine(cfg, tele, sda, speed=1e6)
    sa = json.dumps(build_submission(list(a.lifecycle.alarms.values()), cfg, a.now), sort_keys=False)
    sb = json.dumps(build_submission(list(b.lifecycle.alarms.values()), cfg, b.now), sort_keys=False)
    assert sa == sb
    assert json.loads(sa)["detections"]


def test_parked_alarm_retracted(cfg):
    tele = make_tele(2 * 3600, park_from=T0 + 20 * MIN)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 60 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 9.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert not confirmed(eng)
    assert any(a.reason == "PARKED" for a in eng.lifecycle.alarms.values())
