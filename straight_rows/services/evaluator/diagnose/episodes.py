"""Episodes first, causes second (Update v2).

Three tracks, each deciding *when* something is wrong before deciding *what* it is:

* signal   (§2-§4): per tractor A = F1 v F6_raw v F7 (v2.1 §2 option 1: F2 alone never opens or holds an episode).
  Only WORKING, non-turn batches count (v2.1 §1, option 4): a tractor episode opens on A in 2 of the last 3 such
  batches and closes after 10 A-false ones. Tractor episodes whose onsets fall within 5 min of a farm episode's onset
  join it (v2.1 §3); any other one is its own episode. While any episode is open every tractor's C/N0 level and
  satellite references stay frozen; they are discarded when the last one closes. A tractor episode still
  unattributed after 6 h is force-closed and that tractor's reference rebuilt (option 3, safety valve).
  Findings are judged over the whole episode (fractions phi). SPACE_WEATHER and JAMMING are decided once per farm
  episode on the summed per-tractor log-likelihoods, shared findings once (v2.1 §4); the one-machine classes per
  tractor. A stacked step on >= 2 tractors inside an open farm episode opens a sub-episode, classified against
  the in-event reference.
* position (§5): SPOOFING from the F8 drift CUSUM with a position-domain F12, independent of the signal track.
* short-dip(§6): MULTIPATH from F5 dips, only when no other episode is open on that tractor within +-60 s.

Each episode is shown live as one or more alarms (diagnose/lifecycle.py); `reconcile` keeps the alarms in step
with the episode's classification so far.
"""
from __future__ import annotations

import csv
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.log import hms, iso
from core.srtypes import FINDING_IDS, AlarmState, Reason
from detect.inputs.sattelites import freeze_sats, sats_reference, thaw_sats
from diagnose.lifecycle import Alarm, BatchRecord, lite
from ingest.grid import run_length_true
from ingest.load import haversine_km

log = logging.getLogger("sr.episodes")
J = {f: i for i, f in enumerate(FINDING_IDS)}
SIGNAL_ABSENT = ("F5", "F8")      # held absent in signal classification: their causes cannot win without them (§3)
SIGNAL_MISSING = ("F11",)         # F11 is the §4 split, not a network finding
STICKY = (Reason.NOT_SUSTAINED.value, Reason.PARKED.value)
FARM_CLASSES = ("SPACE_WEATHER", "JAMMING")                 # decided at farm level (v2.1 §4)
SHARED = ("F3", "F4", "F9", "F10", "F12")                   # counted once per farm episode
PER_TRACTOR = ("F1", "F2", "F6", "F7")                      # counted once per tractor


# --------------------------------------------------------------------------- episode records
@dataclass
class TractorEpisode:
    id: str
    tr: str
    start: float
    open_b: int
    last_a_b: int
    end: float | None = None
    streak: int = 0
    farm: str | None = None
    attributed: bool = False          # some class reached BF >= bf_alarm with this tractor in it
    forced: bool = False              # closed by the 6 h safety valve (v2.1 §2 option 3)


@dataclass
class FarmEpisode:
    id: str
    start: float
    open_b: int
    end: float | None = None
    eps: list[TractorEpisode] = field(default_factory=list)
    subs: list["SubEpisode"] = field(default_factory=list)
    alarm_ids: list[str] = field(default_factory=list)
    records: list[BatchRecord] = field(default_factory=list)
    ref_mu: dict[str, float] = field(default_factory=dict)
    sats_ref: dict[str, float] = field(default_factory=dict)
    result: dict | None = None
    closed_t: float | None = None
    onset0: float = 0.0               # the farm onset that later tractor episodes must fall within 5 min of

    @property
    def open(self) -> bool:
        return self.end is None

    def members(self) -> list[str]:
        return sorted({e.tr for e in self.eps})


@dataclass
class SubEpisode:
    id: str
    parent: FarmEpisode
    start: float
    open_b: int
    tractors: set[str]
    last_b: int
    end: float | None = None
    streak: int = 0
    alarm_ids: list[str] = field(default_factory=list)
    result: dict | None = None
    closed_t: float | None = None

    @property
    def open(self) -> bool:
        return self.end is None


@dataclass
class SpoofEpisode:
    id: str
    tr: str
    start: float
    open_b: int
    last_b: int
    end: float | None = None
    absent: int = 0
    alarm_ids: list[str] = field(default_factory=list)
    records: list[BatchRecord] = field(default_factory=list)
    result: dict | None = None
    closed_t: float | None = None

    @property
    def open(self) -> bool:
        return self.end is None


# --------------------------------------------------------------------------- manager
class EpisodeManager:
    def __init__(self, eng):
        self.eng = eng
        cfg = eng.cfg
        self.cfg = cfg
        e = cfg["episodes"]
        self.m, self.n = int(e["open_m"]), int(e["open_n"])
        self.close_b = int(e["close_batches"])
        self.phi_min = float(e["phi_present"])
        self.phi_f12 = float(e["phi_f12_other_max"])
        self.flicker_min = int(e["flicker_min_events"])
        self.stk_min = int(e["stacked_min_tractors"])
        self.stk_win = int(e["stacked_window_batches"])
        self.join_s = float(e.get("farm_join_min", 5)) * 60
        self.max_open_s = float(e.get("max_unattributed_h", 6)) * 3600
        d = cfg["dips"]
        self.dip_excl = float(d["exclusion_s"])
        self.dip_f12_pad = float(d["f12_pad_s"])
        fc = cfg["findings"]
        self.link_total = float(fc.get("link_down_total_s", 30))
        self.link_outages = int(fc.get("link_outages_min", 3))
        self.sats_drop = float(fc["sats_drop"])
        self.fix_frac = float(fc["fix_frac"])
        self.equal_db = float(fc["equal_drop_db"])
        self.unequal_db = float(fc["unequal_drop_db"])
        self.rfi_w = float(fc["rfi"]["match_window_min"]) * 60
        self.use_conf = bool(fc["rfi"].get("use_report_confidence", True))
        self.bf_alarm = float(cfg["fusion"]["bf_alarm"])
        self.batch_s = float(cfg["grid"]["batch_s"])
        self.tractors = list(eng.tractors)
        nb = eng.nb
        self.t00 = float(eng.t0s[0])
        self.A = {tr: np.zeros(nb, dtype=bool) for tr in self.tractors}
        self.E = {tr: np.zeros(nb, dtype=bool) for tr in self.tractors}      # batch counts for open/close (WORKING, no turn)
        self.elig_hist = {tr: deque(maxlen=self.n) for tr in self.tractors}
        self.fu_ep = getattr(eng, "fusion_ep", None) or eng.fusion
        self.forced: list[dict] = []
        self.a_on = {tr: np.full(nb, np.nan) for tr in self.tractors}
        self.drop = {tr: np.full(nb, np.nan) for tr in self.tractors}      # F1 drop vs the (frozen) reference
        self.xdrop = {tr: np.full(nb, np.nan) for tr in self.tractors}     # F11 extra drop vs the in-event level
        self.f11_rise: dict[str, int] = {}
        self.pos12 = {tr: np.full(nb, -1, dtype=np.int8) for tr in self.tractors}   # -1 missing, 0 absent, 1 present
        self.tr_eps: list[TractorEpisode] = []
        self.open_tr: dict[str, TractorEpisode] = {}
        self.farms: list[FarmEpisode] = []
        self.open_groups: list[FarmEpisode] = []
        self.spoofs: list[SpoofEpisode] = []
        self.open_spoof: dict[str, SpoofEpisode] = {}
        self.spoof_hist = {tr: deque(maxlen=self.n) for tr in self.tractors}
        self.dips: list[dict] = []
        self.pending_dips: list[tuple[dict, Alarm]] = []
        self.counter: dict[str, int] = {}
        self.bmax = 0
        self.now = self.t00

    # ---- small helpers
    def _id(self, kind: str) -> str:
        self.counter[kind] = self.counter.get(kind, 0) + 1
        return f"{kind}-{self.counter[kind]}"

    def _b_end(self, b: int) -> float:
        return float(self.eng.t0s[b]) + self.batch_s

    def _brange(self, s: float, e: float) -> tuple[int, int]:
        b0 = max(int(math.floor((s - self.t00) / self.batch_s)), 0)
        b1 = min(int(math.ceil((e - self.t00) / self.batch_s)), self.bmax)
        return b0, max(b1, min(b0 + 1, self.bmax))

    def _irange(self, s: float, e: float) -> tuple[int, int]:
        t = self.eng.tele.t
        e = min(e, self.now)
        return int(np.searchsorted(t, s, side="left")), int(np.searchsorted(t, e, side="left"))

    def _working_in(self, tr: str, s: float, e: float) -> bool:
        b0, b1 = self._brange(s, e)
        return bool(self.eng.working_hist[tr][b0:b1].any())

    def _phi(self, tr: str, fid: str, b0: int, b1: int, denom_fid: str | None = None) -> tuple[float | None, float]:
        eng = self.eng
        j = J[fid]
        pres = eng.fv_present[tr][b0:b1, j]
        ok = ~eng.fv_missing[tr][b0:b1, J[denom_fid or fid]]
        n = int(ok.sum())
        if n == 0:
            return None, 0.0
        hit = pres & ok
        sev = float(np.median(eng.fv_sev[tr][b0:b1, j][hit])) if hit.any() else 0.0
        return float(hit.sum()) / n, sev

    @staticmethod
    def _vec(fu, fd: dict[str, dict], only: tuple[str, ...] | None = None):
        pres, sevs, miss = [], [], []
        for fid in fu.findings:
            f = fd.get(fid)
            if f is None or f["missing"] or (only is not None and fid not in only):
                pres.append(False); sevs.append(0.0); miss.append(True)
            else:
                pres.append(bool(f["present"])); sevs.append(float(f["sev"])); miss.append(False)
        return pres, sevs, miss

    def _evaluate(self, tr: str, t0: float, fd: dict[str, dict], episode: bool = True) -> Any:
        """episode=True: the episode-level network (episode leaks); False: the per-batch network."""
        fu = self.fu_ep if episode else self.eng.fusion
        return fu.evaluate(tr, t0, *self._vec(fu, fd))

    def _joint(self, per: dict[str, dict], trs: list[str], t0: float) -> Any:
        """v2.1 §4: one cause set for the whole farm; per-tractor findings summed over `trs`, shared ones once."""
        fu = self.fu_ep
        fds = [per[tr]["findings"] for tr in trs]
        shared: dict[str, dict] = {}
        for fid in SHARED:
            known = [fd[fid] for fd in fds if fid in fd and not fd[fid]["missing"]]
            if not known:
                shared[fid] = {"present": False, "sev": 0.0, "missing": True}
            else:
                hit = [f for f in known if f["present"]]
                shared[fid] = {"present": bool(hit), "sev": max((f["sev"] for f in hit), default=0.0), "missing": False}
        ll = fu.loglik(*self._vec(fu, shared, SHARED))
        for fd in fds:
            ll = ll + fu.loglik(*self._vec(fu, fd, PER_TRACTOR))
        r = fu.evaluate_ll("*", t0, ll)
        return r, shared

    def signs(self, cls: str, fds: list[dict[str, dict]], bf: float, fu=None) -> str:
        """v2.1 §5: the class's known signs (links >= 0.50), each judged once over the episode; P = BF/(1+BF)."""
        fu = fu or self.fu_ep
        k = fu.causes.index(cls)
        known = [fid for j, fid in enumerate(fu.findings) if fu.P[j, k] >= 0.5]
        seen, miss, na = [], [], []
        for fid in known:
            fs = [fd[fid] for fd in fds if fid in fd and not fd[fid].get("missing", True)]
            if not fs:
                na.append(fid)
            elif any(f.get("present") for f in fs):
                seen.append(fid)
            else:
                miss.append(fid)
        n = len(seen) + len(miss)
        inner = ", ".join(seen) or "none"
        if miss:
            inner += "; " + ", ".join(miss) + " missing"
        if na:
            inner += "; " + ", ".join(na) + " no information"
        return f"signs seen {len(seen)} of {n} ({inner}), P = {bf / (1.0 + bf):.3f}"

    @staticmethod
    def _map_class(r) -> str:
        return max(r.posterior, key=lambda c: (r.posterior[c], c))

    # ================================================================== per batch
    def step(self, b: int, t0: float, now: float, findings: dict[str, dict[str, Any]]) -> None:
        self.bmax = b + 1
        self.now = now
        self._flags(b, t0, findings)
        self._signal(b, t0, now, findings)
        self._position(b, t0, now, findings)
        self._dips(b, t0, now, findings)

    # ---- per-batch anomaly flag A (§2)
    def _flags(self, b: int, t0: float, findings) -> None:
        eng = self.eng
        for tr in self.tractors:
            fm = findings[tr]
            f = eng.feats[tr]
            f1, f2, f6, f7, f11 = (fm.get(k) for k in ("F1", "F2", "F6", "F7", "F11"))
            if f1 is not None and f1.present and not f1.detail.get("fallback"):
                self.drop[tr][b] = float(f1.detail.get("drop_db", np.nan))
            if f11 is not None and f11.present:
                self.xdrop[tr][b] = float(f11.detail.get("extra_drop_db", np.nan))
                if b == 0 or not eng.fv_present[tr][b - 1, J["F11"]]:
                    self.f11_rise[tr] = b
            # F6_raw: fix_rank >= 1 for > 50% of the batch, or satellites >= 2 below the reference median (no chain rule)
            fix_raw = bool(eng.ctx.valid(f.fix_frac[b]) and not math.isnan(f.fix_deg_frac[b]) and f.fix_deg_frac[b] > self.fix_frac)
            sats_low = bool(f6 is not None and (f6.detail.get("sats") or {}).get("low"))
            # v2.1 §1/§2 option 4: only WORKING batches without a headland turn count toward opening and closing
            self.E[tr][b] = bool(eng.working_hist[tr][b]) and not bool(f.turn_batch[b])
            hits, ons = [], []
            for g in (f1, f7):                     # v2.1 §2 option 1: F2 alone never opens or holds an episode
                if g is not None and g.present:
                    hits.append(g.fid)
                    if g.onset is not None:
                        ons.append(float(g.onset))
            if fix_raw or sats_low:
                hits.append("F6")
                if f6 is not None and f6.present and f6.onset is not None:
                    ons.append(float(f6.onset))
            if hits:
                self.A[tr][b] = True
                self.a_on[tr][b] = min(ons) if ons else t0

    # ---- signal track
    def _signal(self, b: int, t0: float, now: float, findings) -> None:
        # opens first (an episode opening near an open farm episode's onset joins it), then closes
        for tr in self.tractors:
            if self.E[tr][b]:
                self.elig_hist[tr].append(b)
            if tr in self.open_tr or not self.E[tr][b] or not self.A[tr][b]:
                continue
            idx = [k for k in self.elig_hist[tr] if self.A[tr][k]]
            if len(idx) >= self.m:
                onset = float(np.nanmin(self.a_on[tr][idx]))
                ep = TractorEpisode(self._id(f"E-{tr}"), tr, onset, b, b)
                self.open_tr[tr] = ep
                self.tr_eps.append(ep)
                self._attach(ep, b, now)
        for tr in self.tractors:
            ep = self.open_tr.get(tr)
            if ep is None or ep.open_b == b:
                continue
            if self.E[tr][b]:                      # turn and parked batches are neither anomalous nor clean
                if self.A[tr][b]:
                    ep.last_a_b = b
                    ep.streak = 0
                else:
                    ep.streak += 1
                    if ep.streak >= self.close_b:
                        ep.end = self._b_end(ep.last_a_b)
                        del self.open_tr[tr]
                        continue
            if not ep.attributed and now - ep.start >= self.max_open_s:
                self._force_close(ep, now)
        for farm in list(self.open_groups):
            self._subs(farm, b, t0, now, findings)
            self._record(farm, t0, findings)
            if not any(e.end is None for e in farm.eps):
                farm.end = max(e.end for e in farm.eps)
                farm.closed_t = now
                for s in farm.subs:
                    if s.open:
                        s.end = min(self._b_end(s.last_b), farm.end)
                        s.closed_t = now
                self.open_groups.remove(farm)
                if not self.open_groups:
                    self._thaw()
                self.classify_farm(farm, now, closed=True)
                for s in farm.subs:
                    if s.closed_t == now:
                        self.classify_sub(s, now, closed=True)
            else:
                self.classify_farm(farm, now, closed=False)
                for s in farm.subs:
                    if s.open or s.closed_t == now:
                        self.classify_sub(s, now, closed=not s.open)

    def _force_close(self, ep: TractorEpisode, now: float) -> None:
        """v2.1 §2 option 3: open 6 h with no class at BF >= bf_alarm -> close it unattributed, discard the frozen
        reference of that tractor and rebuild it."""
        ep.end = self._b_end(ep.last_a_b)
        ep.forced = True
        del self.open_tr[ep.tr]
        self._thaw([ep.tr])
        rec = {"episode": ep.id, "tractor": ep.tr, "start": iso(ep.start), "end": iso(ep.end), "at": iso(now),
               "open_h": round((now - ep.start) / 3600.0, 2), "farm": ep.farm}
        self.forced.append(rec)
        log.warning("[bold yellow]forced close[/bold yellow] %s (%s) open %.1f h with no class at BF >= %g: "
                    "unattributed, reference discarded and rebuilt", ep.id, ep.tr, rec["open_h"], self.bf_alarm)

    def _attach(self, ep: TractorEpisode, b: int, now: float) -> None:
        # v2.1 §3: join by common onset (within 5 min of the farm onset), never by overlap; a tractor that is already
        # a member of an open farm episode re-opening inside it continues there
        g = next((x for x in self.open_groups if ep.tr in x.members()), None)
        if g is None:
            g = next((x for x in self.open_groups if abs(ep.start - x.onset0) <= self.join_s), None)
        if g is None:
            g = FarmEpisode(self._id("F"), ep.start, b, onset0=ep.start)
            self.farms.append(g)
            self.open_groups.append(g)
            self._freeze(g)
        g.eps.append(ep)
        ep.farm = g.id
        g.start = min(g.start, ep.start)

    def _freeze(self, farm: FarmEpisode) -> None:
        ctx = self.eng.ctx
        for tr in self.tractors:
            for name in ("cn0_mean", "cn0_logvar"):
                ch = ctx.channels.get((tr, name))
                if ch is not None:
                    ch.freeze(farm.start)
                    if name == "cn0_mean" and ch.mu is not None:
                        farm.ref_mu[tr] = float(ch.mu)
            freeze_sats(ctx, tr, farm.start)
            farm.sats_ref[tr] = sats_reference(ctx, tr)

    def _thaw(self, trs: list[str] | None = None) -> None:
        ctx = self.eng.ctx
        for tr in (trs or self.tractors):
            for name in ("cn0_mean", "cn0_logvar"):
                ch = ctx.channels.get((tr, name))
                if ch is not None:
                    ch.thaw()
            thaw_sats(ctx, tr)

    def _record(self, farm: FarmEpisode, t0: float, findings) -> None:
        bf = max((a.bf for a in self._alarms(farm.alarm_ids)), default=0.0)
        farm.records.append(BatchRecord(t0, tuple(farm.members()), 0.0, bf, True,
                                        {tr: {fid: lite(f) for fid, f in findings[tr].items()} for tr in self.tractors}))

    # ---- §4 stacked-step sub-episodes
    def _subs(self, farm: FarmEpisode, b: int, t0: float, now: float, findings) -> None:
        eng = self.eng
        sub = next((s for s in farm.subs if s.open), None)
        mem = set(farm.members())
        f11_now = [tr for tr in self.tractors if tr in mem and eng.fv_present[tr][b, J["F11"]]]
        if sub is None:
            recent = sorted(tr for tr, rb in self.f11_rise.items() if b - self.stk_win <= rb <= b and tr in f11_now)
            if len(recent) >= self.stk_min:
                onsets = [findings[tr]["F11"].onset for tr in recent if findings[tr]["F11"].onset is not None]
                start = float(min(onsets)) if onsets else t0
                sub = SubEpisode(self._id("S"), farm, start, b, set(recent), b)
                farm.subs.append(sub)
            return
        if f11_now:
            sub.tractors |= set(f11_now)
            sub.last_b = b
            sub.streak = 0
        else:
            sub.streak += 1
            if sub.streak >= self.close_b:
                sub.end = self._b_end(sub.last_b)
                sub.closed_t = now

    # ---- episode-level findings (§3)
    def _tractor_hull(self, farm: FarmEpisode, tr: str) -> tuple[float, float] | None:
        eps = [e for e in farm.eps if e.tr == tr]
        if not eps:
            return None
        s = min(e.start for e in eps)
        e = max(e.end if e.end is not None else self._b_end(e.last_a_b) for e in eps)
        return s, e

    def _f7(self, tr: str, s: float, e: float) -> dict:
        i0, i1 = self._irange(s, e)
        lk = self.eng.tele.series[tr].link_ok[i0:i1]
        ok = ~np.isnan(lk)
        if not ok.any():
            return {"present": False, "sev": 0.0, "missing": True}
        down = ok & (lk < 0.5)
        n_out = int((down & ~np.concatenate([[False], down[:-1]])).sum())
        total = float(down.sum())
        longest = int(run_length_true(down).max()) if len(down) else 0
        present = total >= self.link_total or n_out >= self.link_outages
        lvl = self.eng.ladders["corr_age_s"].level(float(longest)) or 0
        return {"present": bool(present), "sev": lvl / 4.0 if present else 0.0, "missing": False,
                "down_s": total, "outages": n_out, "longest_s": float(longest)}

    def _flicker(self, tr: str, s: float, e: float, ref: float) -> dict:
        if ref is None or math.isnan(ref):
            return {"events": 0, "sev": 0.0}
        i0, i1 = self._irange(s, e)
        sats = self.eng.tele.series[tr].sats[i0:i1]
        f = self.eng.feats[tr]
        turn = f.turn_batch[np.clip(f.sample_batch[i0:i1], 0, f.nb - 1)]
        ok = ~np.isnan(sats) & ~turn               # v2.1 §1: satellites set at headland turns, not flicker
        low = ok & (sats <= ref - self.sats_drop)
        rec = int((low[:-1] & ~low[1:] & ok[1:]).sum()) if len(low) > 1 else 0
        mn = float(np.nanmin(sats)) if ok.any() else float("nan")
        lvl = self.eng.ladders["sats"].level(mn) or 0
        return {"events": rec, "sev": lvl / 4.0, "min_sats": mn}

    def _f10(self, tr: str, s: float, e: float, now: float) -> dict:
        eng = self.eng
        b0, b1 = self._brange(s, e)
        f = eng.feats[tr]
        lat = float(np.nanmean(f.lat_mean[b0:b1])) if np.any(~np.isnan(f.lat_mean[b0:b1])) else eng.ctx.farm_lat
        lon = float(np.nanmean(f.lon_mean[b0:b1])) if np.any(~np.isnan(f.lon_mean[b0:b1])) else eng.ctx.farm_lon
        reps = []
        for r in eng.sda.rfi_available(now):
            if not (s - self.rfi_w <= r.t_event <= e + self.rfi_w):
                continue
            covers = r.farm_wide or math.isnan(lat) or haversine_km(lat, lon, r.lat, r.lon) <= r.radius_km
            if covers:
                reps.append({"id": r.id, "t_event": r.t_event, "t_avail": r.t_avail, "confidence": r.confidence})
        sev = (max(x["confidence"] for x in reps) if self.use_conf else 1.0) if reps else 0.0
        return {"present": bool(reps), "sev": float(min(max(sev, 0.0), 1.0)), "missing": False, "reports": reps}

    def _episode_f1(self, tr: str, s: float, e: float, sub: bool) -> dict:
        b0, b1 = self._brange(s, e)
        if sub:
            phi, sev = self._phi(tr, "F11", b0, b1, denom_fid="F1")
            src = self.xdrop[tr]
        else:
            phi, sev = self._phi(tr, "F1", b0, b1)
            src = self.drop[tr]
        if phi is None:
            return {"present": False, "sev": 0.0, "missing": True, "phi": None, "drop_db": float("nan")}
        pres = bool(phi >= self.phi_min)
        d = src[b0:b1]
        d = d[~np.isnan(d)]
        return {"present": pres, "sev": sev if pres else 0.0, "missing": False, "phi": round(phi, 3),
                "drop_db": float(np.median(d)) if (pres and len(d)) else float("nan")}

    def _shared_f3_f4(self, spans: dict[str, tuple[float, float]], farm_span: tuple[float, float], sub: bool) -> tuple[dict, dict, dict]:
        """F3/F4 from each tractor's episode-median drop. Members use their own span, others the farm span."""
        f1s = {}
        working = []
        for tr in self.tractors:
            s, e = spans.get(tr, farm_span)
            f1s[tr] = self._episode_f1(tr, s, e, sub)
            if self._working_in(tr, *farm_span):
                working.append(tr)
        with_drop = [tr for tr in self.tractors if f1s[tr]["present"] and not math.isnan(f1s[tr]["drop_db"])]
        drops = {tr: round(f1s[tr]["drop_db"], 2) for tr in with_drop}
        spread = (max(drops.values()) - min(drops.values())) if len(drops) >= 2 else float("nan")
        sev = float(np.mean([f1s[tr]["sev"] for tr in with_drop])) if with_drop else 0.0
        if len(working) < 2:
            f3 = {"present": False, "sev": 0.0, "missing": True}
        else:
            all_f1 = all(f1s[tr]["present"] for tr in working)
            p3 = bool(all_f1 and len(drops) >= 2 and spread <= self.equal_db)
            f3 = {"present": p3, "sev": sev if p3 else 0.0, "missing": False, "spread_db": spread, "drops_db": drops}
        if sum(1 for tr in self.tractors if not f1s[tr]["missing"]) < 2:
            f4 = {"present": False, "sev": 0.0, "missing": True}
        else:
            p4 = bool(len(drops) >= 2 and spread > self.unequal_db)
            f4 = {"present": p4, "sev": sev if p4 else 0.0, "missing": False, "spread_db": spread, "drops_db": drops}
        return f1s, f3, f4

    def _f12(self, tr: str, s: float, e: float, f1s_other_fid: str, exclude: set[str], sub: bool) -> dict:
        others = [o for o in self.tractors if o != tr and o not in exclude and self._working_in(o, s, e)]
        if not others:
            return {"present": False, "sev": 0.0, "missing": True}
        b0, b1 = self._brange(s, e)
        quiet = True
        for o in others:
            phi, _ = self._phi(o, f1s_other_fid, b0, b1, denom_fid="F1")
            if phi is not None and phi >= self.phi_f12:
                quiet = False
                break
            if not sub and any(x.tr == o and x.start < e and (x.end if x.end is not None else self.now) > s for x in self.tr_eps):
                quiet = False
                break
        return {"present": quiet, "sev": 1.0 if quiet else 0.0, "missing": False, "others": others}

    def _tractor_findings(self, tr: str, s: float, e: float, now: float, f1: dict, f3: dict, f4: dict,
                          f12: dict, sats_ref: float) -> dict[str, dict]:
        b0, b1 = self._brange(s, e)
        fd: dict[str, dict] = {"F1": f1, "F3": f3, "F4": f4, "F12": f12}
        for fid in ("F2", "F9"):
            phi, sev = self._phi(tr, fid, b0, b1)
            if phi is None:
                fd[fid] = {"present": False, "sev": 0.0, "missing": True, "phi": None}
            else:
                p = phi >= self.phi_min
                fd[fid] = {"present": bool(p), "sev": sev if p else 0.0, "missing": False, "phi": round(phi, 3)}
        phi6, sev6 = self._phi(tr, "F6", b0, b1)
        fl = self._flicker(tr, s, e, sats_ref)
        p6 = phi6 is not None and phi6 >= self.phi_min
        flick = fl["events"] >= self.flicker_min and f12["present"]
        if phi6 is None and not flick:
            fd["F6"] = {"present": False, "sev": 0.0, "missing": True, "phi": None, "flicker": fl["events"]}
        else:
            fd["F6"] = {"present": bool(p6 or flick), "sev": (sev6 if p6 else fl["sev"]) if (p6 or flick) else 0.0,
                        "missing": False, "phi": None if phi6 is None else round(phi6, 3), "flicker": fl["events"],
                        "via": "phi" if p6 else ("flicker" if flick else None)}
        fd["F7"] = self._f7(tr, s, e)
        fd["F10"] = self._f10(tr, s, e, now)
        for fid in SIGNAL_ABSENT:
            fd[fid] = {"present": False, "sev": 0.0, "missing": False, "held": "absent (own track)"}
        for fid in SIGNAL_MISSING:
            fd[fid] = {"present": False, "sev": 0.0, "missing": True}
        return fd

    # ---- classification (§3, §4; v2.1 §4, §5)
    def _classify(self, members: list[str], spans: dict[str, tuple[float, float]], farm_span: tuple[float, float],
                  now: float, sub: bool, sats_ref: dict[str, float], exclude_f12: set[str]) -> dict:
        f1s, f3, f4 = self._shared_f3_f4(spans, farm_span, sub)
        per: dict[str, dict] = {}
        for tr in members:
            s, e = spans[tr]
            f12 = self._f12(tr, s, e, "F11" if sub else "F1", set(), sub)
            fd = self._tractor_findings(tr, s, e, now, f1s[tr], f3, f4, f12, sats_ref.get(tr, float("nan")))
            r = self._evaluate(tr, s, fd)
            c = self._map_class(r)
            per[tr] = {"findings": fd, "bf": {k: round(v, 3) for k, v in r.bayes_factor.items()}, "map": c,
                       "span": (s, e), "map_set": sorted(r.explanation)}
        out = []
        unattr: dict[str, tuple[str, float]] = {}
        farm_trs: set[str] = set()
        joint = None
        if members:
            # v2.1 §4: SPACE_WEATHER / JAMMING once for the farm episode, over the tractors with F1 present
            trs_f1 = [tr for tr in members if per[tr]["findings"]["F1"]["present"]] or list(members)
            rj, shared = self._joint(per, trs_f1, farm_span[0])
            cj = self._map_class(rj)
            joint = {"tractors": trs_f1, "map": cj, "bf": {k: round(v, 3) for k, v in rj.bayes_factor.items()},
                     "shared": shared}
            if cj in FARM_CLASSES and rj.bayes_factor[cj] >= self.bf_alarm:
                bf = float(rj.bayes_factor[cj])
                if cj == "SPACE_WEATHER" and not sub:
                    tset = {tr for tr in self.tractors if self._working_in(tr, *farm_span)} | set(trs_f1)
                    s, e = farm_span
                else:
                    tset = set(trs_f1)
                    s = min(per[tr]["span"][0] for tr in trs_f1)
                    e = max(per[tr]["span"][1] for tr in trs_f1)
                fds = [{**per[tr]["findings"], **shared} for tr in trs_f1]
                out.append({"cls": cj, "tractors": tset, "bf": bf, "start": s, "end": e, "level": "farm",
                            "signs": self.signs(cj, fds, bf)})
                farm_trs = set(trs_f1)
        for tr in members:
            if tr in farm_trs:
                continue
            c = per[tr]["map"]
            bf = per[tr]["bf"][c]
            if c not in FARM_CLASSES and c != "OTHER" and bf >= self.bf_alarm:
                # one-machine classes stay per tractor: one detection per machine
                out.append({"cls": c, "tractors": {tr}, "bf": float(bf), "start": per[tr]["span"][0],
                            "end": per[tr]["span"][1], "level": "tractor",
                            "signs": self.signs(c, [per[tr]["findings"]], float(bf))})
            else:
                if c in FARM_CLASSES and joint is not None:
                    c, bf = joint["map"], joint["bf"][joint["map"]]
                unattr[tr] = (c, bf)
        prov = None
        if members:
            cands = [(joint["bf"][joint["map"]], joint["map"])] if joint else []
            cands += [(per[tr]["bf"][per[tr]["map"]], per[tr]["map"]) for tr in members
                      if per[tr]["map"] not in FARM_CLASSES]
            bf, c = max(cands, key=lambda x: (x[0], x[1]))
            prov = (c, bf)
        return {"tractors": per, "groups": out, "unattributed": unattr, "prov": prov, "span": farm_span,
                "joint": joint}

    def classify_farm(self, farm: FarmEpisode, now: float, closed: bool) -> None:
        members = farm.members()
        spans = {tr: self._tractor_hull(farm, tr) for tr in members}
        fs = (min(s for s, _ in spans.values()), max(e for _, e in spans.values()))
        res = self._classify(members, spans, fs, now, False, farm.sats_ref, set())
        res["ref_mu"] = dict(farm.ref_mu)
        farm.result = res
        hit = {tr for g in res["groups"] for tr in g["tractors"]}
        for e in farm.eps:
            if e.tr in hit:
                e.attributed = True
        self.reconcile(farm.id, farm.alarm_ids, res, now, closed, "signal", farm.records, farm)

    def classify_sub(self, sub: SubEpisode, now: float, closed: bool) -> None:
        farm = sub.parent
        end = sub.end if sub.end is not None else self._b_end(sub.last_b)
        members = sorted(sub.tractors)
        spans = {tr: (sub.start, end) for tr in members}
        res = self._classify(members, spans, (sub.start, end), now, True, farm.sats_ref, set(members))
        res["ref_mu"] = dict(farm.ref_mu)
        res["sub"] = True
        parent = farm.result or {"groups": [], "prov": None}
        pcls = {g["cls"] for g in parent["groups"]} or ({parent["prov"][0]} if parent.get("prov") else set())
        absorbed = [g for g in res["groups"] if g["cls"] in pcls]
        res["absorbed"] = [g["cls"] for g in absorbed]
        res["groups"] = [g for g in res["groups"] if g["cls"] not in pcls]
        sub.result = res
        self.reconcile(sub.id, sub.alarm_ids, res, now, closed, "sub", farm.records, farm, parent_classes=pcls)

    # ---- position track (§5)
    def _position(self, b: int, t0: float, now: float, findings) -> None:
        eng = self.eng
        fu = eng.fusion
        working = eng.ctx.working
        for tr in self.tractors:
            f8 = findings[tr].get("F8")
            if f8 is None:
                continue
            others = [o for o in self.tractors if o != tr and working.get(o, False)]
            if f8.present and others:
                shared = any(findings[o].get("F8") is not None and findings[o]["F8"].present for o in others)
                self.pos12[tr][b] = 0 if shared else 1
            fd = {"F8": {"present": f8.present, "sev": f8.severity, "missing": f8.missing}}
            if self.pos12[tr][b] >= 0:
                fd["F12"] = {"present": bool(self.pos12[tr][b] == 1), "sev": 1.0, "missing": False}
            bf = self._evaluate(tr, t0, fd, episode=False).bayes_factor["SPOOFING"] if not f8.missing else 0.0
            strong = bool(f8.present and bf >= self.bf_alarm)
            self.spoof_hist[tr].append(strong)
            ep = self.open_spoof.get(tr)
            if ep is None:
                if strong and sum(self.spoof_hist[tr]) >= self.m:
                    onset = float(f8.onset) if f8.onset is not None else t0
                    ep = SpoofEpisode(self._id(f"P-{tr}"), tr, onset, b, b)
                    self.spoofs.append(ep)
                    self.open_spoof[tr] = ep
                else:
                    continue
            else:
                ended = f8.detail.get("ended_at") if not f8.missing else None
                if f8.present:
                    ep.last_b = b
                    ep.absent = 0
                elif ended is not None:
                    ep.end = max(float(ended), ep.start + self.batch_s)
                else:
                    ep.absent += 1
                    if ep.absent >= self.close_b:
                        ep.end = self._b_end(ep.last_b)
            ep.records.append(BatchRecord(t0, (tr,), 0.0, bf, f8.present, {tr: {fid: lite(f) for fid, f in findings[tr].items()}}))
            closed = ep.end is not None
            if closed:
                ep.closed_t = now
                del self.open_spoof[tr]
            self.classify_spoof(ep, now, closed)

    def classify_spoof(self, ep: SpoofEpisode, now: float, closed: bool) -> None:
        e = ep.end if ep.end is not None else self._b_end(ep.last_b)
        b0, b1 = self._brange(ep.start, e)
        phi8, sev8 = self._phi(ep.tr, "F8", b0, b1)
        p12 = self.pos12[ep.tr][b0:b1]
        k = p12[p12 >= 0]
        fd = {"F8": {"present": bool(phi8), "sev": sev8, "missing": phi8 is None, "phi": phi8}}
        if len(k):
            fd["F12"] = {"present": bool((k == 1).mean() >= self.phi_min), "sev": 1.0, "missing": False,
                         "phi": round(float((k == 1).mean()), 3)}
        r = self._evaluate(ep.tr, ep.start, fd)
        bf = r.bayes_factor["SPOOFING"]
        groups = [{"cls": "SPOOFING", "tractors": {ep.tr}, "bf": bf, "start": ep.start, "end": e, "level": "tractor",
                   "signs": self.signs("SPOOFING", [fd], bf)}] if bf >= self.bf_alarm else []
        ep.result = {"tractors": {ep.tr: {"findings": fd, "bf": {c: round(v, 3) for c, v in r.bayes_factor.items()},
                                          "map": "SPOOFING", "span": (ep.start, e)}},
                     "groups": groups, "unattributed": {} if groups else {ep.tr: ("SPOOFING", bf)},
                     "prov": ("SPOOFING", bf), "span": (ep.start, e)}
        self.reconcile(ep.id, ep.alarm_ids, ep.result, now, closed, "position", ep.records, None)

    # ---- short-dip track (§6)
    def _dip_excluded(self, tr: str, s: float, e: float) -> str | None:
        lo, hi = s - self.dip_excl, e + self.dip_excl
        for x in self.tr_eps:
            if x.tr == tr and x.start <= hi and (x.end if x.end is not None else self.now) >= lo:
                return f"signal episode {x.id}"
        for x in self.spoofs:
            if x.tr == tr and x.start <= hi and (x.end if x.end is not None else self.now) >= lo:
                return f"spoof episode {x.id}"
        for a in self.eng.lifecycle.alarms.values():
            # a SPACE_WEATHER detection covers every working tractor, with or without a tractor episode of its own
            if a.track == "dip" or a.state == AlarmState.WATCH or a.reason in STICKY or tr not in a.tractors:
                continue
            if a.state == AlarmState.RETRACTED and a.merged_into is None:
                continue
            if a.start <= hi and a.span_end(self.now) >= lo:
                return f"alarm {a.id}"
        return None

    def _dip_f12(self, tr: str, s: float, e: float) -> dict:
        eng = self.eng
        lo, hi = s - self.dip_f12_pad, e + self.dip_f12_pad
        others = [o for o in self.tractors if o != tr and self._working_in(o, s, e)]
        if not others:
            return {"present": False, "sev": 0.0, "missing": True}
        i0, i1 = self._irange(lo, hi)
        for o in others:
            f = eng.feats[o]
            ma, med, sd = f.cn0_ma[i0:i1], f.cn0_loc_med[i0:i1], f.cn0_loc_std[i0:i1]
            ok = ~(np.isnan(ma) | np.isnan(med) | np.isnan(sd))
            if ok.any() and np.any(np.abs(ma[ok] - med[ok]) >= 2.0 * sd[ok]):
                return {"present": False, "sev": 0.0, "missing": False, "others": others}
        return {"present": True, "sev": 1.0, "missing": False, "others": others}

    def _dip_row(self, tr: str, d: dict, why: str | None) -> dict:
        eng = self.eng
        t = eng.tele.t
        s, e = float(d["dip_start"]), float(d["dip_end"])
        i0, i1 = int(np.searchsorted(t, s)), int(np.searchsorted(t, e))
        ser, f = eng.tele.series[tr], eng.feats[tr]
        i1 = max(i1, i0 + 1)

        def depth(raw, ref):
            r = ref[i0] if i0 < len(ref) else np.nan
            seg = raw[i0:i1]
            return float(r - np.nanmin(seg)) if (not np.isnan(r) and np.any(~np.isnan(seg))) else float("nan")

        def med(x):
            x = x[~np.isnan(x)]
            return float(np.median(x)) if len(x) else float("nan")
        b_now = int(np.searchsorted(t, self.now))
        return {"start": s, "end": e, "tractor": tr, "lat": med(ser.lat[i0:i1]), "lon": med(ser.lon[i0:i1]),
                "depth_mean_db": depth(ser.cn0, f.cn0_loc_med), "depth_min_db": depth(ser.cn0_min, f.cn0min_loc_med),
                "sats_before": med(ser.sats[max(i0 - 30, 0):i0]), "sats_after": med(ser.sats[i1:min(i1 + 30, b_now)]),
                "channel": d.get("channel"), "excluded": why, "bf": float("nan"), "f12": None, "exported": False,
                "alarm": None}

    def _dips(self, b: int, t0: float, now: float, findings) -> None:
        eng = self.eng
        st_all = eng.ctx.state.get("cn0", {})
        for tr in self.tractors:
            st = st_all.get(tr, {})
            for d in st.pop("excluded", []) if st else []:
                self.dips.append(self._dip_row(tr, d, d.get("why")))
            f5 = findings[tr].get("F5")
            if f5 is None or not f5.present:
                continue
            row = self._dip_row(tr, f5.detail, None)
            self.dips.append(row)
            why = self._dip_excluded(tr, row["start"], row["end"])
            if why:
                row["excluded"] = why
                continue
            f12 = self._dip_f12(tr, row["start"], row["end"])
            bf = self._evaluate(tr, t0, {"F5": {"present": True, "sev": f5.severity, "missing": False}, "F12": f12},
                                episode=False).bayes_factor["MULTIPATH"]
            row["bf"], row["f12"] = bf, (None if f12["missing"] else f12["present"])
            if bf < self.bf_alarm:
                row["excluded"] = f"MULTIPATH BF {bf:.1f} < {self.bf_alarm:g}"
                continue
            a = eng.lifecycle.create("MULTIPATH", now, row["start"], {tr}, AlarmState.ALARM, bf, end=row["end"],
                                     note=f"{row['end'] - row['start']:.0f} s dip of {f5.detail.get('depth_db', 0):.1f} dB "
                                          f"({f5.detail.get('channel')})", episode=self._id("D"), track="dip")
            a.bf = bf
            a.confidence = round(bf / (1.0 + bf), 3)
            a.extra["signs"] = self.signs("MULTIPATH", [{"F5": {"present": True, "missing": False}, "F12": f12}], bf, fu=eng.fusion)
            a.extra["dip"] = {"start": row["start"], "end": row["end"], "depth_db": f5.detail.get("depth_db"),
                              "channel": f5.detail.get("channel")}
            a.extra["f12"] = f12
            a.records = [BatchRecord(t0, (tr,), 0.0, bf, True, {tr: {fid: lite(f) for fid, f in findings[tr].items()}})]
            row["alarm"] = a.id
            self.pending_dips.append((row, a))
        self.recheck_dips(now, final=False)

    def recheck_dips(self, now: float, final: bool) -> None:
        """Once the +-60 s window has passed, re-check exclusion (episode onsets are backdated) and F12 over the
        full window. Checked dips stay pending so the final pass can re-check them against later episodes."""
        lc = self.eng.lifecycle
        keep = []
        for row, a in self.pending_dips:
            if a.state == AlarmState.RETRACTED:
                continue
            if not final and (a.extra.get("checked") or now < row["end"] + self.dip_excl):
                keep.append((row, a))
                continue
            tr = next(iter(a.tractors))
            why = self._dip_excluded(tr, row["start"], row["end"])
            if why:
                row["excluded"] = why
                lc.retract(a, now, Reason.EXPLAINED_BY_OTHER_CAUSE, note=f"dip within {self.dip_excl:.0f} s of {why}")
                continue
            f12 = self._dip_f12(tr, row["start"], row["end"])
            sev = a.records[0].findings[tr]["F5"]["severity"]
            dfd = {"F5": {"present": True, "sev": sev, "missing": False}, "F12": f12}
            bf = self._evaluate(tr, row["start"], dfd, episode=False).bayes_factor["MULTIPATH"]
            if bf < self.bf_alarm:
                row["excluded"] = f"MULTIPATH BF {bf:.1f} < {self.bf_alarm:g} with the full F12 window"
                lc.retract(a, now, Reason.EXPLAINED_BY_OTHER_CAUSE, note=row["excluded"], bf=bf)
                continue
            row["bf"], row["f12"] = bf, (None if f12["missing"] else f12["present"])
            a.bf = bf
            a.confidence = round(bf / (1.0 + bf), 3)
            a.extra["f12"] = f12
            a.extra["signs"] = self.signs("MULTIPATH", [dfd], bf, fu=self.eng.fusion)
            a.extra["checked"] = True
            keep.append((row, a))
        self.pending_dips = keep

    # ================================================================== alarms as views of episodes
    def _alarms(self, ids: list[str]) -> list[Alarm]:
        return [self.eng.lifecycle.alarms[i] for i in ids]

    def apply_span(self, a: Alarm) -> None:
        """Span = the episode's raw span, with the watchdog's refinement and working-time clip when they were made
        for the same raw boundary; a SPACE_WEATHER alarm that absorbed others spans them all."""
        s, e = a.raw_start, a.raw_end
        rs, re_ = a.relocalised.get("start"), a.relocalised.get("end")
        if rs and rs.get("raw") == s:
            s = rs["t"]
        if re_ and e is not None and re_.get("raw") == e:
            e = re_["t"]
        clip = a.extra.get("clip")
        if clip and clip.get("raw") == [a.raw_start, a.raw_end]:
            s, e = clip["s"], clip["e"]
        lc = self.eng.lifecycle
        for mid in a.members:
            m = lc.alarms[mid]
            a.tractors |= m.tractors
            s = min(s, m.start)
            e = None if (e is None or m.end is None) else max(e, m.end)
        a.start, a.end = s, e
        if a.merged_into:
            self.apply_span(lc.alarms[a.merged_into])

    def _update(self, a: Alarm, g: dict, res: dict, now: float, closed: bool, records: list[BatchRecord]) -> None:
        lc = self.eng.lifecycle
        new_trs = set(g["tractors"]) - a.tractors
        a.raw_start = float(g["start"])
        a.raw_end = float(g["end"]) if closed else None
        a.tractors = set(g["tractors"])
        a.active = set(a.tractors) if not closed else set()
        a.bf = float(g["bf"])
        a.confidence = round(a.bf / (1.0 + a.bf), 3)    # v2.1 §5: the episode posterior under even prior odds
        if g.get("signs"):
            a.extra["signs"] = g["signs"]
        a.records = records
        a.extra["episode"] = {"id": a.episode, "tractors": {tr: res["tractors"][tr] for tr in sorted(res["tractors"])},
                              "unattributed": res.get("unattributed", {}), "absorbed": res.get("absorbed", []),
                              "ref_mu": res.get("ref_mu", {}), "sub": res.get("sub", False),
                              "level": g.get("level"), "joint": res.get("joint")}
        a.last_touch_t = now
        self.apply_span(a)
        if new_trs and a.state != AlarmState.RETRACTED and len(a.history) > 0:
            lc.event(a, now, a.state.value, a.state, Reason.JOINED, a.bf, note=",".join(sorted(new_trs)) + " joined")

    def reconcile(self, eid: str, ids: list[str], res: dict, now: float, closed: bool, track: str,
                  records: list[BatchRecord], farm: FarmEpisode | None, parent_classes: set[str] | None = None) -> None:
        lc = self.eng.lifecycle
        groups = res["groups"]
        gcls = {g["cls"] for g in groups}
        alarms = self._alarms(ids)
        attached = [a for a in alarms if a.state != AlarmState.RETRACTED or a.merged_into or a.reason in STICKY]
        live = [a for a in attached if a.state != AlarmState.WATCH]
        watch = next((a for a in attached if a.state == AlarmState.WATCH), None)
        used: set[str] = set()
        for g in groups:
            a = next((x for x in live if x.cls == g["cls"] and x.id not in used), None)
            if a is None:
                spare = next((x for x in live if x.id not in used and x.cls not in gcls and not x.merged_into
                              and x.state != AlarmState.RETRACTED), None)
                if spare is not None:
                    a = spare
                    old = a.cls
                    a.cls = g["cls"]
                    a.unattributed = False
                    lc.event(a, now, a.state.value, a.state, Reason.RECLASSIFIED, g["bf"], note=f"{old} -> {g['cls']}")
                elif watch is not None:
                    a = watch
                    watch = None
                    old = a.cls
                    a.cls = g["cls"]
                    a.unattributed = False
                    a.state = AlarmState.ALARM
                    lc.event(a, now, "WATCH", AlarmState.ALARM, Reason.ESCALATED, g["bf"],
                             note=None if old == g["cls"] else f"provisional {old} -> {g['cls']}")
                else:
                    a = lc.create(g["cls"], now, g["start"], set(g["tractors"]), AlarmState.ALARM, g["bf"],
                                  note=f"episode {eid}", episode=eid, track=track)
                    ids.append(a.id)
            used.add(a.id)
            if a.state == AlarmState.RETRACTED and not a.merged_into:
                continue                                   # sticky watchdog retraction (NOT_SUSTAINED / PARKED)
            self._update(a, g, res, now, closed, records)
        for x in live:
            if x.id in used:
                continue
            if x.merged_into:
                p = lc.alarms[x.merged_into]
                if x.id in p.members:
                    p.members.remove(x.id)
                    lc.event(p, now, p.state.value, p.state, Reason.RECLASSIFIED, note=f"{x.id} no longer {x.cls}: detached")
                    self.apply_span(p)
                x.merged_into = None
                continue
            if x.state == AlarmState.RETRACTED:
                continue
            if not groups and watch is None and not closed:
                frm = x.state.value
                x.state = AlarmState.WATCH
                x.confirmed_at = None
                lc.event(x, now, frm, AlarmState.WATCH, Reason.WEAKENED, note=f"{x.cls} below BF {self.bf_alarm:g}")
                watch = x
            else:
                lc.retract(x, now, Reason.EXPLAINED_BY_OTHER_CAUSE,
                           note=f"{x.cls} no longer the episode's class at BF >= {self.bf_alarm:g}" +
                                (f" ({', '.join(sorted(gcls))} instead)" if gcls else ""))
        prov = res.get("prov")
        if not groups:
            if prov is None:
                return
            pc, pbf = prov          # a sub-episode matching its parent stays a watch until it closes, then is absorbed
            g = {"cls": pc, "tractors": set(res["tractors"]), "bf": pbf, "start": res["span"][0], "end": res["span"][1]}
            if watch is None:
                watch = lc.create(pc, now, g["start"], set(g["tractors"]), AlarmState.WATCH, pbf,
                                  note=f"episode {eid} (provisional)", episode=eid, track=track)
                ids.append(watch.id)
            elif watch.cls != pc:
                old = watch.cls
                watch.cls = pc
                lc.event(watch, now, "WATCH", AlarmState.WATCH, Reason.RECLASSIFIED, pbf, note=f"provisional {old} -> {pc}")
            watch.unattributed = pc == "OTHER"
            self._update(watch, g, res, now, closed, records)
            if closed:
                watch.unattributed = True
                if parent_classes is not None and pc in parent_classes:
                    lc.retract(watch, now, Reason.MERGED, note=f"stacked step absorbed into the parent episode ({pc})", bf=pbf)
                else:
                    detail = "; ".join(f"{tr} {c} BF {bf:.1f}" for tr, (c, bf) in sorted(res.get("unattributed", {}).items()))
                    lc.retract(watch, now, Reason.UNATTRIBUTED, note=f"no class at BF >= {self.bf_alarm:g}: {detail}", bf=pbf)
        elif watch is not None:
            lc.retract(watch, now, Reason.CLEARED, note="superseded by the episode's attributed alarms")
        if groups and res.get("unattributed"):
            key = tuple(sorted((tr, c) for tr, (c, _) in res["unattributed"].items()))
            if farm is not None and closed and key:
                for a in self._alarms(ids):
                    if a.state != AlarmState.RETRACTED and a.extra.get("unattr_logged") != key:
                        a.extra["unattr_logged"] = key
                        lc.event(a, now, a.state.value, a.state, Reason.UNATTRIBUTED,
                                 note="unattributed in this episode: " + "; ".join(
                                     f"{tr} {c} BF {bf:.1f}" for tr, (c, bf) in sorted(res["unattributed"].items())))
                        break

    # ================================================================== watchdog support / end of data
    def reclassify_closed(self, now: float, lookback: float, final: bool) -> int:
        """Late evidence (RFI reports published after the fact): re-run closed episodes' classification at `now`."""
        n = 0
        for farm in self.farms:
            if farm.open or (not final and now - farm.end > lookback):
                continue
            self.classify_farm(farm, now, closed=True)
            for s in farm.subs:
                if not s.open:
                    self.classify_sub(s, now, closed=True)
            n += 1
        return n

    def finish(self, t_end: float) -> None:
        now = t_end
        for tr, ep in list(self.open_tr.items()):
            ep.end = self._b_end(ep.last_a_b)
            del self.open_tr[tr]
        for farm in list(self.open_groups):
            farm.end = max(e.end for e in farm.eps)
            farm.closed_t = now
            for s in farm.subs:
                if s.open:
                    s.end = min(self._b_end(s.last_b), farm.end)
                    s.closed_t = now
            self.open_groups.remove(farm)
            self.classify_farm(farm, now, closed=True)
            for s in farm.subs:
                if s.closed_t == now:
                    self.classify_sub(s, now, closed=True)
        for tr, ep in list(self.open_spoof.items()):
            ep.end = self._b_end(ep.last_b)
            ep.closed_t = now
            del self.open_spoof[tr]
            self.classify_spoof(ep, now, closed=True)
        self.recheck_dips(now, final=True)

    def snapshot(self) -> list[dict]:
        out = []
        for f in self.farms:
            if f.open:
                out.append({"episode": f.id, "track": "signal", "start": iso(f.start), "tractors": f.members(),
                            "groups": [(g["cls"], sorted(g["tractors"]), round(g["bf"], 1)) for g in (f.result or {}).get("groups", [])],
                            "provisional": (f.result or {}).get("prov"),
                            "subs": [s.id for s in f.subs if s.open]})
        for p in self.spoofs:
            if p.open:
                out.append({"episode": p.id, "track": "position", "start": iso(p.start), "tractors": [p.tr],
                            "provisional": (p.result or {}).get("prov")})
        return out

    def write_dips(self, out_dir: Path) -> Path:
        path = Path(out_dir) / "dips.csv"
        lc = self.eng.lifecycle
        cols = ["start", "end", "duration_s", "tractor", "lat", "lon", "depth_mean_db", "depth_min_db", "sats_before",
                "sats_after", "channel", "bf_multipath", "f12", "excluded", "alarm", "exported"]
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in sorted(self.dips, key=lambda r: (r["start"], r["tractor"])):
                a = lc.alarms.get(r["alarm"]) if r["alarm"] else None
                exported = bool(a is not None and a.state == AlarmState.CONFIRMED)
                excl = r["excluded"] or (a.reason if a is not None and a.state == AlarmState.RETRACTED else "")

                def f(x, nd=2):
                    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else (round(x, nd) if isinstance(x, float) else x)
                w.writerow([iso(r["start"]), iso(r["end"]), f(r["end"] - r["start"], 0), r["tractor"], f(r["lat"], 6),
                            f(r["lon"], 6), f(r["depth_mean_db"]), f(r["depth_min_db"]), f(r["sats_before"], 1),
                            f(r["sats_after"], 1), r["channel"], f(r["bf"], 1), "" if r["f12"] is None else r["f12"],
                            excl or "", r["alarm"] or "", exported])
        return path
