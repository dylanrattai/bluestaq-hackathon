"""Synthetic telemetry/SDA builders with known answers (no dev labels anywhere)."""
from __future__ import annotations

import numpy as np

from core.config import parse_iso
from ingest.load import Advisory, RfiReport, SdaFeeds, Telemetry, TractorSeries

T0 = parse_iso("2026-05-06T10:00:00+00:00")   # inside the evaluation window
FARM_LAT, FARM_LON = 46.9032, -97.1993


def make_tele(duration_s: int = 7200, tractors=("T1", "T2", "T3"), seed: int = 1, start: float = T0,
              working: bool = True, park_from: float | None = None) -> Telemetry:
    rng = np.random.default_rng(seed)
    n = int(duration_s)
    t = (np.arange(n) + int(start)).astype(np.int64)
    series = {}
    for i, tr in enumerate(tractors):
        cn0 = 45.0 + rng.normal(0, 0.6, n)
        cn0_min = cn0 - 5.0 + rng.normal(0, 0.5, n)
        sats = 12.0 + rng.integers(-1, 2, n)
        fix = np.zeros(n)
        link = np.ones(n)
        speed = np.full(n, 4.5 if working else 0.0)
        if park_from is not None:
            speed[int(park_from - start):] = 0.0
        xte = np.abs(rng.normal(0.02, 0.03, n))
        # rows: north-south passes of 178 s with a 12 m sideways hop at each headland
        step = 4.5 / 111_320.0
        lat = np.zeros(n)
        lon = np.full(n, FARM_LON + i * 0.001)
        d = 1.0
        row = 0
        for k in range(1, n):
            if speed[k] <= 0:
                lat[k], lon[k] = lat[k - 1], lon[k - 1]
                continue
            if k % 178 == 0:
                d = -d
                row += 1
            lat[k] = lat[k - 1] + d * step
            lon[k] = FARM_LON + i * 0.001 + row * 12.0 / (111_320.0 * 0.683)
        lat += FARM_LAT
        xte[speed <= 0] = 0.0
        series[tr] = TractorSeries(cn0=cn0, cn0_min=cn0_min, sats=sats.astype(float), fix_rank=fix, link_ok=link,
                                   xte=xte, speed=speed, lat=lat, lon=lon)
    return Telemetry(t=t, tractors=list(tractors), series=series, units={"xte": "m", "speed": "m/s"})


def idx(tele: Telemetry, t: float) -> int:
    return int(t - tele.t[0])


def drop_cn0(tele: Telemetry, tr: str, t_on: float, t_off: float, db: float, ramp_s: float = 0.0) -> None:
    s = tele.series[tr]
    a, b = idx(tele, t_on), idx(tele, t_off)
    if ramp_s > 0:
        r = int(ramp_s)
        s.cn0[a:a + r] -= db * np.linspace(0, 1, r, endpoint=False)
        s.cn0_min[a:a + r] -= db * np.linspace(0, 1, r, endpoint=False)
        a = a + r
    s.cn0[a:b] -= db
    s.cn0_min[a:b] -= db


def oscillate_cn0(tele: Telemetry, tr: str, t_on: float, t_off: float, amp_db: float = 3.0, period_s: float = 8.0) -> None:
    s = tele.series[tr]
    a, b = idx(tele, t_on), idx(tele, t_off)
    k = np.arange(b - a)
    s.cn0[a:b] += amp_db * np.sin(2 * np.pi * k / period_s)


def dip_cn0(tele: Telemetry, tr: str, t_on: float, dur_s: int, db: float) -> None:
    s = tele.series[tr]
    a = idx(tele, t_on)
    s.cn0[a:a + dur_s] -= db
    s.cn0_min[a:a + dur_s] -= db


def dip_cn0_min(tele: Telemetry, tr: str, t_on: float, dur_s: int, db: float, mean_db: float = 0.5) -> None:
    """A fade on one satellite: min C/N0 drops by db, the mean over ~12 satellites barely moves."""
    s = tele.series[tr]
    a = idx(tele, t_on)
    s.cn0_min[a:a + dur_s] -= db
    s.cn0[a:a + dur_s] -= mean_db


def set_fix(tele: Telemetry, tr: str, t_on: float, t_off: float, rank: int) -> None:
    s = tele.series[tr]
    s.fix_rank[idx(tele, t_on):idx(tele, t_off)] = float(rank)


def set_sats(tele: Telemetry, tr: str, t_on: float, t_off: float, n: int) -> None:
    s = tele.series[tr]
    s.sats[idx(tele, t_on):idx(tele, t_off)] = float(n)


def link_down(tele: Telemetry, tr: str, t_on: float, t_off: float) -> None:
    s = tele.series[tr]
    s.link_ok[idx(tele, t_on):idx(tele, t_off)] = 0.0


def ramp_xte(tele: Telemetry, tr: str, t_on: float, t_off: float, rate_m_per_s: float, hold: bool = False) -> None:
    s = tele.series[tr]
    a, b = idx(tele, t_on), idx(tele, t_off)
    k = np.arange(b - a)
    s.xte[a:b] += rate_m_per_s * k
    if hold:
        s.xte[b:] += rate_m_per_s * (b - a)


def make_sda(cfg, start: float = T0, duration_s: int = 7200, kp: float = 2.0, dst: float = -10.0, s4: float = 0.05,
             kp_storm: tuple | None = None, s4_storm: tuple | None = None, advisories=(), rfi=()) -> SdaFeeds:
    """Quiet indices held across the run; kp_storm=(t_on, t_off, value); s4_storm likewise."""
    kp_recs, dst_recs, s4_recs = [], [], []
    t = start - 3 * 3600
    while t < start + duration_s + 3600:
        v = kp
        if kp_storm and kp_storm[0] <= t < kp_storm[1]:
            v = kp_storm[2]
        kp_recs.append((t, v))
        t += 3 * 3600
    t = start - 3600
    while t < start + duration_s + 3600:
        dst_recs.append((t, dst))
        t += 3600
    t = start - 900
    while t < start + duration_s + 900:
        v = s4
        if s4_storm and s4_storm[0] <= t < s4_storm[1]:
            v = s4_storm[2]
        s4_recs.append((t, v))
        t += 900
    advs = [Advisory(a.get("id", f"ADV-{i}"), a["start"], a["end"], a.get("type", "SPACE_WEATHER"), a.get("text", ""))
            for i, a in enumerate(advisories)]
    reps = [RfiReport(r.get("id", f"RFI-{i}"), r["t_event"], r.get("t_avail", r["t_event"] + 1800), r.get("lat", FARM_LAT),
                      r.get("lon", FARM_LON), r.get("radius_km", 5.0), r.get("confidence", 0.6), "", "L1", False)
            for i, r in enumerate(rfi)]
    return SdaFeeds(kp_recs, dst_recs, s4_recs, advs, reps, cfg)


def run_engine(cfg, tele, sda, speed=None, out_dir=None, write_outputs=False, validator=None):
    from runtime.engine import Engine
    from core.log import EventSink
    sink = EventSink(out_dir if write_outputs else None)
    eng = Engine(cfg, None, out_dir, speed=speed, validator=validator, sink=sink, tele=tele, sda=sda,
                 write_outputs=write_outputs, mode="test")
    eng.run()
    sink.close()
    return eng
