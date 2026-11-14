"""Load and validate config.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent.parent   # the evaluator directory (config.yaml lives there)

REQUIRED_TOP = ["participant", "repository_url", "window", "columns", "grid", "gate", "reference",
                "cusum", "findings", "fusion", "lifecycle", "watchdog", "ladders", "sda"]


def parse_iso(s: str) -> float:
    """ISO-8601 string -> epoch seconds (UTC)."""
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    p = Path(path) if path else HERE / "config.yaml"
    with open(p) as f:
        cfg = yaml.safe_load(f)
    missing = [k for k in REQUIRED_TOP if k not in cfg]
    if missing:
        raise ValueError(f"config.yaml missing sections: {missing}")
    w = cfg["window"]
    w["start_s"] = parse_iso(w["start"])
    w["end_s"] = parse_iso(w["end"])
    if w["end_s"] <= w["start_s"]:
        raise ValueError("window.end must be after window.start")
    g = cfg["grid"]
    if int(g["batch_s"]) <= 0 or int(g["watchdog_every_batches"]) <= 0:
        raise ValueError("grid values must be positive")
    fu = cfg["fusion"]
    for fid, row in fu["links"].items():
        for c, p in row.items():
            if c not in fu["causes"]:
                raise ValueError(f"fusion.links.{fid}: unknown cause {c}")
            if not 0.0 < float(p) < 1.0:
                raise ValueError(f"fusion.links.{fid}.{c} must be in (0,1)")
    if not 0.0 < float(fu["prior"]) < 1.0:
        raise ValueError("fusion.prior must be in (0,1)")
    for name, lad in cfg["ladders"].items():
        if len(lad["edges"]) != 4:
            raise ValueError(f"ladders.{name} needs 4 edges")
    cfg["_path"] = str(p)
    return cfg


def placeholders_unchanged(cfg: dict[str, Any]) -> bool:
    h = str(cfg["participant"].get("github_handle", ""))
    u = str(cfg.get("repository_url", ""))
    return "TODO" in h.upper() or "TODO" in u.upper()
