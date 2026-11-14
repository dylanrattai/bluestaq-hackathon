"""Update v2 §10: episodes first, causes second (synthetic signals only, no dev labels)."""
import numpy as np

from core.srtypes import AlarmState
from synth import (T0, dip_cn0, drop_cn0, flicker_sats, link_down, make_sda, make_tele, ramp_xte, run_engine, set_fix,
                   set_sats, storm)

MIN = 60
H = 3600


def confirmed(eng, cls=None):
    return [a for a in eng.lifecycle.alarms.values() if a.state == AlarmState.CONFIRMED and (cls is None or a.cls == cls)]


def classes(eng):
    return sorted(a.cls for a in confirmed(eng))


# 1 ------------------------------------------------------------------------------------------------------------
def test_v2_1_two_storms_are_two_detections(cfg):
    """A 5 h equal storm (S4 0.45, float on all tractors, dips inside), 3 h clean, then a 3 h storm."""
    s1, e1 = T0 + 1 * H, T0 + 6 * H
    s2, e2 = T0 + 9 * H, T0 + 12 * H
    dur = int(12.5 * H)
    tele = make_tele(dur)
    storm(tele, s1, e1, seed=3)
    storm(tele, s2, e2, seed=4)
    sda = make_sda(cfg, duration_s=dur, s4_storm=[(s1, e1, 0.45), (s2, e2, 0.45)])
    eng = run_engine(cfg, tele, sda)
    assert classes(eng) == ["SPACE_WEATHER", "SPACE_WEATHER"], [(a.cls, a.id) for a in confirmed(eng)]
    sw = sorted(confirmed(eng), key=lambda a: a.start)
    for a, (s, e) in zip(sw, ((s1, e1), (s2, e2))):
        assert a.tractors == {"T1", "T2", "T3"}
        assert abs(a.start - s) <= 15 * MIN and abs(a.end - e) <= 15 * MIN
    # dips inside the storm exist as candidates but have no separate existence
    assert any(r["excluded"] for r in eng.episodes.dips)


# 2 ------------------------------------------------------------------------------------------------------------
def test_v2_2_pulsed_jammer_is_one_detection(cfg):
    """2 min on / 2 min off for 40 min on all three tractors, unequal drops: the 5 min close rule bridges the gaps."""
    tele = make_tele(2 * H)
    for k in range(10):
        t = T0 + 40 * MIN + k * 4 * MIN
        for tr, db in (("T1", 18.0), ("T2", 12.0), ("T3", 5.0)):
            drop_cn0(tele, tr, t, t + 2 * MIN, db)
    eng = run_engine(cfg, tele, make_sda(cfg))
    jam = confirmed(eng, "JAMMING")
    assert classes(eng) == ["JAMMING"]
    assert jam[0].tractors == {"T1", "T2", "T3"}
    # the CUSUM backdates to its last reset, which can precede a pulsed onset by a few batches, and a single-step
    # fit cannot re-localise a pulsed signal: the full-onset-credit window (5 min) is the honest tolerance here
    assert abs(jam[0].start - (T0 + 40 * MIN)) <= 5 * MIN and abs(jam[0].end - (T0 + 78 * MIN)) <= 90


# 3 ------------------------------------------------------------------------------------------------------------
def test_v2_3_flicker_is_equipment_fault(cfg):
    """One tractor at C/N0 -6 dB with satellites flickering 11 <-> 8 for 60 min: F1 rules out the per-batch F6
    (chain rule), the flicker clause brings it back at episode level."""
    tele = make_tele(2 * H)
    set_sats(tele, "T1", T0, T0 + 2 * H, 12)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 100 * MIN, 6.0)
    flicker_sats(tele, "T1", T0 + 40 * MIN, T0 + 100 * MIN, 11, 8)
    eng = run_engine(cfg, tele, make_sda(cfg))
    eq = confirmed(eng, "EQUIPMENT_FAULT")
    assert classes(eng) == ["EQUIPMENT_FAULT"] and eq[0].tractors == {"T1"}
    fd = eq[0].extra["episode"]["tractors"]["T1"]["findings"]
    assert fd["F6"]["present"] and fd["F6"]["via"] == "flicker" and fd["F12"]["present"]


# 4 ------------------------------------------------------------------------------------------------------------
def test_v2_4_link_bursts_with_dropout_dips(cfg):
    """Six 15 s correction-link outages 7 min apart on T1, each with a 15 s C/N0 dropout dip and RTK float until
    the receiver re-converges (3 min). Per batch F7 never holds (15 s per 5 min); the episode sees 90 s / 6 outages."""
    tele = make_tele(2 * H)
    for k in range(6):
        t = T0 + 40 * MIN + k * 7 * MIN
        link_down(tele, "T1", t, t + 15)
        dip_cn0(tele, "T1", t, 15, 8.0)
        set_fix(tele, "T1", t, t + 15 + 3 * MIN, 1)
    eng = run_engine(cfg, tele, make_sda(cfg))
    eq = confirmed(eng, "EQUIPMENT_FAULT")
    assert classes(eng) == ["EQUIPMENT_FAULT"] and eq[0].tractors == {"T1"}
    assert not confirmed(eng, "MULTIPATH")
    assert eq[0].extra["episode"]["tractors"]["T1"]["findings"]["F7"]["outages"] == 6


# 5 ------------------------------------------------------------------------------------------------------------
def test_v2_5_spoofing_overlays_storm(cfg):
    """During a storm, T3's cross-track error drifts 0 -> 5 m while RTK_FIXED (the others are in float)."""
    dur = 4 * H
    s1, e1 = T0 + 30 * MIN, T0 + 3 * H + 30 * MIN
    tele = make_tele(dur)
    storm(tele, s1, e1, float_every_s=10 ** 6, dips_every_s=10 ** 6)
    for tr in ("T1", "T2"):
        set_fix(tele, tr, s1 + 5 * MIN, e1, 1)
    t_on = T0 + 2 * H
    ramp_xte(tele, "T3", t_on, t_on + 30 * MIN, 5.0 / (30 * MIN))
    sda = make_sda(cfg, duration_s=dur, s4_storm=(s1, e1, 0.45))
    eng = run_engine(cfg, tele, sda)
    assert classes(eng) == ["SPACE_WEATHER", "SPOOFING"]
    sp = confirmed(eng, "SPOOFING")[0]
    assert sp.tractors == {"T3"} and abs(sp.start - t_on) <= 3 * MIN


def test_v2_5b_float_noise_is_not_spoofing(cfg):
    """The same storm with all tractors in RTK float and 0.3 m guidance noise, no drift: below the 0.5 m band."""
    dur = 4 * H
    s1, e1 = T0 + 30 * MIN, T0 + 3 * H + 30 * MIN
    tele = make_tele(dur)
    storm(tele, s1, e1, float_every_s=10 ** 6, dips_every_s=10 ** 6)
    rng = np.random.default_rng(7)
    for tr in tele.tractors:
        set_fix(tele, tr, s1 + 5 * MIN, e1, 1)
        a, b = int(s1 + 5 * MIN - T0), int(e1 - T0)
        tele.series[tr].xte[a:b] = np.abs(rng.normal(0.3, 0.1, b - a))
    sda = make_sda(cfg, duration_s=dur, s4_storm=(s1, e1, 0.45))
    eng = run_engine(cfg, tele, sda)
    assert classes(eng) == ["SPACE_WEATHER"]


# 6 ------------------------------------------------------------------------------------------------------------
def test_v2_6_jam_with_dgps_has_no_fragments(cfg):
    """A jam during which T1's fix degrades to DGPS (decimetre guidance error): no SPOOF / EQUIP fragments."""
    tele = make_tele(2 * H)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 60 * MIN, 18.0)
    drop_cn0(tele, "T2", T0 + 40 * MIN, T0 + 60 * MIN, 9.0)
    set_fix(tele, "T1", T0 + 41 * MIN, T0 + 61 * MIN, 2)
    a, b = int(41 * MIN), int(61 * MIN)
    tele.series["T1"].xte[a:b] = np.abs(np.random.default_rng(5).normal(0.4, 0.2, b - a))
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert classes(eng) == ["JAMMING"]
    assert confirmed(eng, "JAMMING")[0].tractors == {"T1", "T2"}


# 7 ------------------------------------------------------------------------------------------------------------
def test_v2_7_isolated_dip_is_multipath(cfg):
    tele = make_tele(90 * MIN)
    t_dip = T0 + 45 * MIN + 7
    set_sats(tele, "T3", t_dip - 120, t_dip + 120, 12)
    dip_cn0(tele, "T3", t_dip, 17, 8.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    mp = confirmed(eng, "MULTIPATH")
    assert classes(eng) == ["MULTIPATH"] and mp[0].tractors == {"T3"}
    assert abs(mp[0].start - t_dip) <= 3 and abs(mp[0].end - (t_dip + 17)) <= 3


def test_v2_7b_same_dip_inside_episode_is_not_multipath(cfg):
    tele = make_tele(2 * H)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 60 * MIN, 18.0)
    drop_cn0(tele, "T3", T0 + 40 * MIN, T0 + 60 * MIN, 9.0)
    t_dip = T0 + 50 * MIN + 7
    set_sats(tele, "T3", t_dip - 120, t_dip + 120, 12)
    dip_cn0(tele, "T3", t_dip, 17, 8.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    assert classes(eng) == ["JAMMING"]
    assert not confirmed(eng, "MULTIPATH")


# 8 ------------------------------------------------------------------------------------------------------------
def test_v2_8_unequal_step_in_storm_is_jamming_sub_episode(cfg):
    """An abrupt step on two tractors in the middle of a storm, unequal by 8 dB (12 dB on T1, 4 dB on T2)."""
    dur = 4 * H
    s1, e1 = T0 + 30 * MIN, T0 + 3 * H + 30 * MIN
    tele = make_tele(dur)
    storm(tele, s1, e1, float_every_s=10 ** 6, dips_every_s=10 ** 6)
    j0, j1 = T0 + 2 * H, T0 + 2 * H + 30 * MIN
    drop_cn0(tele, "T1", j0, j1, 12.0)
    drop_cn0(tele, "T2", j0, j1, 4.0)
    sda = make_sda(cfg, duration_s=dur, s4_storm=(s1, e1, 0.45))
    eng = run_engine(cfg, tele, sda)
    assert classes(eng) == ["JAMMING", "SPACE_WEATHER"]
    jam = confirmed(eng, "JAMMING")[0]
    assert jam.track == "sub" and jam.tractors == {"T1", "T2"}
    assert abs(jam.start - j0) <= 60 and abs(jam.end - j1) <= 90
    sw = confirmed(eng, "SPACE_WEATHER")[0]
    assert sw.start <= s1 + 15 * MIN and sw.end >= e1 - 15 * MIN
