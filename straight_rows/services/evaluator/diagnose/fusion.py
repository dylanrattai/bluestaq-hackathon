"""Two-layer leaky noisy-OR diagnosis network, exact 64-set enumeration (spec §7.5) [R22-R27]."""
from __future__ import annotations

import itertools
import math
from typing import Any, Sequence

import numpy as np

from core.srtypes import FINDING_IDS, EvaluationResult


class Fusion:
    def __init__(self, cfg: dict[str, Any]):
        fu = cfg["fusion"]
        self.causes: list[str] = list(fu["causes"])
        self.findings: list[str] = list(FINDING_IDS)
        self.nc, self.nf = len(self.causes), len(self.findings)
        self.prior = float(fu["prior"])
        self.floor = float(fu["floor"])
        self.bf_watch = float(fu["bf_watch"])
        self.bf_alarm = float(fu["bf_alarm"])
        self.leak = np.full(self.nf, float(fu["leak_default"]))
        self.leak[self.findings.index("F9")] = float(fu["leak_f9"])
        self.P = np.zeros((self.nf, self.nc))
        for fid, row in fu["links"].items():
            for c, p in row.items():
                self.P[self.findings.index(fid), self.causes.index(c)] = float(p)
        # all 2^nc cause sets
        self.sets = list(itertools.product([0, 1], repeat=self.nc))
        self.empty = tuple([0] * self.nc)
        self.M = np.asarray(self.sets, dtype=float)                      # (64, nc)
        lp, l1p = math.log(self.prior), math.log(1 - self.prior)
        self.log_prior = self.M.sum(1) * lp + (self.nc - self.M.sum(1)) * l1p
        self.log1m_leak = np.log1p(-self.leak)
        # absent-finding log-likelihood per set: log(1-q_j) + sum_{c in H} log(1-p_cj)
        self.ll_absent = self.log1m_leak[:, None] + (np.log1p(-self.P) @ self.M.T)   # (nf, 64)
        self.prior_odds = self.prior / (1 - self.prior)

    def set_members(self, idx: int) -> frozenset[str]:
        return frozenset(c for c, m in zip(self.causes, self.sets[idx]) if m)

    def loglik(self, present: Sequence[bool], severity: Sequence[float], missing: Sequence[bool]) -> np.ndarray:
        """Log-likelihood of the finding vector for every cause set. Missing findings are skipped."""
        ll = np.zeros(len(self.sets))
        for j in range(self.nf):
            if missing[j]:
                continue
            if present[j]:
                s = min(max(float(severity[j]), 0.0), 1.0)
                pj = self.P[j] * (self.floor + (1 - self.floor) * s)
                log_none = self.log1m_leak[j] + (np.log1p(-pj) @ self.M.T)   # log P(absent | H) with p'
                ll += np.log(-np.expm1(log_none))                            # log(1 - exp(log_none))
            else:
                ll += self.ll_absent[j]
        return ll

    def evaluate(self, tractor: str, t0: float, present: Sequence[bool], severity: Sequence[float],
                 missing: Sequence[bool]) -> EvaluationResult:
        ll = self.loglik(present, severity, missing)
        lw = self.log_prior + ll
        m = lw.max()
        w = np.exp(lw - m)
        w /= w.sum()
        marg = self.M.T @ w                                                  # (nc,)
        post = {c: float(min(max(marg[i], 0.0), 1.0)) for i, c in enumerate(self.causes)}
        bf = {}
        for c, p in post.items():
            p = min(max(p, 1e-15), 1 - 1e-15)
            bf[c] = float((p / (1 - p)) / self.prior_odds)
        imap = int(np.argmax(lw))
        order = np.argsort(-lw)
        # best non-empty explanation: with a 1% prior the empty set stays the literal MAP until a posterior
        # passes 0.5, so attribution (which cause explains the findings) uses the most probable non-empty set
        # and strength uses the Bayes factor (spec §7.5 thresholds)
        nonempty = [i for i in order if self.sets[i] != self.empty]
        iexp = int(nonempty[0]) if nonempty else imap
        second = self.set_members(int(order[1])) if len(order) > 1 else frozenset()
        reasons = {"map_logw": f"{lw[imap]:.3f}", "second": ",".join(sorted(second)) or "{}",
                   "second_logw": f"{lw[order[1]]:.3f}", "explanation_logw": f"{lw[iexp]:.3f}"}
        res = EvaluationResult(tractor, t0, post, bf, self.set_members(imap), reasons)
        res.explanation = self.set_members(iexp)
        return res

    def vector(self, fmap: dict[str, Any]) -> tuple[list[bool], list[float], list[bool]]:
        """Build (present, severity, missing) lists from {fid: Finding}."""
        pres, sevs, miss = [], [], []
        for fid in self.findings:
            f = fmap.get(fid)
            if f is None:
                pres.append(False); sevs.append(0.0); miss.append(True)
            else:
                pres.append(bool(f.present)); sevs.append(float(f.severity)); miss.append(bool(f.missing))
        return pres, sevs, miss

    # direct (non-log) computation for tests
    def direct_posterior(self, present, severity, missing) -> dict[str, float]:
        ws = []
        for H in self.sets:
            p = 1.0
            for i, m in enumerate(H):
                p *= self.prior if m else (1 - self.prior)
            for j in range(self.nf):
                if missing[j]:
                    continue
                prod = 1 - self.leak[j]
                for i, m in enumerate(H):
                    if m:
                        pj = self.P[j, i]
                        if present[j]:
                            pj = pj * (self.floor + (1 - self.floor) * severity[j])
                        prod *= (1 - pj)
                p *= (1 - prod) if present[j] else prod
            ws.append(p)
        tot = sum(ws)
        return {c: sum(w for w, H in zip(ws, self.sets) if H[i]) / tot for i, c in enumerate(self.causes)}
