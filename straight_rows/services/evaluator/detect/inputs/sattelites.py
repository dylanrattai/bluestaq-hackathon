"""Satellite part of F6 (spec §7.4) [R19]; reference frozen/discarded per farm episode (Update v2 §2)."""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from detect.inputs.ctx import Ctx


def _hist(ctx: Ctx, tr: str) -> deque:
    return ctx.state.setdefault("sats", {}).setdefault(tr, deque())


def freeze_sats(ctx: Ctx, tr: str, t_onset: float) -> None:
    """Freeze the clean-batch median at a farm-episode onset (batches that started before it)."""
    fz = ctx.state.setdefault("sats_frozen", {})
    if tr in fz:
        return
    min_n = int(ctx.cfg["reference"]["min_clean_batches"])
    xs = [x for (t, x) in _hist(ctx, tr) if t < t_onset]
    fz[tr] = float(np.median(xs)) if len(xs) >= min_n else float("nan")


def thaw_sats(ctx: Ctx, tr: str) -> None:
    """Farm episode closed: discard the reference, rebuild from post-close batches only."""
    ctx.state.setdefault("sats_frozen", {}).pop(tr, None)
    _hist(ctx, tr).clear()


def sats_reference(ctx: Ctx, tr: str) -> float:
    fz = ctx.state.get("sats_frozen", {})
    if tr in fz:
        return fz[tr]
    hist = _hist(ctx, tr)
    min_n = int(ctx.cfg["reference"]["min_clean_batches"])
    return float(np.median([x for _, x in hist])) if len(hist) >= min_n else float("nan")


def sat_component(ctx: Ctx, tr: str, f1_present: bool) -> dict:
    """Return {'available', 'low', 'level', 'sats', 'ref'}; keeps a clean-batch reference of the median count."""
    f = ctx.feats[tr]
    b = ctx.b
    hist = _hist(ctx, tr)
    win_b = int(ctx.cfg["reference"]["trailing_min"] * 60 / f.batch_s)
    drop = float(ctx.cfg["findings"]["sats_drop"])
    if not ctx.valid(f.sats_frac[b]) or math.isnan(f.sats_med[b]) or bool(f.turn_batch[b]):
        # v2.1 §1: satellites set and rise at a headland turn: no information, and kept out of the reference
        return {"available": False, "low": False, "level": 0, "sats": float("nan"), "ref": float("nan"),
                "turn": bool(f.turn_batch[b])}
    sats = float(f.sats_med[b])
    ref = sats_reference(ctx, tr)
    low = (not math.isnan(ref)) and sats <= ref - drop
    level = ctx.ladders["sats"].level(sats) or 0
    prev_f6 = ctx.prev_present.get(("F6", tr), False)
    frozen = tr in ctx.state.get("sats_frozen", {})
    if not frozen and not f1_present and not low and not prev_f6:
        hist.append((ctx.t0, sats))
        while len(hist) > win_b:
            hist.popleft()
    return {"available": not math.isnan(ref), "low": bool(low), "level": int(level), "sats": sats, "ref": ref}
