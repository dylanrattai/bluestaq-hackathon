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
