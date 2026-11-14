"""F1 level drop, F2 oscillation (in-event aware), F3 equal drop, F4 unequal drop, F5 short dip, F11 stacked step."""
from __future__ import annotations

import math

import numpy as np

from detect.inputs.ctx import Ctx, missing_finding
from detect.scales import sev
from core.srtypes import Finding


def _state(ctx: Ctx, tr: str) -> dict:
    st = ctx.state.setdefault("cn0", {})
    if tr not in st:
        st[tr] = {"fb_onset": None, "f1_prev": False, "dips": {}, "last_dip": None}
    return st[tr]


def _f1_f2_f11(ctx: Ctx, tr: str) -> tuple[Finding, Finding, Finding]:
    f = ctx.feats[tr]
    b, t0 = ctx.b, ctx.t0
    ch1 = ctx.channel(tr, "cn0_mean", ("lower",), "cn0", stack=True)
    ch2 = ctx.channel(tr, "cn0_logvar", ("upper",), "logvar")
    st = _state(ctx, tr)
    if not ctx.valid(f.cn0_frac[b]):
        ch1.update(t0, float("nan"))
        ch2.update(t0, float("nan"))
        st["fb_onset"] = None
        st["f1_prev"] = False
        ctx.tracker.reset((tr, "F1"))
        return missing_finding("F1", tr, t0), missing_finding("F2", tr, t0), missing_finding("F11", tr, t0)
    x = float(f.cn0_mean[b])
    s1 = ch1.update(t0, x)
    # Fix 3: F2 is measured against the in-event variance: re-reference the log-variance channel whenever the
    # level event opens or closes, seeding sigma with the pre-event value
    warm = int(ctx.cfg["cusum"].get("in_event_warmup_batches", 2))
    if st.get("f2_restart_pending"):
        ch2.restart(seed_sigma=st.get("f2_seed_sigma"), min_batches=warm)
        st["f2_restart_pending"] = False
    if s1.present != st["f1_prev"]:
        # the transition batch holds the step edge: skip it, then re-reference from the next batch on
        st["f2_restart_pending"] = True
        st["f2_seed_sigma"] = ch2.sigma_eff
        s2 = ch2.update(t0, float("nan"))
    else:
        s2 = ch2.update(t0, float(f.cn0_logvar[b]))
    st["f1_prev"] = s1.present
    lad = ctx.ladders
    if s1.fallback:
        lvl_abs = lad["cn0_abs_dbhz"].level(x)
        present = lvl_abs is not None and lvl_abs >= 2
        if present:
            if st["fb_onset"] is None:
                st["fb_onset"] = t0
            level = ctx.tracker.update((tr, "F1"), lvl_abs)
        else:
            st["fb_onset"] = None
            ctx.tracker.reset((tr, "F1"))
            level = 0
        f1 = Finding("F1", tr, t0, present, sev(level), st["fb_onset"] if present else None,
                     {"fallback": True, "cn0": x, "level": level, "drop_db": float("nan"),
                      "n_clean": s1.n_clean, "ref_age_s": s1.ref_age_s, "ended_at": s1.ended_at})
        f11 = Finding("F11", tr, t0, False, 0.0, None, {"missing": True, "why": "no reference"})
    else:
        st["fb_onset"] = None
        drop = s1.drop
        if s1.present:
            level = ctx.tracker.update((tr, "F1"), lad["dcn0_db"].level(drop))
        else:
            ctx.tracker.reset((tr, "F1"))
            level = 0
        f1 = Finding("F1", tr, t0, s1.present, sev(level), s1.onset if s1.present else None,
                     {"fallback": False, "cn0": x, "mu": s1.mu, "sigma_eff": s1.sigma_eff, "z": s1.z,
                      "s_lo": s1.s_lo, "drop_db": drop, "level": level, "rho": s1.rho, "ev_mu": s1.ev_mu,
                      "ended_at": s1.ended_at})
        if s1.stacked:
            lvl11 = ctx.tracker.update((tr, "F11"), lad["dcn0_db"].level(s1.stacked_extra))
            f11 = Finding("F11", tr, t0, True, sev(lvl11), s1.stacked_onset,
                          {"extra_drop_db": s1.stacked_extra, "level": lvl11, "event_level": s1.ev_mu, "cn0": x})
        else:
            # F11 is defined only relative to an open event: present or no information, never "absent"
            ctx.tracker.reset((tr, "F11"))
            f11 = Finding("F11", tr, t0, False, 0.0, None, {"missing": True, "why": "no stacked step", "event_open": s1.present})
    if s2.fallback or s2.missing:
        f2 = missing_finding("F2", tr, t0, "no log-variance reference")
    else:
        f2 = Finding("F2", tr, t0, s2.present, 0.5 if s2.present else 0.0, s2.onset if s2.present else None,
                     {"logvar": s2.x, "mu": s2.mu, "z": s2.z, "s_hi": s2.s_hi, "in_event": s1.present, "ended_at": s2.ended_at})
    return f1, f2, f11


def _f5(ctx: Ctx, tr: str, f1: Finding) -> Finding:
    """Finite moving-average dip test [R4] on min C/N0 and mean C/N0 against a local 2-min reference (Fix 6)."""
    f = ctx.feats[tr]
    b, t0 = ctx.b, ctx.t0
    st = _state(ctx, tr)
    dcfg = ctx.cfg["findings"]["dip"]
    if f1.missing:
        st["dips"] = {}
        return missing_finding("F5", tr, t0)
    ma_w = int(dcfg["ma_window_s"])
    max_len = int(dcfg["max_len_s"])
    tol = float(dcfg.get("sats_tolerance", 1))
    sl = f.samples_of_batch(b)
    series = ctx.tele.series[tr]
    t = ctx.tele.t
    chans = {"cn0_min": (series.cn0_min, f.cn0min_ma, f.cn0min_loc_med, f.cn0min_loc_std, False),
             "cn0": (series.cn0, f.cn0_ma, f.cn0_loc_med, f.cn0_loc_std, True)}
    found = None
    any_ref = False
    for name, (raw, ma, loc_med, loc_std, need_sats) in chans.items():
        cs = st["dips"].setdefault(name, {"dip": None, "armed": True})
        for i in range(sl.start, sl.stop):
            mu = loc_med[i]
            sd = loc_std[i]
            if math.isnan(mu) or math.isnan(ma[i]):
                continue
            any_ref = True
            dip = cs["dip"]
            if dip is not None:
                mu, thr = dip["mu"], dip["thr"]          # frozen at detection: the local window must not learn the dip
            else:
                thr = max(float(dcfg["min_drop_db"]), float(dcfg["min_sigma"]) * (sd if not math.isnan(sd) else 0.0))
            below = ma[i] < mu - thr
            if dip is None:
                if not below:
                    cs["armed"] = True
                elif cs["armed"]:
                    cs["dip"] = {"i_detect": i, "mu": mu, "thr": thr}
                continue
            if below:
                if i - dip["i_detect"] > max_len + ma_w:
                    cs["dip"] = None
                    cs["armed"] = False
                continue
            lo = max(dip["i_detect"] - ma_w, 0)
            seg = raw[lo:i + 1]
            cs["dip"] = None
            if np.all(np.isnan(seg)):
                continue
            mu_d = dip["mu"]
            depth = mu_d - float(np.nanmin(seg))
            half = mu_d - depth / 2.0
            idx = np.nonzero(seg < half)[0]
            if len(idx) == 0:
                continue
            a, z = lo + int(idx[0]), lo + int(idx[-1]) + 1
            dur = z - a
            if dur > max_len or depth < dip["thr"]:
                continue
            if need_sats:
                sats_seg = series.sats[a:z]
                sats_loc = series.sats[max(a - 150, 0):max(a - 30, 1)]
                if np.any(~np.isnan(sats_seg)) and np.any(~np.isnan(sats_loc)):
                    if abs(float(np.nanmedian(sats_seg)) - float(np.nanmedian(sats_loc))) > tol:
                        continue
            cand = {"dip_start": float(t[a]), "dip_end": float(t[z - 1]) + 1.0, "depth_db": depth, "duration_s": dur,
                    "channel": name, "thr_db": dip["thr"], "local_ref": mu_d}
            if found is None or cand["depth_db"] > found["depth_db"]:
                found = cand
            break
    if not any_ref:
        return missing_finding("F5", tr, t0, "no local reference")
    if found is None:
        return Finding("F5", tr, t0, False, 0.0, None, {})
    level = ctx.ladders["dcn0_db"].level(found["depth_db"]) or 0
    st["last_dip"] = found
    return Finding("F5", tr, t0, True, sev(level), found["dip_start"], {**found, "level": level})


def findings(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    f1s: dict[str, Finding] = {}
    for tr in ctx.tractors:
        f1, f2, f11 = _f1_f2_f11(ctx, tr)
        ctx.put(f1)
        ctx.put(f2)
        ctx.put(f11)
        f5 = _f5(ctx, tr, f1)
        ctx.put(f5)
        f1s[tr] = f1
        out += [f1, f2, f5, f11]
    fc = ctx.cfg["findings"]
    t0 = ctx.t0
    working = [tr for tr in ctx.tractors if ctx.working.get(tr, False)]
    valid = [tr for tr in ctx.tractors if not f1s[tr].missing]
    present = [tr for tr in valid if f1s[tr].present]

    def spread_of(trs: list[str]) -> float:
        if not trs:
            return float("nan")
        drops = [float(f1s[tr].detail.get("drop_db", float("nan"))) for tr in trs]
        if all(not math.isnan(d) for d in drops):
            return max(drops) - min(drops)
        levels = [float(f1s[tr].detail["cn0"]) for tr in trs]
        return max(levels) - min(levels)

    if len(working) < 2 or any(f1s[tr].missing for tr in working):
        f3 = Finding("F3", "*", t0, False, 0.0, None, {"missing": True, "why": "fewer than 2 working tractors with data"})
    else:
        all_drop = all(f1s[tr].present for tr in working)
        spread = spread_of(working) if all_drop else float("nan")
        p3 = bool(all_drop and spread <= float(fc["equal_drop_db"]))
        s3 = float(np.mean([f1s[tr].severity for tr in working])) if p3 else 0.0
        on3 = min((f1s[tr].onset for tr in working if f1s[tr].onset is not None), default=None) if p3 else None
        f3 = Finding("F3", "*", t0, p3, s3, on3, {"tractors": working, "spread_db": spread,
                                                  "drops_db": {tr: f1s[tr].detail.get("drop_db") for tr in working}})
    if len(valid) < 2:
        f4 = Finding("F4", "*", t0, False, 0.0, None, {"missing": True, "why": "fewer than 2 tractors with data"})
    else:
        if len(present) >= 2:
            spread = spread_of(present)
            p4 = spread > float(fc["unequal_drop_db"])
        else:
            spread = float("nan")
            p4 = False
        s4_ = float(np.mean([f1s[tr].severity for tr in present])) if p4 else 0.0
        on4 = min((f1s[tr].onset for tr in present if f1s[tr].onset is not None), default=None) if p4 else None
        f4 = Finding("F4", "*", t0, p4, s4_, on4, {"tractors": present, "spread_db": spread,
                                                   "drops_db": {tr: f1s[tr].detail.get("drop_db") for tr in present}})
    for tr in ctx.tractors:
        for shared in (f3, f4):
            fin = Finding(shared.fid, tr, t0, shared.present, shared.severity, shared.onset, shared.detail)
            ctx.put(fin)
            out.append(fin)
    return out
