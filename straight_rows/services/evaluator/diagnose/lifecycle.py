"""Alarm store and history (spec §7.6). Update v2: alarms are views of episodes (diagnose/episodes.py); the
per-batch class lifecycle and per-class M-of-N are gone (M-of-N only opens episodes)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.log import EventSink, iso
from core.srtypes import AlarmState, Finding, Reason

log = logging.getLogger("sr.lifecycle")


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
    episode: str | None = None              # id of the episode this alarm is a view of
    track: str = "signal"                   # signal | sub | position | dip
    members: list[str] = field(default_factory=list)   # SPACE_WEATHER alarms merged into this one (watchdog)
    raw_start: float = 0.0                  # span from the episode, before watchdog refinement/clipping
    raw_end: float | None = None
    bf: float = 0.0

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
            "episode": self.episode, "track": self.track,
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
    """The alarm store: ids, history events, queries."""

    def __init__(self, cfg: dict[str, Any], tractors: list[str], sink: EventSink):
        self.cfg = cfg
        self.tractors = tractors
        self.sink = sink
        self.batch_s = float(cfg["grid"]["batch_s"])
        self.alarms: dict[str, Alarm] = {}
        self.order: list[str] = []
        self.counter: dict[str, int] = {}

    def _new_id(self, cls: str) -> str:
        self.counter[cls] = self.counter.get(cls, 0) + 1
        return f"A-{cls}-{self.counter[cls]}"

    def event(self, a: Alarm, t_data: float, frm: str | None, to: AlarmState | str, reason: Reason | str,
              bf: float | None = None, note: str | None = None, quiet: bool = False) -> None:
        to_s = to.value if isinstance(to, AlarmState) else str(to)
        rs = reason.value if isinstance(reason, Reason) else str(reason)
        rec = {"kind": "alarm", "t_data": t_data, "t_data_iso": iso(t_data), "alarm_id": a.id, "cls": a.cls,
               "from": frm, "to": to_s, "reason": rs, "confidence": round(a.confidence, 3),
               "tractors": sorted(a.tractors), "start": iso(a.start), "end": iso(a.end) if a.end is not None else None,
               "bf": None if bf is None else round(float(bf), 3), "unattributed": a.unattributed}
        if a.episode:
            rec["episode"] = a.episode
        if note:
            rec["note"] = note
        a.history.append({k: v for k, v in rec.items() if k != "kind"})
        self.sink.emit(rec, quiet=quiet)

    def create(self, cls: str, t_data: float, start: float, tractors: set[str], state: AlarmState,
               bf: float | None, reason: Reason = Reason.RAISED, end: float | None = None, note: str | None = None,
               episode: str | None = None, track: str = "signal", quiet: bool = False) -> Alarm:
        a = Alarm(self._new_id(cls), cls, state, start, end, set(tractors), set(tractors) if end is None else set(),
                  unattributed=(cls == "OTHER"), created_t=t_data, last_touch_t=t_data, episode=episode, track=track,
                  raw_start=start, raw_end=end)
        self.alarms[a.id] = a
        self.order.append(a.id)
        self.event(a, t_data, None, state, reason, bf, note, quiet=quiet)
        return a

    def retract(self, a: Alarm, t_data: float, reason: Reason, note: str | None = None, bf: float | None = None) -> None:
        frm = a.state.value
        a.state = AlarmState.RETRACTED
        a.reason = reason.value
        a.active.clear()
        if a.end is None:
            a.end = t_data
        self.event(a, t_data, frm, AlarmState.RETRACTED, reason, bf, note)

    def list(self, state: str | None = None, cls: str | None = None, tractor: str | None = None,
             include_suppressed: bool = False) -> list[Alarm]:
        out = []
        for aid in self.order:
            a = self.alarms[aid]
            if a.extra.get("suppressed") and not include_suppressed:
                continue                  # v2.3 P1: known-nuisance alerts are not in the operator alarm list
            if state and a.state.value != state:
                continue
            if cls and a.cls != cls:
                continue
            if tractor and tractor not in a.tractors:
                continue
            out.append(a)
        return out
