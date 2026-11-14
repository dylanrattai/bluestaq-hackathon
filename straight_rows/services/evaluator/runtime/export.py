"""submission.json builder and compliance checks (spec §4)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.config import placeholders_unchanged
from diagnose.lifecycle import Alarm
from core.log import iso
from core.srtypes import ACTIONS, AlarmState

log = logging.getLogger("sr.export")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
STUDENT_ID_RE = re.compile(r"\b(?:student[_ -]?id|ndsu[_ -]?id)\b", re.I)
SCHEMA_KEYS = ("start", "end", "tractors", "class", "action", "confidence", "evidence")


class ExportError(RuntimeError):
    pass


def build_detections(alarms: list[Alarm], cfg: dict[str, Any], now: float) -> list[dict]:
    w0, w1 = float(cfg["window"]["start_s"]), float(cfg["window"]["end_s"])
    dets = []
    for a in alarms:
        if a.state != AlarmState.CONFIRMED or a.cls not in ACTIONS or a.unattributed:
            continue
        start = float(a.start)
        end = float(a.span_end(now))
        if not (w0 <= start < w1):
            if start < w0 <= end:
                start = w0          # clip; evidence says when it really began
            else:
                continue
        end = min(end, w1)
        start = float(round(start))
        end = float(round(end))
        if end <= start:
            log.warning("dropping %s: end <= start after clipping", a.id)
            continue
        if not a.tractors:
            log.warning("dropping %s: no tractors", a.id)
            continue
        conf = min(max(float(a.confidence), 0.0), 1.0)
        ev = [str(s) for s in a.evidence if str(s).strip()][:5]
        if len(ev) < 2:
            ev = (ev + [f"{a.cls} alarm {a.id} on {','.join(sorted(a.tractors))} {iso(start)}..{iso(end)}"])[:5]
        dets.append({
            "start": iso(start), "end": iso(end), "tractors": sorted(set(a.tractors)), "class": a.cls,
            "action": ACTIONS[a.cls], "confidence": round(conf, 3), "evidence": ev,
        })
    dets.sort(key=lambda d: (d["start"], d["class"], ",".join(d["tractors"])))
    return dets


def build_submission(alarms: list[Alarm], cfg: dict[str, Any], now: float) -> dict:
    part = {"github_handle": str(cfg["participant"]["github_handle"])}
    dn = cfg["participant"].get("display_name")
    if dn:
        part["display_name"] = str(dn)
    return {"participant": part, "repository_url": str(cfg["repository_url"]),
            "detections": build_detections(alarms, cfg, now)}


def check_submission(sub: dict, cfg: dict[str, Any]) -> list[str]:
    problems = []
    w0, w1 = cfg["window"]["start"], cfg["window"]["end"]
    text = json.dumps(sub)
    if EMAIL_RE.search(text):
        problems.append("an email address appears in the submission")
    if STUDENT_ID_RE.search(text):
        problems.append("a student-ID field appears in the submission")
    for i, d in enumerate(sub.get("detections", [])):
        if tuple(sorted(d.keys())) != tuple(sorted(SCHEMA_KEYS)):
            problems.append(f"detections[{i}] has unexpected fields {sorted(set(d) ^ set(SCHEMA_KEYS))}")
        if d["class"] not in ACTIONS or ACTIONS[d["class"]] != d["action"]:
            problems.append(f"detections[{i}] class/action mismatch")
        if not (0.0 <= d["confidence"] <= 1.0):
            problems.append(f"detections[{i}] confidence out of range")
        if not (2 <= len(d["evidence"]) <= 5):
            problems.append(f"detections[{i}] needs 2-5 evidence strings")
        if not d["tractors"] or len(set(d["tractors"])) != len(d["tractors"]):
            problems.append(f"detections[{i}] tractors must be non-empty and distinct")
        if not (w0 <= d["start"] < w1) or not (d["start"] < d["end"] <= w1):
            problems.append(f"detections[{i}] outside the window or end <= start")
    return problems


def resolve_validator(cfg: dict[str, Any], override: str | None, here: Path) -> Path | None:
    cands = []
    if override:
        cands.append(Path(override))
    cands.append((here / str(cfg.get("validator_path", ""))).resolve())
    cands.append(Path("/submission/validate_submission.py"))
    cands.append(here / "submission" / "validate_submission.py")
    for c in cands:
        if c.is_file():
            return c
    return None


def run_validator(validator: Path, path: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-I", str(validator), str(path)], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def write_submission(sub: dict, out_dir: Path, cfg: dict[str, Any], validator: Path | None,
                     allow_empty: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "submission.json"
    if placeholders_unchanged(cfg):
        log.error("[bold red]participant.github_handle / repository_url are still placeholders in config.yaml "
                  "(ASK DYLAN): the validator will reject the file until they are set[/bold red]")
    problems = check_submission(sub, cfg)
    if not sub["detections"]:
        msg = "no CONFIRMED detections in the evaluation window"
        if allow_empty:
            log.error("[bold red]%s: writing an empty detections list (the validator will reject it)[/bold red]", msg)
        else:
            problems.append(msg)
    if problems:
        for p in problems:
            log.error("[bold red]submission check failed: %s[/bold red]", p)
        raise ExportError("; ".join(problems))
    with open(path, "w") as f:
        json.dump(sub, f, indent=1)
        f.write("\n")
    log.info("wrote %s with %d detection(s)", path, len(sub["detections"]))
    if validator is None:
        log.error("[bold red]validator not found (set validator_path or --validator); skipping the validator run[/bold red]")
        return path
    code, text = run_validator(validator, path)
    if code != 0:
        log.error("[bold red]validator failed (%s):\n%s[/bold red]", validator, text)
        if placeholders_unchanged(cfg) and "github" in text.lower() and sub["detections"]:
            log.error("[bold red]the only failures above are the placeholder participant fields: fix config.yaml[/bold red]")
        raise ExportError("validator failed: " + text)
    log.info("validator: %s", text)
    return path
