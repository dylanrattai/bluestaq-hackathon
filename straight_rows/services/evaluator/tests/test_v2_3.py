"""ACCURACY_UPDATE v2.3 P1: site multipath on a synthetic farm with its own site map."""
import copy

import numpy as np
import yaml

from core.srtypes import AlarmState
from detect.site_multipath import cell_of, find_dips, params
from synth import T0, make_sda, make_tele, run_engine

H = 3600


def _site_dips(tele, tr, every=178, depth=10.0):
    """A 16 s min-C/N0 dip at every second turn (the 'north' end) of tractor tr."""
    s = tele.series[tr]
    ks = [k for k in range(178, len(s.cn0), 178) if s.speed[k] > 0][::2]
    for k in ks:
        s.cn0_min[k - 8:k + 8] -= depth
    return ks


def test_v23_site_multipath_reports_and_suppression(cfg, tmp_path):
    tele = make_tele(2 * H)
    for tr in tele.tractors:
        _site_dips(tele, tr)
    c = copy.deepcopy(cfg)
    p = params(c)
    # site map from this synthetic farm: every cell where a dip peaks
    cells = sorted({tuple(d["cell"]) for tr in tele.tractors for d in find_dips(tele, tr, p)})
    assert cells
    f = tmp_path / "sites.yaml"
    f.write_text(yaml.safe_dump({"sites": [{"cell": list(k), "lat": [k[0] * p["cell"], (k[0] + 1) * p["cell"]],
                                            "lon": [k[1] * p["cell"], (k[1] + 1) * p["cell"]], "passes": 30, "dips": 29,
                                            "rate": 0.97, "name": "north headland turn"} for k in cells]}))
    c["site_multipath"].update({"enabled": True, "sites": str(f)})
    eng = run_engine(c, tele, make_sda(c))
    site = [a for a in eng.lifecycle.alarms.values() if a.track == "site"]
    assert len(site) >= 3 * 15
    a = site[0]
    assert a.state == AlarmState.CONFIRMED and len(a.tractors) == 1 and a.cls == "MULTIPATH"
    assert 14 <= a.end - a.start <= 20 and a.confidence <= 0.95
    assert a.extra["suppressed"] and "min C/N0" in a.evidence[0]
    # never in the operator alarm list, visible with include_suppressed
    assert not any(x["class"] == "MULTIPATH" for x in eng.alarms())
    assert any(x["class"] == "MULTIPATH" for x in eng.alarms(include_suppressed=True))
    assert any("site-multipath dips" in act for r in eng.watchdog.runs for act in r.actions)
    # no other detection is raised by the dips
    assert not [x for x in eng.lifecycle.alarms.values() if x.state == AlarmState.CONFIRMED and x.track != "site"]


def test_v23_site_dips_are_causal(cfg):
    tele = make_tele(1 * H)
    _site_dips(tele, "T1")
    p = params(cfg)
    full = find_dips(tele, "T1", p)
    cut = T0 + 1800
    part = find_dips(tele.truncate(cut), "T1", p)
    known = [d for d in full if d["known_t"] < cut]
    assert [(d["start"], d["end"]) for d in part] == [(d["start"], d["end"]) for d in known]
