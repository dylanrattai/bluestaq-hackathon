"""Tests shipped with Fix Spec v1.1 (synthetic signals only)."""
import numpy as np
import pytest

from detect.baseline import CusumChannel, solve_h
from diagnose.fusion import Fusion
from core.srtypes import FINDING_IDS, AlarmState
from synth import (T0, dip_cn0, dip_cn0_min, drop_cn0, link_down, make_sda, make_tele, oscillate_cn0, ramp_xte,
                   run_engine, set_fix, set_sats)

MIN = 60
J = {f: i for i, f in enumerate(FINDING_IDS)}


def confirmed(eng, cls=None):
    return [a for a in eng.lifecycle.alarms.values() if a.state == AlarmState.CONFIRMED and (cls is None or a.cls == cls)]


def frac_present(eng, tr, fid, t_on, t_off):
    b0 = int((t_on - eng.t0s[0]) // 30)
    b1 = int((t_off - eng.t0s[0]) // 30)
    return eng.fv_present[tr][b0:b1, J[fid]].mean()


# ---------------------------------------------------------------- Fix 1
def test_fix1_recovery_ends_event_and_stacked_step():
    from core.config import load_config
    cfg = load_config()
    rng = np.random.default_rng(11)
    h = solve_h(2880, 0.5)
    ch = CusumChannel("cn0", cfg, h, ("lower",), sigma_floor=0.02, stack=True)
    level = np.full(900, 45.0)
    level[120:240] = 35.0            # event 1: 10 dB drop for 60 min
    level[240:480] = 41.0            # partial recovery to -4 dB
    level[480:600] = 31.0            # event 2: a new 10 dB drop 2 h later
    level[600:] = 41.0
    level[180:210] -= 6.0            # a stacked 6 dB step inside event 1
    runs, ends, f11_batches = [], [], []
    prev = False
    for b in range(900):
        x = level[b] + rng.normal(0, 0.6, 30).mean()
        st = ch.update(b * 30.0, x)
        if st.present and not prev:
            runs.append((b, st.onset / 30.0))
        if st.ended_at is not None:
            ends.append((b, st.ended_at / 30.0))
        if st.stacked:
            f11_batches.append(b)
        prev = st.present
    assert len(runs) >= 2, runs
    assert abs(runs[0][1] - 120) <= 10 and abs(runs[1][1] - 480) <= 10   # CUSUM backtracking; the watchdog step fit refines
    assert any(abs(e - 240) <= 2 for _, e in ends), ends       # event 1 ends on partial recovery, not at the old level
    assert any(abs(e - 600) <= 2 for _, e in ends), ends
    assert f11_batches and 180 <= f11_batches[0] <= 183 and max(f11_batches) <= 212


# ---------------------------------------------------------------- Fix 2
def test_fix2_machine_specific(cfg):
    fu = Fusion(cfg)

    def ev(present):
        p = [f in present for f in fu.findings]
        s = [present.get(f, 0.0) for f in fu.findings]
        m = [f in ("F11", "F12") and f not in present for f in fu.findings]
        return fu.evaluate("T1", 0.0, p, s, m)

    r = ev({"F1": 0.5, "F2": 0.5, "F12": 1.0})
    assert not ("JAMMING" in r.explanation and r.bayes_factor["JAMMING"] >= 20)
    r = ev({"F1": 0.5, "F2": 0.5, "F12": 1.0, "F6": 0.5})
    assert "EQUIPMENT_FAULT" in r.explanation and r.bayes_factor["EQUIPMENT_FAULT"] >= 20
    r = ev({"F1": 0.5, "F4": 0.5})
    assert "JAMMING" in r.explanation and r.bayes_factor["JAMMING"] >= 20


def test_fix2_one_tractor_drop_is_not_jamming(cfg):
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 12.0)
    oscillate_cn0(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T2", "F12", T0 + 41 * MIN, T0 + 59 * MIN) > 0.8
    assert not confirmed(eng, "JAMMING")


# ---------------------------------------------------------------- Fix 3
def test_fix3_jamming_inside_storm(cfg):
    """A storm (equal ramped drop, S4 0.45, extra variance) with a jammer switching on for 30 min inside it.
    The step is unequal across machines (12/8/4 dB): under the approved link table an *equal* step leaves
    JAMMING at BF ~ 2 (F4/F2/F5/F10 absent), so F11 alone cannot carry an alarm."""
    tele = make_tele(3 * 3600)
    for tr, step in zip(tele.tractors, (12.0, 8.0, 4.0)):
        drop_cn0(tele, tr, T0 + 30 * MIN, T0 + 170 * MIN, 8.0, ramp_s=300)
        tele.series[tr].cn0[int(30 * MIN):int(170 * MIN)] += np.random.default_rng(3).normal(0, 0.6, 140 * MIN)  # scintillation-like extra variance
        drop_cn0(tele, tr, T0 + 90 * MIN, T0 + 120 * MIN, step)             # abrupt extra step: a jammer
    sda = make_sda(cfg, duration_s=3 * 3600, s4_storm=(T0 + 20 * MIN, T0 + 3 * 3600, 0.45))
    eng = run_engine(cfg, tele, sda)
    assert frac_present(eng, "T1", "F11", T0 + 92 * MIN, T0 + 119 * MIN) > 0.8
    sw = confirmed(eng, "SPACE_WEATHER")
    jam = confirmed(eng, "JAMMING")
    assert sw and sw[0].start <= T0 + 36 * MIN and sw[0].span_end(eng.now) >= T0 + 150 * MIN
    assert len(jam) == 1 and abs(jam[0].start - (T0 + 90 * MIN)) <= 60 and abs(jam[0].end - (T0 + 120 * MIN)) <= 60


# ---------------------------------------------------------------- Fix 4
def test_fix4_spoofing_during_cn0_drop(cfg):
    tele = make_tele(2 * 3600)
    for tr in tele.tractors:
        drop_cn0(tele, tr, T0 + 30 * MIN, T0 + 110 * MIN, 8.0)
    ramp_xte(tele, "T2", T0 + 50 * MIN, T0 + 80 * MIN, 5.0 / (30 * MIN))
    eng = run_engine(cfg, tele, make_sda(cfg))
    sp = confirmed(eng, "SPOOFING")
    assert len(sp) == 1 and sp[0].tractors == {"T2"}
    assert abs(sp[0].start - (T0 + 50 * MIN)) <= 60
    raised = [h for h in sp[0].history if h["to"] == "ALARM"]
    assert raised and raised[0]["t_data"] <= T0 + 55 * MIN


# ---------------------------------------------------------------- Fix 5
def test_fix5_link_bursts(cfg):
    """Repeated short outages: 15 s bursts every 100 s (>= 3 per trailing 5 min) over ~40 min on one tractor."""
    tele = make_tele(2 * 3600)
    for k in range(24):
        t = T0 + 40 * MIN + k * 100
        link_down(tele, "T1", t, t + 15)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert frac_present(eng, "T1", "F7", T0 + 45 * MIN, T0 + 78 * MIN) > 0.9
    eq = confirmed(eng, "EQUIPMENT_FAULT")
    assert len(eq) == 1 and eq[0].tractors == {"T1"}
    assert eq[0].start <= T0 + 46 * MIN and eq[0].end >= T0 + 75 * MIN


# ---------------------------------------------------------------- Fix 6
def test_fix6_min_cn0_dip(cfg):
    tele = make_tele(90 * MIN)
    t_dip = T0 + 45 * MIN + 7
    dip_cn0_min(tele, "T3", t_dip, 17, 8.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    mp = confirmed(eng, "MULTIPATH")
    assert len(mp) == 1 and mp[0].tractors == {"T3"}
    assert abs(mp[0].start - t_dip) <= 3 and abs(mp[0].end - (t_dip + 17)) <= 3


def test_fix6_dips_inside_jamming_are_explained(cfg):
    tele = make_tele(2 * 3600)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 70 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 40 * MIN, T0 + 70 * MIN, 9.0)
    for tr in tele.tractors:
        dip_cn0(tele, tr, T0 + 50 * MIN, 17, 8.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert confirmed(eng, "JAMMING")
    assert not confirmed(eng, "MULTIPATH")


# ---------------------------------------------------------------- Fix 7
def test_fix7_confidence_is_bf_posterior(cfg):
    tele = make_tele(2 * 3600)
    ramp_xte(tele, "T3", T0 + 40 * MIN, T0 + 70 * MIN, 0.005)
    eng = run_engine(cfg, tele, make_sda(cfg))
    sp = confirmed(eng, "SPOOFING")
    assert len(sp) == 1
    bfs = [r.bf / (1 + r.bf) for r in sp[0].records]
    assert abs(sp[0].confidence - float(np.median(bfs))) < 1e-9
    assert sp[0].confidence > 0.9
