"""Severity ladders L0..L4 and fallback scales (spec §7.3)."""
from __future__ import annotations

import math
from typing import Any


class Ladder:
    def __init__(self, edges: list[float], direction: str = "up", inclusive: bool = True):
        self.edges = [float(e) for e in edges]
        self.direction = direction
        self.inclusive = inclusive

    def level(self, v: float | None) -> int | None:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        lvl = 0
        for e in self.edges:
            if self.direction == "up":
                hit = v >= e if self.inclusive else v > e
            else:
                hit = v <= e if self.inclusive else v < e
            if hit:
                lvl += 1
            else:
                break
        return lvl

    def severity(self, v: float | None) -> float:
        l = self.level(v)
        return 0.0 if l is None else l / 4.0


class Ladders:
    def __init__(self, cfg: dict[str, Any]):
        self.l: dict[str, Ladder] = {}
        for name, spec in cfg["ladders"].items():
            self.l[name] = Ladder(spec["edges"], spec.get("direction", "up"), bool(spec.get("inclusive", True)))

    def __getitem__(self, name: str) -> Ladder:
        return self.l[name]


class LevelTracker:
    """Per-key hysteresis: a level moves up immediately and down only after one batch inside the lower band."""

    def __init__(self):
        self.cur: dict[str, int] = {}
        self.pending: dict[str, int] = {}

    def update(self, key: str, level: int | None) -> int:
        if level is None:
            return self.cur.get(key, 0)
        cur = self.cur.get(key, 0)
        if level >= cur:
            self.cur[key] = level
            self.pending[key] = 0
            return level
        self.pending[key] = self.pending.get(key, 0) + 1
        if self.pending[key] >= 2:
            self.cur[key] = level
            self.pending[key] = 0
            return level
        return cur

    def reset(self, key: str) -> None:
        self.cur.pop(key, None)
        self.pending.pop(key, None)


def sev(level: int | None) -> float:
    return 0.0 if level is None else min(max(level, 0), 4) / 4.0
