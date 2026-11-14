"""Live watchdog: every 5 min of data time and once at the end (spec §7.7, Update v2 §7) [R30, R31]."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from diagnose.lifecycle import Alarm, Lifecycle
from core.log import hms, iso
from core.srtypes import AlarmState, Reason

log = logging.getLogger("sr.watchdog")


# --------------------------------------------------------------------------- exact single-change fits
def fit_step(t: np.ndarray, y: np.ndarray, min_seg: int = 5) -> dict | None:
    """Two-segment constant fit minimising the summed squared error (exact search over the change time)."""
    n = len(y)
    if n < 2 * min_seg:
        return None
    cs = np.concatenate([[0.0], np.cumsum(y)])
    cs2 = np.concatenate([[0.0], np.cumsum(y * y)])
    k = np.arange(min_seg, n - min_seg + 1)          # left segment = y[:k]
    n1, n2 = k.astype(float), (n - k).astype(float)
    s1, s2 = cs[k], cs[n] - cs[k]
    sse = cs2[n] - s1 * s1 / n1 - s2 * s2 / n2
    i = int(np.argmin(sse))
    kk = int(k[i])
    m1, m2 = s1[i] / n1[i], s2[i] / n2[i]
    resid = np.concatenate([y[:kk] - m1, y[kk:] - m2])
    sigma = float(np.std(resid)) if n > 2 else 0.0
    sse_flat = float(cs2[n] - cs[n] ** 2 / n)
    return {"tau": float(t[kk]), "idx": kk, "left": float(m1), "right": float(m2), "magnitude": float(m2 - m1),
            "sigma": sigma, "sse": float(sse[i]), "sse_flat": sse_flat, "method": "step"}


def fit_hinge(t: np.ndarray, y: np.ndarray, min_seg: int = 5) -> dict | None:
    """Flat-then-linear fit, continuous at the break (CPOP's model restricted to one change), exact search."""
    n = len(y)
    if n < 2 * min_seg:
        return None
    tt = t - t[0]
    cs_y = np.concatenate([[0.0], np.cumsum(y)])
    cs_t = np.concatenate([[0.0], np.cumsum(tt)])
    cs_tt = np.concatenate([[0.0], np.cumsum(tt * tt)])
    cs_ty = np.concatenate([[0.0], np.cumsum(tt * y)])
    cs_yy = np.concatenate([[0.0], np.cumsum(y * y)])
    k = np.arange(min_seg, n - min_seg + 1)           # break after sample k-1: right segment = y[k:], tau = tt[k]
    tau = tt[k]
    N = float(n)
    Sy = cs_y[n]
    n2 = (n - k).astype(float)
    St2 = cs_t[n] - cs_t[k]
    Stt2 = cs_tt[n] - cs_tt[k]
    Sty2 = cs_ty[n] - cs_ty[k]
    Sy2 = cs_y[n] - cs_y[k]
    Su = St2 - tau * n2
    Suu = Stt2 - 2 * tau * St2 + tau * tau * n2
    Syu = Sty2 - tau * Sy2
    # normal equations: [N Su; Su Suu] [a b]' = [Sy Syu]'
    det = N * Suu - Su * Su
    ok = det > 1e-12
    a = np.where(ok, (Sy * Suu - Su * Syu) / np.where(ok, det, 1.0), Sy / N)
    b = np.where(ok, (N * Syu - Su * Sy) / np.where(ok, det, 1.0), 0.0)
    sse = cs_yy[n] - a * Sy - b * Syu
    i = int(np.argmin(sse))
    kk = int(k[i])
    aa, bb = float(a[i]), float(b[i])
    fit = np.where(tt <= tau[i], aa, aa + bb * (tt - tau[i]))
    resid = y - fit
    sigma = float(np.std(resid))
    rise = bb * (tt[-1] - tau[i])
    sse_flat = float(cs_yy[n] - Sy * Sy / N)
    return {"tau": float(t[kk]), "idx": kk, "level": aa, "slope": bb, "magnitude": float(rise), "sigma": sigma,
            "sse": float(sse[i]), "sse_flat": sse_flat, "method": "hinge"}


# --------------------------------------------------------------------------- watchdog
@dataclass
class WatchdogRun:
    t_data: float
    final: bool
    touched: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"t_data": iso(self.t_data), "final": self.final, "n_touched": len(self.touched),
                "alarms": self.touched, "actions": self.actions}


class Watchdog:
    """Every 5 min of data time (data <= now): 1. late-evidence reclassification of closed episodes, 2. exact
    step/hinge boundary refinement, 3. merge consecutive SPACE_WEATHER alarms <= 30 min apart, 4. retract alarms
    shorter than 2 batches (NOT_SUSTAINED) or entirely parked (PARKED), clip to working time, 5. confirm.
    (Spec order is refine, merge, reclassify, ...; reclassifying first lets an alarm a late report creates be
    refined in the same pass. Nothing else depends on the order.)"""

    def __init__(self, eng):
        self.eng = eng
        self.cfg = eng.cfg
        w = self.cfg["watchdog"]
        self.margin = float(w["margin_min"]) * 60
        self.lookback = float(w["lookback_min"]) * 60
        self.merge_gap = {k: float(v) * 60 for k, v in w["merge_gap_min"].items()}
        self.min_batches = int(w["min_batches"])
        self.min_step_sigma = float(w["min_step_sigma"])
        self.batch_s = float(self.cfg["grid"]["batch_s"])
        self.fix_frac = float(self.cfg["findings"]["fix_frac"])
        self.runs: list[WatchdogRun] = []

    # ---- channel access
    def _channel(self, a: Alarm, tr: str) -> tuple[np.ndarray, str]:
        s = self.eng.tele.series[tr]
        f = self.eng.feats[tr]
        if a.cls == "SPOOFING":
            return np.where(f.turn_mask, np.nan, s.xte), "xte"
        if a.cls == "EQUIPMENT_FAULT":
            fd = ((a.extra.get("episode") or {}).get("tractors", {}).get(tr) or {}).get("findings", {})
            if (fd.get("F7") or {}).get("present"):
                return 1.0 - s.link_ok, "link_down"
            b0 = max(int((a.raw_start - f.t0[0]) // self.batch_s), 0)
            b1 = int(np.ceil(((a.raw_end if a.raw_end is not None else self.eng.now) - f.t0[0]) / self.batch_s))
            deg = f.fix_deg_frac[b0:b1]
            if np.any(deg[~np.isnan(deg)] > self.fix_frac):
                return s.fix_rank, "fix_rank"
            if (fd.get("F6") or {}).get("present"):
                return s.sats, "sats"
        return s.cn0, "cn0"

    def _window(self, t_b: float, now: float) -> tuple[int, int]:
        t = self.eng.tele.t
        lo = int(np.searchsorted(t, t_b - self.margin, side="left"))
        hi = int(np.searchsorted(t, min(t_b + self.margin, now), side="left"))
        return lo, hi

    def _relocalise(self, a: Alarm, now: float, run: WatchdogRun) -> None:
        """Exact single-change fit around the episode's *raw* boundary (so repeated passes do not drift)."""
        if a.track == "dip" or a.state == AlarmState.WATCH:
            return
        t = self.eng.tele.t
        for which in ("start", "end"):
            bnd = a.raw_start if which == "start" else a.raw_end
            if bnd is None:
                continue
            lo, hi = self._window(bnd, now)
            if hi - lo < 60:
                continue
            ramp = which == "start" and a.cls in ("SPOOFING", "SPACE_WEATHER")
            cands = []
            for tr in sorted(a.tractors):
                y_all, name = self._channel(a, tr)
                y = y_all[lo:hi].astype(float)
                tt = t[lo:hi].astype(float)
                ok = ~np.isnan(y)
                if ok.sum() < 60:
                    continue
                y, tt = y[ok], tt[ok]
                fit = fit_hinge(tt, y) if ramp else fit_step(tt, y)
                if fit is None:
                    continue
                sig = max(fit["sigma"], 1e-9)
                if abs(fit["magnitude"]) < self.min_step_sigma * sig and not (name == "link_down" and abs(fit["magnitude"]) > 0.5):
                    continue
                cands.append((fit["tau"], tr, fit, name))
            if not cands:
                continue
            tau, tr, fit, name = min(cands, key=lambda c: c[0]) if which == "start" else max(cands, key=lambda c: c[0])
            new = float(tau)
            if which == "start" and a.end is not None and new >= a.end - 1:
                continue
            if which == "end" and new <= a.start + 1:
                continue
            shift = new - bnd
            if abs(shift) < 1.0:
                continue
            old = a.relocalised.get(which)
            if old and old.get("raw") == bnd and abs(old.get("t", 0.0) - new) < 1.0:
                continue
            a.relocalised[which] = {"raw": bnd, "t": new, "from": iso(bnd), "to": iso(new), "shift_s": shift,
                                    "method": fit["method"], "channel": name, "tractor": tr,
                                    "magnitude": round(fit["magnitude"], 3)}
            self.eng.episodes.apply_span(a)
            run.actions.append(f"{a.id} {which} {shift:+.0f}s ({fit['method']} on {name})")
            self.eng.lifecycle.event(a, now, a.state.value, a.state, Reason.RELOCALISED, None,
                                     note=f"{which} {hms(bnd)} -> {hms(new)} ({fit['method']} fit on {name}, {tr})")

    # ---- merge consecutive SPACE_WEATHER alarms
    def _merge_sw(self, cands: list[Alarm], now: float, run: WatchdogRun) -> None:
        lc: Lifecycle = self.eng.lifecycle
        gap = self.merge_gap.get("SPACE_WEATHER", 0.0)
        sw = sorted((a for a in cands if a.cls == "SPACE_WEATHER" and a.state != AlarmState.RETRACTED),
                    key=lambda a: (a.start, a.id))
        i = 0
        while i + 1 < len(sw):
            p, q = sw[i], sw[i + 1]
            if p.end is not None and q.start - p.end <= gap:
                p.members.append(q.id)
                q.merged_into = p.id
                frm = q.state.value
                q.state = AlarmState.RETRACTED
                q.reason = Reason.MERGED.value
                q.active.clear()
                lc.event(q, now, frm, AlarmState.RETRACTED, Reason.MERGED, note=f"merged into {p.id} (gap {(q.start - p.end) / 60:.0f} min)")
                p.bf = max(p.bf, q.bf)
                p.confidence = round(p.bf / (1.0 + p.bf), 3)
                self.eng.episodes.apply_span(p)
                lc.event(p, now, p.state.value, p.state, Reason.MERGED, note=f"absorbed {q.id}")
                run.actions.append(f"{q.id} merged into {p.id}")
                del sw[i + 1]
            else:
                i += 1

    # ---- confirm / retract
    def _working_clip(self, a: Alarm, now: float) -> tuple[float | None, float | None, float]:
        """(clipped start, clipped end, working fraction) over the alarm's tractors; None if entirely parked."""
        eng = self.eng
        f0 = eng.feats[eng.tractors[0]]
        end = a.span_end(now)
        b_lo = max(int(np.searchsorted(f0.t0, a.start, side="right")) - 1, 0)
        b_hi = min(int(np.searchsorted(f0.t0, end, side="left")), eng.nb, eng.b_done)
        if b_hi <= b_lo:
            b_hi = b_lo + 1
        trs = [tr for tr in sorted(a.tractors) if tr in eng.working_hist]
        if not trs:
            return a.start, end, 1.0
        w = np.zeros(b_hi - b_lo, dtype=bool)
        for tr in trs:
            w |= eng.working_hist[tr][b_lo:b_hi]
        if not w.any():
            return None, None, 0.0
        idx = np.nonzero(w)[0]
        s = max(a.start, float(f0.t0[b_lo + idx[0]]))
        e = min(end, float(f0.t0[b_lo + idx[-1]] + self.batch_s))
        return s, e, float(w.mean())

    def _retract(self, a: Alarm, now: float, reason: Reason, note: str) -> None:
        self.eng.lifecycle.retract(a, now, reason, note=note)

    # ---- entry point
    def run(self, now: float, final: bool = False) -> WatchdogRun:
        lc: Lifecycle = self.eng.lifecycle
        epm = self.eng.episodes
        run = WatchdogRun(now, final)
        n_re = epm.reclassify_closed(now, self.lookback, final)
        if n_re and final:
            run.actions.append(f"{n_re} closed episode(s) reclassified with the evidence available at the end")
        cands = []
        for aid in lc.order:
            a = lc.alarms[aid]
            if a.state in (AlarmState.RETRACTED, AlarmState.WATCH):
                continue
            if final or a.open or (a.end is not None and now - a.end <= self.lookback):
                cands.append(a)
        for a in cands:
            self._relocalise(a, now, run)
        self._merge_sw(cands, now, run)
        for a in cands:
            if a.state == AlarmState.RETRACTED:
                continue
            run.touched.append(a.id)
            if a.end is not None and a.track != "dip":
                dur = a.end - a.start
                if dur < self.min_batches * self.batch_s:
                    self._retract(a, now, Reason.NOT_SUSTAINED, f"{dur:.0f} s < {self.min_batches} batches")
                    run.actions.append(f"{a.id} retracted (not sustained)")
                    continue
                s, e, frac = self._working_clip(a, now)
                if s is None:
                    self._retract(a, now, Reason.PARKED, "entirely during PARKED")
                    run.actions.append(f"{a.id} retracted (parked)")
                    continue
                if frac < 1.0 and (s > a.start or e < a.end):
                    note = f"clipped to working time {hms(a.start)}..{hms(a.end)} -> {hms(s)}..{hms(e)}"
                    a.extra["clip"] = {"raw": [a.raw_start, a.raw_end], "s": s, "e": e, "note": note}
                    epm.apply_span(a)
                    run.actions.append(f"{a.id} {note}")
            if a.track == "dip" and not a.extra.get("checked"):
                continue                       # the +-60 s exclusion window has not passed yet
            if a.state == AlarmState.ALARM:
                a.state = AlarmState.CONFIRMED
                a.confirmed_at = now
                lc.event(a, now, "ALARM", AlarmState.CONFIRMED, Reason.CONFIRMED, a.bf)
                run.actions.append(f"{a.id} confirmed")
        self.runs.append(run)
        if len(self.runs) > 200:
            del self.runs[:-200]
        self.eng.sink.emit({"kind": "watchdog", "t_data": now, "t_data_iso": iso(now), "final": final,
                            "n_touched": len(run.touched), "alarms": run.touched, "actions": run.actions},
                           console=bool(run.actions) or final)
        return run
