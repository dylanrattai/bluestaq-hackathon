"""Evidence strings built from the final span and class (spec §7.9)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from diagnose.lifecycle import Alarm
from core.log import hms
from core.srtypes import FIX_NAME

LEVEL_NAME = {0: "L0", 1: "L1", 2: "L2", 3: "L3", 4: "L4"}


def _level_dcn0(ladders, drop: float) -> str:
    l = ladders["dcn0_db"].level(drop)
    return LEVEL_NAME.get(l if l is not None else 0, "L0")


def _span_idx(feats, start: float, end: float) -> tuple[int, int]:
    b_lo = int(np.searchsorted(feats.t0, start, side="right")) - 1
    b_hi = int(np.searchsorted(feats.t0, end, side="left"))
    b_lo = max(b_lo, 0)
    b_hi = max(b_hi, b_lo + 1)
    return b_lo, min(b_hi, feats.nb)


def _pre_ref(feats, b_lo: int, n: int = 120) -> float:
    seg = feats.cn0_mean[max(0, b_lo - n):b_lo]
    seg = seg[~np.isnan(seg)]
    return float(np.median(seg)) if len(seg) else float("nan")


def _first_record_mu(a: Alarm, tr: str) -> float | None:
    mu = ((a.extra.get("episode") or {}).get("ref_mu") or {}).get(tr)
    if mu is not None and not math.isnan(mu):
        return float(mu)                 # the farm episode's frozen pre-onset reference (Update v2 §2)
    for rec in a.records:
        f1 = rec.findings.get(tr, {}).get("F1")
        if f1 and f1.get("mu") is not None and not f1.get("fallback"):
            return float(f1["mu"])
    return None


def build_evidence(a: Alarm, feats: dict[str, Any], tele, sda, ladders, cfg: dict[str, Any], now: float) -> list[str]:
    out: list[str] = []
    start, end = a.start, a.span_end(now)
    trs = sorted(a.tractors)
    others = [t for t in feats if t not in a.tractors]
    t_lbl = f"{hms(start)[:-1]}–{hms(end)}"
    per_tr_drop: dict[str, float] = {}
    per_tr_pre: dict[str, float] = {}
    for tr in feats:
        f = feats[tr]
        b_lo, b_hi = _span_idx(f, start, end)
        mu = _first_record_mu(a, tr) if tr in a.tractors else None
        if mu is None:
            mu = _pre_ref(f, b_lo)
        seg = f.cn0_mean[b_lo:b_hi]
        seg = seg[~np.isnan(seg)]
        per_tr_pre[tr] = mu
        per_tr_drop[tr] = (mu - float(np.mean(seg))) if (len(seg) and not math.isnan(mu)) else float("nan")

    cls = a.cls
    # 1. main measurement
    if cls in ("JAMMING", "SPACE_WEATHER"):
        parts = []
        for tr in trs:
            d = per_tr_drop.get(tr, float("nan"))
            if not math.isnan(d):
                parts.append(f"{tr} {d:.1f} dB ({_level_dcn0(ladders, d)})")
        if parts:
            out.append(f"{t_lbl}: mean C/N0 below its pre-event reference by " + ", ".join(parts))
    elif cls == "SPOOFING":
        for tr in trs:
            f = feats[tr]
            b_lo, b_hi = _span_idx(f, start, end)
            xs = f.xte_mean[b_lo:b_hi]
            xs = xs[~np.isnan(xs)]
            d = per_tr_drop.get(tr, float("nan"))
            if len(xs):
                peak = float(xs[np.argmax(np.abs(xs))])
                out.append(f"{tr} {t_lbl}: cross-track error drifted to {peak:+.2f} m (batch mean, peak) while mean C/N0 stayed "
                           f"within {abs(d):.1f} dB of reference and satellites {int(np.nanmedian(f.sats_med[b_lo:b_hi]))}")
    elif cls == "EQUIPMENT_FAULT":
        for tr in trs:
            f = feats[tr]
            b_lo, b_hi = _span_idx(f, start, end)
            down = float(np.nansum(f.link_down_frac[b_lo:b_hi]) * f.batch_s)
            rank = f.fix_rank_max[b_lo:b_hi]
            rank = rank[~np.isnan(rank)]
            worst = FIX_NAME.get(int(rank.max()), "?") if len(rank) else "?"
            sats = f.sats_med[b_lo:b_hi]
            sats_min = int(np.nanmin(sats)) if np.any(~np.isnan(sats)) else None
            bits = []
            if down >= cfg["findings"]["link_down_min_s"]:
                bits.append(f"RTK correction link down for {down:.0f} s")
            if worst != "RTK_FIXED":
                bits.append(f"fix degraded to {worst}")
            if sats_min is not None:
                bits.append(f"satellites used down to {sats_min}")
            d = per_tr_drop.get(tr, float("nan"))
            bits.append(f"mean C/N0 within {abs(d):.1f} dB of reference" if not math.isnan(d) else "C/N0 reference unavailable")
            out.append(f"{tr} {t_lbl}: " + "; ".join(bits))
    elif cls == "MULTIPATH":
        dip = a.extra.get("dip") or {}
        for tr in trs:
            f = feats[tr]
            b_lo, b_hi = _span_idx(f, start, end)
            sats = f.sats_med[b_lo:b_hi]
            sats_s = f"{int(np.nanmedian(sats))}" if np.any(~np.isnan(sats)) else "n/a"
            depth = dip.get("depth_db")
            dur = (dip.get("end") or end) - (dip.get("start") or start)
            out.append(f"{tr} {hms(dip.get('start') or start)[:-1]}–{hms(dip.get('end') or end)}: {dur:.0f} s C/N0 dip of "
                       f"{depth:.1f} dB with satellites unchanged ({sats_s}); recovered without a fix change"
                       if depth is not None else f"{tr} {t_lbl}: short C/N0 dip, satellites unchanged ({sats_s})")
    else:
        out.append(f"{','.join(trs)} {t_lbl}: unattributed anomaly")

    # 2. cross-tractor geometry
    drops = {tr: per_tr_drop[tr] for tr in feats if not math.isnan(per_tr_drop.get(tr, float("nan")))}
    if cls == "JAMMING" and len([t for t in trs if t in drops]) >= 2:
        vals = [drops[t] for t in trs if t in drops]
        out.append(f"drops on {'/'.join(trs)} differ by {max(vals) - min(vals):.1f} dB: "
                   + ("unequal, consistent with a nearby emitter" if max(vals) - min(vals) > cfg["findings"]["unequal_drop_db"]
                      else "similar across machines"))
    elif cls == "SPACE_WEATHER" and len(drops) >= 2:
        vals = list(drops.values())
        out.append(f"all tractors with data ({'/'.join(sorted(drops))}) dropped within {max(vals) - min(vals):.1f} dB of each other: "
                   f"regional, not a local emitter")
    elif others:
        od = [f"{t} {abs(drops[t]):.1f} dB" for t in others if t in drops]
        if od:
            out.append(f"other machines unaffected: C/N0 within " + ", ".join(od) + " of reference" +
                       (": machine-specific" if cls in ("EQUIPMENT_FAULT", "SPOOFING", "MULTIPATH") else ""))

    # 3. solution impact
    for tr in trs[:2]:
        f = feats[tr]
        b_lo, b_hi = _span_idx(f, start, end)
        rank = f.fix_rank_max[b_lo:b_hi]
        if len(rank) and np.any(~np.isnan(rank)):
            worst = int(np.nanmax(rank))
            if worst >= 1:
                i = b_lo + int(np.nanargmax(rank >= worst))
                out.append(f"{tr} fix degraded to {FIX_NAME.get(worst, '?')} from {hms(f.t0[i])}")
                break
    else:
        if cls not in ("MULTIPATH",):
            out.append("fix state stayed RTK_FIXED throughout")

    # 4. SDA context
    sda_bits = []
    idx = sda.indices_at(end if end <= now else now)
    f9_hits = set()
    rfi_ids: dict[str, dict] = {}
    for rec in a.records:
        for tr, fm in rec.findings.items():
            f9 = fm.get("F9")
            if f9 and f9.get("present"):
                for h in f9.get("hits", []):
                    f9_hits.add(h)
            f10 = fm.get("F10")
            if f10 and f10.get("present"):
                for r in f10.get("reports", []):
                    rfi_ids[r["id"]] = r
    for r in sda.rfi_available(now):
        if abs(r.t_event - (start + end) / 2) <= (end - start) / 2 + cfg["findings"]["rfi"]["match_window_min"] * 60:
            rfi_ids.setdefault(r.id, {"id": r.id, "t_event": r.t_event, "t_avail": r.t_avail, "confidence": r.confidence})
    if rfi_ids:
        sda_bits.append("; ".join(f"{rid} (event {hms(r['t_event'])}, available {hms(r['t_avail'])}, conf {r['confidence']:.2f}) covers the farm"
                                  for rid, r in sorted(rfi_ids.items())))
    kp = idx["kp"].value if not idx["kp"].missing else None
    dst = idx["dst"].value if not idx["dst"].missing else None
    s4 = idx["s4"].value if not idx["s4"].missing else None
    adv = [x.id for x in sda.advisories_open(min(end, now))]
    ctx = []
    if kp is not None:
        ctx.append(f"Kp {kp:g}")
    if dst is not None:
        ctx.append(f"Dst {dst:g} nT")
    if s4 is not None:
        ctx.append(f"S4 {s4:.2f}")
    if adv:
        ctx.append("advisory " + ",".join(adv) + " active")
    if f9_hits or cls == "SPACE_WEATHER":
        sda_bits.append("space weather active: " + ", ".join(sorted(f9_hits) or ctx))
    elif ctx:
        quiet = (kp is None or kp < cfg["findings"]["f9"]["kp"]) and (s4 is None or s4 < cfg["findings"]["f9"]["s4"])
        sda_bits.append(("space weather " + ("quiet" if quiet else "elevated") + " at the time: ") + ", ".join(ctx)
                        + ("" if cls == "SPACE_WEATHER" or not quiet else ": rules out a regional storm as the cause"))
    if not rfi_ids and cls == "JAMMING":
        sda_bits.append("no RFI report covers this window (reports can be late or missing)")
    if sda_bits:
        out.append("; ".join(sda_bits))

    # 5. decision (Update v2: one classification per episode, on findings judged over the whole episode)
    dec = _decision(a)
    if a.extra.get("signs"):
        dec = a.extra["signs"] + "; " + dec           # v2.1 §5
    rl = a.relocalised
    if rl:
        dec += "; watchdog " + ", ".join(f"{k} moved {v.get('shift_s', 0):+.0f} s ({v.get('method')})" for k, v in rl.items() if isinstance(v, dict))
    if a.confirmed_at:
        dec += f"; confirmed {hms(a.confirmed_at)}"
    out.append(dec)
    out = [s for s in out if s and s.strip()]
    out = out[:5] if len(out) >= 2 else (out + ["see alarm history"])[:5]
    conf = a.extra.get("conf")
    if conf and conf.get("line"):
        out.append(conf["line"])                     # v2.2: the confidence line comes on top of the existing five
    return out


def _decision(a: Alarm) -> str:
    ep = a.extra.get("episode") or {}
    if a.track == "dip":
        f12 = a.extra.get("f12") or {}
        ctx = "other machines' C/N0 within 2 sigma" if f12.get("present") else (
            "no other working machine to compare" if f12.get("missing") else "other machines also disturbed")
        return f"short-dip track: MULTIPATH BF {a.bf:.0f} on {{F5, F12}} ({ctx}); no other episode on {','.join(sorted(a.tractors))} within 60 s"
    per = ep.get("tractors") or {}
    bits = []
    for tr in sorted(per):
        if tr not in a.tractors:
            continue
        fd = per[tr].get("findings", {})
        pres = []
        for fid in ("F1", "F2", "F3", "F4", "F6", "F7", "F8", "F9", "F10", "F12"):
            f = fd.get(fid) or {}
            if f.get("present"):
                phi = f.get("phi")
                pres.append(f"{fid} {phi:.0%}" if isinstance(phi, (int, float)) else fid)
        bits.append(f"{tr} {per[tr].get('map', a.cls)} BF {per[tr].get('bf', {}).get(a.cls, a.bf):.0f} on " + (", ".join(pres) or "nothing"))
    kind = {"signal": "signal episode", "sub": "stacked-step sub-episode", "position": "position-track episode"}.get(a.track, "episode")
    expl = a.extra.get("explained_away") or {}
    if ep.get("level") == "farm" and expl:
        jt = (ep.get("joint") or {}).get("tractors") or []
        return (f"{kind} {ep.get('id', a.episode)}: {a.cls} BF {a.bf:.0f} decided once for the farm over {','.join(jt)}; "
                + "; ".join(expl[k] for k in sorted(expl)))
    if ep.get("level") == "farm":
        jt = (ep.get("joint") or {}).get("tractors") or []
        dec = (f"{kind} {ep.get('id', a.episode)}: {a.cls} BF {a.bf:.0f} decided once for the farm over "
               f"{','.join(jt)} (per-tractor findings summed, shared ones once)")
        return dec + ("; unattributed: " + ", ".join(f"{tr} {c}" for tr, (c, _) in sorted(ep["unattributed"].items()))
                      if ep.get("unattributed") else "")
    dec = f"{kind} {ep.get('id', a.episode)}: {a.cls} BF {a.bf:.0f}" + ("; " + "; ".join(bits[:3]) if bits else "")
    if ep.get("unattributed"):
        dec += "; unattributed: " + ", ".join(f"{tr} {c}" for tr, (c, _) in sorted(ep["unattributed"].items()))
    return dec
