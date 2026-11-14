"""webui hook: read-only helpers for the React web UI in services/webui (nothing here feeds a detection).

Three uses, all read-only:
  * `tractor_now`  adds position and receiver state per tractor to /status (engine.snapshot)
  * `meta`         is served at /webui/meta: window, field outlines, RFI reports, the real fusion tables,
                   multipath sites and the version scores, so the UI never hard-codes them
  * `python -m runtime.webui --data ../../data --out ../../out`
                   runs a full replay in a temp folder and writes ../webui/public/demo/snapshot.json, which the UI
                   replays in DEMO mode (API unreachable) and uses for the judge-view walkthroughs
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from core.log import iso
from core.srtypes import ACTIONS, FIX_NAME

HERE = Path(__file__).resolve().parent.parent   # the evaluator directory
VERSION = "v2.3"


def _num(x: Any, nd: int = 6) -> float | None:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, nd)


def _last_finite(a: np.ndarray, i: int, back: int = 30) -> float | None:
    """Latest finite value at or before index i, looking back at most `back` samples."""
    lo = max(0, i - back)
    seg = a[lo:i + 1]
    ok = np.flatnonzero(np.isfinite(seg))
    return float(seg[ok[-1]]) if ok.size else None


def tractor_now(tele, tr: str, now: float) -> dict[str, Any]:
    """Latest 1 Hz sample at data time `now` (data <= now only)."""
    i = int(np.searchsorted(tele.t, now, side="right")) - 1
    if i < 0:
        return {"lat": None, "lon": None, "cn0": None, "cn0_min": None, "sats": None, "fix": None, "speed": None,
                "link_ok": None}
    s = tele.series[tr]
    fr = _last_finite(s.fix_rank, i)
    lk = _last_finite(s.link_ok, i)
    return {"lat": _num(_last_finite(s.lat, i)), "lon": _num(_last_finite(s.lon, i)),
            "cn0": _num(_last_finite(s.cn0, i), 1), "cn0_min": _num(_last_finite(s.cn0_min, i), 1),
            "sats": None if (v := _last_finite(s.sats, i)) is None else int(v),
            "fix": None if fr is None else FIX_NAME.get(int(fr), str(fr)),
            "speed": _num(_last_finite(s.speed, i), 2), "link_ok": None if lk is None else bool(lk)}


def _fields(tele, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Field outline per tractor: 1st..99th percentile box of its moving samples (no field geometry in the data)."""
    v = float(cfg["gate"]["park_speed_mps"])
    out = []
    for tr in tele.tractors:
        s = tele.series[tr]
        m = np.isfinite(s.lat) & np.isfinite(s.lon) & (np.nan_to_num(s.speed) > v)
        if not m.any():
            continue
        la, lo = s.lat[m], s.lon[m]
        out.append({"tractor": tr, "source": "1-99th percentile of moving samples",
                    "lat": [_num(np.percentile(la, 1)), _num(np.percentile(la, 99))],
                    "lon": [_num(np.percentile(lo, 1)), _num(np.percentile(lo, 99))]})
    return out


def _sites(cfg: dict[str, Any]) -> dict[str, Any] | None:
    sm = cfg.get("site_multipath") or {}
    f = Path(sm.get("sites", "multipath_sites.yaml"))
    f = f if f.is_absolute() else HERE / f
    if not sm.get("enabled") or not f.exists():
        return None
    doc = yaml.safe_load(f.read_text())
    keep = ("n_cells_seen", "n_sites", "site_passes", "site_dips", "north_headland_rate", "south_headland_rate", "dev_end")
    return {**{k: doc.get(k) for k in keep},
            "sites": [{"lat": c["lat"], "lon": c["lon"], "rate": c.get("rate"), "passes": c.get("passes"),
                       "dips": c.get("dips"), "name": c.get("name")} for c in doc.get("sites", [])]}


def _scores(out_dir: Path | None) -> dict[str, Any] | None:
    """Version table from the latest dev report (diagnostic score against the dev labels; never the eval window)."""
    p = (out_dir / "dev_report.md") if out_dir else None
    if not p or not p.exists():
        return None
    lines = p.read_text().splitlines()
    head = next((l for l in lines if l.startswith("- **Diagnostic score")), None)
    lenient = next((l for l in lines if l.startswith("- Lenient")), None)
    rows, cls = [], []
    for l in lines:
        c = [x.strip().strip("*") for x in l.strip().strip("|").split("|")]
        if len(c) == 6 and c[0] and c[0][0] in "vc" and c[1][:1].isdigit():
            rows.append({"version": c[0], "score": c[1], "macro_f1": c[2], "found": c[3], "extras": c[4], "onset": c[5]})
        if len(c) == 8 and c[0] in ACTIONS:
            cls.append(dict(zip(("class", "truth", "found", "extra", "missed", "precision", "recall", "f1"), c)))
    return {"headline": head, "lenient": lenient, "versions": rows, "classes": cls}


def meta(cfg: dict[str, Any], tele, sda, out_dir: str | Path | None = None) -> dict[str, Any]:
    fu = cfg["fusion"]
    return {
        # cfg["_path"] is overwritten by a loop variable in core/config.py (a link value, e.g. "0.2"); only trust a .yaml name
        "version": VERSION, "config": n if (n := Path(str(cfg.get("_path", ""))).name).endswith(".yaml") else None,
        "data_start": iso(tele.t_start), "data_end": iso(tele.t_end),
        "window": {"start": cfg["window"]["start"], "end": cfg["window"]["end"]},
        "batch_s": int(cfg["grid"]["batch_s"]), "watchdog_every_s": int(cfg["grid"]["batch_s"]) * int(cfg["grid"]["watchdog_every_batches"]),
        "tractors": list(tele.tractors), "fields": _fields(tele, cfg),
        "rfi": [{"id": r.id, "t_event": iso(r.t_event), "t_avail": iso(r.t_avail), "lat": r.lat, "lon": r.lon,
                 "radius_km": r.radius_km, "confidence": r.confidence, "farm_wide": r.farm_wide, "text": r.text}
                for r in sda.rfi],
        "fusion": {"causes": fu["causes"], "prior": fu["prior"], "floor": fu["floor"], "bf_watch": fu["bf_watch"],
                   "bf_alarm": fu["bf_alarm"], "leak_default": fu["leak_default"], "leak_f9": fu["leak_f9"],
                   "leaks": fu.get("leaks") or {}, "links": fu["links"]},
        "episodes": {k: cfg["episodes"][k] for k in ("open_m", "open_n", "close_batches", "phi_present")},
        "actions": ACTIONS,
        "site_multipath": _sites(cfg),
        "site_cap": int((cfg.get("site_multipath") or {}).get("max_detections", 500)),
        "scores": _scores(Path(out_dir) if out_dir else None),
    }


# ---------------------------------------------------------------- DEMO snapshot (offline; never reads labels)
def _track(tele, every_s: int = 120) -> dict[str, list]:
    t = tele.t
    idx = np.arange(0, len(t), every_s)
    out: dict[str, list] = {"t": [iso(t[i]) for i in idx]}
    for tr in tele.tractors:
        out[tr] = [tractor_now(tele, tr, float(t[i])) for i in idx]
    return out


def write_snapshot(cfg: dict[str, Any], data_dir: Path, scores_dir: Path | None, dest: Path) -> Path:
    """Run a full max-speed replay into a temporary folder (never touches out/) and bundle what the UI replays:
    every logged event, each alarm's final span/track/evidence (the log has no event when an episode closes
    normally), the submission rows and a 2-min position/receiver track."""
    import tempfile
    from core.log import EventSink
    from runtime.engine import Engine
    from runtime.export import build_submission
    with tempfile.TemporaryDirectory() as tmp:
        sink = EventSink(tmp)
        eng = Engine(cfg, data_dir, tmp, speed=None, sink=sink, write_outputs=False, mode="batch")
        eng.run()
        sink.close()
        events = [json.loads(l) for l in (Path(tmp) / "alarm_log.jsonl").read_text().splitlines() if l.strip()]
    t_end = eng.now
    keep = ("kind", "t_data_iso", "alarm_id", "cls", "from", "to", "reason", "confidence", "tractors", "start", "end",
            "bf", "note", "episode", "n_touched", "actions", "final")
    alarms = []
    for a in eng.lifecycle.alarms.values():
        d = a.summary(t_end)
        d["suppressed"] = bool(a.extra.get("suppressed"))
        d["evidence"] = list(a.evidence)
        alarms.append(d)
    sub = build_submission(list(eng.lifecycle.alarms.values()), cfg, t_end)
    snap = {"meta": meta(cfg, eng.tele, eng.sda, scores_dir), "track": _track(eng.tele),
            "events": [{k: e[k] for k in keep if k in e} for e in events],
            "alarms": alarms, "submission": sub["detections"]}
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / "snapshot.json"
    p.write_text(json.dumps(snap, separators=(",", ":"), default=str))
    return p


def main(argv: list[str] | None = None) -> int:
    import logging
    from core.config import load_config
    ap = argparse.ArgumentParser(description="write the web UI's DEMO snapshot (runs a full replay, ~1 min)")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=None, help="folder with dev_report.md for the score table (optional; read only)")
    ap.add_argument("--dest", default=str(HERE.parent / "webui" / "public" / "demo"))
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    p = write_snapshot(load_config(a.config), Path(a.data), Path(a.out) if a.out else None, Path(a.dest))
    print(f"wrote {p} ({p.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
