"""Reference, batch means and CUSUM design (spec §7.2, Fix 1) [R1, R2, R3, R7, R9]."""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger("sr.baseline")


def siegmund_arl0(h: float, k: float = 0.5) -> float:
    """Two-sided in-control ARL from Siegmund's approximation, b = h + 1.166 [R2]."""
    b = h + 1.166
    return (math.exp(2 * k * b) - 2 * k * b - 1) / (4 * k * k)


def solve_h(arl0: float, k: float = 0.5) -> float:
    lo, hi = 0.01, 60.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if siegmund_arl0(mid, k) < arl0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass
class CusumState:
    t0: float
    x: float
    missing: bool
    fallback: bool
    present: bool
    side: str | None
    z: float
    s_lo: float
    s_hi: float
    onset: float | None
    mu: float | None
    sigma: float | None
    sigma_eff: float | None
    rho: float
    n_clean: int
    ref_age_s: float | None
    ended_at: float | None = None        # set on the batch where the event closed (backtracked end)
    ev_mu: float | None = None           # in-event reference level
    stacked: bool = False                # F11: a step on top of the open event is active
    stacked_extra: float = float("nan")  # extra drop (previous in-event level - x), event units
    stacked_onset: float | None = None

    @property
    def drop(self) -> float:
        if self.mu is None or np.isnan(self.x):
            return float("nan")
        return self.mu - self.x


class CusumChannel:
    """Tabular CUSUM on 30 s batch means with a trailing, freezing reference and in-event tracking (Fix 1).

    * pre-event reference: mean/std of clean batch means over the trailing window, sigma inflated for lag-1
      autocorrelation (capped); frozen while a run is in progress
    * detection: lower/upper CUSUM (configured sides), onset = last reset before crossing h
    * once open, an in-event reference (mean/sigma of the batch means since onset, after the first 2 batches)
      drives two more CUSUMs: recovery (ends the event at its backtracked reset) and, when `stack` is set,
      a stacked drop (F11) which restarts the in-event reference. A batch back inside the pre-event band also
      ends the event. After an end the reference is rebuilt from post-event clean batches.
    """

    def __init__(self, name: str, cfg: dict[str, Any], h: float, sides: tuple[str, ...] = ("lower",),
                 sigma_floor: float = 0.0, stack: bool = False):
        ref = cfg["reference"]
        cu = cfg["cusum"]
        self.name = name
        self.batch_s = int(cfg["grid"]["batch_s"])
        self.k = float(cu["k"])
        self.h = float(h)
        self.reset_z = float(cu.get("reset_z", self.k))
        self.band_z = float(cu.get("recovered_band_z", 2.0))
        self.sides = sides
        self.stack = stack
        self.win_s = float(ref["trailing_min"]) * 60
        self.min_clean = int(ref["min_clean_batches"])
        self.max_age = float(ref["max_age_min"]) * 60
        self.pause_age_in_event = bool(ref.get("pause_max_age_in_event", True))
        self.cap = float(ref["sigma_inflation_cap"])
        self.sigma_floor = float(sigma_floor)
        self.ev_warmup = int(cu.get("in_event_warmup_batches", 2))
        self.stack_max_rise = float(cu.get("stacked_max_rise_batches", 2))
        self.hist: deque[tuple[float, float, bool]] = deque()
        self.mu = self.sigma = self.sigma_eff = None
        self.rho = 0.0
        self.n_clean = 0
        self.ref_t: float | None = None
        self.restart_min: int | None = None
        self.restart_sigma: float | None = None
        self.s_lo = self.s_hi = 0.0
        self.onset_lo = self.onset_hi = None
        self.present = False
        self.side: str | None = None
        self.run_onset: float | None = None
        self.zero_streak = 0
        self._clear_event()

    # ---- event bookkeeping
    def _clear_event(self) -> None:
        self.ev_vals: list[float] = []
        self.ev_mu = self.ev_sigma = None
        self.rec_s = self.stk_s = 0.0
        self.rec_onset = self.stk_onset = None
        self.f11_open = False
        self.f11_level = None
        self.f11_onset = None

    def _restart_in_event(self, x: float) -> None:
        self.ev_vals = [x]
        self.ev_mu = self.ev_sigma = None
        self.rec_s = self.stk_s = 0.0
        self.rec_onset = self.stk_onset = None

    def _close_event(self, ended_at: float | None, rebuild: bool = False) -> None:
        self.present, self.side, self.run_onset = False, None, None
        self.s_lo = self.s_hi = 0.0
        self.onset_lo = self.onset_hi = None
        self._clear_event()
        if rebuild:
            # the level settled somewhere else (partial recovery): the pre-event reference is gone with the event,
            # rebuild it from post-event clean batches
            self.hist.clear()
            self.mu = self.sigma = self.sigma_eff = None
            self.ref_t = None
        self.zero_streak = 0
        self._ended_at = ended_at

    # ---- reference
    def restart(self, seed_sigma: float | None = None, min_batches: int = 2) -> None:
        """Re-reference from now on (Fix 3: F2 against the in-event variance). The first `min_batches` batches
        form a provisional reference with sigma floored at `seed_sigma`."""
        self.hist.clear()
        self.mu = self.sigma = self.sigma_eff = None
        self.ref_t = None
        self.s_lo = self.s_hi = 0.0
        self.present, self.side, self.run_onset = False, None, None
        self._clear_event()
        self.restart_min = max(int(min_batches), 2)
        self.restart_sigma = seed_sigma

    def _recompute_reference(self, now: float) -> None:
        xs = [x for (t, x, c) in self.hist if c and (now - t) <= self.win_s]
        self.n_clean = len(xs)
        need = self.min_clean
        provisional = False
        if len(xs) < need and self.restart_min is not None and len(xs) >= self.restart_min:
            provisional = True
        elif len(xs) < need:
            return
        a = np.asarray(xs, dtype=float)
        mu = float(a.mean())
        sigma = float(a.std(ddof=1)) if len(a) > 1 else 0.0
        sigma = max(sigma, self.sigma_floor)
        if provisional and self.restart_sigma is not None:
            sigma = max(sigma, float(self.restart_sigma))
        d = a - mu
        den = float((d * d).sum())
        rho = float((d[:-1] * d[1:]).sum() / den) if den > 0 else 0.0
        infl = math.sqrt((1 + rho) / (1 - rho)) if 0.0 < rho < 1.0 else 1.0
        self.mu, self.sigma, self.rho, self.sigma_eff, self.ref_t = mu, sigma, rho, sigma * min(infl, self.cap), now
        if not provisional:
            self.restart_min = None
            self.restart_sigma = None

    def reference_valid(self, now: float) -> bool:
        if self.mu is None or self.ref_t is None:
            return False
        if self.present and self.pause_age_in_event:
            return True
        return (now - self.ref_t) <= self.max_age

    def ref_age(self, now: float) -> float | None:
        return None if self.ref_t is None else now - self.ref_t

    def _state(self, t0, x, missing, fallback, z, ended_at=None) -> CusumState:
        extra = float("nan")
        if self.f11_open and self.f11_level is not None and not math.isnan(x):
            extra = (self.f11_level - x) if self.side == "lower" else (x - self.f11_level)
        return CusumState(t0, x, missing, fallback, self.present, self.side, z, self.s_lo, self.s_hi,
                          self.run_onset if self.present else None, self.mu, self.sigma, self.sigma_eff, self.rho,
                          self.n_clean, self.ref_age(t0 + self.batch_s), ended_at, self.ev_mu, self.f11_open, extra,
                          self.f11_onset if self.f11_open else None)

    # ---- update
    def update(self, t0: float, x: float) -> CusumState:
        now = t0 + self.batch_s
        self._ended_at = None
        while self.hist and (now - self.hist[0][0]) > self.win_s:
            self.hist.popleft()
        if x is None or (isinstance(x, float) and math.isnan(x)):
            ended = None
            if self.present:
                ended = t0
                self._close_event(t0)
            return self._state(t0, float("nan"), True, not self.reference_valid(now), float("nan"), ended)
        fallback = not self.reference_valid(now)
        z = float("nan")
        ended_at = None
        if not fallback:
            z = (x - self.mu) / self.sigma_eff
            if not self.present:
                prev_lo, prev_hi = self.s_lo, self.s_hi
                if "lower" in self.sides:
                    self.s_lo = max(0.0, self.s_lo - z - self.k)
                if "upper" in self.sides:
                    self.s_hi = max(0.0, self.s_hi + z - self.k)
                if prev_lo == 0.0 and self.s_lo > 0.0:
                    self.onset_lo = t0
                if prev_hi == 0.0 and self.s_hi > 0.0:
                    self.onset_hi = t0
                lo_fire = "lower" in self.sides and self.s_lo >= self.h
                hi_fire = "upper" in self.sides and self.s_hi >= self.h
                if lo_fire or hi_fire:
                    side = "lower" if (lo_fire and (not hi_fire or self.s_lo >= self.s_hi)) else "upper"
                    cand = self.onset_lo if side == "lower" else self.onset_hi
                    self.present, self.side, self.run_onset = True, side, (cand if cand is not None else t0)
                    self._clear_event()
                    self.ev_vals = [x]
                if self.s_lo == 0.0:
                    self.onset_lo = None
                if self.s_hi == 0.0:
                    self.onset_hi = None
            else:
                sign = -1.0 if self.side == "lower" else 1.0     # event direction
                # back inside the pre-event band: the event is over now
                if abs(z) < self.band_z:
                    ended_at = t0
                    self._close_event(t0)
                else:
                    self.ev_vals.append(x)
                    # the in-event reference keeps learning until one of its CUSUMs shows a real trend (>= h/2);
                    # sigma carries the uncertainty of a mean estimated from n batches
                    if len(self.ev_vals) > self.ev_warmup and (self.ev_mu is None or max(self.rec_s, self.stk_s) < 0.5 * self.h):
                        a = np.asarray(self.ev_vals)
                        n = len(a)
                        self.ev_mu = float(a.mean())
                        self.ev_sigma = max(float(a.std(ddof=1)) if n > 1 else 0.0, float(self.sigma_eff)) * math.sqrt(1.0 + 1.0 / n)
                    if self.f11_open and self.f11_level is not None:
                        # a return to the level before the stacked step closes F11, it is not the event recovering
                        extra = (self.f11_level - x) * (1.0 if self.side == "lower" else -1.0)
                        if extra / self.sigma_eff < self.band_z:
                            self.f11_open = False
                            self.f11_level = self.f11_onset = None
                            self._restart_in_event(x)
                    if self.ev_mu is not None:
                        ze = (x - self.ev_mu) / self.ev_sigma
                        prev = self.rec_s
                        self.rec_s = max(0.0, self.rec_s - sign * ze - self.k)      # recovery: against the event direction
                        if prev == 0.0 and self.rec_s > 0.0:
                            self.rec_onset = t0
                        if self.stack:
                            prev = self.stk_s
                            self.stk_s = max(0.0, self.stk_s + sign * ze - self.k)  # further in the event direction
                            if prev == 0.0 and self.stk_s > 0.0:
                                self.stk_onset = t0
                        if self.rec_s >= self.h:
                            ended_at = self.rec_onset if self.rec_onset is not None else t0
                            self._close_event(ended_at, rebuild=True)
                        elif self.stack and self.stk_s >= self.h and not self.f11_open:
                            rise_batches = (t0 - self.stk_onset) / self.batch_s if self.stk_onset is not None else 0.0
                            if rise_batches <= self.stack_max_rise:
                                # abrupt: a step on top of the open event (F11)
                                self.f11_open = True
                                self.f11_level = self.ev_mu
                                self.f11_onset = self.stk_onset if self.stk_onset is not None else t0
                            # gradual: the event itself deepened; either way the in-event reference restarts here
                            self._restart_in_event(x)
        else:
            if self.present:
                ended_at = t0
                self._close_event(t0)
        clean = not self.present
        self.hist.append((t0, x, clean))
        if self.s_lo == 0.0 and self.s_hi == 0.0 and not self.present:
            self.zero_streak += 1
        else:
            self.zero_streak = 0
        frozen = self.zero_streak < 2 and self.mu is not None
        if not frozen or self.mu is None:
            self._recompute_reference(now)
        return self._state(t0, float(x), False, fallback, z, ended_at)

    def close_run(self) -> None:
        if self.present:
            self._close_event(None)
        self.present, self.side, self.run_onset = False, None, None
        self._clear_event()
