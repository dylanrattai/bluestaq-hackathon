"""Replay loop: grid -> findings -> fusion -> lifecycle -> watchdog (spec §5)."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from detect.baseline import siegmund_arl0, solve_h
from diagnose.evidence import build_evidence
from runtime.export import build_submission, resolve_validator, write_submission
from diagnose.fusion import Fusion
from ingest.grid import Gate, compute_features
from detect.inputs import cn0, cross_track, fix, fleet, link, s4
from detect.inputs.ctx import Ctx
from diagnose.lifecycle import Lifecycle
from ingest.load import SdaFeeds, Telemetry, load_sda, load_telemetry
from core.log import EventSink, iso
from detect.scales import Ladders
from core.srtypes import FINDING_IDS, AlarmState
from diagnose.watchdog import Watchdog

log = logging.getLogger("sr.engine")
HERE = Path(__file__).resolve().parent.parent   # the evaluator directory


class Engine:
    def __init__(self, cfg: dict[str, Any], data_dir: str | Path | None, out_dir: str | Path | None,
                 speed: float | None = None, validator: str | None = None, sink: EventSink | None = None,
                 tele: Telemetry | None = None, sda: SdaFeeds | None = None, write_outputs: bool = True,
                 mode: str = "batch"):
        self.cfg = cfg
        self.data_dir = Path(data_dir) if data_dir else None
        self.out_dir = Path(out_dir) if out_dir else None
        self.speed = speed                      # data seconds per wall second; None = max
        self.mode = mode
        self.write_outputs = write_outputs
        self.sink = sink or EventSink(self.out_dir if write_outputs else None)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.done = False
        self.error: str | None = None
        self.submission_path: Path | None = None
        self.validator = resolve_validator(cfg, validator, HERE)

        self.batch_s = int(cfg["grid"]["batch_s"])
        self.wd_every = int(cfg["grid"]["watchdog_every_batches"])
        arl0 = float(cfg["cusum"]["arl0_hours"]) * 3600 / self.batch_s
        self.h = solve_h(arl0, float(cfg["cusum"]["k"]))
        log.info("CUSUM design: k=%.2f ARL0=%d batches -> h=%.3f (Siegmund; check h=5 -> ARL0 %.0f)",
                 cfg["cusum"]["k"], arl0, self.h, siegmund_arl0(5.0, float(cfg["cusum"]["k"])))

        self.tele = tele if tele is not None else load_telemetry(self.data_dir, cfg)
        self.sda = sda if sda is not None else load_sda(self.data_dir, cfg)
        self.tractors = list(self.tele.tractors)
        self.feats = {tr: compute_features(self.tele, tr, cfg) for tr in self.tractors}
        f0 = self.feats[self.tractors[0]]
        self.nb = f0.nb
        self.t0s = f0.t0
        self.ladders = Ladders(cfg)
        self.fusion = Fusion(cfg)
        self.gate = Gate(cfg, self.tractors)
        self.lifecycle = Lifecycle(cfg, self.tractors, self.sink)
        self.watchdog = Watchdog(self)
        self.ctx = Ctx(cfg, self.ladders, self.h, self.tele, self.tractors, self.feats, self.sda)
        lat = np.nanmean(np.concatenate([self.tele.series[t].lat for t in self.tractors]))
        lon = np.nanmean(np.concatenate([self.tele.series[t].lon for t in self.tractors]))
        self.ctx.farm_lat, self.ctx.farm_lon = float(lat), float(lon)
        nf = len(FINDING_IDS)
        self.fv_present = {tr: np.zeros((self.nb, nf), dtype=bool) for tr in self.tractors}
        self.fv_sev = {tr: np.zeros((self.nb, nf), dtype=float) for tr in self.tractors}
        self.fv_missing = {tr: np.ones((self.nb, nf), dtype=bool) for tr in self.tractors}
        self.working_hist = {tr: np.zeros(self.nb, dtype=bool) for tr in self.tractors}
        self.post_hist = {tr: np.zeros((self.nb, len(self.fusion.causes)), dtype=np.float32) for tr in self.tractors}
        self.b_done = 0
        self.now = float(self.t0s[0])
        self.last_results: dict[str, Any] = {}
        self.last_findings: dict[str, dict[str, Any]] = {}
        self.wall_start: float | None = None
        self.started = time.time()

    # ------------------------------------------------------------------ batch
    def _pace(self, now: float) -> None:
        if self.speed is None or self.speed <= 0:
            return
        if self.wall_start is None:
            self.wall_start = time.time()
            self.data_start = now
            return
        target = self.wall_start + (now - self.data_start) / self.speed
        delay = target - time.time()
        while delay > 0 and not self.stop_event.is_set():
            time.sleep(min(delay, 0.25))
            delay = target - time.time()

    def step(self, b: int) -> None:
        ctx = self.ctx
        t0 = float(self.t0s[b])
        now = t0 + self.batch_s
        ctx.b, ctx.t0, ctx.now = b, t0, now
        ctx.out = {}
        # working gate (holds while fix_rank >= 3 or F8 active on the previous batch)
        for tr in self.tractors:
            f8_active = ctx.prev_present.get(("F8", tr), False)
            st = self.gate.update(tr, b, self.feats[tr], f8_active)
            ctx.working[tr] = st == "WORKING"
            self.working_hist[tr][b] = ctx.working[tr]
        # findings
        cn0.findings(ctx)
        link.findings(ctx)
        fix.findings(ctx)
        cross_track.findings(ctx)
        s4.findings(ctx)
        fleet.findings(ctx)
        findings = {tr: {fid: ctx.out[(fid, tr)] for fid in FINDING_IDS if (fid, tr) in ctx.out} for tr in self.tractors}
        # fusion
        results = {}
        for tr in self.tractors:
            pres, sevs, miss = self.fusion.vector(findings[tr])
            self.fv_present[tr][b] = pres
            self.fv_sev[tr][b] = sevs
            self.fv_missing[tr][b] = miss
            r = self.fusion.evaluate(tr, t0, pres, sevs, miss)
            results[tr] = r
            self.post_hist[tr][b] = [r.posterior[c] for c in self.fusion.causes]
        # lifecycle
        self.lifecycle.step(b, t0, now, results, findings, ctx.working)
        ctx.prev_present = {k: v.present for k, v in ctx.out.items()}
        self.last_results = results
        self.last_findings = findings
        self.now = now
        self.b_done = b + 1

    def run(self) -> None:
        try:
            log.info("replay: %d batches of %ds from %s to %s, mode=%s speed=%s", self.nb, self.batch_s,
                     iso(self.t0s[0]), iso(self.t0s[-1] + self.batch_s), self.mode,
                     "max" if self.speed is None else self.speed)
            for b in range(self.nb):
                if self.stop_event.is_set():
                    log.warning("stopped at batch %d", b)
                    break
                self._pace(float(self.t0s[b]) + self.batch_s)
                with self.lock:
                    self.step(b)
                    if (b + 1) % self.wd_every == 0:
                        self.watchdog.run(self.now)
            with self.lock:
                self.finish()
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            log.exception("engine failed")
            raise
        finally:
            self.done = True

    def finish(self) -> None:
        t_end = float(self.t0s[self.b_done - 1]) + self.batch_s if self.b_done else self.now
        self.lifecycle.close_all(t_end)
        self.watchdog.run(t_end, final=True)
        self.build_all_evidence(t_end)
        n_conf = sum(1 for a in self.lifecycle.alarms.values() if a.state == AlarmState.CONFIRMED)
        log.info("final: %d alarms, %d confirmed, %.1f s wall", len(self.lifecycle.alarms), n_conf, time.time() - self.started)
        if self.write_outputs and self.out_dir:
            sub = build_submission(list(self.lifecycle.alarms.values()), self.cfg, t_end)
            self.submission_path = write_submission(sub, self.out_dir, self.cfg, self.validator,
                                                    allow_empty=bool(self.cfg.get("_allow_empty")))

    def build_all_evidence(self, now: float) -> None:
        for a in self.lifecycle.alarms.values():
            if a.records:
                try:
                    a.evidence = build_evidence(a, self.feats, self.tele, self.sda, self.ladders, self.cfg, now)
                except Exception as e:  # noqa: BLE001
                    log.warning("evidence for %s failed: %s", a.id, e)
                    a.evidence = [f"{a.cls} {a.id} {iso(a.start)}..{iso(a.span_end(now))} on {','.join(sorted(a.tractors))}",
                                  "evidence builder failed; see alarm history"]

    # ------------------------------------------------------------------ read side (API)
    def snapshot(self) -> dict:
        with self.lock:
            prog = self.b_done / self.nb if self.nb else 0.0
            per = {}
            for tr in self.tractors:
                r = self.last_results.get(tr)
                fm = self.last_findings.get(tr, {})
                present = {fid: round(f.severity, 3) for fid, f in fm.items() if f.present}
                missing = [fid for fid, f in fm.items() if f.missing]
                top = sorted(r.bayes_factor.items(), key=lambda kv: -kv[1])[:3] if r else []
                per[tr] = {"working": bool(self.ctx.working.get(tr, False)), "present": present, "missing": missing,
                           "explanation": sorted(r.explanation) if r else [], "map_set": sorted(r.map_set) if r else [],
                           "top_bf": [{"cause": c, "bf": round(v, 2), "posterior": round(r.posterior[c], 3)} for c, v in top]}
            wd = self.watchdog.runs[-1].as_dict() if self.watchdog.runs else None
            return {"mode": self.mode, "data_time": iso(self.now), "progress": round(prog, 4),
                    "speed": "max" if self.speed is None else self.speed, "batches_done": self.b_done, "batches": self.nb,
                    "done": self.done, "error": self.error, "last_watchdog": wd, "tractors": per,
                    "open_alarms": [a.summary(self.now) for a in self.lifecycle.alarms.values() if a.open],
                    "n_alarms": len(self.lifecycle.alarms)}

    def alarms(self, state=None, cls=None, tractor=None) -> list[dict]:
        with self.lock:
            return [a.summary(self.now) for a in self.lifecycle.list(state, cls, tractor)]

    def alarm(self, aid: str) -> dict | None:
        with self.lock:
            a = self.lifecycle.alarms.get(aid)
            if a is None:
                return None
            if not a.evidence and a.records:
                try:
                    a.evidence = build_evidence(a, self.feats, self.tele, self.sda, self.ladders, self.cfg, self.now)
                except Exception:  # noqa: BLE001
                    pass
            return a.full()

    def watchdog_runs(self, n: int = 20) -> list[dict]:
        with self.lock:
            return [r.as_dict() for r in self.watchdog.runs[-n:]]

    def submission_now(self) -> dict:
        with self.lock:
            if not self.done:
                for a in self.lifecycle.alarms.values():
                    if a.state == AlarmState.CONFIRMED and a.records:
                        a.evidence = build_evidence(a, self.feats, self.tele, self.sda, self.ladders, self.cfg, self.now)
            return build_submission(list(self.lifecycle.alarms.values()), self.cfg, self.now)
