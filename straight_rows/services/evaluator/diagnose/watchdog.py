"""Live watchdog: every 5 min of data time and once at the end (spec §7.7) [R30, R31]."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from detect.inputs.s4 import f10_at
from diagnose.lifecycle import Alarm, BatchRecord, Lifecycle
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
        self.runs: list[WatchdogRun] = []

    # ---- channel access
    def _channel(self, a: Alarm, tr: str) -> tuple[np.ndarray, str]:
        s = self.eng.tele.series[tr]
        f = self.eng.feats[tr]
        if a.cls == "SPOOFING":
            return np.where(f.turn_mask, np.nan, s.xte), "xte"
        if a.cls == "EQUIPMENT_FAULT":
            has_f7 = has_fix = has_sats = False
            for rec in a.records:
                fm = rec.findings.get(tr, {})
                if fm.get("F7", {}).get("present"):
                    has_f7 = True
                f6 = fm.get("F6", {})
                if f6.get("present"):
                    if (f6.get("fix") or {}).get("degraded"):
                        has_fix = True
                    if (f6.get("sats") or {}).get("low"):
                        has_sats = True
            if has_f7:
                return 1.0 - s.link_ok, "link_down"
            if has_fix:
                return s.fix_rank, "fix_rank"
            if has_sats:
                return s.sats, "sats"
        return s.cn0, "cn0"

    def _window(self, t_b: float, now: float) -> tuple[int, int]:
        t = self.eng.tele.t
        lo = int(np.searchsorted(t, t_b - self.margin, side="left"))
        hi = int(np.searchsorted(t, min(t_b + self.margin, now), side="left"))
        return lo, hi

    def _relocalise(self, a: Alarm, now: float, run: WatchdogRun) -> None:
        if a.cls == "MULTIPATH" or a.state == AlarmState.WATCH:
            return
        t = self.eng.tele.t
        for which in ("start", "end"):
            if which == "end" and a.end is None:
                continue
            bnd = a.start if which == "start" else a.end
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
            if which == "start":
                tau, tr, fit, name = min(cands, key=lambda c: c[0])
            else:
                tau, tr, fit, name = max(cands, key=lambda c: c[0])
            new = float(tau)
            other = a.end if which == "start" else a.start
            if which == "start" and other is not None and new >= other - 1:
                continue
            if which == "end" and new <= a.start + 1:
                continue
            shift = new - bnd
            if abs(shift) < 1.0:
                continue
            if which == "start":
                a.start = new
            else:
                a.end = new
            a.relocalised[which] = {"from": iso(bnd), "to": iso(new), "shift_s": shift, "method": fit["method"],
                                    "channel": name, "tractor": tr, "magnitude": round(fit["magnitude"], 3)}
            run.actions.append(f"{a.id} {which} {shift:+.0f}s ({fit['method']} on {name})")
            self.eng.lifecycle.event(a, now, a.state.value, a.state, Reason.RELOCALISED, None,
                                     note=f"{which} {hms(bnd)} -> {hms(new)} ({fit['method']} fit on {name}, {tr})")

    # ---- merge
    def _merge_pair(self, p: Alarm, q: Alarm, now: float, run: WatchdogRun) -> None:
        lc: Lifecycle = self.eng.lifecycle
        p.tractors |= q.tractors
        p.active |= q.active
        p.end = None if (p.end is None or q.end is None) else max(p.end, q.end)
        p.start = min(p.start, q.start)
        recs = {r.t0: r for r in p.records}
        for r in q.records:
            if r.t0 in recs:
                recs[r.t0].findings.update(r.findings)
                recs[r.t0].tractors = tuple(sorted(set(recs[r.t0].tractors) | set(r.tractors)))
                recs[r.t0].bf = max(recs[r.t0].bf, r.bf)
                recs[r.t0].in_expl = recs[r.t0].in_expl or r.in_expl
            else:
                recs[r.t0] = r
        p.records = [recs[k] for k in sorted(recs)]
        p.relocalised.pop("end", None)
        for key, aid in list(lc.attach.items()):
            if aid == q.id:
                lc.attach[key] = p.id
        q.state = AlarmState.RETRACTED
        q.reason = Reason.MERGED.value
        q.merged_into = p.id
        q.active.clear()
        if q.end is None:
            q.end = now
        lc.event(q, now, q.history[-1]["to"] if q.history else None, AlarmState.RETRACTED, Reason.MERGED,
                 note=f"merged into {p.id}")
        lc.event(p, now, p.state.value, p.state, Reason.MERGED, note=f"absorbed {q.id}")
        run.actions.append(f"{q.id} merged into {p.id}")

    def _merge(self, cands: list[Alarm], now: float, run: WatchdogRun) -> list[Alarm]:
        """Merge same-class alarms closer than the class merge gap. All pairs are compared, repeatedly, until
        nothing merges. Multi-machine classes (SW, JAM) merge across tractors; the others need a shared tractor.
        MULTIPATH never merges."""
        from diagnose.lifecycle import MULTI_MACHINE
        by_cls: dict[str, list[Alarm]] = {}
        for a in cands:
            if a.cls == "MULTIPATH" or a.state == AlarmState.WATCH:
                continue
            by_cls.setdefault(a.cls, []).append(a)
        out = list(cands)
        for cls, group in by_cls.items():
            gap = self.merge_gap.get(cls, 0.0)
            changed = True
            while changed:
                changed = False
                group.sort(key=lambda a: a.start)
                for i in range(len(group)):
                    p = group[i]
                    if p.state == AlarmState.RETRACTED:
                        continue
                    for j in range(i + 1, len(group)):
                        q = group[j]
                        if q.state == AlarmState.RETRACTED:
                            continue
                        overlap = cls in MULTI_MACHINE or bool(p.tractors & q.tractors)
                        if not overlap:
                            continue
                        gap_s = max(q.start - p.span_end(now), p.start - q.span_end(now))
                        if gap_s <= gap:
                            self._merge_pair(p, q, now, run)
                            if q in out:
                                out.remove(q)
                            changed = True
                            break
                    if changed:
                        break
        return out

    def _split_parked(self, a: Alarm, now: float, run: WatchdogRun) -> list[Alarm]:
        """Final pass: an alarm whose span crosses a parked gap longer than the class merge gap becomes one alarm
        per working segment (a parked night separates two guidance episodes). Returns the new alarms."""
        eng = self.eng
        lc: Lifecycle = eng.lifecycle
        if a.cls == "MULTIPATH" or a.state in (AlarmState.RETRACTED, AlarmState.WATCH):
            return []
        f0 = eng.feats[eng.tractors[0]]
        end = a.span_end(now)
        b_lo = max(int(np.searchsorted(f0.t0, a.start, side="right")) - 1, 0)
        b_hi = min(int(np.searchsorted(f0.t0, end, side="left")), eng.nb, eng.b_done)
        if b_hi - b_lo < 2:
            return []
        trs = [tr for tr in sorted(a.tractors) if tr in eng.working_hist]
        w = np.zeros(b_hi - b_lo, dtype=bool)
        for tr in trs:
            w |= eng.working_hist[tr][b_lo:b_hi]
        if not w.any():
            return []
        idx = np.nonzero(w)[0]
        breaks = np.nonzero(np.diff(idx) * self.batch_s > max(self.merge_gap.get(a.cls, 0.0), self.batch_s))[0]
        if len(breaks) == 0:
            return []
        segs = []
        s_i = idx[0]
        for br in breaks:
            segs.append((s_i, idx[br]))
            s_i = idx[br + 1]
        segs.append((s_i, idx[-1]))
        new_alarms = []
        orig_end = end
        for k, (i0, i1) in enumerate(segs):
            s = float(f0.t0[b_lo + i0])
            e = float(f0.t0[b_lo + i1] + self.batch_s)
            if k == 0:
                a.start = max(a.start, s)
                a.end = min(orig_end, e)
                a.extra["split"] = f"split at parked gaps into {len(segs)} segments"
                run.actions.append(f"{a.id} split at parked gap ({len(segs)} segments)")
                continue
            recs = [r for r in a.records if s <= r.t0 < e]
            n = lc.create(a.cls, now, max(s, a.records[0].t0 if a.records else s), set(a.tractors), AlarmState.ALARM, None,
                          Reason.RAISED, end=min(e, orig_end), note=f"segment {k + 1} split from {a.id} at a parked gap")
            n.start = s
            n.records = [BatchRecord(r.t0, r.tractors, r.post, r.bf, r.in_expl, r.findings) for r in recs]
            n.extra["split_from"] = a.id
            new_alarms.append(n)
        a.records = [r for r in a.records if r.t0 < a.end]
        return new_alarms

    # ---- reclassify with late evidence
    def _hindsight(self, a: Alarm, now: float) -> tuple[dict[str, float], dict[str, float], int]:
        """Fractions of (batch, tractor) pairs where each class is in the explanation / alarm-strong, with hindsight F10."""
        eng = self.eng
        fu = eng.fusion
        j10 = fu.findings.index("F10")
        n = 0
        in_expl = {c: 0 for c in fu.causes}
        strong = {c: 0 for c in fu.causes}
        for rec in a.records:
            b = int(round((rec.t0 - eng.feats[eng.tractors[0]].t0[0]) / self.batch_s))
            if b < 0 or b >= eng.nb:
                continue
            for tr in rec.tractors:
                pres = eng.fv_present[tr][b].copy()
                sevs = eng.fv_sev[tr][b].copy()
                miss = eng.fv_missing[tr][b].copy()
                f = eng.feats[tr]
                lat, lon = float(f.lat_mean[b]), float(f.lon_mean[b])
                if np.isnan(lat):
                    lat, lon = eng.ctx.farm_lat, eng.ctx.farm_lon
                r10 = f10_at(eng.sda, self.cfg, lat, lon, rec.t0, self.batch_s, now)
                pres[j10], sevs[j10], miss[j10] = r10["present"], r10["severity"], False
                res = fu.evaluate(tr, rec.t0, pres, sevs, miss)
                n += 1
                for c in fu.causes:
                    if c in res.explanation:
                        in_expl[c] += 1
                        if res.bayes_factor[c] >= fu.bf_alarm:
                            strong[c] += 1
        if n == 0:
            return {c: 0.0 for c in fu.causes}, {c: 0.0 for c in fu.causes}, 0
        return {c: in_expl[c] / n for c in fu.causes}, {c: strong[c] / n for c in fu.causes}, n

    def _reclassify(self, a: Alarm, now: float, run: WatchdogRun) -> Alarm | None:
        """Returns a replacement alarm when the class changed, else None. May retract `a`."""
        if a.state == AlarmState.WATCH or not a.records:
            return None
        lc: Lifecycle = self.eng.lifecycle
        frac_expl, frac_strong, n = self._hindsight(a, now)
        if n == 0 or frac_expl.get(a.cls, 0.0) >= 0.5:
            return None
        alt = max((c for c in frac_strong if c != a.cls and c != "OTHER"), key=lambda c: frac_strong[c], default=None)
        if alt is not None and frac_strong[alt] >= 0.5:
            frm = a.state.value
            a.state = AlarmState.RETRACTED
            a.reason = Reason.RECLASSIFIED.value
            a.active.clear()
            if a.end is None:
                a.end = now
            lc.event(a, now, frm, AlarmState.RETRACTED, Reason.RECLASSIFIED,
                     note=f"{a.cls} in explanation for {frac_expl[a.cls]:.0%} of span; {alt} strong in {frac_strong[alt]:.0%}")
            for key, aid in list(lc.attach.items()):
                if aid == a.id:
                    lc.attach.pop(key, None)
            tgt = lc.joinable(alt, now)
            if tgt is not None and not (tgt.span_end(now) < a.start - self.merge_gap.get(alt, 0.0)
                                         or tgt.start > a.span_end(now) + self.merge_gap.get(alt, 0.0)):
                tgt.start = min(tgt.start, a.start)
                tgt.end = None if (tgt.end is None or a.end is None) else max(tgt.end, a.end)
                tgt.tractors |= a.tractors
                tgt.records = sorted(tgt.records + [r for r in a.records if r.t0 not in {x.t0 for x in tgt.records}], key=lambda r: r.t0)
                lc.event(tgt, now, tgt.state.value, tgt.state, Reason.RECLASSIFIED, note=f"extended from {a.id}")
                run.actions.append(f"{a.id} {a.cls}->{alt} (extended {tgt.id})")
                return tgt
            new = lc.create(alt, now, a.start, set(a.tractors), AlarmState.ALARM, None, Reason.RECLASSIFIED,
                            end=a.end, note=f"reclassified from {a.id}")
            new.records = [BatchRecord(r.t0, r.tractors, r.post, r.bf, r.in_expl, r.findings) for r in a.records]
            new.extra["reclassified_from"] = a.id
            run.actions.append(f"{a.id} {a.cls}->{alt} ({new.id})")
            return new
        frm = a.state.value
        a.state = AlarmState.RETRACTED
        a.reason = Reason.EXPLAINED_BY_OTHER_CAUSE.value
        a.active.clear()
        if a.end is None:
            a.end = now
        for key, aid in list(lc.attach.items()):
            if aid == a.id:
                lc.attach.pop(key, None)
        lc.event(a, now, frm, AlarmState.RETRACTED, Reason.EXPLAINED_BY_OTHER_CAUSE,
                 note=f"{a.cls} in explanation for only {frac_expl[a.cls]:.0%} of its span with hindsight")
        run.actions.append(f"{a.id} retracted (explained by other cause)")
        return None

    def _inside_jamming(self, m: Alarm, now: float) -> bool:
        lc: Lifecycle = self.eng.lifecycle
        for aid in lc.order:
            j = lc.alarms[aid]
            if j.cls != "JAMMING" or j.state in (AlarmState.RETRACTED, AlarmState.WATCH):
                continue
            if (j.tractors & m.tractors) and j.start <= m.start and j.span_end(now) >= m.span_end(now):
                return True
        return False

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

    def _confirm(self, a: Alarm, now: float, run: WatchdogRun) -> None:
        if a.state in (AlarmState.RETRACTED, AlarmState.WATCH):
            return
        lc: Lifecycle = self.eng.lifecycle
        dur = a.span_end(now) - a.start
        if a.cls != "MULTIPATH" and dur < self.min_batches * self.batch_s and a.end is not None:
            self._retract(a, now, Reason.NOT_SUSTAINED, f"{dur:.0f} s < {self.min_batches} batches")
            run.actions.append(f"{a.id} retracted (not sustained)")
            return
        s, e, frac = self._working_clip(a, now)
        if s is None:
            self._retract(a, now, Reason.PARKED, "entirely during PARKED")
            run.actions.append(f"{a.id} retracted (parked)")
            return
        if frac < 1.0 and (s > a.start or (a.end is not None and e < a.end)):
            note = f"clipped to working time {hms(a.start)}..{hms(a.span_end(now))} -> {hms(s)}..{hms(e)}"
            a.start = s
            if a.end is not None:
                a.end = e
            a.extra["parked_clip"] = note
            run.actions.append(f"{a.id} {note}")
        # Fix 7: confidence = median over batches of BF/(1+BF) (posterior under even prior odds)
        bfs = [r.bf / (1.0 + r.bf) for r in a.records if np.isfinite(r.bf)]
        a.confidence = float(np.median(bfs)) if bfs else 0.0
        if a.state == AlarmState.ALARM:
            a.state = AlarmState.CONFIRMED
            a.confirmed_at = now
            lc.event(a, now, "ALARM", AlarmState.CONFIRMED, Reason.CONFIRMED, max((r.bf for r in a.records), default=None))
            run.actions.append(f"{a.id} confirmed")

    def _retract(self, a: Alarm, now: float, reason: Reason, note: str) -> None:
        lc: Lifecycle = self.eng.lifecycle
        frm = a.state.value
        a.state = AlarmState.RETRACTED
        a.reason = reason.value
        a.active.clear()
        if a.end is None:
            a.end = now
        for key, aid in list(lc.attach.items()):
            if aid == a.id:
                lc.attach.pop(key, None)
        lc.event(a, now, frm, AlarmState.RETRACTED, reason, note=note)

    # ---- entry point
    def run(self, now: float, final: bool = False) -> WatchdogRun:
        lc: Lifecycle = self.eng.lifecycle
        run = WatchdogRun(now, final)
        cands = []
        for aid in lc.order:
            a = lc.alarms[aid]
            if a.state in (AlarmState.RETRACTED, AlarmState.WATCH):
                continue
            if final or a.open or (a.end is not None and now - a.end <= self.lookback):
                cands.append(a)
        for a in cands:
            self._relocalise(a, now, run)
        cands = self._merge(cands, now, run)
        if final:
            extra = []
            for a in list(cands):
                extra += self._split_parked(a, now, run)
            cands += extra
        queue = list(cands)
        seen = set()
        while queue:
            a = queue.pop(0)
            if a.id in seen or a.state == AlarmState.RETRACTED:
                continue
            seen.add(a.id)
            run.touched.append(a.id)
            dur = a.span_end(now) - a.start
            if a.cls != "MULTIPATH" and a.end is not None and dur < self.min_batches * self.batch_s:
                self._retract(a, now, Reason.NOT_SUSTAINED, f"{dur:.0f} s < {self.min_batches} batches")
                run.actions.append(f"{a.id} retracted (not sustained)")
                continue
            if a.cls == "MULTIPATH" and self._inside_jamming(a, now):
                self._retract(a, now, Reason.EXPLAINED_BY_OTHER_CAUSE, "dip inside a JAMMING alarm on the same tractor (Fix 6)")
                run.actions.append(f"{a.id} retracted (inside jamming)")
                continue
            repl = self._reclassify(a, now, run)
            if repl is not None and repl.id not in seen:
                self._relocalise(repl, now, run)
                queue.append(repl)
                continue
            self._confirm(a, now, run)
        self.runs.append(run)
        if len(self.runs) > 200:
            del self.runs[:-200]
        self.eng.sink.emit({"kind": "watchdog", "t_data": now, "t_data_iso": iso(now), "final": final,
                            "n_touched": len(run.touched), "alarms": run.touched, "actions": run.actions},
                           console=bool(run.actions) or final)
        return run
