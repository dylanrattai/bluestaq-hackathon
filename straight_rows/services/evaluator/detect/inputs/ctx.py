"""Per-batch context shared by the input modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from detect.baseline import CusumChannel
from ingest.grid import Features
from ingest.load import SdaFeeds, Telemetry
from detect.scales import Ladders, LevelTracker
from core.srtypes import Finding


@dataclass
class Ctx:
    cfg: dict[str, Any]
    ladders: Ladders
    h: float
    tele: Telemetry
    tractors: list[str]
    feats: dict[str, Features]
    sda: SdaFeeds
    tracker: LevelTracker = field(default_factory=LevelTracker)
    channels: dict[tuple[str, str], CusumChannel] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)       # persistent scratch per module
    farm_lat: float = float("nan")
    farm_lon: float = float("nan")
    # per batch
    b: int = 0
    t0: float = 0.0
    now: float = 0.0
    working: dict[str, bool] = field(default_factory=dict)
    out: dict[tuple[str, str], Finding] = field(default_factory=dict)   # (fid, tractor) -> Finding this batch
    prev_present: dict[tuple[str, str], bool] = field(default_factory=dict)

    def channel(self, tr: str, name: str, sides: tuple[str, ...], floor_key: str, stack: bool = False) -> CusumChannel:
        key = (tr, name)
        if key not in self.channels:
            floor = float(self.cfg["reference"]["sigma_floor"].get(floor_key, 0.0))
            self.channels[key] = CusumChannel(f"{tr}:{name}", self.cfg, self.h, sides, floor, stack=stack)
        return self.channels[key]

    def valid(self, frac: float) -> bool:
        return bool(frac >= float(self.cfg["min_valid_frac"]))

    def put(self, f: Finding) -> Finding:
        self.out[(f.fid, f.tractor)] = f
        return f

    def get(self, fid: str, tr: str) -> Finding | None:
        return self.out.get((fid, tr))


def missing_finding(fid: str, tr: str, t0: float, why: str = "insufficient data") -> Finding:
    return Finding(fid, tr, t0, False, 0.0, None, {"missing": True, "why": why})
