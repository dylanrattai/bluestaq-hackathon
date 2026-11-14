"""F9 space weather active (GLOBAL) and F10 RFI report matches (per tractor, late) (spec §7.4, §3.2)."""
from __future__ import annotations

import math
from typing import Any

from detect.inputs.ctx import Ctx
from ingest.load import SdaFeeds, haversine_km
from detect.scales import Ladders, sev
from core.srtypes import Finding


def f9_at(sda: SdaFeeds, cfg: dict[str, Any], ladders: Ladders, now: float) -> dict:
    """Hindsight-free evaluation of F9 at data time `now` (uses records available at `now`)."""
    thr = cfg["findings"]["f9"]
    idx = sda.indices_at(now)
    adv = sda.advisories_open(now)
    kp, dst, s4 = idx["kp"], idx["dst"], idx["s4"]
    levels = []
    hits = []
    if not kp.missing:
        levels.append(ladders["kp"].level(kp.value) or 0)
        if kp.value >= thr["kp"]:
            hits.append(f"Kp {kp.value:g}")
    if not dst.missing:
        levels.append(ladders["dst_nt"].level(dst.value) or 0)
        if dst.value <= thr["dst"]:
            hits.append(f"Dst {dst.value:g} nT")
    if not s4.missing:
        levels.append(ladders["s4"].level(s4.value) or 0)
        if s4.value >= thr["s4"]:
            hits.append(f"S4 {s4.value:g}")
    if adv:
        levels.append(2)
        hits.append("advisory " + ",".join(a.id for a in adv))
    missing = kp.missing and dst.missing and s4.missing and not adv
    present = bool(hits)
    level = max(levels) if (present and levels) else 0
    return {"present": present, "level": level, "missing": missing, "hits": hits,
            "kp": kp.value, "dst": dst.value, "s4": s4.value, "kp_ts": kp.ts, "dst_ts": dst.ts, "s4_ts": s4.ts,
            "advisories": [a.id for a in adv]}


def f10_at(sda: SdaFeeds, cfg: dict[str, Any], lat: float, lon: float, t0: float, batch_s: float, now: float) -> dict:
    """RFI reports available at `now` whose window covers the batch and whose area covers (lat, lon)."""
    rc = cfg["findings"]["rfi"]
    w = float(rc["match_window_min"]) * 60
    mid = t0 + batch_s / 2.0
    matches = []
    for r in sda.rfi_available(now):
        if abs(r.t_event - mid) > w + batch_s / 2.0:
            continue
        if r.farm_wide or math.isnan(lat) or math.isnan(lon):
            dist = float("nan")
            covers = True
        else:
            dist = haversine_km(lat, lon, r.lat, r.lon)
            covers = dist <= r.radius_km
        if covers:
            matches.append({"id": r.id, "t_event": r.t_event, "t_avail": r.t_avail, "dist_km": dist,
                            "radius_km": r.radius_km, "confidence": r.confidence})
    present = bool(matches)
    if present:
        sevv = max(m["confidence"] for m in matches) if rc.get("use_report_confidence", True) else 1.0
    else:
        sevv = 0.0
    return {"present": present, "severity": float(min(max(sevv, 0.0), 1.0)), "reports": matches}


def findings(ctx: Ctx) -> list[Finding]:
    out = []
    t0, now = ctx.t0, ctx.now
    st = ctx.state.setdefault("f9", {"onset": None})
    r9 = f9_at(ctx.sda, ctx.cfg, ctx.ladders, now)
    if r9["missing"]:
        st["onset"] = None
        ctx.tracker.reset(("*", "F9"))
        for tr in ctx.tractors:
            out.append(ctx.put(Finding("F9", tr, t0, False, 0.0, None, {"missing": True, "why": "all SDA indices stale"})))
    else:
        if r9["present"]:
            if st["onset"] is None:
                st["onset"] = t0
            level = ctx.tracker.update(("*", "F9"), r9["level"])
        else:
            st["onset"] = None
            ctx.tracker.reset(("*", "F9"))
            level = 0
        for tr in ctx.tractors:
            out.append(ctx.put(Finding("F9", tr, t0, r9["present"], sev(level), st["onset"] if r9["present"] else None,
                                       {**r9, "level": level})))
    batch_s = float(ctx.cfg["grid"]["batch_s"])
    for tr in ctx.tractors:
        f = ctx.feats[tr]
        lat, lon = float(f.lat_mean[ctx.b]), float(f.lon_mean[ctx.b])
        if math.isnan(lat) or math.isnan(lon):
            lat, lon = ctx.farm_lat, ctx.farm_lon
        r10 = f10_at(ctx.sda, ctx.cfg, lat, lon, t0, batch_s, now)
        onset = min((m["t_event"] for m in r10["reports"]), default=None) if r10["present"] else None
        out.append(ctx.put(Finding("F10", tr, t0, r10["present"], r10["severity"], onset,
                                   {"reports": r10["reports"]})))
    return out
