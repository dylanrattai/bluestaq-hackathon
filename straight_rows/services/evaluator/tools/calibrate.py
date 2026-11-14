"""Decision v2.1 §9: learn leaks and links from the labelled dev episodes, LOEO threshold search.

    python -m tools.calibrate --data ../../data --out ../../out [--workers 3]

* Leaks (§9.1) from WORKING dev batches outside every labelled span +-10 min (background): per batch, and per
  "episode" (a sliding window as long as the median labelled episode; present when phi >= phi_present in it).
  (k + 1) / (n + 2), clamped to [0.005, 0.20].
* Links (§9.2) shrunk toward the physics table with two pseudo-episodes: (k_cj + 2 q_cj) / (n_c + 2), where k_cj counts
  the class-c labelled episodes whose episode-level finding j was present (per-tractor findings: the fraction of the
  label's tractors). Learned inside each leave-one-episode-out fold without the held-out episode.
* Thresholds (§9.3): fusion.bf_alarm x episodes.phi_present on cached episodes (offline re-classification);
  episodes.close_batches, position.float_band_m and findings.turn_heading_deg by coordinate descent on real replays.
  Per fold the best setting on the 11 training episodes + background extras is chosen and scored on the held-out one.

Everything is computed on the development period only; the evaluation window is never read.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from core.config import load_config, parse_iso
from core.log import iso
from core.srtypes import ACTIONS, CAUSES, FINDING_IDS, AlarmState
from diagnose.fusion import Fusion
from tools.dev_check import onset_credit, score

log = logging.getLogger("sr.calib")
J = {f: i for i, f in enumerate(FINDING_IDS)}
CLASSES = list(ACTIONS)
PAD = 600.0                                   # labelled time = span +- 10 min
LEAK_LO, LEAK_HI = 0.005, 0.20
NET = [f for f in FINDING_IDS if f != "F11"]  # F11 is not a network finding (v2 §8)
BF_GRID = (10.0, 20.0, 50.0)
PHI_GRID = (0.2, 0.333, 0.5)
CB_GRID = (6, 10, 20)
FB_GRID = (0.3, 0.5, 0.8)
TURN_GRID = (20.0, 30.0, 45.0)
PHYSICS_T = {"bf_alarm": 20.0, "phi_present": 0.333, "close_batches": 10, "float_band_m": 0.5, "turn_heading_deg": 30.0}

_G: dict[str, Any] = {}                       # tele / sda / truth shared with forked workers


# --------------------------------------------------------------------------- data
def load_truth(data: Path) -> list[dict]:
    lab = json.loads((data / "labels" / "train_episodes.json").read_text())
    return [{"id": e["id"], "s": parse_iso(e["start"]), "e": parse_iso(e["end"]), "tractors": sorted(e["tractors"]),
             "class": e["class"]} for e in lab["episodes"]]


def set_knobs(cfg: dict, t: dict) -> dict:
    c = copy.deepcopy(cfg)
    c["fusion"]["bf_alarm"] = float(t["bf_alarm"])
    c["episodes"]["phi_present"] = float(t["phi_present"])
    c["episodes"]["close_batches"] = int(t["close_batches"])
    c["position"]["float_band_m"] = float(t["float_band_m"])
    c["findings"]["turn_heading_deg"] = float(t["turn_heading_deg"])
    return c


def physics_links(cfg: dict) -> dict[str, dict[str, float]]:
    return {fid: dict(row) for fid, row in cfg["fusion"]["links"].items()}


def q_of(links: dict, fid: str, c: str) -> float:
    return float(links.get(fid, {}).get(c, 0.025))      # blank cells count as 0.025 (§9.2)


def run_engine(cfg: dict):
    from runtime.engine import Engine
    from core.log import EventSink
    sink = EventSink(None)
    eng = Engine(cfg, None, None, tele=_G["tele"], sda=_G["sda"], write_outputs=False, mode="calib", sink=sink)
    eng.run()
    return eng


# --------------------------------------------------------------------------- background and leaks (§9.1)
def background_mask(eng, truth: list[dict]) -> dict[str, np.ndarray]:
    t0 = eng.t0s[:eng.b_done]
    lab = np.zeros(len(t0), dtype=bool)
    for t in truth:
        lab |= (t0 + eng.batch_s > t["s"] - PAD) & (t0 < t["e"] + PAD)
    return {tr: eng.working_hist[tr][:eng.b_done] & ~lab for tr in eng.tractors}


def leaks(eng, bg: dict[str, np.ndarray], phi: float, win_b: int) -> tuple[dict, dict, dict]:
    """Per-batch and per-episode background leaks, with the counts behind them."""
    batch, ep, counts = {}, {}, {}
    for fid in NET:
        j = J[fid]
        k = n = 0
        ke = ne = 0
        for tr in eng.tractors:
            m = bg[tr]
            pres = eng.fv_present[tr][:eng.b_done, j]
            info = ~eng.fv_missing[tr][:eng.b_done, j]
            k += int((pres & info & m).sum())
            n += int((info & m).sum())
            # sliding window over contiguous background stretches (stride 1 batch)
            ok = m.astype(np.int64)
            c_ok = np.concatenate([[0], np.cumsum(ok)])
            c_inf = np.concatenate([[0], np.cumsum((info & m).astype(np.int64))])
            c_hit = np.concatenate([[0], np.cumsum((pres & info & m).astype(np.int64))])
            if len(ok) >= win_b:
                s = np.arange(0, len(ok) - win_b + 1)
                full = (c_ok[s + win_b] - c_ok[s]) == win_b
                ninf = c_inf[s + win_b] - c_inf[s]
                nhit = c_hit[s + win_b] - c_hit[s]
                use = full & (ninf > 0)
                ke += int(((nhit[use] / ninf[use]) >= phi).sum())
                ne += int(use.sum())
        counts[fid] = {"batch_k": k, "batch_n": n, "ep_k": ke, "ep_n": ne}
        if n == 0:
            continue                                  # never measured per batch (F12): keep the physics leak
        batch[fid] = float(min(max((k + 1) / (n + 2), LEAK_LO), LEAK_HI))
        if ne:
            ep[fid] = float(min(max((ke + 1) / (ne + 2), LEAK_LO), LEAK_HI))
    return batch, ep, counts


# --------------------------------------------------------------------------- label findings and links (§9.2)
def _sats_ref(eng, tr: str, s: float) -> float:
    f = eng.feats[tr]
    b = int((s - eng.t0s[0]) // eng.batch_s)
    seg = f.sats_med[max(0, b - 120):max(b, 1)]
    seg = seg[~np.isnan(seg)]
    return float(np.median(seg)) if len(seg) else float("nan")


def label_findings(eng, t: dict, phi: float) -> dict[str, dict[str, dict]]:
    """Episode-level findings over a label's span and tractors, as the pipeline judges them; the F8/F12 of the
    position track and the F5/F12 of the dip track replace the signal track's held-absent values for their classes."""
    mgr = eng.episodes
    saved = mgr.phi_min
    mgr.phi_min = phi
    mgr.now = eng.now
    try:
        return _label_findings(eng, mgr, t, phi)
    finally:
        mgr.phi_min = saved


def _label_findings(eng, mgr, t: dict, phi: float) -> dict[str, dict[str, dict]]:
    s, e = t["s"], max(t["e"], t["s"] + eng.batch_s)
    trs = t["tractors"]
    spans = {tr: (s, e) for tr in trs}
    f1s, f3, f4 = mgr._shared_f3_f4(spans, (s, e), False)
    out = {}
    b0, b1 = mgr._brange(s, e)
    for tr in trs:
        f12 = mgr._f12(tr, s, e, "F1", set(), False)
        fd = mgr._tractor_findings(tr, s, e, eng.now, f1s[tr], f3, f4, f12, _sats_ref(eng, tr, s))
        phi8, sev8 = mgr._phi(tr, "F8", b0, b1)
        fd["F8"] = {"present": bool(phi8 is not None and phi8 >= phi), "sev": sev8, "missing": phi8 is None}
        if t["class"] == "SPOOFING":
            p12 = mgr.pos12[tr][b0:b1]
            k = p12[p12 >= 0]
            fd["F12"] = ({"present": bool((k == 1).mean() >= phi), "sev": 1.0, "missing": False} if len(k)
                         else {"present": False, "sev": 0.0, "missing": True})
        # F5: a dip candidate that reached the network on this tractor within the label (+-30 s for 17 s labels)
        rows = [r for r in mgr.dips if r["tractor"] == tr and r["start"] <= e + 30 and r["end"] >= s - 30]
        live = [r for r in rows if not r["excluded"] or str(r["excluded"]).startswith(("MULTIPATH BF", "signal", "spoof", "alarm"))]
        fd["F5"] = {"present": bool(live), "sev": 0.5, "missing": False}
        if t["class"] == "MULTIPATH":
            f12d = mgr._dip_f12(tr, s, e)
            fd["F12"] = f12d
        out[tr] = fd
    return out


def learn_links(phys: dict, lab_fd: list[tuple[dict, dict]], exclude: int | None) -> tuple[dict, dict]:
    """(k + 2 q) / (n + 2) per class and finding, without label `exclude`."""
    k: dict[tuple[str, str], float] = {}
    n: dict[tuple[str, str], int] = {}
    for i, (t, fds) in enumerate(lab_fd):
        if i == exclude:
            continue
        c = t["class"]
        for fid in NET:
            vals = [fd[fid]["present"] for fd in fds.values() if fid in fd and not fd[fid].get("missing", True)]
            if not vals:
                continue
            k[(c, fid)] = k.get((c, fid), 0.0) + float(np.mean(vals))
            n[(c, fid)] = n.get((c, fid), 0) + 1
    links = {fid: dict(row) for fid, row in phys.items()}
    stats = {}
    for (c, fid), nn in n.items():
        q = q_of(phys, fid, c)
        v = (k[(c, fid)] + 2.0 * q) / (nn + 2.0)
        v = float(min(max(v, 0.005), 0.995))
        links.setdefault(fid, {})[c] = round(v, 4)
        stats[(c, fid)] = (round(k[(c, fid)], 2), nn, q, round(v, 4))
    return links, stats


def fusions(cfg: dict, links: dict, leak_b: dict, leak_e: dict) -> tuple[Fusion, Fusion]:
    c = copy.deepcopy(cfg)
    c["fusion"]["links"] = links
    c["fusion"]["leaks"] = leak_b
    c["fusion"]["leaks_episode"] = leak_e
    return Fusion(c), Fusion(c, episode=True)


# --------------------------------------------------------------------------- offline re-classification
def offline_detections(eng, fu_b: Fusion, fu_e: Fusion, phi: float, bf: float) -> list[dict]:
    """Re-run the episode classification of a finished replay with other links / leaks / phi / bf_alarm.
    Episode structure (opens, closes, freezes) is the replay's; spans are the raw episode spans."""
    mgr = eng.episodes
    saved = (mgr.fu_ep, mgr.phi_min, mgr.bf_alarm, eng.fusion)
    mgr.fu_ep, mgr.phi_min, mgr.bf_alarm, eng.fusion = fu_e, phi, bf, fu_b
    now = eng.now
    mgr.now = now
    dets = []
    try:
        for farm in mgr.farms:
            members = farm.members()
            spans = {tr: mgr._tractor_hull(farm, tr) for tr in members}
            fs = (min(s for s, _ in spans.values()), max(e for _, e in spans.values()))
            res = mgr._classify(members, spans, fs, now, False, farm.sats_ref, set())
            for g in res["groups"]:
                dets.append({"class": g["cls"], "s": g["start"], "e": g["end"], "tractors": sorted(g["tractors"]),
                             "bf": g["bf"], "track": "signal"})
            pcls = {g["cls"] for g in res["groups"]} or ({res["prov"][0]} if res.get("prov") else set())
            for sub in farm.subs:
                end = sub.end if sub.end is not None else mgr._b_end(sub.last_b)
                m2 = sorted(sub.tractors)
                r2 = mgr._classify(m2, {tr: (sub.start, end) for tr in m2}, (sub.start, end), now, True, farm.sats_ref, set(m2))
                for g in r2["groups"]:
                    if g["cls"] not in pcls:
                        dets.append({"class": g["cls"], "s": g["start"], "e": g["end"], "tractors": sorted(g["tractors"]),
                                     "bf": g["bf"], "track": "sub"})
        for ep in mgr.spoofs:
            e = ep.end if ep.end is not None else mgr._b_end(ep.last_b)
            b0, b1 = mgr._brange(ep.start, e)
            phi8, sev8 = mgr._phi(ep.tr, "F8", b0, b1)
            p12 = mgr.pos12[ep.tr][b0:b1]
            k = p12[p12 >= 0]
            fd = {"F8": {"present": bool(phi8), "sev": sev8, "missing": phi8 is None}}
            if len(k):
                fd["F12"] = {"present": bool((k == 1).mean() >= phi), "sev": 1.0, "missing": False}
            b = mgr._evaluate(ep.tr, ep.start, fd).bayes_factor["SPOOFING"]
            if b >= bf:
                dets.append({"class": "SPOOFING", "s": ep.start, "e": e, "tractors": [ep.tr], "bf": b, "track": "position"})
        for a in eng.lifecycle.alarms.values():         # MULTIPATH: the replay's own dip decisions (§9.4 handles it)
            if a.track == "dip" and a.state == AlarmState.CONFIRMED:
                dets.append({"class": "MULTIPATH", "s": a.start, "e": a.span_end(now), "tractors": sorted(a.tractors),
                             "bf": a.bf, "track": "dip"})
    finally:
        mgr.fu_ep, mgr.phi_min, mgr.bf_alarm, eng.fusion = saved
    return postprocess(eng, dets)


def postprocess(eng, dets: list[dict]) -> list[dict]:
    """The watchdog's structural rules: SPACE_WEATHER within 30 min merge; < 2 batches NOT_SUSTAINED; all parked."""
    gap = float(eng.cfg["watchdog"]["merge_gap_min"].get("SPACE_WEATHER", 30)) * 60
    min_s = float(eng.cfg["watchdog"]["min_batches"]) * eng.batch_s
    sw = sorted([d for d in dets if d["class"] == "SPACE_WEATHER"], key=lambda d: d["s"])
    merged = []
    for d in sw:
        if merged and d["s"] - merged[-1]["e"] <= gap:
            m = merged[-1]
            m["e"] = max(m["e"], d["e"])
            m["tractors"] = sorted(set(m["tractors"]) | set(d["tractors"]))
            m["bf"] = max(m["bf"], d["bf"])
        else:
            merged.append(dict(d))
    out = []
    for d in [x for x in dets if x["class"] != "SPACE_WEATHER"] + merged:
        if d["class"] != "MULTIPATH" and d["e"] - d["s"] < min_s:
            continue
        b0 = int((d["s"] - eng.t0s[0]) // eng.batch_s)
        b1 = int(math.ceil((d["e"] - eng.t0s[0]) / eng.batch_s))
        if not any(eng.working_hist[tr][b0:max(b1, b0 + 1)].any() for tr in d["tractors"]):
            continue
        out.append(d)
    return out


def real_detections(eng) -> list[dict]:
    dets = []
    for a in eng.lifecycle.alarms.values():
        if a.state == AlarmState.CONFIRMED and a.cls in ACTIONS:
            dets.append({"class": a.cls, "s": a.start, "e": a.span_end(eng.now), "tractors": sorted(a.tractors),
                         "bf": a.bf, "track": a.track})
    return dets


def clip_dev(dets: list[dict], t0: float, t1: float) -> list[dict]:
    out = []
    for d in dets:
        if d["e"] <= t0 or d["s"] >= t1:
            continue
        out.append({**d, "s": max(d["s"], t0), "e": min(d["e"], t1)})
    return out


# --------------------------------------------------------------------------- scoring helpers
def near(d: dict, t: dict) -> bool:
    return d["s"] < t["e"] + PAD and d["e"] > t["s"] - PAD


def fold_eval(dets: list[dict], truth: list[dict], k: int) -> dict:
    """Held-out result of fold k (match, onset credit, extras near the held-out episode), the in-fold training
    score (11 episodes + background extras; detections near the held-out episode are left out) and the per-class
    background extras (detections near no labelled episode)."""
    full = score(dets, truth)
    m = full["match_by_truth"].get(k)
    unmatched = [i for i in range(len(dets)) if i not in full["used_d"]]
    ex_k = [dets[i]["class"] for i in unmatched if near(dets[i], truth[k])]
    bg = [dets[i]["class"] for i in unmatched if not any(near(dets[i], t) for t in truth)]
    train_truth = [t for i, t in enumerate(truth) if i != k]
    train_dets = [d for d in dets if not (near(d, truth[k]) and not any(near(d, t) for j, t in enumerate(truth) if j != k))]
    tr = score(train_dets, train_truth)
    return {"matched": m is not None, "credit": m[2] if m else 0.0, "err_s": m[1] if m else None,
            "extras_near": ex_k, "bg_extras": bg, "train_score": tr["score"]}


def aggregate(truth: list[dict], held: dict[int, dict], bg_extras: dict[str, float]) -> dict:
    per = {}
    for c in CLASSES:
        n_t = sum(1 for t in truth if t["class"] == c)
        tp = sum(1 for k, h in held.items() if truth[k]["class"] == c and h["matched"])
        fp = sum(h["extras_near"].count(c) for h in held.values()) + bg_extras.get(c, 0.0)
        prec = tp / (tp + fp) if tp + fp else (1.0 if n_t == 0 else 0.0)
        rec = tp / n_t if n_t else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[c] = {"n_truth": n_t, "tp": tp, "fp": round(fp, 2), "precision": prec, "recall": rec, "f1": f1}
    scored = [c for c in CLASSES if per[c]["n_truth"] > 0 or per[c]["fp"] > 0]
    macro = sum(per[c]["f1"] for c in scored) / len(scored)
    credit = sum(h["credit"] for h in held.values()) / len(truth)
    return {"score": 25 * macro + 15 * credit, "macro_f1": macro, "onset_credit": credit, "per_class": per}


# --------------------------------------------------------------------------- one structural setting
def evaluate_structure(task: tuple[str, dict]) -> dict:
    """Worker: one real replay for a structural setting, then every fold x phi x bf offline."""
    key, knobs = task
    logging.disable(logging.WARNING)
    cfg = set_knobs(_G["cfg"], {**PHYSICS_T, **knobs})
    truth = _G["truth"]
    t_a = time.time()
    eng = run_engine(cfg)
    dev0, dev1 = float(eng.t0s[0]), float(cfg["window"]["start_s"])
    real = clip_dev(real_detections(eng), dev0, dev1)
    real_score = score(real, truth)
    phys = physics_links(_G["cfg"])
    bg = background_mask(eng, truth)
    durs = sorted(t["e"] - t["s"] for t in truth)
    win_b = max(int(round(float(np.median(durs)) / eng.batch_s)), 1)
    res = {"key": key, "knobs": knobs, "real_physics": {"score": real_score["score"], "macro_f1": real_score["macro_f1"]},
           "win_b": win_b, "bg_batches": {tr: int(m.sum()) for tr, m in bg.items()}, "grid": {}, "t_run": time.time() - t_a}
    for phi in PHI_GRID:
        leak_b, leak_e, counts = leaks(eng, bg, phi, win_b)
        lab_fd = [(t, label_findings(eng, t, phi)) for t in truth]
        res.setdefault("leaks", {})[phi] = {"batch": leak_b, "episode": leak_e, "counts": counts}
        res.setdefault("label_findings", {})[phi] = [
            {"id": t["id"], "class": t["class"],
             "present": {fid: round(float(np.mean([fd[fid]["present"] for fd in fds.values() if fid in fd and not fd[fid].get("missing", True)])), 2)
                         for fid in NET if any(fid in fd and not fd[fid].get("missing", True) for fd in fds.values())}}
            for t, fds in lab_fd]
        for fold in [None] + list(range(len(truth))):
            links, stats = learn_links(phys, lab_fd, fold)
            if fold is None:
                res.setdefault("links", {})[phi] = {"links": links, "stats": {f"{c}|{f}": v for (c, f), v in stats.items()}}
            fu_b, fu_e = fusions(cfg, links, leak_b, leak_e)
            for bf in BF_GRID:
                dets = clip_dev(offline_detections(eng, fu_b, fu_e, phi, bf), dev0, dev1)
                if fold is None:
                    sc = score(dets, truth)
                    ex = {c: len(sc["per_class"][c]["extras"]) for c in CLASSES}
                    res["grid"][(phi, bf, None)] = {"score": sc["score"], "macro_f1": sc["macro_f1"], "onset": sc["mean_onset_credit"],
                                                    "per_class": {c: {k2: v for k2, v in p.items() if k2 != "extras"} for c, p in sc["per_class"].items()},
                                                    "extras": ex, "matched": sorted(truth[j]["id"] for j in sc["match_by_truth"])}
                else:
                    res["grid"][(phi, bf, fold)] = fold_eval(dets, truth, fold)
        # offline with physics links/leaks, for the offline-vs-replay check
        fu_b, fu_e = fusions(cfg, phys, {}, {})
        dets = clip_dev(offline_detections(eng, fu_b, fu_e, phi, cfg["fusion"]["bf_alarm"]), dev0, dev1)
        res.setdefault("offline_physics", {})[phi] = score(dets, truth)["score"]
    res["t_total"] = time.time() - t_a
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="../../data")
    ap.add_argument("--out", default="../../out")
    ap.add_argument("--config", default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--stage", default="all", choices=["all", "cb", "fb", "turn"])
    args = ap.parse_args(argv)
    from core.log import setup_logging
    from ingest.load import load_sda, load_telemetry
    setup_logging("INFO")
    data, out = Path(args.data), Path(args.out)
    cfg = load_config(args.config)
    tele = load_telemetry(data, cfg)
    sda = load_sda(data, cfg)
    t_cut = float(cfg["window"]["start_s"])
    _G.update(cfg=cfg, tele=tele.truncate(t_cut), sda=sda, truth=load_truth(data))    # dev period only
    results: dict[str, dict] = {}
    ctx = mp.get_context("fork")

    def stage(tasks):
        with ctx.Pool(min(args.workers, len(tasks))) as pool:
            for r in pool.imap_unordered(evaluate_structure, tasks):
                results[r["key"]] = r
                best = max(((k, v["score"]) for k, v in r["grid"].items() if k[2] is None), key=lambda x: x[1])
                log.info("%s %s: replay %.1f, offline-physics %s, best offline %s -> %.1f (%.0f s)", r["key"], r["knobs"],
                         r["real_physics"]["score"], {p: round(v, 1) for p, v in r["offline_physics"].items()}, best[0][:2],
                         best[1], r["t_total"])

    def best_of(keys):
        cand = [(v["score"], k, g) for k in keys for g, v in results[k]["grid"].items() if g[2] is None]
        return max(cand, key=lambda x: (x[0], x[2][1] == 20.0, x[2][0] == 0.333))

    # coordinate descent: close_batches, then float band, then turn threshold
    stage([(f"cb{cb}", {"close_batches": cb}) for cb in CB_GRID])
    _, kcb, _ = best_of([f"cb{cb}" for cb in CB_GRID])
    cb = results[kcb]["knobs"]["close_batches"]
    stage([(f"cb{cb}-fb{fb}", {"close_batches": cb, "float_band_m": fb}) for fb in FB_GRID if fb != 0.5])
    results[f"cb{cb}-fb0.5"] = results[kcb]
    _, kfb, _ = best_of([f"cb{cb}-fb{fb}" for fb in FB_GRID])
    fb = results[kfb]["knobs"].get("float_band_m", 0.5)
    stage([(f"cb{cb}-fb{fb}-t{tu:g}", {"close_batches": cb, "float_band_m": fb, "turn_heading_deg": tu}) for tu in TURN_GRID if tu != 30.0])
    results[f"cb{cb}-fb{fb}-t30"] = results[kfb]
    out.mkdir(parents=True, exist_ok=True)
    dump = {k: {**v, "grid": {json.dumps(list(g)): x for g, x in v["grid"].items()},
                "leaks": {str(p): x for p, x in v["leaks"].items()},
                "links": {str(p): x for p, x in v["links"].items()},
                "label_findings": {str(p): x for p, x in v["label_findings"].items()},
                "offline_physics": {str(p): x for p, x in v["offline_physics"].items()}}
            for k, v in results.items()}
    (out / "calibration_raw.json").write_text(json.dumps(dump, indent=1, default=float))
    log.info("wrote %s", out / "calibration_raw.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
