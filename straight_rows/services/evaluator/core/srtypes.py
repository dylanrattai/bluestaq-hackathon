"""Shared dataclasses and enums.

Named ``srtypes`` (not ``types``) because a module called ``types.py`` in the
script directory shadows the standard library's ``types`` module and breaks
``enum``/``dataclasses`` at import time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Cls(str, Enum):
    SPACE_WEATHER = "SPACE_WEATHER"
    JAMMING = "JAMMING"
    SPOOFING = "SPOOFING"
    EQUIPMENT_FAULT = "EQUIPMENT_FAULT"
    MULTIPATH = "MULTIPATH"
    OTHER = "OTHER"


class AlarmState(str, Enum):
    WATCH = "WATCH"
    ALARM = "ALARM"
    CONFIRMED = "CONFIRMED"
    RETRACTED = "RETRACTED"


class Reason(str, Enum):
    NOT_SUSTAINED = "NOT_SUSTAINED"
    EXPLAINED_BY_OTHER_CAUSE = "EXPLAINED_BY_OTHER_CAUSE"
    PARKED = "PARKED"
    RECLASSIFIED = "RECLASSIFIED"
    MERGED = "MERGED"
    CLEARED = "CLEARED"          # a WATCH that never escalated
    RAISED = "RAISED"            # first state of a new alarm
    ESCALATED = "ESCALATED"      # WATCH -> ALARM
    CONFIRMED = "CONFIRMED"      # survived a watchdog pass
    CLOSED = "CLOSED"            # live close (BF below watch level)
    JOINED = "JOINED"            # another tractor joined
    RELOCALISED = "RELOCALISED"  # watchdog moved a boundary
    FINAL = "FINAL"              # final-pass bookkeeping


# Fixed class -> action table (never computed).
ACTIONS: dict[str, str] = {
    "SPACE_WEATHER": "HALT_OR_DEGRADED_MODE",
    "JAMMING": "HALT_AND_REPORT",
    "SPOOFING": "HALT_CROSSCHECK_AND_REPORT",
    "EQUIPMENT_FAULT": "SERVICE_MACHINE_OTHERS_CONTINUE",
    "MULTIPATH": "CONTINUE_FALSE_ALARM",
}

EXPORT_CLASSES = tuple(ACTIONS.keys())  # OTHER never exports
CAUSES = ("SPACE_WEATHER", "JAMMING", "SPOOFING", "EQUIPMENT_FAULT", "MULTIPATH", "OTHER")
FINDING_IDS = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12")
FIX_RANK: dict[str, int] = {"RTK_FIXED": 0, "RTK_FLOAT": 1, "DGPS": 2, "AUTONOMOUS": 3, "NO_FIX": 4}
FIX_NAME: dict[int, str] = {v: k for k, v in FIX_RANK.items()}


@dataclass(frozen=True)
class Finding:
    fid: str              # "F1".."F10"
    tractor: str          # "T1".."T3" (shared/GLOBAL findings are copied to each tractor)
    t0: float             # batch start, epoch seconds UTC
    present: bool
    severity: float       # s in [0, 1]
    onset: float | None   # backdated change time (CUSUM last reset), if any
    detail: dict = field(default_factory=dict)

    @property
    def missing(self) -> bool:
        return bool(self.detail.get("missing", False))


@dataclass
class EvaluationResult:
    """One per tractor per batch."""
    tractor: str
    t0: float
    posterior: dict[str, float]      # marginal P(cause | findings) per cause
    bayes_factor: dict[str, float]
    map_set: frozenset[str]
    reasons: dict[str, str] = field(default_factory=dict)
    explanation: frozenset[str] = frozenset()   # most probable non-empty cause set (attribution)
