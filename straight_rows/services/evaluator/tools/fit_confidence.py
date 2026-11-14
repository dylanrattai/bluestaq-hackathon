"""CONFIDENCE_UPDATE v2.2 §3/§5: freeze `confidence_table.yaml` from the v2.1-physics dev run and check its
reliability by leave-one-event-out.

    python -m tools.fit_confidence --data ../../data [--out ../../out] [--table confidence_table.yaml]

Conditioning set: every episode the physics pipeline opens on dev (each exported group of a signal episode, each
sub-episode's groups, the episodes that opened but exported nothing, every position-track episode), labelled with
the scorer's matching rule (time IoU >= 0.30, tractor IoU >= 0.50) as the class of the labelled event it matches,
else NONE. The table depends only on config.yaml, the code and the data, never on the clock: re-running gives the
same file. The evaluation window is never read.
"""
from __future__ import annotations

import argparse
import copy
import math
import hashlib
import json
import logging
from pathlib import Path

import yaml

from core.config import load_config
from core.log import iso
from core.srtypes import AlarmState, ACTIONS
from diagnose.confidence import (CLASSES, CLIP, GROUPS, LEAK_TARGET, LABELS, confidence, fit_table, group_states,
                                 groups_for, table_hash)
from tools.dev_check import iou_set, iou_time, score

HERE = Path(__file__).resolve().parent.parent
BINS = [(0.02, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.97)]
W_GRID = (0.0, 0.25, 0.5, 1.0)          # weight of the group LRs, chosen by leave-one-out log-loss on dev detections


def run_dev(cfg: dict, data: Path):
    from core.log import EventSink
    from ingest.load import load_sda, load_telemetry
    from runtime.engine import Engine
    tele = load_telemetry(data, cfg).truncate(float(cfg["window"]["start_s"]))
    sda = load_sda(data, cfg)
    eng = Engine(cfg, None, None, tele=tele, sda=sda, write_outputs=False, mode="calib", sink=EventSink(None))
    eng.run()
    return eng


def match_event(s: float, e: float, trs: list[str], truth: list[dict]) -> dict | None:
    best = None
    for t in truth:
        if iou_time(s, e, t["s"], t["e"]) >= 0.30 and iou_set(set(trs), set(t["tractors"])) >= 0.50:
            ov = min(e, t["e"]) - max(s, t["s"])
            if best is None or ov > best[0]:
                best = (ov, t)
    return best[1] if best else None


def states_all(mgr, s: float, e: float, trs: list[str], sub: bool) -> dict:
    return {c: {g: v["state"] for g, v in group_states(c, list(mgr.span_findings(c, s, e, trs, sub).values())).items()}
            for c in CLASSES}


def collect_units(eng, truth: list[dict]) -> list[dict]:
    mgr = eng.episodes
    mgr.now = eng.now
    raw = []
    for farm in mgr.farms:
        res = farm.result or {}
        spans = [mgr._tractor_hull(farm, tr) for tr in farm.members()]
        fs = (min(a for a, _ in spans), max(b for _, b in spans))
        if res.get("groups"):
            for i, g in enumerate(res["groups"]):
                raw.append((f"{farm.id}/{g['cls']}/{i}", g["start"], g["end"], sorted(g["tractors"]), False))
        else:
            raw.append((farm.id, fs[0], fs[1], farm.members(), False))
        for sub in farm.subs:
            end = sub.end if sub.end is not None else mgr._b_end(sub.last_b)
            r = sub.result or {}
            if r.get("groups"):
                for i, g in enumerate(r["groups"]):
                    raw.append((f"{sub.id}/{g['cls']}/{i}", g["start"], g["end"], sorted(g["tractors"]), True))
            else:
                raw.append((sub.id, sub.start, end, sorted(sub.tractors), True))
    for ep in mgr.spoofs:
        raw.append((ep.id, ep.start, ep.end if ep.end is not None else mgr._b_end(ep.last_b), [ep.tr], False))
    units = []
    for uid, s, e, trs, sub in raw:
        t = match_event(s, e, trs, truth)
        units.append({"id": uid, "start": iso(s), "end": iso(e), "tractors": trs, "sub": sub,
                      "label": t["class"] if t else "NONE", "event": t["id"] if t else None,
                      "states": states_all(mgr, s, e, trs, sub), "_s": s, "_e": e})
    return units


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else "missing"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="../../data")
    ap.add_argument("--out", default="../../out")
    ap.add_argument("--table", default=str(HERE / "confidence_table.yaml"))
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    data = Path(args.data)
    cfg = load_config(None)
    cfg = copy.deepcopy(cfg)
    cfg.setdefault("confidence", {})["table"] = None          # fit without any previous table
    from tools.calibrate import load_truth
    truth = load_truth(data)
    eng = run_dev(cfg, data)
    links = cfg["fusion"]["links"]
    units = collect_units(eng, truth)
    tab = fit_table(units, links)
    # ---------------------------------------------------------------- dev detections (hit = matched, same class)
    dev0, dev1 = float(eng.t0s[0]), float(cfg["window"]["start_s"])
    alarms = [a for a in eng.lifecycle.alarms.values() if a.state == AlarmState.CONFIRMED and a.cls in ACTIONS and a.track != "site"
              and a.start < dev1 and a.span_end(eng.now) > dev0]
    dets = [{"id": a.id, "s": max(a.start, dev0), "e": min(a.span_end(eng.now), dev1), "tractors": sorted(a.tractors),
             "class": a.cls} for a in alarms]
    sc = score(dets, truth)
    ev_of = {m[0]: truth[j]["id"] for j, m in sc["match_by_truth"].items()}
    hits = [i in ev_of for i in range(len(dets))]
    mgr = eng.episodes
    st_of = []
    for a in alarms:
        fds = mgr.span_findings(a.cls, a.start, a.span_end(eng.now), sorted(a.tractors), sub=(a.track == "sub"))
        st_of.append(group_states(a.cls, list(fds.values())))
    tabs_loo = []
    for i, d in enumerate(dets):
        if i in ev_of:
            excl = {u["id"] for u in units if u["event"] == ev_of[i]}
        else:
            excl = {u["id"] for u in units if iou_time(u["_s"], u["_e"], d["s"], d["e"]) >= 0.30
                    and set(u["tractors"]) & set(d["tractors"])}
        others = [h for k, h in enumerate(hits) if k != i and not (k in ev_of and ev_of.get(i) and ev_of[k] == ev_of[i])]
        t_l = fit_table(units, links, exclude=excl)
        t_l["detection_prior"] = {"hits": sum(others), "n": len(others), "p": round((sum(others) + 1) / (len(others) + 2), 6)}
        tabs_loo.append((t_l, len(excl)))

    def loo_p(w: float) -> list[float]:
        out = []
        for i, a in enumerate(alarms):
            t_l = dict(tabs_loo[i][0], lr_weight=w)
            out.append(confidence(a.cls, st_of[i], t_l)[0])
        return out

    def logloss(ps):
        return -sum(math.log(p if h else 1 - p) for p, h in zip(ps, hits)) / len(ps)

    grid = {w: logloss(loo_p(w)) for w in W_GRID}
    w_best = min(grid, key=lambda w: (round(grid[w], 9), abs(w - 0.5)))
    tab["detection_prior"] = {"hits": sum(hits), "n": len(hits), "p": round((sum(hits) + 1) / (len(hits) + 2), 6)}
    tab["lr_weight"] = w_best
    pub_units = [{k: v for k, v in u.items() if not k.startswith("_")} for u in units]
    src = {"config_sha": file_sha(HERE / "config.yaml"),
           "telemetry_sha": file_sha(data / "farm" / "tractor_telemetry.csv.gz"),
           "labels_sha": file_sha(data / "labels" / "train_episodes.json"),
           "units_hash": table_hash(pub_units)}
    src["run_hash"] = table_hash(src)
    doc = {"what": "confidence table v2.3 (frozen; eval and live read it, never the dev labels)",
           "source": src, "leak_target": LEAK_TARGET, "clip": list(CLIP),
           "detection_prior": tab["detection_prior"], "lr_weight": w_best,
           "lr_weight_grid_logloss": {str(k): round(v, 6) for k, v in grid.items()},
           "groups": {g: {"name": n, "findings": list(f)} for g, (n, f) in GROUPS.items()},
           "N": tab["N"], "classes": tab["classes"], "units": pub_units}
    Path(args.table).write_text(yaml.safe_dump(doc, sort_keys=False, width=160))
    print(f"wrote {args.table}: N={tab['N']} opened dev episodes, detection prior {tab['detection_prior']}, "
          f"lr_weight {w_best} (LOO log-loss {grid}), run {src['run_hash']}")
    if args.no_report:
        return 0

    # ---------------------------------------------------------------- reliability (honest: leave-one-out)
    rows = []
    ps = loo_p(w_best)
    for i, (a, d) in enumerate(zip(alarms, dets)):
        t_l = dict(tabs_loo[i][0], lr_weight=w_best)
        p, br = confidence(a.cls, st_of[i], t_l)
        p_in, _ = confidence(a.cls, st_of[i], tab)
        p22, _ = confidence(a.cls, st_of[i], {k: v for k, v in t_l.items() if k not in ("detection_prior", "lr_weight")})
        old = float(a.extra.get("bf_conf", a.bf / (1 + a.bf)))
        rows.append({"id": a.id, "class": a.cls, "tractors": d["tractors"], "start": iso(d["s"]), "hit": hits[i],
                     "event": ev_of.get(i), "p_loeo": p, "p_in": p_in, "p_v22": p22, "p_old": round(old, 3),
                     "excluded_units": tabs_loo[i][1],
                     "groups": {g: (x["state"], round(x["lr"], 2)) for g, x in br["groups"].items()}})
    n = len(rows)
    brier_new = sum((r["p_loeo"] - r["hit"]) ** 2 for r in rows) / n
    brier_old = sum((r["p_old"] - r["hit"]) ** 2 for r in rows) / n
    brier_v22 = sum((r["p_v22"] - r["hit"]) ** 2 for r in rows) / n
    ll = lambda key: -sum(math.log(min(max(r[key], 1e-3), 1 - 1e-3) if r["hit"] else 1 - min(max(r[key], 1e-3), 1 - 1e-3)) for r in rows) / n
    bins = []
    for lo, hi in BINS:
        xs = [r for r in rows if lo <= r["p_loeo"] < hi or (hi == 0.97 and r["p_loeo"] == 0.97)]
        if xs:
            mp = sum(r["p_loeo"] for r in xs) / len(xs)
            hr = sum(r["hit"] for r in xs) / len(xs)
        else:
            mp = hr = float("nan")
        bins.append({"bin": f"[{lo}, {hi}{']' if hi == 0.97 else ')'}", "count": len(xs), "mean_p": mp, "hit_rate": hr})
    ok_brier = brier_new < brier_old
    bad_bins = [b for b in bins if b["count"] >= 3 and abs(b["mean_p"] - b["hit_rate"]) > 0.20]
    verdict = "PASS" if ok_brier and not bad_bins else "FAIL"
    res = {"dev_score": sc["score"], "n": n, "brier_new": brier_new, "brier_old": brier_old, "brier_v22": brier_v22,
           "logloss_new": ll("p_loeo"), "logloss_old": ll("p_old"), "logloss_v22": ll("p_v22"),
           "lr_weight": w_best, "lr_weight_grid": grid, "detection_prior": tab["detection_prior"], "bins": bins,
           "verdict": verdict, "rows": rows, "table_run": src["run_hash"]}
    out = Path(args.out)
    (out / "confidence_reliability.json").write_text(json.dumps(res, indent=1, default=float))
    L = ["## Confidence reliability (v2.2)", "",
         "**v2.3 (owner allowed tuning on dev):** v2.2's prior was P(class c | an episode opened), split six ways, which "
         "ignores that the classifier has already chosen c, so P sat near 0.5 while 9 of 10 dev detections were right. "
         f"v2.3 starts from how often a dev *detection* was right, shrunk: (hits + 1) / (n + 2) = "
         f"({tab['detection_prior']['hits']} + 1) / ({tab['detection_prior']['n']} + 2) = {tab['detection_prior']['p']:.2f}, "
         f"and counts the group LRs with weight w = {w_best} (chosen by leave-one-out log-loss over {list(W_GRID)}: "
         + ", ".join(f"w {k}: {v:.3f}" for k, v in grid.items()) + "). Everything below is leave-one-out (the detection's "
         "own event is out of both the group table and the detection prior). The weight itself was picked on the same "
         "10 detections.", "",
         f"Table `confidence_table.yaml` (run {src['run_hash']}): N = {tab['N']} opened dev episodes of the "
         f"v2.1-physics run, labelled by the scorer's matching rule; leak target l = {LEAK_TARGET}, clip {list(CLIP)}. "
         f"Dev score of that run: {sc['score']:.1f} (confidence is not scored).", "",
         "| class | prior | group | q | k1/n1 | p1 | k0/n0 | p0 | LR present | LR absent |", "|---|---|---|---|---|---|---|---|---|---|"]
    for c in CLASSES:
        row = tab["classes"][c]
        for g, t in row["groups"].items():
            L.append(f"| {c} (n={row['n_c']}) | {row['prior']:.3f} | {g} {t['name']} [{','.join(t['signs'])}] | {t['q']:g} | "
                     f"{t['k1']}/{t['n1']} | {t['p1']:.3f} | {t['k0']}/{t['n0']} | {t['p0']:.3f} | "
                     f"{t['p1'] / t['p0']:.2f} | {(1 - t['p1']) / (1 - t['p0']):.2f} |")
    L += ["", f"Leave-one-event-out P for the {n} exported dev detections (each detection's table is rebuilt without "
          "the opened episodes of its labelled event; an unmatched detection drops the episodes it overlaps):", "",
          "| bin | count | mean P | hit rate |", "|---|---|---|---|"]
    for b in bins:
        L.append(f"| {b['bin']} | {b['count']} | {b['mean_p']:.2f} | {b['hit_rate']:.2f} |" if b["count"] else f"| {b['bin']} | 0 | | |")
    L += ["", f"- Brier score: **v2.3 {brier_new:.3f}** vs old BF/(1+BF) {brier_old:.3f} vs v2.2 {brier_v22:.3f} on the same {n} detections; "
          f"log-loss v2.3 {res['logloss_new']:.3f}, old {res['logloss_old']:.3f}, v2.2 {res['logloss_v22']:.3f}.",
          f"- Pass criteria (new Brier < old; no bin with count >= 3 off by > 0.20): **{verdict}**"
          + ("" if verdict == "PASS" else f" (Brier {'ok' if ok_brier else 'worse'}; bins off: {', '.join(b['bin'] for b in bad_bins) or 'none'}). Reported as is; l, the groups and the clip were not changed."),
          "", "| detection | class | tractors | start | hit | old | v2.2 | P (LOEO) | P (all dev) | groups (state, weighted LR) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['id']} | {r['class']} | {','.join(r['tractors'])} | {r['start'][5:16]} | {'yes' if r['hit'] else 'no'} | "
                 f"{r['p_old']:.3f} | {r['p_v22']:.2f} | {r['p_loeo']:.2f} | {r['p_in']:.2f} | "
                 + "; ".join(f"{g} {s} ×{lr:g}" for g, (s, lr) in r["groups"].items()) + " |")
    cal = out / "calibration.md"
    text = cal.read_text() if cal.is_file() else ""
    if "## Confidence reliability (v2.2)" in text:
        text = text[:text.index("## Confidence reliability (v2.2)")].rstrip() + "\n"
    cal.write_text(text + "\n" + "\n".join(L) + "\n")
    print(json.dumps({k: res[k] for k in ("dev_score", "n", "brier_new", "brier_old", "brier_v22", "logloss_new", "logloss_old", "lr_weight", "verdict")}, default=float))
    for b in bins:
        print(b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
