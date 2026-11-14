"""F12 machine-specific: is this tractor the only working machine showing anything? (Fix 2)."""
from __future__ import annotations

import math

from detect.inputs.ctx import Ctx
from core.srtypes import Finding

TRIGGERS = ("F1", "F5", "F6", "F7", "F8")


def _clean(f1: Finding | None, zmax: float, ladders) -> bool | None:
    """True = clean, False = shares an F1, None = ambiguous/no information."""
    if f1 is None or f1.missing:
        return None
    if f1.present:
        return False
    if f1.detail.get("fallback"):
        return (ladders["cn0_abs_dbhz"].level(f1.detail.get("cn0")) or 0) == 0
    z = f1.detail.get("z", float("nan"))
    if math.isnan(z) or abs(z) >= zmax:
        return None            # F1 absent but not within the clean band: ambiguous, not "shares an F1"
    return True


def findings(ctx: Ctx) -> list[Finding]:
    out = []
    zmax = float(ctx.cfg["findings"]["cn0_normal_z"])
    t0 = ctx.t0
    for tr in ctx.tractors:
        others = [o for o in ctx.tractors if o != tr and ctx.working.get(o, False)]
        trig = [fid for fid in TRIGGERS if (g := ctx.get(fid, tr)) is not None and g.present]
        if not others:
            fin = Finding("F12", tr, t0, False, 0.0, None, {"missing": True, "why": "no other tractor working"})
        elif not trig:
            fin = Finding("F12", tr, t0, False, 0.0, None, {"missing": True, "why": "nothing to attribute on this tractor"})
        else:
            # C/N0-family triggers (F1, F5) follow the spec's clause: shared as soon as another working tractor
            # carries an F1 (the machine is part of a farm-scale C/N0 event), unique only when the others are
            # C/N0-clean (|z| < 2). Position/link triggers (F6, F7, F8) are unique when no other working tractor
            # shows the same finding: a drift or link outage on one machine is machine-specific even inside a storm.
            unique, shared, ambiguous = [], [], False
            for fid in trig:
                others_have = []
                for o in others:
                    if fid in ("F1", "F5"):
                        c = _clean(ctx.get("F1", o), zmax, ctx.ladders)
                        if c is None:
                            ambiguous = True
                        g5 = ctx.get("F5", o)
                        others_have.append(c is False or bool(g5 is not None and g5.present))
                    else:
                        g = ctx.get(fid, o)
                        others_have.append(bool(g is not None and g.present))
                (shared if any(others_have) else unique).append(fid)
            if unique and not (ambiguous and not shared and trig == ["F1"]):
                onset = min((ctx.get(fid, tr).onset for fid in unique if ctx.get(fid, tr).onset is not None), default=None)
                fin = Finding("F12", tr, t0, True, 1.0, onset, {"unique": unique, "shared": shared, "others": others})
            elif shared:
                fin = Finding("F12", tr, t0, False, 0.0, None, {"unique": unique, "shared": shared, "others": others})
            else:
                fin = Finding("F12", tr, t0, False, 0.0, None, {"missing": True, "why": "other tractors ambiguous"})
        out.append(ctx.put(fin))
    return out
