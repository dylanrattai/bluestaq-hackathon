"""ACCURACY_UPDATE v2.3 P3: a jammer switching on inside a storm (a second, flat step on an already depressed floor).

On the 1-min mean C/N0 of working tractors: ref = trailing 20-min median. A JAMMING sub-episode opens when ref <= 40
dB-Hz (the floor is already depressed) and ref - now >= 6 dB on >= 2 tractors for >= 3 consecutive minutes; the onset
backtracks to the first such minute and each tractor's ref is frozen there. It closes when every member is back
within 3 dB of its frozen ref for 2 minutes. It is exported as its own JAMMING detection; the storm stays (concurrent
causes). Causal: each minute is judged when it has ended.
"""
from __future__ import annotations

import numpy as np

from core.log import hms
from core.srtypes import AlarmState


class StormJam:
    def __init__(self, eng):
        self.eng = eng
        c = eng.cfg.get("storm_jam") or {}
        self.enabled = bool(c.get("enabled", True))
        self.ref_min = int(c.get("ref_minutes", 20))
        self.floor = float(c.get("ref_max_dbhz", 40.0))
        self.step_db = float(c.get("step_db", 6.0))
        self.n_tr = int(c.get("min_tractors", 2))
        self.n_min = int(c.get("min_minutes", 3))
        self.back = float(c.get("recover_db", 3.0))
        self.back_min = int(c.get("recover_minutes", 2))
        self.per_min = int(60 // eng.batch_s)
        self.x: dict[str, list[float]] = {tr: [] for tr in eng.tractors}
        self.t: list[float] = []
        self.streak: list[tuple[int, set[str]]] = []
        self.open: dict | None = None
        self.events: list[dict] = []

    def _minute(self, tr: str, b_last: int) -> float:
        f = self.eng.feats[tr]
        bs = range(b_last - self.per_min + 1, b_last + 1)
        if not all(self.eng.working_hist[tr][b] for b in bs):
            return float("nan")
        v = [f.cn0_mean[b] for b in bs if not np.isnan(f.cn0_mean[b])]
        return float(np.mean(v)) if v else float("nan")

    def step(self, b: int, now: float) -> None:
        if not self.enabled or (b + 1) % self.per_min:
            return
        m = len(self.t)
        self.t.append(float(self.eng.t0s[b - self.per_min + 1]))
        for tr in self.eng.tractors:
            self.x[tr].append(self._minute(tr, b))
        if self.open is None:
            hit = set()
            for tr in self.eng.tractors:
                hist = [v for v in self.x[tr][max(0, m - self.ref_min):m] if not np.isnan(v)]
                if len(hist) < self.ref_min // 2 or np.isnan(self.x[tr][m]):
                    continue
                ref = float(np.median(hist))
                if ref <= self.floor and ref - self.x[tr][m] >= self.step_db:
                    hit.add(tr)
            self.streak = self.streak + [(m, hit)] if len(hit) >= self.n_tr else []
            if len(self.streak) >= self.n_min:
                m0 = self.streak[0][0]
                trs = set().union(*(h for _, h in self.streak))
                refs = {}
                for tr in self.eng.tractors:
                    hist = [v for v in self.x[tr][max(0, m0 - self.ref_min):m0] if not np.isnan(v)]
                    if hist:
                        refs[tr] = float(np.median(hist))
                self.open = {"m0": m0, "start": self.t[m0], "trs": trs, "refs": refs, "ok": 0, "alarm": None, "depth": {}}
                self._raise(now)
                self.streak = []
            return
        ev = self.open
        for tr, ref in ev["refs"].items():           # late joiners: the same step against the frozen reference
            x = self.x[tr][m]
            if tr not in ev["trs"] and not np.isnan(x) and ref <= self.floor and ref - x >= self.step_db:
                ev["trs"].add(tr)
                ev["alarm"].tractors.add(tr)
        back = all(np.isnan(self.x[tr][m]) or abs(ev["refs"][tr] - self.x[tr][m]) <= self.back for tr in ev["trs"])
        for tr in ev["trs"]:
            x = self.x[tr][m]
            if not np.isnan(x):
                ev["depth"][tr] = max(ev["depth"].get(tr, 0.0), ev["refs"][tr] - x)
        ev["ok"] = ev["ok"] + 1 if back else 0
        if ev["ok"] >= self.back_min:
            end = self.t[m - self.back_min + 1]
            a = ev["alarm"]
            a.end = a.raw_end = end
            a.active = set()
            a.extra["storm_jam"] = {"refs": ev["refs"], "depth": ev["depth"]}
            a.extra["ev"] = self._evidence(ev, end)
            self.events.append({"start": ev["start"], "end": end, "tractors": sorted(ev["trs"])})
            self.eng.lifecycle.event(a, now, a.state.value, a.state, "CLOSED", a.bf, note="storm-jam step recovered")
            self.open = None

    def _raise(self, now: float) -> None:
        ev = self.open
        a = self.eng.lifecycle.create("JAMMING", now, ev["start"], set(ev["trs"]), AlarmState.ALARM, None,
                                      note="stacked step on a depressed (storm) floor", track="stormjam")
        a.bf = 0.0
        ev["alarm"] = a

    def _evidence(self, ev: dict, end: float) -> list[str]:
        trs = sorted(ev["trs"])
        parts = ", ".join(f"{tr} {ev['refs'][tr]:.1f} -> {ev['refs'][tr] - ev['depth'].get(tr, 0):.1f} dB-Hz" for tr in trs)
        return [f"{hms(ev['start'])[:-1]}–{hms(end)}: second, flat step of 1-min mean C/N0 on an already depressed floor "
                f"(20-min reference <= {self.floor:g} dB-Hz): {parts}",
                f"step >= {self.step_db:g} dB on {len(trs)} tractors at once for >= {self.n_min} min: a jammer switching on inside "
                f"the storm, not scintillation (which does not step); the storm detection stays (concurrent causes)",
                f"ended when every tractor was back within {self.back:g} dB of its pre-step reference for {self.back_min} min"]
