"""Satellite part of F6 (spec §7.4) [R19]."""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from detect.inputs.ctx import Ctx


def sat_component(ctx: Ctx, tr: str, f1_present: bool) -> dict:
    """Return {'available', 'low', 'level', 'sats', 'ref'}; keeps a clean-batch reference of the median count."""
    f = ctx.feats[tr]
    b = ctx.b
    st = ctx.state.setdefault("sats", {})
    hist: deque = st.setdefault(tr, deque())
    win_b = int(ctx.cfg["reference"]["trailing_min"] * 60 / f.batch_s)
    min_n = int(ctx.cfg["reference"]["min_clean_batches"])
    drop = float(ctx.cfg["findings"]["sats_drop"])
    if not ctx.valid(f.sats_frac[b]) or math.isnan(f.sats_med[b]):
        return {"available": False, "low": False, "level": 0, "sats": float("nan"), "ref": float("nan")}
    sats = float(f.sats_med[b])
    ref = float(np.median(hist)) if len(hist) >= min_n else float("nan")
    low = (not math.isnan(ref)) and sats <= ref - drop
    level = ctx.ladders["sats"].level(sats) or 0
    prev_f6 = ctx.prev_present.get(("F6", tr), False)
    if not f1_present and not low and not prev_f6:
        hist.append(sats)
        while len(hist) > win_b:
            hist.popleft()
    return {"available": not math.isnan(ref), "low": bool(low), "level": int(level), "sats": sats, "ref": ref}
