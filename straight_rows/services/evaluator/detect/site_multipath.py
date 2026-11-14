"""ACCURACY_UPDATE v2.3 P1: recurring site multipath (known nuisance, ISA-18.2 "suppressed" alert).

Per tractor at 1 Hz, working samples only (speed > 1 m/s):
  ref(t) = median of min C/N0 over [t - 360 s, t - 60 s];  def(t) = ref(t) - min C/N0(t), 3-sample trailing mean;
  a dip = a maximal run with def > 5 dB lasting 5-60 s, reported once a later sample shows it has ended (causal).
A dip whose peak (largest deficit) falls in a learned site cell (0.0002 deg x 0.0002 deg with >= 20 working passes and
a dip rate >= 0.5 on dev, frozen in multipath_sites.yaml by tools/fit_sites.py) is one MULTIPATH report on that
tractor. Dips elsewhere are not reported here (the normal tracks see them).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from core.log import hms, iso
from core.srtypes import AlarmState, Reason


def params(cfg: dict) -> dict:
    sm = cfg.get("site_multipath") or {}
    return {"enabled": bool(sm.get("enabled", True)), "ref_s": int(sm.get("ref_window_s", 360)),
            "lag_s": int(sm.get("ref_lag_s", 60)), "smooth": int(sm.get("smooth_samples", 3)),
            "thr": float(sm.get("deficit_db", 5.0)), "min_s": int(sm.get("min_s", 5)), "max_s": int(sm.get("max_s", 60)),
            "speed": float(sm.get("speed_mps", 1.0)), "cell": float(sm.get("cell_deg", 0.0002)),
            "min_passes": int(sm.get("min_passes", 20)), "min_rate": float(sm.get("min_rate", 0.5)),
            "sites": sm.get("sites", "multipath_sites.yaml"), "cap": int(sm.get("max_detections", 500))}


def cell_of(lat: float, lon: float, size: float) -> tuple[int, int]:
    return int(math.floor(lat / size + 1e-9)), int(math.floor(lon / size + 1e-9))


def deficit(series, p: dict) -> tuple[np.ndarray, np.ndarray]:
    """Causal deficit and working mask for one tractor (each value uses samples at or before its own time only)."""
    x = pd.Series(series.cn0_min)
    w = p["ref_s"] - p["lag_s"]
    ref = x.rolling(w + 1, min_periods=(w + 1) // 2).median().shift(p["lag_s"])
    d = (ref - x).rolling(p["smooth"], min_periods=p["smooth"]).mean().to_numpy()
    working = ~np.isnan(series.speed) & (series.speed > p["speed"])
    return d, working


def find_dips(tele, tr: str, p: dict) -> list[dict]:
    s = tele.series[tr]
    d, working = deficit(s, p)
    on = working & ~np.isnan(d) & (d > p["thr"])
    n = len(on)
    out = []
    if not on.any():
        return out
    edges = np.diff(np.concatenate([[0], on.astype(np.int8), [0]]))
    starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]        # run = [start, end)
    t = tele.t
    for a, z in zip(starts, ends):
        dur = int(z - a)
        if z >= n or dur < p["min_s"] or dur > p["max_s"]:
            continue                                     # still running at the end of the data, or not a dip
        k = a + int(np.nanargmax(d[a:z]))
        out.append({"tractor": tr, "i0": int(a), "i1": int(z), "start": float(t[a]), "end": float(t[z - 1]) + 1.0,
                    "known_t": float(t[z]), "peak_t": float(t[k]), "depth_db": float(d[k]), "dur_s": dur,
                    "lat": float(s.lat[k]), "lon": float(s.lon[k]), "cell": cell_of(float(s.lat[k]), float(s.lon[k]), p["cell"])})
    return out


def visits(tele, tr: str, p: dict, t_end: float | None = None) -> dict[tuple[int, int], list[tuple[float, float]]]:
    """Working passes per cell: maximal runs of consecutive working samples inside one cell, as (t_first, t_last)."""
    s = tele.series[tr]
    t = tele.t
    m = ~np.isnan(s.speed) & (s.speed > p["speed"]) & ~np.isnan(s.lat) & ~np.isnan(s.lon)
    if t_end is not None:
        m &= t < t_end
    ci = np.floor(s.lat / p["cell"] + 1e-9)
    cj = np.floor(s.lon / p["cell"] + 1e-9)
    out: dict[tuple[int, int], list] = {}
    prev = None
    for k in np.nonzero(m)[0]:
        key = (int(ci[k]), int(cj[k]))
        if prev is None or key != prev[0] or k != prev[1] + 1:
            out.setdefault(key, []).append([float(t[k]), float(t[k])])
        else:
            out[key][-1][1] = float(t[k])
        prev = (key, k)
    return out


def pass_rates(tele, p: dict, t_end: float | None = None, pad_s: float = 10.0) -> dict:
    """Per cell: passes, and passes during which that tractor had a dip (dip within the pass +- pad_s)."""
    out: dict[tuple[int, int], list[int]] = {}
    for tr in tele.tractors:
        dl = sorted((d["start"], d["end"]) for d in find_dips(tele, tr, p) if t_end is None or d["peak_t"] < t_end)
        ds = np.array([a for a, _ in dl]) if dl else np.zeros(0)
        de = np.array([b for _, b in dl]) if dl else np.zeros(0)
        for key, vs in visits(tele, tr, p, t_end).items():
            row = out.setdefault(key, [0, 0])
            for a, b in vs:
                row[0] += 1
                if len(ds):
                    k = int(np.searchsorted(ds, b + pad_s, side="right"))
                    if k > 0 and np.any(de[max(0, k - 5):k] >= a - pad_s):
                        row[1] += 1
    return out


def load_sites(cfg: dict, here: Path) -> dict | None:
    p = params(cfg)
    f = Path(p["sites"])
    f = f if f.is_absolute() else here / f
    if not p["enabled"] or not f.is_file():
        return None
    doc = yaml.safe_load(f.read_text())
    doc["_cells"] = {(int(c["cell"][0]), int(c["cell"][1])): c for c in doc.get("sites", [])}
    return doc


class SiteMultipath:
    def __init__(self, eng):
        self.eng = eng
        self.p = params(eng.cfg)
        here = Path(__file__).resolve().parent.parent
        self.sites = load_sites(eng.cfg, here)
        self.pending: list[dict] = []
        self.reported: list[dict] = []
        if self.sites:
            for tr in eng.tractors:
                self.pending += [d for d in find_dips(eng.tele, tr, self.p) if d["cell"] in self.sites["_cells"]]
            self.pending.sort(key=lambda d: (d["known_t"], d["tractor"]))
        self.k = 0

    def step(self, now: float) -> int:
        """Report every site dip known by `now` (the sample after it is <= now)."""
        n = 0
        while self.k < len(self.pending) and self.pending[self.k]["known_t"] < now:
            self._report(self.pending[self.k], now)
            self.k += 1
            n += 1
        return n

    def _report(self, d: dict, now: float) -> None:
        lc = self.eng.lifecycle
        c = self.sites["_cells"][d["cell"]]
        conf = min(max((c["dips"] + 1) / (c["passes"] + 2), 0.02), 0.95)
        a = lc.create("MULTIPATH", now, d["start"], {d["tractor"]}, AlarmState.CONFIRMED, None, end=d["end"],
                      note=f"site multipath {d['dur_s']} s, {d['depth_db']:.1f} dB (suppressed)", track="site", quiet=True)
        a.confidence = round(conf, 2)
        a.confirmed_at = now
        a.extra["suppressed"] = True
        a.extra["site"] = {"cell": list(d["cell"]), "depth_db": d["depth_db"], "dip": d}
        lat0, lat1 = c["lat"]
        lon0, lon1 = c["lon"]
        south = self.sites.get("south_headland_rate")
        a.evidence = [
            f"{d['tractor']} min C/N0 -{d['depth_db']:.1f} dB for {d['dur_s']} s at {hms(d['start'])}, "
            f"{c.get('name', 'recurring site')} (cell {lat0:.4f}–{lat1:.4f}, {lon0:.4f}–{lon1:.4f})",
            f"Site dips on {c['dips']}/{c['passes']} dev passes ({c['rate']:.0%})"
            + (f"; south headland {south:.0%}" if south is not None else ""),
            "Mean C/N0, RTK fix and cross-track unaffected at this site on dev → site multipath, not a guidance fault; continue",
            f"P={a.confidence:.2f} = (site dips + 1)/(site passes + 2), capped at 0.95 (recurrence is not proof of attribution)",
        ]
        self.reported.append({"alarm": a.id, **{k: v for k, v in d.items() if k != "cell"}, "cell": list(d["cell"])})
