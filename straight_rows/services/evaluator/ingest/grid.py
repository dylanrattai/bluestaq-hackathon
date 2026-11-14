"""30 s batch grid, vectorised backward-looking features, and the working gate (spec §5, §7.1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ingest.load import Telemetry, TractorSeries

log = logging.getLogger("sr.grid")


# --------------------------------------------------------------------------- causal 1 Hz helpers
def run_length_true(mask: np.ndarray) -> np.ndarray:
    """r[i] = number of consecutive True values ending at i (0 where mask[i] is False). Backward-looking only."""
    mask = np.asarray(mask, dtype=bool)
    n = len(mask)
    idx = np.arange(n)
    last_false = np.maximum.accumulate(np.where(~mask, idx, -1))
    return np.where(mask, idx - last_false, 0)


def backward_mean(x: np.ndarray, w: int) -> np.ndarray:
    """Mean of x over the trailing window [i-w+1, i], ignoring NaN. Backward-looking only."""
    v = np.nan_to_num(x, nan=0.0)
    c = (~np.isnan(x)).astype(float)
    cs = np.concatenate([[0.0], np.cumsum(v)])
    cc = np.concatenate([[0.0], np.cumsum(c)])
    n = len(x)
    i = np.arange(1, n + 1)
    j = np.maximum(i - w, 0)
    s = cs[i] - cs[j]
    k = cc[i] - cc[j]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = s / k
    out[k == 0] = np.nan
    return out


def heading_change_mask(lat: np.ndarray, lon: np.ndarray, thresh_deg: float) -> np.ndarray:
    """True at samples where the derived heading changes by more than thresh_deg (headland turns).

    Heading is derived from consecutive lat/lon (no heading column in the data). Only the samples
    touching the turn are masked; the change is computed from past samples only.
    """
    n = len(lat)
    out = np.zeros(n, dtype=bool)
    if n < 3:
        return out
    dn = np.diff(lat) * 111_320.0
    de = np.diff(lon) * 111_320.0 * np.cos(np.radians(np.nanmean(lat)))
    hd = np.degrees(np.arctan2(de, dn))                 # heading of step i -> i+1, length n-1
    dh = np.abs((np.diff(hd) + 180.0) % 360.0 - 180.0)  # change between consecutive steps, length n-2
    turn = np.zeros(n - 1, dtype=bool)
    turn[1:] = dh > thresh_deg                          # step i (i>=1) differs from step i-1
    moving = np.hypot(dn, de) > 0.05                    # ignore heading noise while stationary
    turn &= moving
    # a turn at step i affects samples i and i+1
    out[:-1] |= turn
    out[1:] |= turn
    return out


def turn_batches(turn: np.ndarray, sample_batch: np.ndarray, nb: int, after_s: int) -> np.ndarray:
    """v2.1 §1: batches containing a headland turn, or whose first `after_s` seconds follow one (the satellite /
    min-C/N0 disturbance lasts ~10 s past the turn in the dev background). Causal: a batch is marked only from
    samples at or before its own end. Assumes the 1 Hz axis."""
    n = len(turn)
    m = turn.copy()
    if after_s > 0 and n:
        idx = np.nonzero(turn)[0]
        for k in range(1, after_s + 1):
            j = idx + k
            m[j[j < n]] = True
    return np.bincount(sample_batch[m], minlength=nb)[:nb] > 0 if m.any() else np.zeros(nb, dtype=bool)


# --------------------------------------------------------------------------- batch features
@dataclass
class Features:
    tractor: str
    b0: int                 # global index of the first batch (floor(t/batch_s))
    nb: int
    batch_s: int
    t0: np.ndarray          # batch start epoch seconds (nb,)
    # per batch
    cn0_mean: np.ndarray
    cn0_var: np.ndarray
    cn0_logvar: np.ndarray
    cn0_frac: np.ndarray
    cn0_min_mean: np.ndarray
    xte_mean: np.ndarray
    xte_med: np.ndarray
    xte_frac: np.ndarray
    speed_mean: np.ndarray
    speed_frac: np.ndarray
    fix_deg_frac: np.ndarray
    fix_deg2_frac: np.ndarray          # fraction of samples with fix_rank >= 2 (DGPS or worse)
    fix_rank_med: np.ndarray
    fix_rank_max: np.ndarray
    fix_frac: np.ndarray
    sats_med: np.ndarray
    sats_frac: np.ndarray
    link_down_frac: np.ndarray
    link_maxrun: np.ndarray
    link_run_end: np.ndarray
    link_down_recent: np.ndarray   # any link-down sample within link_fix_window_s before the batch end
    link_onset_recent: np.ndarray  # a link outage *started* within link_fix_window_s before the batch end (F6 window)
    link_frac: np.ndarray
    link_down_5min_s: np.ndarray       # seconds of link_down in the trailing 5 min window (Fix 5)
    link_outages_5min: np.ndarray      # separate outages starting in that window
    link_maxrun_5min: np.ndarray       # longest outage run ending in that window
    link_first_onset_5min: np.ndarray  # epoch of the first outage onset in that window (NaN if none)
    lat_mean: np.ndarray
    lon_mean: np.ndarray
    # 1 Hz (aligned to Telemetry.t)
    cn0_ma: np.ndarray                 # backward moving average (dip window) of mean C/N0
    cn0_loc_med: np.ndarray            # local reference: 2-min median ending 30 s earlier (Fix 6)
    cn0_loc_std: np.ndarray
    cn0min_ma: np.ndarray
    cn0min_loc_med: np.ndarray
    cn0min_loc_std: np.ndarray
    link_down_run: np.ndarray
    slow_run: np.ndarray
    fast_run: np.ndarray
    turn_mask: np.ndarray
    turn_batch: np.ndarray             # v2.1 §1: a headland turn in the batch or within turn_after_s before it (causal)
    sample_batch: np.ndarray       # local batch index per sample

    def samples_of_batch(self, b: int) -> slice:
        """Index slice of the 1 Hz samples in local batch b (assumes a contiguous 1 Hz axis)."""
        lo = int(np.searchsorted(self.sample_batch, b, side="left"))
        hi = int(np.searchsorted(self.sample_batch, b, side="right"))
        return slice(lo, hi)


def _agg(sample_batch: np.ndarray, nb: int, x: np.ndarray):
    """Per-batch mean, variance, valid count of x (NaN ignored)."""
    ok = ~np.isnan(x)
    cnt = np.bincount(sample_batch[ok], minlength=nb).astype(float)
    s1 = np.bincount(sample_batch[ok], weights=x[ok], minlength=nb)
    s2 = np.bincount(sample_batch[ok], weights=x[ok] ** 2, minlength=nb)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s1 / cnt
        var = np.maximum(s2 / cnt - mean ** 2, 0.0) * np.where(cnt > 1, cnt / np.maximum(cnt - 1, 1), np.nan)
    mean[cnt == 0] = np.nan
    var[cnt <= 1] = np.nan
    return mean, var, cnt


def _median_per_batch(sample_batch: np.ndarray, nb: int, x: np.ndarray) -> np.ndarray:
    out = np.full(nb, np.nan)
    ok = ~np.isnan(x)
    sb = sample_batch[ok]
    xv = x[ok]
    if len(xv) == 0:
        return out
    order = np.lexsort((xv, sb))
    sb_s, xv_s = sb[order], xv[order]
    starts = np.searchsorted(sb_s, np.arange(nb), side="left")
    ends = np.searchsorted(sb_s, np.arange(nb), side="right")
    for b in np.nonzero(ends > starts)[0]:
        seg = xv_s[starts[b]:ends[b]]
        out[b] = seg[len(seg) // 2]
    return out


def _max_per_batch(sample_batch: np.ndarray, nb: int, x: np.ndarray) -> np.ndarray:
    out = np.full(nb, -np.inf)
    ok = ~np.isnan(x)
    if ok.any():
        np.maximum.at(out, sample_batch[ok], x[ok])
    out[np.isneginf(out)] = np.nan
    return out


def compute_features(tele: Telemetry, tractor: str, cfg: dict[str, Any]) -> Features:
    s: TractorSeries = tele.series[tractor]
    batch_s = int(cfg["grid"]["batch_s"])
    t = tele.t
    gb = t // batch_s
    b0 = int(gb[0])
    sample_batch = (gb - b0).astype(np.int64)
    nb = int(sample_batch[-1]) + 1
    t0 = (np.arange(nb) + b0) * batch_s
    n = len(t)
    expected = np.bincount(sample_batch, minlength=nb).astype(float)
    full = float(batch_s)

    cn0_mean, cn0_var, cn0_cnt = _agg(sample_batch, nb, s.cn0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cn0_logvar = np.log(np.where(cn0_var > 0, cn0_var, np.nan))
    cn0_min_mean, _, _ = _agg(sample_batch, nb, s.cn0_min)

    fcfg = cfg["findings"]
    turn = heading_change_mask(s.lat, s.lon, float(fcfg["turn_heading_deg"]))
    xte_clean = np.where(turn, np.nan, s.xte)
    xte_mean, _, xte_cnt = _agg(sample_batch, nb, xte_clean)
    xte_med = _median_per_batch(sample_batch, nb, xte_clean)
    speed_mean, _, speed_cnt = _agg(sample_batch, nb, s.speed)

    fix_valid = ~np.isnan(s.fix_rank)
    fix_cnt = np.bincount(sample_batch[fix_valid], minlength=nb).astype(float)
    fix_deg = np.bincount(sample_batch[fix_valid & (s.fix_rank >= 1)], minlength=nb).astype(float)
    fix_deg2 = np.bincount(sample_batch[fix_valid & (s.fix_rank >= 2)], minlength=nb).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        fix_deg_frac = fix_deg / fix_cnt
        fix_deg2_frac = fix_deg2 / fix_cnt
    fix_rank_med = _median_per_batch(sample_batch, nb, s.fix_rank)
    fix_rank_max = _max_per_batch(sample_batch, nb, s.fix_rank)
    sats_med = _median_per_batch(sample_batch, nb, s.sats)
    sats_cnt = np.bincount(sample_batch[~np.isnan(s.sats)], minlength=nb).astype(float)

    link_valid = ~np.isnan(s.link_ok)
    down = link_valid & (s.link_ok < 0.5)
    link_cnt = np.bincount(sample_batch[link_valid], minlength=nb).astype(float)
    down_cnt = np.bincount(sample_batch[down], minlength=nb).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        link_down_frac = down_cnt / link_cnt
    link_down_run = run_length_true(down)
    link_maxrun = np.zeros(nb)
    np.maximum.at(link_maxrun, sample_batch, link_down_run)
    last_idx = np.searchsorted(sample_batch, np.arange(nb), side="right") - 1
    link_run_end = link_down_run[last_idx].astype(float)
    win = int(fcfg["link_fix_window_s"])
    cdown = np.concatenate([[0], np.cumsum(down.astype(np.int64))])
    end_pos = last_idx + 1
    start_pos = np.maximum(end_pos - win, 0)
    link_down_recent = (cdown[end_pos] - cdown[start_pos]) > 0
    onset = down & ~np.concatenate([[False], down[:-1]])
    conset = np.concatenate([[0], np.cumsum(onset.astype(np.int64))])
    link_onset_recent = (conset[end_pos] - conset[start_pos]) > 0
    w5 = int(fcfg.get("link_window_s", 300))
    start5 = np.maximum(end_pos - w5, 0)
    link_down_5min_s = (cdown[end_pos] - cdown[start5]).astype(float)
    link_outages_5min = (conset[end_pos] - conset[start5]).astype(float)
    run_rollmax = pd.Series(link_down_run.astype(float)).rolling(w5, min_periods=1).max().to_numpy()
    link_maxrun_5min = run_rollmax[last_idx]
    onset_t = np.where(onset, t.astype(float), np.nan)
    first_onset = pd.Series(onset_t).rolling(w5, min_periods=1).min().to_numpy()
    link_first_onset_5min = first_onset[last_idx]

    lat_mean, _, _ = _agg(sample_batch, nb, s.lat)
    lon_mean, _, _ = _agg(sample_batch, nb, s.lon)

    gate = cfg["gate"]
    spd_ok = ~np.isnan(s.speed)
    slow_run = run_length_true(spd_ok & (s.speed < gate["park_speed_mps"]))
    fast_run = run_length_true(spd_ok & (s.speed > gate["park_speed_mps"]))
    dcfg = fcfg["dip"]
    cn0_ma = backward_mean(s.cn0, int(dcfg["ma_window_s"]))
    cn0min_ma = backward_mean(s.cn0_min, int(dcfg["ma_window_s"]))
    loc_w, loc_lag = int(dcfg.get("local_ref_s", 120)), int(dcfg.get("local_lag_s", 30))
    def local_ref(x):
        ser = pd.Series(x)
        med = ser.rolling(loc_w, min_periods=loc_w // 2).median().shift(loc_lag).to_numpy()
        std = ser.rolling(loc_w, min_periods=loc_w // 2).std().shift(loc_lag).to_numpy()
        return med, std
    cn0_loc_med, cn0_loc_std = local_ref(s.cn0)
    cn0min_loc_med, cn0min_loc_std = local_ref(s.cn0_min)

    def frac(c):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(expected > 0, c / full, 0.0)

    return Features(
        tractor=tractor, b0=b0, nb=nb, batch_s=batch_s, t0=t0.astype(float),
        cn0_mean=cn0_mean, cn0_var=cn0_var, cn0_logvar=cn0_logvar, cn0_frac=frac(cn0_cnt), cn0_min_mean=cn0_min_mean,
        xte_mean=xte_mean, xte_med=xte_med, xte_frac=frac(xte_cnt), speed_mean=speed_mean, speed_frac=frac(speed_cnt),
        fix_deg_frac=fix_deg_frac, fix_deg2_frac=fix_deg2_frac, fix_rank_med=fix_rank_med, fix_rank_max=fix_rank_max, fix_frac=frac(fix_cnt),
        sats_med=sats_med, sats_frac=frac(sats_cnt),
        link_down_frac=link_down_frac, link_maxrun=link_maxrun, link_run_end=link_run_end,
        link_down_recent=link_down_recent, link_onset_recent=link_onset_recent, link_frac=frac(link_cnt),
        link_down_5min_s=link_down_5min_s, link_outages_5min=link_outages_5min, link_maxrun_5min=link_maxrun_5min,
        link_first_onset_5min=link_first_onset_5min,
        lat_mean=lat_mean, lon_mean=lon_mean,
        cn0_ma=cn0_ma, cn0_loc_med=cn0_loc_med, cn0_loc_std=cn0_loc_std, cn0min_ma=cn0min_ma,
        cn0min_loc_med=cn0min_loc_med, cn0min_loc_std=cn0min_loc_std, link_down_run=link_down_run, slow_run=slow_run, fast_run=fast_run, turn_mask=turn,
        turn_batch=turn_batches(turn, sample_batch, nb, int(fcfg.get("turn_after_s", 12))),
        sample_batch=sample_batch,
    )


# --------------------------------------------------------------------------- working gate
class Gate:
    """PARKED / WORKING per tractor (§7.1). Causal: uses run lengths ending at the batch's last sample."""

    def __init__(self, cfg: dict[str, Any], tractors: list[str]):
        g = cfg["gate"]
        self.park_s = int(g["park_min_s"])
        self.work_s = int(g["work_min_s"])
        self.park_speed = float(g["park_speed_mps"])
        self.hold_fix = float(g["hold_fix_rank"])
        self.min_valid = float(cfg["min_valid_frac"])
        self.tz = ZoneInfo(cfg["timezone_local"])
        h0, m0 = (int(x) for x in g["tod_work_local"][0].split(":"))
        h1, m1 = (int(x) for x in g["tod_work_local"][1].split(":"))
        self.tod = (h0 * 60 + m0, h1 * 60 + m1)
        self.state: dict[str, str | None] = {t: None for t in tractors}
        self.tod_used = False

    def update(self, tr: str, b: int, f: Features, f8_active: bool) -> str:
        prev = self.state[tr]
        hold = f8_active or (not np.isnan(f.fix_rank_med[b]) and f.fix_rank_med[b] >= self.hold_fix)
        if prev is not None and hold:
            return prev
        if f.speed_frac[b] < self.min_valid:
            if not self.tod_used:
                log.warning("%s: speed missing at %s; falling back to local time-of-day gate (a guess)", tr, f.t0[b])
                self.tod_used = True
            lt = datetime.fromtimestamp(f.t0[b] + f.batch_s, tz=self.tz)
            mins = lt.hour * 60 + lt.minute
            new = "WORKING" if self.tod[0] <= mins < self.tod[1] else "PARKED"
            self.state[tr] = new
            return new
        sl = f.samples_of_batch(b)
        last = sl.stop - 1
        fast = int(f.fast_run[last]) if last >= 0 else 0
        slow = int(f.slow_run[last]) if last >= 0 else 0
        if prev is None:
            new = "WORKING" if f.speed_mean[b] > self.park_speed else "PARKED"
        elif fast >= self.work_s:
            new = "WORKING"
        elif slow >= self.park_s:
            new = "PARKED"
        else:
            new = prev
        self.state[tr] = new
        return new
