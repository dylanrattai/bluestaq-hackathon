"""CONFIDENCE_UPDATE v2.2: `confidence` = P(this detection is class c | an episode opened, these sign groups seen).

Reporting only: nothing here gates, classifies or bounds a detection (the noisy-OR Bayes factors still decide).
Each sign group is one witness (correlated views of one signal count once); rates come from the opened dev
episodes of the v2.1-physics run (`tools/fit_confidence.py` -> `confidence_table.yaml`), read at startup.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "G1": ("C/N0", ("F1", "F2", "F3", "F4", "F5", "F11")),
    "G2": ("solution", ("F6", "F7")),
    "G3": ("position", ("F8",)),
    "G4": ("context", ("F9", "F10")),
    "G5": ("spatial", ("F12",)),
}
SIGNS: dict[str, tuple[str, ...]] = {
    "SPACE_WEATHER": ("F1", "F2", "F3", "F6", "F9"),
    "JAMMING": ("F1", "F2", "F4", "F5", "F10", "F11"),
    "SPOOFING": ("F8", "F12"),
    "EQUIPMENT_FAULT": ("F6", "F7", "F12"),
    "MULTIPATH": ("F5", "F12"),
}
CLASSES = tuple(SIGNS)
LABELS = CLASSES + ("NONE",)
LEAK_TARGET = 0.20                    # shrinkage target for the episode-level leak (the old F9 leak level)
CLIP = (0.02, 0.97)                   # ~20 dev episodes cannot support claims beyond this


def groups_for(cls: str) -> list[str]:
    return [g for g, (_, fids) in GROUPS.items() if set(fids) & set(SIGNS[cls])]


def signs_in(cls: str, g: str) -> list[str]:
    return [f for f in GROUPS[g][1] if f in SIGNS[cls]]


def group_states(cls: str, fds: list[dict[str, dict]]) -> dict[str, dict]:
    """present: one of c's signs in g present on any of the detection's tractors; absent: one evaluable, none
    present; no-info: none evaluable."""
    out = {}
    for g in groups_for(cls):
        seen, evaluable = [], False
        for fid in signs_in(cls, g):
            fs = [fd[fid] for fd in fds if fid in fd and not fd[fid].get("missing", True)]
            if fs:
                evaluable = True
                if any(f.get("present") for f in fs):
                    seen.append(fid)
        out[g] = {"state": "present" if seen else ("absent" if evaluable else "no-info"), "seen": seen}
    return out


# --------------------------------------------------------------------------- table
def q_link(cfg_links: dict, cls: str, g: str) -> float:
    return max(float(cfg_links.get(f, {}).get(cls, 0.0)) for f in signs_in(cls, g))


def fit_table(units: list[dict], cfg_links: dict, exclude: set[str] | None = None) -> dict:
    """units: {"id", "label" (class or NONE), "states": {cls: {g: state}}}; `exclude` = unit ids left out."""
    us = [u for u in units if not exclude or u["id"] not in exclude]
    n_all = len(us)
    tab: dict[str, Any] = {"N": n_all, "classes": {}}
    for c in CLASSES:
        n_c = sum(1 for u in us if u["label"] == c)
        row = {"n_c": n_c, "prior": round((n_c + 1) / (n_all + 6), 6), "groups": {}}
        for g in groups_for(c):
            q = q_link(cfg_links, c, g)
            pos = [u["states"][c][g] for u in us if u["label"] == c and u["states"][c][g] != "no-info"]
            neg = [u["states"][c][g] for u in us if u["label"] != c and u["states"][c][g] != "no-info"]
            k1, n1 = sum(1 for x in pos if x == "present"), len(pos)
            k0, n0 = sum(1 for x in neg if x == "present"), len(neg)
            row["groups"][g] = {"name": GROUPS[g][0], "signs": signs_in(c, g), "q": q, "n1": n1, "k1": k1,
                                "n0": n0, "k0": k0, "p1": round((k1 + 2 * q) / (n1 + 2), 6),
                                "p0": round((k0 + 2 * LEAK_TARGET) / (n0 + 2), 6)}
        tab["classes"][c] = row
    return tab


def table_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_table(cfg: dict, here: Path) -> dict | None:
    name = (cfg.get("confidence") or {}).get("table")
    if not name:
        return None
    p = Path(name)
    p = p if p.is_absolute() else here / p
    if not p.is_file():
        return None
    return yaml.safe_load(p.read_text())


# --------------------------------------------------------------------------- P
def confidence(cls: str, states: dict[str, dict], table: dict) -> tuple[float, dict]:
    """v2.3 (tuned on dev, owner-approved): when the table carries a `detection_prior` the start point is how often a
    dev *detection* was right (the classifier has already chosen c), and the group LRs count with weight
    `lr_weight` (they were fitted against opened episodes, not against the classifier's call). Without it: v2.2."""
    row = table["classes"][cls]
    dp = table.get("detection_prior")
    prior = float(dp["p"]) if dp else float(row["prior"])
    w = float(table.get("lr_weight", 1.0)) if dp else 1.0
    lo = math.log(prior / (1 - prior))
    br = {}
    for g in groups_for(cls):
        st = states.get(g, {"state": "no-info", "seen": []})
        t = row["groups"][g]
        p1, p0 = float(t["p1"]), float(t["p0"])
        lr = p1 / p0 if st["state"] == "present" else ((1 - p1) / (1 - p0) if st["state"] == "absent" else 1.0)
        lr = lr ** w
        lo += math.log(lr)
        br[g] = {"name": GROUPS[g][0], "state": st["state"], "seen": list(st["seen"]), "lr": lr}
    p = 1.0 / (1.0 + math.exp(-lo))
    p = min(max(p, CLIP[0]), CLIP[1])
    return round(p, 2), {"prior": prior, "groups": br, "raw_logodds": lo}


def evidence_line(p: float, br: dict) -> str:
    bits = [f"P={p:.2f} (prior {br['prior']:.2f})"]
    seen = n = 0
    for g, x in br["groups"].items():
        if x["state"] == "no-info":
            bits.append(f"{x['name']} n/a")
            continue
        n += 1
        if x["state"] == "present":
            seen += 1
            bits.append(f"{x['name']} yes [{','.join(x['seen'])}] ×{x['lr']:.1f}")
        else:
            bits.append(f"{x['name']} no ×{x['lr']:.2f}")
    bits.append(f"signs seen {seen} of {n} groups")
    return " | ".join(bits)


# --------------------------------------------------------------------------- v2.4 hypothesis scoring
"""CONFIDENCE v2.4 (owner request): confidence = how well the evidence supports the hypothesis "this is class c".
Start undecided (0 points = P 0.5). Every finding judged over the detection's span and tractors adds points when
it supports c and docks points when it argues against c; findings with no data count 0. Points come from the
fusion link table (P(finding | cause)), so no dev labels are read:

  present, c predicts it strongly (link >= 0.8)              +2   e.g. F1 C/N0 drop for JAMMING
  present, c predicts it sometimes (link 0.5)                +1
  present, c predicts it rarely (link <= 0.025 or none)
          and a rival predicts it (link >= 0.5)              -2   e.g. F9 storm active for EQUIPMENT_FAULT
  present, weak link (0.2) while a rival has >= 0.8          -1
  absent but evaluable, c nearly always shows it (>= 0.95)   -2   e.g. no C/N0 drop for JAMMING
  absent but evaluable, c usually shows it (>= 0.8)          -1
  otherwise                                                   0

Correlated views of one signal (the GROUPS) are capped at +-cap_group points so one channel cannot carry the call.
A sign pointing to a rival that a confirmed, overlapping detection of that rival already covers counts 0 (it is
explained away, e.g. the storm's C/N0 drop under a spoof). A close rival (its Bayes factor >= rival_ratio x ours on the same tractors) docks rival_pts more.
P = sigmoid(scale * points), clipped to clip. With scale 0.5: +2 -> 0.73, +4 -> 0.88, +6 -> 0.95.
"""
HYP_DEFAULTS = {"scale": 0.5, "cap_group": 3, "rival_ratio": 0.5, "rival_pts": 1, "clip": [0.05, 0.95],
                "quorum": 0.5}
FID_ORDER = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12")


def _group_of(fid: str) -> str:
    return next((g for g, (_, fids) in GROUPS.items() if fid in fids), fid)


def finding_points(cls: str, fid: str, present: bool, links: dict, explained: dict | None = None) -> tuple[int, str]:
    """explained: {rival class: alarm id} for confirmed detections of other classes overlapping this one in time and
    tractors. A sign of such a rival is already accounted for, so it does not count against c (explaining away)."""
    row = links.get(fid) or {}
    lc = float(row.get(cls, 0.0))
    rivals = {k: float(v) for k, v in row.items() if k not in (cls, "OTHER")}
    rmax = max(rivals.values(), default=0.0)
    rname = max(rivals, key=rivals.get) if rivals else ""
    if present and lc <= 0.2 and explained:
        by = [explained[k] for k, v in sorted(rivals.items(), key=lambda kv: -kv[1]) if v >= 0.5 and k in explained]
        if by:
            return 0, f"explained by {by[0]}"
    if present:
        if lc >= 0.8:
            return 2, "supports"
        if lc >= 0.5:
            return 1, "supports"
        if lc <= 0.025 and rmax >= 0.5:
            return -2, f"points to {rname}"
        if lc <= 0.2 and rmax >= 0.8:
            return -1, f"leans {rname}"
        return 0, "neutral"
    if lc >= 0.95:
        return -2, "expected, missing"
    if lc >= 0.8:
        return -1, "expected, missing"
    return 0, "neutral"


def hypothesis_score(cls: str, fds: list[dict], links: dict, params: dict | None = None,
                     rival: tuple[str, float] | None = None, extra: list[tuple[str, int]] | None = None,
                     explained: dict | None = None) -> tuple[float, dict]:
    """fds: per-tractor findings ({fid: {present, missing}}). A finding is present when it is present on at least
    `quorum` of the tractors that could evaluate it (so one machine does not decide a farm-wide call); no tractor
    could evaluate it -> no information, 0 points. `rival` = (class, BF ratio rival/ours); `extra` = additional
    (label, points) terms from the caller (e.g. site recurrence)."""
    p = {**HYP_DEFAULTS, **(params or {})}
    items, by_group = [], {}
    for fid in FID_ORDER:
        if fid not in links:
            continue
        fs = [fd[fid] for fd in fds if fid in fd and not fd[fid].get("missing", True)]
        if not fs:
            continue
        present = sum(1 for f in fs if f.get("present")) >= p["quorum"] * len(fs)
        pts, why = finding_points(cls, fid, present, links, explained)
        if why.startswith("explained"):
            items.append({"fid": fid, "present": present, "pts": 0, "why": why})
        if pts:
            items.append({"fid": fid, "present": present, "pts": pts, "why": why})
            by_group.setdefault(_group_of(fid), []).append(pts)
    total = 0
    for g, v in by_group.items():
        total += max(-p["cap_group"], min(p["cap_group"], sum(v)))
    if rival and rival[1] >= p["rival_ratio"]:
        items.append({"fid": "rival", "pts": -p["rival_pts"], "why": f"{rival[0]} nearly as likely (BF ratio {rival[1]:.2f})"})
        total -= p["rival_pts"]
    for lab, pts in extra or []:
        items.append({"fid": lab, "pts": pts, "why": lab})
        total += pts
    pr = 1.0 / (1.0 + math.exp(-p["scale"] * total))
    pr = min(max(pr, p["clip"][0]), p["clip"][1])
    return round(pr, 2), {"points": total, "items": items}


def hypothesis_line(p: float, br: dict) -> str:
    pro = [f"{x['fid']} +{x['pts']}" for x in br["items"] if x["pts"] > 0]
    con = [f"{x['fid']} {x['pts']} ({x['why']})" for x in br["items"] if x["pts"] < 0]
    exp = [f"{x['fid']} ({x['why']})" for x in br["items"] if x["pts"] == 0]
    return (f"P={p:.2f} from {br['points']:+d} points (0 = undecided)"
            + f" | for: {', '.join(pro) or 'none'} | against: {', '.join(con) or 'none'}"
            + (f" | not counted: {', '.join(exp)}" if exp else ""))
