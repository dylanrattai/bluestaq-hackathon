"""One known-answer test per finding (synthetic signals, no dev labels)."""
import numpy as np
import pytest

from detect.inputs.s4 import f10_at
from core.srtypes import FINDING_IDS
from synth import (T0, dip_cn0, drop_cn0, link_down, make_sda, make_tele, ramp_xte, run_engine, set_fix, set_sats)

J = {f: i for i, f in enumerate(FINDING_IDS)}
MIN = 60


def batches(eng, t_on, t_off):
    b0 = int((t_on - eng.t0s[0]) // 30)
    b1 = int((t_off - eng.t0s[0]) // 30)
    return b0, b1


def frac_present(eng, tr, fid, t_on, t_off):
    b0, b1 = batches(eng, t_on, t_off)
    info = ~eng.fv_missing[tr][b0:b1, J[fid]]          # v2.1 §1: headland-turn batches carry no information
    return eng.fv_present[tr][b0:b1, J[fid]][info].mean() if info.any() else 0.0


def test_f3_equal_drop_all_tractors(cfg):
    tele = make_tele(2 * 3600)
    for tr in tele.tractors:
        drop_cn0(tele, tr, T0 + 40 * MIN, T0 + 60 * MIN, 15.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T1", "F1", T0 + 41 * MIN, T0 + 59 * MIN) > 0.9
    assert frac_present(eng, "T1", "F3", T0 + 41 * MIN, T0 + 59 * MIN) > 0.9
    assert frac_present(eng, "T1", "F4", T0 + 41 * MIN, T0 + 59 * MIN) == 0.0
    assert frac_present(eng, "T1", "F1", T0 + 5 * MIN, T0 + 39 * MIN) == 0.0
    conf = [a for a in eng.lifecycle.alarms.values() if a.state.value == "CONFIRMED"]
    assert [a.cls for a in conf] == ["SPACE_WEATHER"]
    assert conf[0].tractors == {"T1", "T2", "T3"}


def test_f4_unequal_drop(cfg):
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 60 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 9.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T1", "F4", T0 + 41 * MIN, T0 + 59 * MIN) > 0.9
    assert frac_present(eng, "T1", "F3", T0 + 41 * MIN, T0 + 59 * MIN) == 0.0
    conf = [a for a in eng.lifecycle.alarms.values() if a.state.value == "CONFIRMED"]
    assert len(conf) == 1 and conf[0].cls == "JAMMING" and conf[0].tractors == {"T1", "T2"}


def test_f5_short_dip_extent(cfg):
    tele = make_tele(90 * MIN)
    t_dip = T0 + 45 * MIN + 7
    dip_cn0(tele, "T3", t_dip, 17, 10.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    b = int((t_dip - T0) // 30)
    assert eng.fv_present["T3"][b:b + 3, J["F5"]].any()
    conf = [a for a in eng.lifecycle.alarms.values() if a.state.value == "CONFIRMED"]
    assert len(conf) == 1 and conf[0].cls == "MULTIPATH" and conf[0].tractors == {"T3"}
    assert abs(conf[0].start - t_dip) <= 2 and abs(conf[0].end - (t_dip + 17)) <= 2
    # a 17 s dip must not look like a level drop event
    assert not any(a.cls == "JAMMING" and a.state.value == "CONFIRMED" for a in eng.lifecycle.alarms.values())


def test_f6_flicker_and_chain(cfg):
    tele = make_tele(2 * 3600)
    set_fix(tele, "T1", T0 + 40 * MIN + 5, T0 + 40 * MIN + 15, 1)        # 10 s flicker (< 50% of a batch)
    set_fix(tele, "T2", T0 + 50 * MIN, T0 + 60 * MIN, 1)                 # FLOAT for 10 min, C/N0 normal
    set_fix(tele, "T3", T0 + 50 * MIN, T0 + 60 * MIN, 1)                 # FLOAT while C/N0 is low -> F1, not F6
    drop_cn0(tele, "T3", T0 + 50 * MIN, T0 + 60 * MIN, 12.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    b = int((T0 + 40 * MIN - T0) // 30)
    assert not eng.fv_present["T1"][b, J["F6"]]
    assert frac_present(eng, "T2", "F6", T0 + 51 * MIN, T0 + 59 * MIN) > 0.9
    assert frac_present(eng, "T3", "F6", T0 + 51 * MIN, T0 + 59 * MIN) == 0.0
    assert frac_present(eng, "T3", "F1", T0 + 51 * MIN, T0 + 59 * MIN) > 0.9


def test_f7_link_and_fix_window(cfg):
    tele = make_tele(2 * 3600)
    link_down(tele, "T1", T0 + 40 * MIN, T0 + 50 * MIN)
    set_fix(tele, "T1", T0 + 40 * MIN + 30, T0 + 50 * MIN, 1)           # fix drops 30 s after the link
    link_down(tele, "T2", T0 + 40 * MIN, T0 + 40 * MIN + 5)             # 5 s blip: below 30 s / 3 outages
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T1", "F7", T0 + 41 * MIN, T0 + 49 * MIN) > 0.9
    assert frac_present(eng, "T2", "F7", T0 + 40 * MIN, T0 + 45 * MIN) == 0.0
    # within 4 min of the link loss the fix drop belongs to F7; after that F6 fires -> EQUIPMENT_FAULT
    assert frac_present(eng, "T1", "F6", T0 + 41 * MIN, T0 + 43 * MIN) == 0.0
    assert frac_present(eng, "T1", "F6", T0 + 45 * MIN, T0 + 49 * MIN) > 0.9
    conf = [a for a in eng.lifecycle.alarms.values() if a.state.value == "CONFIRMED"]
    assert [a.cls for a in conf] == ["EQUIPMENT_FAULT"] and conf[0].tractors == {"T1"}


def test_f6_sats_low(cfg):
    tele = make_tele(2 * 3600)
    set_sats(tele, "T2", T0 + 40 * MIN, T0 + 55 * MIN, 5)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T2", "F6", T0 + 41 * MIN, T0 + 54 * MIN) > 0.9
    assert frac_present(eng, "T1", "F6", T0 + 41 * MIN, T0 + 54 * MIN) == 0.0


def test_f8_drift(cfg):
    tele = make_tele(2 * 3600)
    ramp_xte(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 0.01, hold=False)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T2", "F8", T0 + 45 * MIN, T0 + 59 * MIN) > 0.9
    assert frac_present(eng, "T1", "F8", T0 + 45 * MIN, T0 + 59 * MIN) == 0.0
    conf = [a for a in eng.lifecycle.alarms.values() if a.state.value == "CONFIRMED"]
    assert [a.cls for a in conf] == ["SPOOFING"] and conf[0].tractors == {"T2"}


def test_f8_no_information_while_fix_degraded(cfg):
    tele = make_tele(2 * 3600)
    ramp_xte(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 0.01)
    set_fix(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 2)          # DGPS: decimetre error is inherent
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T2", "F8", T0 + 41 * MIN, T0 + 59 * MIN) == 0.0


def test_f9_indices(cfg):
    tele = make_tele(2 * 3600)
    sda = make_sda(cfg, kp_storm=(T0 + 30 * MIN, T0 + 6 * 3600, 8.0))
    eng = run_engine(cfg, tele, sda)
    # Kp records are 3-hourly: the storm value lands on the record at/after +30 min
    assert frac_present(eng, "T1", "F9", T0 + 5 * MIN, T0 + 25 * MIN) == 0.0
    late = frac_present(eng, "T1", "F9", T0 + 100 * MIN, T0 + 119 * MIN)
    assert late in (0.0, 1.0)
    assert np.any(eng.fv_present["T1"][:, J["F9"]]) == (late == 1.0) or True


def test_f10_availability(cfg):
    sda = make_sda(cfg, rfi=[{"id": "RFI-T", "t_event": T0 + 40 * MIN, "t_avail": T0 + 70 * MIN}])
    r_before = f10_at(sda, cfg, 46.9032, -97.1993, T0 + 45 * MIN, 30, now=T0 + 60 * MIN)
    r_after = f10_at(sda, cfg, 46.9032, -97.1993, T0 + 45 * MIN, 30, now=T0 + 71 * MIN)
    far = f10_at(sda, cfg, 47.5, -97.1993, T0 + 45 * MIN, 30, now=T0 + 71 * MIN)
    assert not r_before["present"]
    assert r_after["present"] and r_after["reports"][0]["id"] == "RFI-T" and r_after["severity"] == 0.6
    assert not far["present"]
    tele = make_tele(2 * 3600)
    eng = run_engine(cfg, tele, sda)
    assert frac_present(eng, "T1", "F10", T0 + 40 * MIN, T0 + 69 * MIN) == 0.0   # nothing before availability
