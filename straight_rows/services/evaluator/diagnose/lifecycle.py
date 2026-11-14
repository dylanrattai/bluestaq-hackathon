"""Alarm state machine, alarm store and history (spec §7.6) [R28, R29]."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from core.log import EventSink, iso
from core.srtypes import CAUSES, AlarmState, EvaluationResult, Finding, Reason

log = logging.getLogger("sr.lifecycle")

MULTI_MACHINE = ("SPACE_WEATHER", "JAMMING")

# findings that can carry the onset of each cause (links in the network)
CAUSE_FINDINGS = {
    "SPACE_WEATHER": ("F1", "F3", "F6", "F9"),
    "JAMMING": ("F1", "F2", "F4", "F10", "F11"),
    "SPOOFING": ("F8",),
    "EQUIPMENT_FAULT": ("F6", "F7", "F1", "F5"),
    "MULTIPATH": ("F5",),
    "OTHER": ("F1", "F2", "F5", "F6", "F8"),
}


@dataclass
class BatchRecord:
    t0: float
    tractors: tuple[str, ...]
    post: float
    bf: float
    in_expl: bool
    findings: dict[str, dict[str, Any]] = field(default_factory=dict)   # tractor -> fid -> lite detail


@dataclass
class Alarm:
    id: str
    cls: str
    state: AlarmState
    start: float
    end: float | None
    tractors: set[str]
    active: set[str]
    history: list[dict] = field(default_factory=list)
    records: list[BatchRecord] = field(default_factory=list)
    confidence: float = 0.0
    reason: str | None = None
    unattributed: bool = False
    created_t: float = 0.0
    last_touch_t: float = 0.0
    merged_into: str | None = None
    relocalised: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    confirmed_at: float | None = None

    @property
    def open(self) -> bool:
        return self.end is None and self.state in (AlarmState.WATCH, AlarmState.ALARM, AlarmState.CONFIRMED)

    def span_end(self, now: float) -> float:
        return self.end if self.end is not None else now

    def summary(self, now: float | None = None) -> dict:
        return {
            "alarm_id": self.id, "class": self.cls, "state": self.state.value, "start": iso(self.start),
            "end": iso(self.end) if self.end is not None else None, "tractors": sorted(self.tractors),
            "confidence": round(self.confidence, 3), "reason": self.reason, "unattributed": self.unattributed,
            "open": self.open, "n_batches": len(self.records), "merged_into": self.merged_into,
        }

    def full(self) -> dict:
        d = self.summary()
        d.update({"history": self.history, "evidence": self.evidence, "relocalised": self.relocalised,
                  "extra": {k: v for k, v in self.extra.items() if k != "records"},
                  "records": [{"t0": iso(r.t0), "post": round(r.post, 4), "bf": round(r.bf, 2), "in_explanation": r.in_expl,
                               "tractors": list(r.tractors)} for r in self.records[-200:]]})
        return d


def lite(f: Finding) -> dict[str, Any]:
    """Compact per-finding record kept inside alarms for evidence strings."""
    d = {"present": f.present, "severity": round(f.severity, 3), "missing": f.missing, "onset": f.onset}
    keep = ("drop_db", "cn0", "mu", "level", "z", "fallback", "spread_db", "drops_db", "tractors", "dip_start", "extra_drop_db",
            "channel", "triggers", "clean", "down_5min_s", "outages_5min", "longest_run_s", "ev_mu",
            "dip_end", "depth_db", "duration_s", "sats_med", "sats_ref", "down_run_s", "run_end_s", "xte_m",
            "offset_m", "S", "side", "hits", "kp", "dst", "s4", "advisories", "reports", "fix", "sats", "logvar")
    for k in keep:
        if k in f.detail:
            d[k] = f.detail[k]
    return d


class Lifecycle:
    def __init__(self, cfg: dict[str, Any], tractors: list[str], sink: EventSink):
        lc = cfg["lifecycle"]
        fu = cfg["fusion"]
        self.cfg = cfg
        self.tractors = tractors
        self.sink = sink
        self.batch_s = float(cfg["grid"]["batch_s"])
        self.m, self.n = int(lc["alarm_m"]), int(lc["alarm_n"])
        self.mm, self.mn = int(lc.get("multipath_m", 1)), int(lc.get("multipath_n", 1))
        self.bf_watch, self.bf_alarm = float(fu["bf_watch"]), float(fu["bf_alarm"])
        self.merge_gap = {k: float(v) * 60 for k, v in cfg["watchdog"]["merge_gap_min"].items()}
        self.hist: dict[tuple[str, str], deque] = {}
        self.alarms: dict[str, Alarm] = {}
        self.order: list[str] = []
        self.counter: dict[str, int] = {}
        self.attach: dict[tuple[str, str], str] = {}     # (tractor, cls) -> alarm id

    # ---- helpers
    def _new_id(self, cls: str) -> str:
        self.counter[cls] = self.counter.get(cls, 0) + 1
        return f"A-{cls}-{self.counter[cls]}"

    def event(self, a: Alarm, t_data: float, frm: str | None, to: AlarmState | str, reason: Reason | str,
              bf: float | None = None, note: str | None = None) -> None:
        to_s = to.value if isinstance(to, AlarmState) else str(to)
        rs = reason.value if isinstance(reason, Reason) else str(reason)
        rec = {"kind": "alarm", "t_data": t_data, "t_data_iso": iso(t_data), "alarm_id": a.id, "cls": a.cls,
               "from": frm, "to": to_s, "reason": rs, "confidence": round(a.confidence, 3),
               "tractors": sorted(a.tractors), "start": iso(a.start), "end": iso(a.end) if a.end is not None else None,
               "bf": bf, "unattributed": a.unattributed}
        if note:
            rec["note"] = note
        a.history.append({k: v for k, v in rec.items() if k != "kind"})
        self.sink.emit(rec)

    def create(self, cls: str, t_data: float, start: float, tractors: set[str], state: AlarmState,
               bf: float | None, reason: Reason = Reason.RAISED, end: float | None = None, note: str | None = None) -> Alarm:
        a = Alarm(self._new_id(cls), cls, state, start, end, set(tractors), set(tractors) if end is None else set(),
                  unattributed=(cls == "OTHER"), created_t=t_data, last_touch_t=t_data)
        self.alarms[a.id] = a
        self.order.append(a.id)
        for tr in tractors:
            if end is None:
                self.attach[(tr, cls)] = a.id
        self.event(a, t_data, None, state, reason, bf, note)
        return a

    def joinable(self, cls: str, now: float, exclude: str | None = None, tractor: str | None = None) -> Alarm | None:
        """An open alarm of this class, or one closed within the class merge gap (MULTIPATH never merges).

        SPACE_WEATHER and JAMMING are multi-machine causes: any tractor joins. The machine-specific classes
        (SPOOFING, EQUIPMENT_FAULT) only rejoin an alarm that already holds the same tractor.
        """
        if cls == "MULTIPATH":
            return None
        gap = self.merge_gap.get(cls, 0.0)
        for aid in reversed(self.order):
            a = self.alarms[aid]
            if a.cls != cls or aid == exclude or a.state in (AlarmState.RETRACTED, AlarmState.WATCH):
                continue
            if tractor is not None and cls not in MULTI_MACHINE and tractor not in a.tractors:
                continue
            if a.open or (a.end is not None and now - a.end <= gap):
                return a
        return None

    @staticmethod
    def _onset(cls: str, fmap: dict[str, Finding], fallback: float, all_fmaps: dict[str, dict[str, Finding]] | None = None) -> float:
        if cls == "JAMMING":
            # a step on top of an open event (F11, on any tractor): the jammer started at the step, not at the event
            steps = [fm["F11"].onset for fm in ([fmap] + list((all_fmaps or {}).values()))
                     if "F11" in fm and fm["F11"].present and fm["F11"].onset is not None]
            if steps:
                return min(steps)
        cands = [fmap[fid].onset for fid in CAUSE_FINDINGS.get(cls, ()) if fid in fmap and fmap[fid].present
                 and fmap[fid].onset is not None]
        return min(cands) if cands else fallback

    # ---- per batch
    def step(self, b: int, t0: float, now: float, results: dict[str, EvaluationResult],
             findings: dict[str, dict[str, Finding]], working: dict[str, bool]) -> None:
        for tr in self.tractors:
            r = results.get(tr)
            fmap = findings.get(tr, {})
            if r is None:
                continue
            for cls in CAUSES:
                bf = r.bayes_factor.get(cls, 0.0)
                in_expl = cls in r.explanation
                cand = in_expl and bf >= self.bf_alarm
                m, n = (self.mm, self.mn) if cls == "MULTIPATH" else (self.m, self.n)
                dq = self.hist.setdefault((tr, cls), deque(maxlen=n))
                dq.append((t0, cand))
                fire = sum(1 for _, c in dq if c) >= m
                aid = self.attach.get((tr, cls))
                a = self.alarms.get(aid) if aid else None
                if a is not None and not a.open:
                    a = None
                    self.attach.pop((tr, cls), None)
                if fire:
                    first_cand_t0 = min(t for t, c in dq if c)
                    onset = self._onset(cls, fmap, first_cand_t0, findings)
                    if a is not None and a.state == AlarmState.WATCH:
                        j = self.joinable(cls, now, exclude=a.id, tractor=tr)
                        if j is not None:
                            # another machine already carries this alarm: fold the watch into it
                            w = a
                            w.state = AlarmState.RETRACTED
                            w.reason = Reason.MERGED.value
                            w.merged_into = j.id
                            w.end = now
                            w.active.clear()
                            self.event(w, now, "WATCH", AlarmState.RETRACTED, Reason.MERGED, bf, note=f"merged into {j.id}")
                            self.attach.pop((tr, cls), None)
                            a = None
                    if a is None:
                        j = self.joinable(cls, now, tractor=tr)
                        if j is not None:
                            frm = j.state.value
                            reopened = j.end is not None
                            j.end = None
                            j.tractors.add(tr)
                            j.active.add(tr)
                            j.start = min(j.start, onset)
                            j.last_touch_t = now
                            self.attach[(tr, cls)] = j.id
                            self.event(j, now, frm, j.state, Reason.JOINED, bf,
                                       note=f"{tr} joined" + (" (reopened within merge gap)" if reopened else ""))
                            a = j
                        else:
                            a = self.create(cls, now, onset, {tr}, AlarmState.ALARM, bf)
                    elif a.state == AlarmState.WATCH:
                        a.start = onset           # a watch's own start never binds the alarm: the findings' onset does
                        a.state = AlarmState.ALARM
                        a.last_touch_t = now
                        self.event(a, now, "WATCH", AlarmState.ALARM, Reason.ESCALATED, bf)
                    else:
                        a.active.add(tr)          # the onset was fixed when the alarm was raised; later batches never backdate it
                elif bf >= self.bf_watch:
                    if a is None:
                        a = self.create(cls, now, t0, {tr}, AlarmState.WATCH, bf)
                else:
                    if a is not None:
                        a.active.discard(tr)
                        if not a.active:
                            self._close(a, cls, t0, now, fmap, bf)
                        self.attach.pop((tr, cls), None)
                        a = None
                if a is not None:
                    self._record(a, t0, r, fmap, cls, tr)
            # SPACE_WEATHER alarms include every working tractor during the span
        for aid in self.order:
            a = self.alarms[aid]
            if a.cls == "SPACE_WEATHER" and a.open and a.state != AlarmState.WATCH:
                for tr in self.tractors:
                    if working.get(tr) and tr not in a.tractors:
                        a.tractors.add(tr)
                        self.event(a, now, a.state.value, a.state, Reason.JOINED, None, note=f"{tr} working during storm")

    def _record(self, a: Alarm, t0: float, r: EvaluationResult, fmap: dict[str, Finding], cls: str, tr: str) -> None:
        if a.records and a.records[-1].t0 == t0:
            rec = a.records[-1]
            k = len(rec.tractors)
            rec.post = (rec.post * k + r.posterior.get(cls, 0.0)) / (k + 1)
            rec.bf = max(rec.bf, r.bayes_factor.get(cls, 0.0))
            rec.in_expl = rec.in_expl or (cls in r.explanation)
            rec.tractors = tuple(sorted(set(rec.tractors) | {tr}))
        else:
            rec = BatchRecord(t0, (tr,), r.posterior.get(cls, 0.0), r.bayes_factor.get(cls, 0.0), cls in r.explanation)
            a.records.append(rec)
        rec.findings[tr] = {fid: lite(f) for fid, f in fmap.items()}
        a.last_touch_t = t0

    def _close(self, a: Alarm, cls: str, t0: float, now: float, fmap: dict[str, Finding], bf: float) -> None:
        end = t0
        # Fix 1: a CUSUM finding that just closed carries its backtracked end
        ends = [float(fmap[fid].detail["ended_at"]) for fid in CAUSE_FINDINGS.get(cls, ())
                if fid in fmap and not fmap[fid].present and fmap[fid].detail.get("ended_at") is not None]
        if ends:
            end = min(ends)
        if cls == "MULTIPATH":
            for rec in reversed(a.records):
                for tr, fm in rec.findings.items():
                    f5 = fm.get("F5")
                    if f5 and f5.get("present") and f5.get("dip_end"):
                        end = float(f5["dip_end"])
                        a.start = min(a.start, float(f5.get("dip_start", a.start)))
                        a.extra["dip"] = {"start": f5.get("dip_start"), "end": f5.get("dip_end"), "depth_db": f5.get("depth_db")}
                        break
                else:
                    continue
                break
        if end <= a.start:
            end = a.start + self.batch_s
        a.end = end
        frm = a.state.value
        if a.state == AlarmState.WATCH:
            a.state = AlarmState.RETRACTED
            a.reason = Reason.CLEARED.value
            self.event(a, now, frm, a.state, Reason.CLEARED, bf)
        else:
            self.event(a, now, frm, a.state, Reason.CLOSED, bf)

    def close_all(self, t_end: float) -> None:
        """At the end of data: close everything still open."""
        for aid in self.order:
            a = self.alarms[aid]
            if a.open:
                a.active.clear()
                a.end = t_end
                frm = a.state.value
                if a.state == AlarmState.WATCH:
                    a.state = AlarmState.RETRACTED
                    a.reason = Reason.CLEARED.value
                    self.event(a, t_end, frm, a.state, Reason.CLEARED)
                else:
                    self.event(a, t_end, frm, a.state, Reason.CLOSED, note="end of data")
        self.attach.clear()

    # ---- queries
    def list(self, state: str | None = None, cls: str | None = None, tractor: str | None = None) -> list[Alarm]:
        out = []
        for aid in self.order:
            a = self.alarms[aid]
            if state and a.state.value != state:
                continue
            if cls and a.cls != cls:
                continue
            if tractor and tractor not in a.tractors:
                continue
            out.append(a)
        return out
