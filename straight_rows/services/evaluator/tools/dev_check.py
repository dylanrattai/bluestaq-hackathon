"""Diagnostic scorer against the development labels (spec §10). Since Decision v2.1 the labels also train
config_trained.yaml (tools/calibrate.py); this module itself only scores.

Run through the CLI:  python main.py batch --dev --data ../../data --out ../../out   (or `python main.py dev ...`)

Matching follows the official diagnostic: same class, time IoU >= 0.30 and tractor-set IoU >= 0.50; onset credit is
full within 5 min and falls linearly to 0 at 60 min; score ~ 25 x macro-F1 + 15 x mean onset credit (out of 40).
Recurrent MULTIPATH reports within 3 min of a matched one are not counted as extras.

For every labelled episode the report also explains *why*: which findings were present during its span, what the
network's strongest causes were, and every alarm (confirmed or retracted, with reason) that overlapped it.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

import numpy as np

from core.config import parse_iso
from core.log import iso
from core.srtypes import ACTIONS, FINDING_IDS, AlarmState

log = logging.getLogger("sr.dev")
CLASSES = list(ACTIONS.keys())
# earlier dev-check results, for the before/after table (never used by the pipeline)
PREVIOUS = {"v1 (2026-11-14, before Fix Spec v1.1)": {"score": 13.3, "macro_f1": 0.333, "found": 4, "truth": 12, "extras": 11, "onset_credit": 0.33},
            "v1.1 (first run, before Update v2)": {"score": 18.9, "macro_f1": 0.444, "found": 7, "truth": 12, "extras": 33, "onset_credit": 0.52},
            "v2 (before Decision v2.1)": {"score": 12.6, "macro_f1": 0.305, "found": 4, "truth": 12, "extras": 3, "onset_credit": 0.33},
            "v2.1-physics (config.yaml)": {"score": 29.5, "macro_f1": 0.731, "found": 9, "truth": 12, "extras": 1, "onset_credit": 0.75}}


def iou_time(a0, a1, b0, b1) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def iou_set(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def onset_credit(err_s: float) -> float:
    if err_s <= 300:
        return 1.0
    if err_s >= 3600:
        return 0.0
    return 1.0 - (err_s - 300) / 3300


def score(dets: list[dict], truth: list[dict], strict: bool = True) -> dict:
    """PDF p. 4: recurrent MULTIPATH reports lasting <= 3 min are exempt from extra-detection penalties except
    duplicates overlapping the scored representative window. strict: a duplicate = any unmatched short MULTIPATH
    report overlapping a labelled MULTIPATH window; lenient (strict=False): only on a tractor of that label."""
    pairs = []
    for i, d in enumerate(dets):
        for j, t in enumerate(truth):
            if d["class"] != t["class"]:
                continue
            ti = iou_time(d["s"], d["e"], t["s"], t["e"])
            si = iou_set(set(d["tractors"]), set(t["tractors"]))
            if ti >= 0.30 and si >= 0.50:
                pairs.append((min(d["e"], t["e"]) - max(d["s"], t["s"]), i, j))
    pairs.sort(reverse=True)
    used_d, used_t, matches = set(), set(), []
    for _, i, j in pairs:
        if i in used_d or j in used_t:
            continue
        used_d.add(i)
        used_t.add(j)
        matches.append((i, j))
    per = {}
    for c in CLASSES:
        tp = sum(1 for i, j in matches if truth[j]["class"] == c)
        n_t = sum(1 for t in truth if t["class"] == c)
        extras = [i for i, d in enumerate(dets) if d["class"] == c and i not in used_d]
        if c == "MULTIPATH":
            reps = [t for t in truth if t["class"] == c]

            def duplicate(d):
                return any(d["s"] < t["e"] and d["e"] > t["s"] and (strict or set(d["tractors"]) & set(t["tractors"]))
                           for t in reps)
            extras = [i for i in extras if (dets[i]["e"] - dets[i]["s"]) > 180 or duplicate(dets[i])]
        fp, fn = len(extras), n_t - tp
        prec = tp / (tp + fp) if tp + fp else (1.0 if n_t == 0 else 0.0)
        rec = tp / n_t if n_t else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[c] = {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "f1": f1, "n_truth": n_t, "extras": extras}
    scored = [c for c in CLASSES if per[c]["n_truth"] > 0 or per[c]["fp"] > 0]
    macro = sum(per[c]["f1"] for c in scored) / len(scored) if scored else 0.0
    credits, match_by_truth = [], {}
    for i, j in matches:
        err = abs(dets[i]["s"] - truth[j]["s"])
        credits.append(onset_credit(err))
        match_by_truth[j] = (i, err, onset_credit(err))
    credits += [0.0] * (len(truth) - len(matches))
    mean_credit = sum(credits) / len(credits) if credits else 0.0
    return {"per_class": per, "macro_f1": macro, "mean_onset_credit": mean_credit,
            "score": 25 * macro + 15 * mean_credit, "match_by_truth": match_by_truth, "used_d": used_d}


# --------------------------------------------------------------------------- explanations
def _span_batches(eng, s: float, e: float) -> tuple[int, int]:
    b0 = max(int((s - eng.t0s[0]) // eng.batch_s), 0)
    b1 = min(int(np.ceil((e - eng.t0s[0]) / eng.batch_s)), eng.nb)
    return b0, max(b1, b0 + 1)


def explain_span(eng, s: float, e: float, tractors: list[str]) -> dict:
    """What the pipeline saw during [s, e): finding presence per tractor, strongest causes, overlapping alarms."""
    b0, b1 = _span_batches(eng, s, e)
    J = {f: i for i, f in enumerate(FINDING_IDS)}
    out = {"findings": {}, "causes": {}, "working": {}, "alarms": []}
    causes = eng.fusion.causes
    for tr in tractors:
        if tr not in eng.fv_present:
            continue
        pres = eng.fv_present[tr][b0:b1]
        miss = eng.fv_missing[tr][b0:b1]
        out["findings"][tr] = {fid: round(float(pres[:, J[fid]].mean()), 2) for fid in FINDING_IDS if pres[:, J[fid]].any()}
        out["findings"][tr]["_missing"] = [fid for fid in FINDING_IDS if miss[:, J[fid]].mean() > 0.5]
        post = eng.post_hist[tr][b0:b1]
        out["causes"][tr] = {c: round(float(np.median(post[:, k])), 2) for k, c in enumerate(causes) if np.median(post[:, k]) >= 0.02}
        out["working"][tr] = round(float(eng.working_hist[tr][b0:b1].mean()), 2)
    for a in eng.lifecycle.alarms.values():
        ae = a.span_end(eng.now)
        if a.state == AlarmState.RETRACTED and a.reason in ("CLEARED",) and a.cls == "OTHER":
            continue
        if a.start < e and ae > s and (set(a.tractors) & set(tractors) or a.cls == "SPACE_WEATHER"):
            out["alarms"].append(f"{a.id} {a.state.value}" + (f"({a.reason})" if a.reason else "") +
                                 f" {iso(a.start)[11:19]}..{iso(ae)[11:19]} {','.join(sorted(a.tractors))} conf {a.confidence:.2f}")
    return out


def _fmt_expl(x: dict) -> list[str]:
    lines = []
    for tr, f in x["findings"].items():
        pres = ", ".join(f"{k} {v:.0%}" for k, v in f.items() if not k.startswith("_"))
        miss = ",".join(f.get("_missing", []))
        cz = ", ".join(f"{c} {p:.2f}" for c, p in sorted(x["causes"].get(tr, {}).items(), key=lambda kv: -kv[1])[:3])
        lines.append(f"  - {tr}: working {x['working'].get(tr, 0):.0%}; findings present: {pres or 'none'}"
                     + (f"; no information: {miss}" if miss else "") + f"; median posterior: {cz or 'nothing above 0.02'}")
    if x["alarms"]:
        lines.append("  - alarms overlapping: " + "; ".join(x["alarms"][:8]))
    else:
        lines.append("  - alarms overlapping: none")
    return lines


def _verdict(t: dict, x: dict, matched: bool) -> str:
    """One-line reading of what went right or wrong."""
    cls = t["class"]
    key = {"SPACE_WEATHER": ("F1", "F3", "F9"), "JAMMING": ("F1", "F4", "F2", "F10"), "SPOOFING": ("F8",),
           "EQUIPMENT_FAULT": ("F6", "F7"), "MULTIPATH": ("F5",)}[cls]
    seen = {fid for f in x["findings"].values() for fid in f if not fid.startswith("_")}
    missing_key = [k for k in key if k not in seen]
    other_alarms = [a for a in x["alarms"] if cls not in a]
    if matched:
        return "RIGHT: " + (f"the expected findings ({', '.join(k for k in key if k in seen)}) drove a confirmed {cls} alarm")
    if not seen & set(key):
        return f"WRONG (detector): none of the {cls} findings ({', '.join(key)}) fired during the episode"
    if any(cls in a and "RETRACTED" in a for a in x["alarms"]):
        return f"WRONG (watchdog): a {cls} alarm existed but was retracted; see the reason in the alarm list"
    if any(cls in a for a in x["alarms"]):
        return f"WRONG (extent/tractors): a {cls} alarm overlapped but failed the IoU match (time >= 0.30, tractors >= 0.50)"
    if other_alarms:
        return f"WRONG (attribution): findings fired but the network preferred another cause ({other_alarms[0].split()[0]})"
    return f"WRONG (strength): {', '.join(sorted(seen & set(key)))} fired but never reached ALARM (BF >= 20 in 2 of 3 batches)" + \
           (f"; missing corroboration {', '.join(missing_key)}" if missing_key else "")


def run_dev_check(eng, cfg: dict, data_dir: Path, out_dir: Path) -> dict:
    with open(Path(data_dir) / "labels" / "train_episodes.json") as f:
        lab = json.load(f)
    truth = [{"id": e["id"], "s": parse_iso(e["start"]), "e": parse_iso(e["end"]), "tractors": sorted(e["tractors"]),
              "class": e["class"]} for e in lab["episodes"]]
    dev0 = float(eng.t0s[0])
    dev1 = float(cfg["window"]["start_s"])
    now = eng.now
    dets = []
    for a in eng.lifecycle.alarms.values():
        if a.state != AlarmState.CONFIRMED or a.cls not in ACTIONS:
            continue
        s, e = a.start, a.span_end(now)
        if e <= dev0 or s >= dev1:
            continue
        dets.append({"id": a.id, "s": max(s, dev0), "e": min(e, dev1), "tractors": sorted(a.tractors), "class": a.cls,
                     "confidence": round(a.confidence, 3)})
    res = score(dets, truth)
    tp = sum(p["tp"] for p in res["per_class"].values())
    fp = sum(p["fp"] for p in res["per_class"].values())
    n_t = len(truth)
    lines = ["# Dev check: pipeline vs development labels (diagnostic score)", "",
             f"Development period {iso(dev0)} .. {iso(dev1)}: {n_t} labelled episodes, {len(dets)} confirmed detections.", "",
             "## Accuracy rating", "",
             f"- **Diagnostic score {res['score']:.1f} / 40** (25 x macro-F1 + 15 x mean onset credit)",
             f"- Lenient reading of the multipath duplicate rule (only same-tractor duplicates count): "
             f"{score(dets, truth, strict=False)['score']:.1f} / 40",
             f"- Macro-F1 {res['macro_f1']:.3f}; episodes found {tp}/{n_t} (recall {tp / n_t:.0%}); "
             f"extra detections {fp} (precision {tp / (tp + fp) if tp + fp else 0:.0%}); mean onset credit {res['mean_onset_credit']:.2f}",
             "", "| version | score /40 | macro-F1 | found | extras | onset credit |", "|---|---|---|---|---|---|"]
    for name, v in PREVIOUS.items():
        lines.append(f"| {name} | {v['score']:.1f} | {v['macro_f1']:.3f} | {v['found']}/{v['truth']} | {v['extras']} | {v['onset_credit']:.2f} |")
    lines += [f"| **current** | **{res['score']:.1f}** | **{res['macro_f1']:.3f}** | **{tp}/{n_t}** | **{fp}** | **{res['mean_onset_credit']:.2f}** |",
              "", "| class | truth | found | extra | missed | precision | recall | F1 |", "|---|---|---|---|---|---|---|---|"]
    for c, p in res["per_class"].items():
        lines.append(f"| {c} | {p['n_truth']} | {p['tp']} | {p['fp']} | {p['fn']} | {p['precision']:.2f} | {p['recall']:.2f} | {p['f1']:.2f} |")
    lines += ["", "## Episode by episode", ""]
    for j, t in enumerate(truth):
        m = res["match_by_truth"].get(j)
        x = explain_span(eng, t["s"], t["e"], t["tractors"])
        head = f"### {t['id']} {t['class']} {iso(t['s'])[5:19]}..{iso(t['e'])[11:19]} {','.join(t['tractors'])}"
        if m:
            i, err, cr = m
            d = dets[i]
            head += f" — MATCHED by {d['id']} {iso(d['s'])[11:19]}..{iso(d['e'])[11:19]} {','.join(d['tractors'])}, onset error {err:.0f} s (credit {cr:.2f}), confidence {d['confidence']}"
        else:
            head += " — MISSED"
        lines += [head, "", "- " + _verdict(t, x, bool(m))] + _fmt_expl(x) + [""]
    extras = [dets[i] for c in CLASSES for i in res["per_class"][c]["extras"]]
    lines += [f"## Extra detections ({len(extras)}): confirmed but matching no label", ""]
    for d in extras:
        x = explain_span(eng, d["s"], d["e"], d["tractors"])
        lines += [f"### {d['id']} {d['class']} {iso(d['s'])[5:19]}..{iso(d['e'])[11:19]} {','.join(d['tractors'])} conf {d['confidence']}", ""]
        lines += ["- WRONG (false alarm) unless the label set is incomplete; what the pipeline saw:"] + _fmt_expl(x) + [""]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dev_report.md").write_text("\n".join(lines) + "\n")
    summary = {"score": res["score"], "macro_f1": res["macro_f1"], "onset_credit": res["mean_onset_credit"],
               "found": tp, "truth": n_t, "extras": fp, "report": str(out_dir / "dev_report.md")}
    log.info("[bold]dev check: score %.1f/40, macro-F1 %.3f, found %d/%d, extras %d, onset credit %.2f -> %s[/bold]",
             res["score"], res["macro_f1"], tp, n_t, fp, res["mean_onset_credit"], out_dir / "dev_report.md")
    for j, t in enumerate(truth):
        m = res["match_by_truth"].get(j)
        log.info("  %s %-16s %s", t["id"], t["class"], ("MATCHED onset err %4.0f s" % m[1]) if m else "MISSED")
    return summary
