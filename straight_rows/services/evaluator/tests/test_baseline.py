import numpy as np
import pytest

from detect.baseline import CusumChannel, siegmund_arl0, solve_h
from ingest.grid import Gate, compute_features
from synth import make_tele, set_fix


def test_h_from_siegmund():
    h = solve_h(2880, 0.5)
    assert abs(h - 6.8) <= 0.1
    assert abs(siegmund_arl0(5.0) - 465) < 15
    assert abs(siegmund_arl0(4.0) - 168) < 8


def _false_triggers(rho: float, days: int, seed: int, cfg) -> float:
    rng = np.random.default_rng(seed)
    n = days * 86400
    e = rng.normal(0, 1, n) * np.sqrt(1 - rho * rho)
    x = np.empty(n)
    x[0] = e[0]
    for i in range(1, n):
        x[i] = rho * x[i - 1] + e[i]
    bm = x.reshape(-1, 30).mean(1)
    ch = CusumChannel("t", cfg, solve_h(2880, 0.5), ("lower", "upper"))
    runs, prev = 0, False
    for b, v in enumerate(bm):
        st = ch.update(b * 30.0, float(v))
        if st.present and not prev:
            runs += 1
        prev = st.present
    return runs / days


@pytest.mark.slow
def test_false_trigger_rate_ar1(cfg):
    """AR(1) rho=0.95 at 1 Hz, 7 days: 0.25-4 false runs per day per stream (sigma inflation is approximate)."""
    rate = _false_triggers(0.95, 7, 3, cfg)
    print(f"measured false-trigger rate: {rate:.2f}/day")
    assert 0.25 <= rate <= 4.0, f"measured {rate:.2f}/day: report to Dylan, do not tune k or h"


def test_step_detected_and_backtracked(cfg):
    rng = np.random.default_rng(7)
    h = solve_h(2880, 0.5)
    ch = CusumChannel("cn0", cfg, h, ("lower",), sigma_floor=0.02)
    first_present, onset = None, None
    for b in range(200):
        x = 45.0 + rng.normal(0, 0.6, 30).mean() - (10.0 if b >= 120 else 0.0)
        st = ch.update(b * 30.0, x)
        if st.present and first_present is None:
            first_present, onset = b, st.onset
    assert first_present is not None and first_present - 120 <= 2
    assert abs(onset - 120 * 30.0) <= 30.0


def test_reference_freezes_during_event(cfg):
    rng = np.random.default_rng(8)
    ch = CusumChannel("cn0", cfg, solve_h(2880, 0.5), ("lower",), sigma_floor=0.02)
    mus = []
    for b in range(300):
        x = 45.0 + rng.normal(0, 0.6, 30).mean() - (8.0 if 150 <= b < 250 else 0.0)
        st = ch.update(b * 30.0, x)
        mus.append(st.mu)
    assert abs(mus[200] - 45.0) < 0.3           # frozen pre-event reference during the drop
    assert abs(mus[290] - 45.0) < 0.3


def test_gate_holds_while_fix_degraded(cfg):
    tele = make_tele(3600, tractors=("T1",), seed=2)
    s = tele.series["T1"]
    s.speed[1800:] = 0.0                       # stops at +30 min
    set_fix(tele, "T1", tele.t[0] + 1500, tele.t[0] + 3600, 4)   # NO_FIX from +25 min onwards
    f = compute_features(tele, "T1", cfg)
    g = Gate(cfg, ["T1"])
    states = [g.update("T1", b, f, False) for b in range(f.nb)]
    assert states[10] == "WORKING"
    assert all(st == "WORKING" for st in states[60:120]), "state must hold while fix_rank >= 3"
    # without the hold it would have parked 5 min after stopping
    tele2 = make_tele(3600, tractors=("T1",), seed=2)
    tele2.series["T1"].speed[1800:] = 0.0
    f2 = compute_features(tele2, "T1", cfg)
    g2 = Gate(cfg, ["T1"])
    states2 = [g2.update("T1", b, f2, False) for b in range(f2.nb)]
    assert states2[70] == "PARKED"
