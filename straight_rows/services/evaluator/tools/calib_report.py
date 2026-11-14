"""Decision v2.1 §9.3-§9.6: pick the trained parameters from tools/calibrate.py's raw results, LOEO score,
guardrail, config_trained.yaml and out/calibration.md.

    python -m tools.calib_report --raw <calibration_raw.json> --out ../../out [--real-loeo <json>] [--mp <json>]
"""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

from core.config import load_config
from tools.calibrate import (BF_GRID, CLASSES, LEAK_HI, LEAK_LO, NET, PHI_GRID, PHYSICS_T, aggregate, load_truth,
                             physics_links, q_of)

HERE = Path(__file__).resolve().parent.parent


def knobs_of(raw: dict, key: str, phi: float, bf: float) -> dict:
    return {**PHYSICS_T, **raw[key]["knobs"], "phi_present": phi, "bf_alarm": bf}


def dist(t: dict) -> int:
    return sum(1 for k, v in PHYSICS_T.items() if abs(float(t[k]) - float(v)) > 1e-9)


def links_for(raw: dict, key: str, phi: float, phys: dict, exclude_id: str | None) -> dict:
    """Recompute the shrunk links from the stored per-label findings (the fold's held-out label excluded)."""
    k: dict = {}
    n: dict = {}
    for lf in raw[key]["label_findings"][str(phi)]:
        if lf["id"] == exclude_id:
            continue
        for fid, v in lf["present"].items():
            k[(lf["class"], fid)] = k.get((lf["class"], fid), 0.0) + float(v)
            n[(lf["class"], fid)] = n.get((lf["class"], fid), 0) + 1
    links = {fid: dict(row) for fid, row in phys.items()}
    for (c, fid), nn in n.items():
        v = (k[(c, fid)] + 2.0 * q_of(phys, fid, c)) / (nn + 2.0)
        links.setdefault(fid, {})[c] = round(float(min(max(v, 0.005), 0.995)), 4)
    return links


def trained_config(base_yaml: dict, t: dict, links: dict, leak_b: dict, leak_e: dict) -> dict:
    c = copy.deepcopy(base_yaml)
    c["fusion"]["bf_alarm"] = float(t["bf_alarm"])
    c["episodes"]["phi_present"] = float(t["phi_present"])
    c["episodes"]["close_batches"] = int(t["close_batches"])
    c["position"]["float_band_m"] = float(t["float_band_m"])
    c["findings"]["turn_heading_deg"] = float(t["turn_heading_deg"])
    c["fusion"]["links"] = links
    c["fusion"]["leaks"] = {k: round(v, 4) for k, v in leak_b.items()}
    c["fusion"]["leaks_episode"] = {k: round(v, 4) for k, v in leak_e.items()}
    return c


def select(raw: dict, truth: list[dict]) -> dict:
    cands = [(key, phi, bf) for key in raw for phi in PHI_GRID for bf in BF_GRID]
    g = lambda key, phi, bf, k: raw[key]["grid"][json.dumps([phi, bf, k])]
    # in-sample: best on all 12 (ties -> closest to physics)
    final = max(cands, key=lambda c: (round(g(*c, None)["score"], 9), -dist(knobs_of(raw, *c))))
    # nested LOEO: per fold, best on its 11 training episodes + background extras
    held, chosen, bg = {}, {}, Counter()
    for k in range(len(truth)):
        ch = max(cands, key=lambda c: (round(g(*c, k)["train_score"], 9), -dist(knobs_of(raw, *c))))
        held[k] = g(*ch, k)
        chosen[truth[k]["id"]] = knobs_of(raw, *ch)
        bg.update(held[k]["bg_extras"])
    bg_avg = {c: bg[c] / len(truth) for c in CLASSES}
    loeo = aggregate(truth, held, bg_avg)
    ins = g(*final, None)
    # links / leaks only, physics thresholds (the guardrail's fallback, and a reference point)
    phys_key = next(k for k, v in raw.items() if not v["knobs"] or all(PHYSICS_T[a] == b for a, b in v["knobs"].items()))
    held_p, bg_p = {}, Counter()
    for k in range(len(truth)):
        held_p[k] = g(phys_key, 0.333, 20.0, k)
        bg_p.update(held_p[k]["bg_extras"])
    loeo_p = aggregate(truth, held_p, {c: bg_p[c] / len(truth) for c in CLASSES})
    ins_p = g(phys_key, 0.333, 20.0, None)
    return {"final": final, "final_knobs": knobs_of(raw, *final), "in_sample": ins, "loeo": loeo, "held": held,
            "chosen": chosen, "bg_avg": bg_avg, "phys_key": phys_key, "in_sample_physT": ins_p, "loeo_physT": loeo_p,
            "held_physT": held_p}


def fmt(x, nd=3):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--data", default="../../data")
    ap.add_argument("--out", default="../../out")
    ap.add_argument("--mp", default=None, help="multipath comparison json (§9.4)")
    ap.add_argument("--real", default=None, help="real-replay scores json (in-sample / LOEO), optional")
    ap.add_argument("--write-config", action="store_true")
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.raw).read_text())
    truth = load_truth(Path(args.data))
    cfg = load_config(None)
    base_yaml = yaml.safe_load((HERE / "config.yaml").read_text())
    phys = physics_links(cfg)
    sel = select(raw, truth)
    key, phi, bf = sel["final"]
    t = sel["final_knobs"]
    ins, lo = sel["in_sample"]["score"], sel["loeo"]["score"]
    guard = ins - lo > 8.0
    if guard:
        t_used = dict(PHYSICS_T)
        key_used, phi_used = sel["phys_key"], 0.333
    else:
        t_used = t
        key_used, phi_used = key, phi
    links = links_for(raw, key_used, phi_used, phys, None)
    lk = raw[key_used]["leaks"][str(phi_used)]
    tc = trained_config(base_yaml, t_used, links, lk["batch"], lk["episode"])
    out = Path(args.out)
    summary = {"final": sel["final"], "final_knobs": t, "used_knobs": t_used, "guardrail_reverted": guard,
               "in_sample": ins, "loeo": lo, "in_sample_physT": sel["in_sample_physT"]["score"],
               "loeo_physT": sel["loeo_physT"]["score"], "chosen_per_fold": sel["chosen"],
               "per_class_in_sample": sel["in_sample"]["per_class"], "per_class_loeo": sel["loeo"]["per_class"],
               "held": {truth[k]["id"]: v for k, v in sel["held"].items()}, "bg_avg": sel["bg_avg"]}
    (out / "calibration_summary.json").write_text(json.dumps(summary, indent=1, default=float))
    if args.write_config:
        hdr = ("# Straight Rows evaluator configuration: TRAINED (Decision v2.1 §9). Generated by tools/calib_report.py.\n"
               "# config.yaml is the physics version; this file differs only in fusion.links (shrunk toward the physics\n"
               "# table, two pseudo-episodes), fusion.leaks / fusion.leaks_episode (dev background rates) and the five\n"
               "# §9.3 knobs. See out/calibration.md.\n")
        (HERE / "config_trained.yaml").write_text(hdr + yaml.safe_dump(tc, sort_keys=False, width=140))
    # ------------------------------------------------------------------ calibration.md
    L = ["# Calibration on the labelled dev episodes (Decision v2.1 §9)", "",
         "Everything below was computed on the development period (2026-05-04/05) only; the evaluation window was never "
         "read. Physics values stay in `config.yaml`; the trained values are in `config_trained.yaml`.", "",
         "## Data split", "",
         f"- Labelled time: each of the 12 episodes' span +- 10 min. Background: WORKING dev batches outside all labelled "
         f"time: {', '.join(f'{tr} {n} batches ({n * 30 / 3600:.1f} h)' for tr, n in raw[key_used]['bg_batches'].items())}.",
         f"- Episode-level leak window: the median labelled episode, {raw[key_used]['win_b']} batches "
         f"({raw[key_used]['win_b'] * 30 / 60:.0f} min), slid one batch at a time over contiguous background.",
         "- Leave-one-episode-out (LOEO): links are re-learned without the held-out episode; the thresholds are chosen per "
         "fold on the other 11 episodes plus background extras, then the held-out episode (match, onset) and the extras "
         "within its +-10 min are scored. Background extras are averaged over the folds' chosen settings. Leaks come from "
         "background only, which never contains labelled time, so every fold sees the same leaks.",
         "- Threshold search: `fusion.bf_alarm` x `episodes.phi_present` on cached episodes (offline re-classification of "
         "each replay; with physics values it reproduces the replay's score exactly: "
         + ", ".join(f"{k} {raw[k]['real_physics']['score']:.1f} vs {raw[k]['offline_physics']['0.333']:.1f}" for k in sorted(raw)[:3])
         + "); `episodes.close_batches`, `position.float_band_m`, `findings.turn_heading_deg` by coordinate descent on "
         "real replays (in that order). The descent path was picked on all 12 episodes; the LOEO choice inside it is per fold.",
         "", "## Leaks: learned vs physics", "",
         f"(k + 1) / (n + 2), clamped to [{LEAK_LO}, {LEAK_HI}]; k = background batches (or windows) with the finding present, "
         "n = those with information. F12 has no per-batch value since Update v2, so it keeps the physics leak.", "",
         "| finding | physics | learned per batch | k / n (batches) | learned per episode | k / n (windows) |", "|---|---|---|---|---|---|"]
    for fid in NET:
        c = lk["counts"].get(fid, {})
        pq = cfg["fusion"]["leak_f9"] if fid == "F9" else cfg["fusion"]["leak_default"]
        L.append(f"| {fid} | {pq} | {fmt(lk['batch'].get(fid))} | {c.get('batch_k', '')} / {c.get('batch_n', '')} | "
                 f"{fmt(lk['episode'].get(fid))} | {c.get('ep_k', '')} / {c.get('ep_n', '')} |")
    L += ["", "## Links: learned vs physics", "",
          "(k + 2 q) / (n + 2) with q the physics value (blank = 0.025). k = labelled episodes of the class with the "
          "episode-level finding present (per-tractor findings count the fraction of the label's tractors). "
          "Cells show physics -> learned (k / n); only cells that moved by >= 0.05 are listed.", "",
          "| class | " + " | ".join(NET) + " |", "|---|" + "---|" * len(NET)]
    st = raw[key_used]["links"][str(phi_used)]["stats"]
    for c in CLASSES:
        row = []
        for fid in NET:
            s = st.get(f"{c}|{fid}")
            if not s:
                row.append("")
                continue
            kk, nn, q, v = s
            row.append(f"{q:g} -> **{v:.2f}** ({kk:g}/{nn})" if abs(v - q) >= 0.05 else f"{v:.2f}")
        L.append(f"| {c} | " + " | ".join(row) + " |")
    L += ["", "Per-label episode-level findings that drove the links (fraction of the label's tractors with the finding present):", ""]
    for lf in raw[key_used]["label_findings"][str(phi_used)]:
        L.append(f"- {lf['id']} {lf['class']}: " + ", ".join(f"{k} {v:g}" for k, v in lf["present"].items() if v > 0))
    L += ["", "## Thresholds", "", "| knob | physics | best on all 12 | used in config_trained.yaml |", "|---|---|---|---|"]
    for k2 in PHYSICS_T:
        L.append(f"| {k2} | {PHYSICS_T[k2]} | {t[k2]} | {t_used[k2]} |")
    L += ["", "Per-fold choice (inner selection on the other 11):", ""]
    for tid, kn in sel["chosen"].items():
        diff = {k2: v for k2, v in kn.items() if abs(float(v) - float(PHYSICS_T[k2])) > 1e-9}
        L.append(f"- {tid}: " + (", ".join(f"{k2} {v}" for k2, v in diff.items()) or "physics values"))
    L += ["", "Coordinate-descent replays (best offline score on all 12 per structural setting):", "",
          "| setting | replay, physics links | best (phi, bf) with trained links | score |", "|---|---|---|---|"]
    for k2 in sorted(raw):
        best = max(((json.loads(g)[:2], v["score"]) for g, v in raw[k2]["grid"].items() if json.loads(g)[2] is None), key=lambda x: x[1])
        L.append(f"| {k2} {raw[k2]['knobs']} | {raw[k2]['real_physics']['score']:.1f} | {best[0]} | {best[1]:.1f} |")
    L += ["", "## Scores (dev, diagnostic score /40 = 25 x macro-F1 + 15 x onset credit)", "",
          f"- **In-sample {ins:.1f}**, **LOEO {lo:.1f}** (gap {ins - lo:.1f}; guardrail at 8 points: "
          + ("**tripped: thresholds reverted to physics**" + (" (the search had already picked the physics values, so "
                 "nothing moved; the gap comes from the links, see the variants below)" if dist(t) == 0 else "")
                 if guard else "not tripped") + ").",
          f"- Learned links/leaks with physics thresholds: in-sample {sel['in_sample_physT']['score']:.1f}, LOEO {sel['loeo_physT']['score']:.1f}.",
          "", "| class | in-sample P | R | F1 | LOEO P | R | F1 |", "|---|---|---|---|---|---|---|"]
    for c in CLASSES:
        a, b = sel["in_sample"]["per_class"][c], sel["loeo"]["per_class"][c]
        L.append(f"| {c} | {a['precision']:.2f} | {a['recall']:.2f} | {a['f1']:.2f} | {b['precision']:.2f} | {b['recall']:.2f} | {b['f1']:.2f} |")
    L += ["", "Held-out results per fold:", ""]
    for k2, h in sel["held"].items():
        L.append(f"- {truth[k2]['id']} {truth[k2]['class']}: " + (f"matched, onset error {h['err_s']:.0f} s" if h["matched"] else "missed")
                 + (f"; extras nearby: {', '.join(h['extras_near'])}" if h["extras_near"] else ""))
    if args.real:
        r = json.loads(Path(args.real).read_text())
        L += ["", "Real replays (not offline):", ""] + [f"- {k2}: {v}" for k2, v in r.items()]
    if args.mp:
        mp = json.loads(Path(args.mp).read_text())
        L += ["", "## Multipath (§9.4)", "",
              f"TRN-11 (T3 05-04 14:04:18) and TRN-12 (T1 05-05 19:00:35) both fall {', '.join(str(int(x['turn_offset_s'])) for x in mp['labels'])} s "
              f"before a north-headland turn, on a min-C/N0 dip that the other two tractors show in the same second. "
              f"Compared against the {mp['n']} dev turn dips (min-C/N0 dip > 3 dB around a turn):", "",
              "| feature | TRN-11 | TRN-12 | turn dips: median (p5..p95) | turn dips outside the labelled range |", "|---|---|---|---|---|"]
        for f2, v in mp["features"].items():
            a, b = mp["labels"]
            nd = ".7g" if f2 == "lat" else ".3g"
            L.append(f"| {f2} | {a[f2]:{nd}} | {b[f2]:{nd}} | {v['median']:{nd}} ({v['p5']:{nd}}..{v['p95']:{nd}}) | {v['outside']:.0%} |")
        bp = mp["pairs"][0]
        L += ["", f"Best pair: {bp[1]} + {bp[2]} separates {bp[0]:.0%} of the turn dips, below the 95% bar, and the "
              "'range' is a min-max box around two points (the latitude box is ~1 m wide). **No F5 gate is added and no "
              "MULTIPATH is exported**: MULTIPATH F1 is 0 either way, and every extra costs precision."]
    (out / "calibration.md").write_text("\n".join(L) + "\n")
    print(json.dumps({k2: summary[k2] for k2 in ("final", "used_knobs", "guardrail_reverted", "in_sample", "loeo",
                                                 "in_sample_physT", "loeo_physT")}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
