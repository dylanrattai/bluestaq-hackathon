"""F8 cross-track drift: upper CUSUM on the batch median of cross-track error during moving passes [R10] (Fix 4)."""
from __future__ import annotations

import math

from detect.inputs.ctx import Ctx, missing_finding
from detect.scales import sev
from core.srtypes import Finding


def findings(ctx: Ctx) -> list[Finding]:
    out = []
    st = ctx.state.setdefault("f8_fb_onset", {})
    fc = ctx.cfg["findings"]
    park_speed = float(ctx.cfg["gate"]["park_speed_mps"])
    fix_frac = float(fc["fix_frac"])
    float_band = float(fc.get("f8_float_band_m", 0.3))
    for tr in ctx.tractors:
        f = ctx.feats[tr]
        b, t0 = ctx.b, ctx.t0
        # DISCOVERED: cross_track_error_m is unsigned and heavy-tailed -> batch median, upper side only
        ch = ctx.channel(tr, "xte", ("upper",), "xte")
        moving = (not math.isnan(f.speed_mean[b])) and f.speed_mean[b] > park_speed and ctx.valid(f.speed_frac[b])
        if not ctx.working.get(tr, False) or not moving:
            ch.close_run()
            st[tr] = None
            out.append(ctx.put(missing_finding("F8", tr, t0, "parked or stationary: no guidance information")))
            continue
        if not ctx.valid(f.xte_frac[b]):
            ch.update(t0, float("nan"))
            st[tr] = None
            out.append(ctx.put(missing_finding("F8", tr, t0)))
            continue
        # no information while DGPS/AUTONOMOUS/NO_FIX (decimetre+ error is inherent [R18]) or in the link -> fix window
        fix_bad = (not math.isnan(f.fix_deg2_frac[b])) and f.fix_deg2_frac[b] > fix_frac
        link_bad = bool(f.link_onset_recent[b])
        if fix_bad or link_bad:
            ch.close_run()
            st[tr] = None
            out.append(ctx.put(Finding("F8", tr, t0, False, 0.0, None,
                                       {"missing": True, "why": "guidance error attributed to " + ("degraded fix" if fix_bad else "correction link")})))
            continue
        x = float(f.xte_med[b])
        floating = (not math.isnan(f.fix_deg_frac[b])) and f.fix_deg_frac[b] > fix_frac
        if floating and x <= float_band:
            # RTK float: keep watching, but only a median beyond the float error band counts as drift
            out.append(ctx.put(Finding("F8", tr, t0, False, 0.0, None,
                                       {"missing": True, "why": f"RTK float within the {float_band} m error band", "xte_m": x})))
            continue
        s = ch.update(t0, x)
        if s.fallback:
            lvl = ctx.ladders["xte_abs_m"].level(abs(x)) or 0
            present = lvl >= 2
            if present and st.get(tr) is None:
                st[tr] = t0
            if not present:
                st[tr] = None
            out.append(ctx.put(Finding("F8", tr, t0, present, sev(lvl) if present else 0.0, st.get(tr) if present else None,
                                       {"fallback": True, "xte_m": x, "level": lvl, "n_clean": s.n_clean})))
            continue
        st[tr] = None
        stat = s.s_hi
        severity = min(max((stat - ctx.h) / ctx.h, 0.0), 1.0) if s.present else 0.0
        out.append(ctx.put(Finding("F8", tr, t0, s.present, severity, s.onset if s.present else None,
                                   {"fallback": False, "xte_m": x, "mu": s.mu, "sigma_eff": s.sigma_eff, "z": s.z,
                                    "S": stat, "side": s.side, "offset_m": (x - s.mu) if s.mu is not None else None,
                                    "ended_at": s.ended_at, "floating": floating})))
    return out
