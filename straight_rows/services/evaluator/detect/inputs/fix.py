"""F6 fix or satellites degraded while C/N0 is normal (spec §7.4) [R18, R19]. Combines fix.py and sattelites.py."""
from __future__ import annotations

import math

from detect.inputs.ctx import Ctx, missing_finding
from detect.inputs.sattelites import sat_component
from detect.scales import sev
from core.srtypes import FIX_NAME, Finding


def fix_component(ctx: Ctx, tr: str) -> dict:
    f = ctx.feats[tr]
    b = ctx.b
    fc = ctx.cfg["findings"]
    if not ctx.valid(f.fix_frac[b]):
        return {"available": False, "degraded": False, "level": 0, "rank": float("nan"), "suppressed": False}
    frac = float(f.fix_deg_frac[b])
    rank = float(f.fix_rank_med[b])
    degraded = frac > float(fc["fix_frac"])
    suppressed = bool(f.link_onset_recent[b])      # a fix drop 0-4 min after the link went down belongs to F7
    level = ctx.ladders["fix_rank"].level(rank) or 0
    return {"available": True, "degraded": bool(degraded and not suppressed), "raw_degraded": bool(degraded),
            "level": int(level), "rank": rank, "frac": frac, "suppressed": suppressed and degraded,
            "fix_name": FIX_NAME.get(int(rank), "?") if not math.isnan(rank) else "?"}


def findings(ctx: Ctx) -> list[Finding]:
    out = []
    zmax = float(ctx.cfg["findings"]["cn0_normal_z"])
    st = ctx.state.setdefault("f6_onset", {})
    for tr in ctx.tractors:
        t0 = ctx.t0
        f1 = ctx.get("F1", tr)
        if f1 is None or f1.missing:
            ctx.tracker.reset((tr, "F6"))
            st[tr] = None
            out.append(ctx.put(missing_finding("F6", tr, t0, "no C/N0 information for the chain rule")))
            continue
        fx = fix_component(ctx, tr)
        sc = sat_component(ctx, tr, f1.present)
        if (not fx["available"] or not fx["degraded"]) and sc.get("turn"):
            # v2.1 §1: the satellite half has no information at a headland turn; only a degraded fix still speaks
            ctx.tracker.reset((tr, "F6"))
            st[tr] = None
            out.append(ctx.put(missing_finding("F6", tr, t0, "headland turn")))
            continue
        if not fx["available"] and not sc["available"]:
            ctx.tracker.reset((tr, "F6"))
            st[tr] = None
            out.append(ctx.put(missing_finding("F6", tr, t0)))
            continue
        z = f1.detail.get("z", float("nan"))
        if f1.detail.get("fallback"):
            cn0_normal = (ctx.ladders["cn0_abs_dbhz"].level(f1.detail.get("cn0")) or 0) == 0
        else:
            cn0_normal = (not math.isnan(z)) and abs(z) < zmax
        if f1.present or not cn0_normal:
            # v2.1: F6 is defined only while C/N0 is normal (chain rule); with C/N0 down it says nothing, it is not
            # "absent" (as absent it counted against SPACE_WEATHER once per tractor in the farm-level sum, §4)
            ctx.tracker.reset((tr, "F6"))
            st[tr] = None
            out.append(ctx.put(Finding("F6", tr, t0, False, 0.0, None,
                                       {"missing": True, "why": "C/N0 not normal (chain rule)", "fix": fx, "sats": sc,
                                        "z_cn0": z, "chain_f1": bool(f1.present)})))
            continue
        present = bool((fx["degraded"] or sc["low"]) and not f1.present and cn0_normal)
        if present:
            lvl = max(fx["level"] if fx["degraded"] else 0, sc["level"] if sc["low"] else 0)
            level = ctx.tracker.update((tr, "F6"), lvl)
            if st.get(tr) is None:
                st[tr] = t0
            onset = st[tr]
        else:
            ctx.tracker.reset((tr, "F6"))
            st[tr] = None
            level, onset = 0, None
        out.append(ctx.put(Finding("F6", tr, t0, present, sev(level), onset,
                                   {"fix": fx, "sats": sc, "z_cn0": z, "cn0_normal": cn0_normal,
                                    "chain_f1": bool(f1.present), "level": level})))
    return out
