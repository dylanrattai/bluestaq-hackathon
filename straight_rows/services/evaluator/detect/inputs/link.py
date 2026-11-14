"""F7 correction link down, evaluated on the trailing 5-minute window (spec §7.4, Fix 5) [R20, R21]."""
from __future__ import annotations

import math

from detect.inputs.ctx import Ctx, missing_finding
from detect.scales import sev
from core.srtypes import Finding


def findings(ctx: Ctx) -> list[Finding]:
    out = []
    fc = ctx.cfg["findings"]
    min_total = float(fc.get("link_down_total_s", 30))
    min_outages = int(fc.get("link_outages_min", 3))
    for tr in ctx.tractors:
        f = ctx.feats[tr]
        b, t0 = ctx.b, ctx.t0
        if not ctx.valid(f.link_frac[b]):
            ctx.tracker.reset((tr, "F7"))
            out.append(ctx.put(missing_finding("F7", tr, t0)))
            continue
        total = float(f.link_down_5min_s[b])
        n_out = int(f.link_outages_5min[b])
        longest = float(f.link_maxrun_5min[b])
        present = total >= min_total or n_out >= min_outages
        if present:
            level = ctx.tracker.update((tr, "F7"), ctx.ladders["corr_age_s"].level(longest))
            first = f.link_first_onset_5min[b]
            onset = float(first) if not math.isnan(first) else t0
        else:
            ctx.tracker.reset((tr, "F7"))
            level, onset = 0, None
        out.append(ctx.put(Finding("F7", tr, t0, present, sev(level), onset,
                                   {"down_5min_s": total, "outages_5min": n_out, "longest_run_s": longest,
                                    "down_frac": float(f.link_down_frac[b]), "level": level})))
    return out
