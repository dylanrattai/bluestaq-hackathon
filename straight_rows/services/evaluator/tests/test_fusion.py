import numpy as np
import pytest

from diagnose.fusion import Fusion


@pytest.fixture(scope="module")
def fu(cfg):
    return Fusion(cfg)


def ev(fu, present: dict, missing=()):
    F = fu.findings
    p = [f in present for f in F]
    s = [present.get(f, 0.0) for f in F]
    # F11 is present-or-missing by definition; F12 needs another tractor's data, so it is "no information" unless given
    m = [f in missing or (f in ("F11", "F12") and f not in present) for f in F]
    return fu.evaluate("T1", 0.0, p, s, m)


def alarm(r, c):
    return c in r.explanation and r.bayes_factor[c] >= 20


def test_lone_f1_no_alarm(fu):
    r = ev(fu, {"F1": 1.0})
    assert not any(alarm(r, c) for c in fu.causes if c != "OTHER")


def test_f1_f2_jam_needs_corroboration(fu):
    """v1.1: a lone drop with oscillation ranks JAMMING first but is not an ALARM on its own (Fix 2);
    with an unequal drop across machines (F4) it is."""
    r = ev(fu, {"F1": 0.5, "F2": 0.5})
    top = max(r.bayes_factor, key=r.bayes_factor.get)
    assert top in ("JAMMING", "OTHER") and not alarm(r, "OTHER")
    assert alarm(ev(fu, {"F1": 0.5, "F2": 0.5, "F4": 0.5}), "JAMMING")


def test_f1_f4_jam(fu):
    r = ev(fu, {"F1": 0.5, "F4": 0.5})
    assert alarm(r, "JAMMING") and not alarm(r, "SPACE_WEATHER")


def test_f1_f3_sw_with_and_without_f9(fu):
    assert alarm(ev(fu, {"F1": 0.5, "F3": 0.5}), "SPACE_WEATHER")
    r = ev(fu, {"F1": 0.5, "F3": 0.5, "F9": 0.5})
    assert alarm(r, "SPACE_WEATHER") and not alarm(r, "JAMMING")


def test_f7_f6_equip(fu):
    r = ev(fu, {"F7": 0.5, "F6": 0.5})
    assert alarm(r, "EQUIPMENT_FAULT") and not alarm(r, "JAMMING")


def test_f8_watch_then_alarm(fu):
    r0 = ev(fu, {"F8": 0.0})
    assert 3 <= r0.bayes_factor["SPOOFING"] < 20
    r1 = ev(fu, {"F8": 1.0})
    assert alarm(r1, "SPOOFING")


def test_f5_multi(fu):
    assert alarm(ev(fu, {"F5": 0.5}), "MULTIPATH")


def test_nothing(fu):
    r = ev(fu, {})
    assert all(bf < 3 for bf in r.bayes_factor.values())


def test_missing_findings_are_skipped(fu):
    a = ev(fu, {"F1": 0.5, "F9": 0.75}, missing=("F3", "F8"))
    b = ev(fu, {"F1": 0.5, "F9": 0.75})
    assert a.bayes_factor["SPACE_WEATHER"] > b.bayes_factor["SPACE_WEATHER"]


def test_probabilities_sum_to_one_and_match_direct(fu):
    rng = np.random.default_rng(0)
    for _ in range(20):
        p = rng.random(fu.nf) < 0.3
        s = rng.random(fu.nf)
        m = rng.random(fu.nf) < 0.1
        ll = fu.loglik(p, s, m)
        w = np.exp(fu.log_prior + ll)
        assert abs((w / w.sum()).sum() - 1.0) < 1e-12
        d = fu.direct_posterior(list(p), list(s), list(m))
        r = fu.evaluate("T1", 0.0, list(p), list(s), list(m))
        for c in fu.causes:
            assert abs(d[c] - r.posterior[c]) < 1e-9
