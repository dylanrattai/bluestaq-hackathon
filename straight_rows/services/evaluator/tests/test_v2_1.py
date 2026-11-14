"""Decision v2.1 §7: turn exclusion, onset grouping, farm-level decision, episode confidence, 6 h safety valve.
Synthetic signals only."""
import copy

import numpy as np

from core.srtypes import FINDING_IDS, AlarmState
from synth import T0, drop_cn0, make_sda, make_tele, oscillate_cn0, run_engine, storm

MIN = 60
H = 3600
J = {f: i for i, f in enumerate(FINDING_IDS)}


def confirmed(eng, cls=None):
    return [a for a in eng.lifecycle.alarms.values() if a.state == AlarmState.CONFIRMED and (cls is None or a.cls == cls)]


def turn_starts(tele, tr):
    """Sample indices of the synthetic headland turns (make_tele reverses every 178 s while moving)."""
    s = tele.series[tr]
    return [k for k in range(178, len(s.cn0), 178) if s.speed[k] > 0]


# 1 ------------------------------------------------------------------------------------------------------------
def test_v21_1_turn_variance_never_holds_an_episode(cfg):
    """T3 loses satellites and gets a burst of C/N0 variance at every row end for 48 h (8 s before to 10 s after
    each turn): no episode stays open more than 1 h and F2 is never present in a turn batch."""
    tele = make_tele(48 * H)
    s = tele.series["T3"]
    rng = np.random.default_rng(7)
    for k in turn_starts(tele, "T3"):
        a, b = max(k - 8, 0), min(k + 10, len(s.cn0))
        s.cn0[a:b] += rng.normal(0, 4.0, b - a)
        s.cn0_min[a:b] -= 11.0
        s.sats[a:b] -= 2.0
    eng = run_engine(cfg, tele, make_sda(cfg, duration_s=48 * H))
    f = eng.feats["T3"]
    assert f.turn_batch.mean() > 0.1                                  # the turns were seen
    turn = f.turn_batch[:eng.b_done]
    assert not eng.fv_present["T3"][:eng.b_done, J["F2"]][turn].any()
    assert eng.fv_missing["T3"][:eng.b_done, J["F2"]][turn].all()
    for ep in eng.episodes.tr_eps:
        assert (ep.end - ep.start) <= 1 * H, (ep.id, ep.start, ep.end)
    assert not eng.episodes.forced


# 2 ------------------------------------------------------------------------------------------------------------
def test_v21_2_onsets_40_min_apart_are_two_episodes(cfg):
    """T1 drops at 40 min (until 120 min), T2 at 80 min (until 110 min): overlap, but no common onset."""
    tele = make_tele(3 * H)
    drop_cn0(tele, "T1", T0 + 40 * MIN, T0 + 120 * MIN, 12.0)
    drop_cn0(tele, "T2", T0 + 80 * MIN, T0 + 110 * MIN, 12.0)
    eng = run_engine(cfg, tele, make_sda(cfg, duration_s=3 * H))
    farms = eng.episodes.farms
    assert len(farms) == 2, [(f.id, f.members()) for f in farms]
    assert [f.members() for f in sorted(farms, key=lambda f: f.start)] == [["T1"], ["T2"]]
    assert abs(farms[1].onset0 - farms[0].onset0) > 30 * MIN


# 3 ------------------------------------------------------------------------------------------------------------
def test_v21_3_unequal_jam_is_one_farm_detection(cfg):
    """A 3-tractor jam with unequal drops (10 / 8 / 6 dB, spread inside the 6 dB F4 band, so no F4) and C/N0
    oscillation on the two near machines: one farm-level JAMMING detection on all three, BF >= 20, although each
    machine's own BF is below 20 (TRN-03's dilution)."""
    tele = make_tele(2 * H)
    for tr, db in (("T1", 10.0), ("T2", 8.0), ("T3", 6.0)):
        drop_cn0(tele, tr, T0 + 40 * MIN, T0 + 80 * MIN, db)
    for tr in ("T1", "T2"):
        oscillate_cn0(tele, tr, T0 + 40 * MIN, T0 + 80 * MIN, 3.0, 8.0)
    eng = run_engine(cfg, tele, make_sda(cfg))
    jam = confirmed(eng, "JAMMING")
    assert len(jam) == 1 and jam[0].tractors == {"T1", "T2", "T3"}, [(a.cls, sorted(a.tractors)) for a in confirmed(eng)]
    ep = jam[0].extra["episode"]
    assert ep["level"] == "farm" and jam[0].bf >= 20
    own = {tr: v["bf"]["JAMMING"] for tr, v in ep["tractors"].items()}
    assert min(own.values()) < 20, own
    assert "signs seen" in " ".join(jam[0].evidence)


# 4 ------------------------------------------------------------------------------------------------------------
def _storm_p(cfg, with_s4: bool) -> tuple[float, str]:
    tele = make_tele(4 * H)
    storm(tele, T0 + 1 * H, T0 + 3 * H, seed=5)
    sda = make_sda(cfg, duration_s=4 * H, s4_storm=[(T0 + 1 * H, T0 + 3 * H, 0.45)] if with_s4 else None)
    eng = run_engine(cfg, tele, sda)
    farm = max(eng.episodes.farms, key=lambda f: f.end - f.start)
    bf = farm.result["joint"]["bf"]["SPACE_WEATHER"]
    sw = confirmed(eng, "SPACE_WEATHER")
    line = next((x for a in sw for x in a.evidence if "signs seen" in x), "")
    return bf / (1.0 + bf), line


def test_v21_4_confidence_from_signs(cfg):
    """The same storm with S4 (F9 present) and without (F9 missing as a sign): P drops; with the signs present
    P > 0.95, and the evidence says which signs were seen."""
    p_all, line = _storm_p(cfg, True)
    p_less, _ = _storm_p(cfg, False)
    assert p_all > 0.95
    assert p_less < p_all
    assert "F9" in line and "P = " in line


def test_v21_4b_confidence_unit(cfg):
    """Unit form of §5 on the farm-level network: removing one known sign lowers P."""
    tele = make_tele(30 * MIN)
    eng = run_engine(cfg, tele, make_sda(cfg, duration_s=30 * MIN))
    mgr = eng.episodes
    on = {"present": True, "sev": 0.75, "missing": False}
    off = {"present": False, "sev": 0.0, "missing": False}
    fd = {"F1": on, "F2": on, "F3": on, "F4": off, "F6": {"missing": True, "present": False, "sev": 0.0},
          "F7": off, "F9": on, "F10": off, "F12": off}
    per = {tr: {"findings": copy.deepcopy(fd)} for tr in ("T1", "T2", "T3")}
    r_all, _ = mgr._joint(per, ["T1", "T2", "T3"], T0)
    for tr in per:
        per[tr]["findings"]["F9"] = off
    r_less, _ = mgr._joint(per, ["T1", "T2", "T3"], T0)
    p = lambda r: r.bayes_factor["SPACE_WEATHER"] / (1 + r.bayes_factor["SPACE_WEATHER"])
    assert p(r_all) > 0.95 and p(r_less) < p(r_all)
    line = mgr.signs("SPACE_WEATHER", [per["T1"]["findings"]], r_less.bayes_factor["SPACE_WEATHER"])
    assert line.startswith("signs seen") and "F9" in line.split(";")[1] and "missing" in line


# 5 ------------------------------------------------------------------------------------------------------------
def test_v21_5_unattributed_6h_is_force_closed(cfg):
    """A 7 h drop on T1 that never reaches a class (bf_alarm set out of reach): force-closed at 6 h, logged, and the
    tractor's reference is discarded and rebuilt."""
    c = copy.deepcopy(cfg)
    c["fusion"]["bf_alarm"] = 1e30
    tele = make_tele(9 * H)
    drop_cn0(tele, "T1", T0 + 30 * MIN, T0 + 30 * MIN + 7 * H, 12.0)
    eng = run_engine(c, tele, make_sda(c, duration_s=9 * H))
    forced = eng.episodes.forced
    assert len(forced) >= 1 and forced[0]["tractor"] == "T1"
    assert 6.0 <= forced[0]["open_h"] < 6.1
    ep = next(e for e in eng.episodes.tr_eps if e.id == forced[0]["episode"])
    assert ep.forced and not ep.attributed
    assert not confirmed(eng)
    ch = eng.ctx.channels[("T1", "cn0_mean")]
    assert not ch.frozen
