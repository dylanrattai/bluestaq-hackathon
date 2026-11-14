// Port of diagnose/fusion.py (two-layer leaky noisy-OR, exact 2^6 cause-set enumeration) so the UI can show
// Bayes factors computed from the engine's own tables (served by /webui/meta from config.yaml).
// Per-tractor, per-batch network only: the engine's farm-level decision sums several tractors' log-likelihoods
// and uses episode-level findings, so its logged BFs are larger than one tractor's.
import type { Fusion } from './types'

export const FINDINGS = ['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'] as const
export type FindingState = 'present' | 'absent' | 'missing'
export interface FindingInput { state: FindingState; severity: number }

export interface NetResult {
  posterior: Record<string, number>
  bf: Record<string, number>
  explanation: string[]          // most probable non-empty cause set
}

export function evaluate(fu: Fusion, input: Record<string, FindingInput>): NetResult {
  const C = fu.causes, nc = C.length
  const leak = (f: string) => fu.leaks?.[f] ?? (f === 'F9' ? fu.leak_f9 : fu.leak_default)
  const link = (f: string, c: string) => fu.links[f]?.[c] ?? 0
  const sets: number[][] = []
  for (let m = 0; m < 1 << nc; m++) sets.push(C.map((_, i) => (m >> (nc - 1 - i)) & 1))   // itertools.product order
  const lw = sets.map((H) => {
    const k = H.reduce((a, b) => a + b, 0)
    let l = k * Math.log(fu.prior) + (nc - k) * Math.log(1 - fu.prior)
    for (const f of FINDINGS) {
      const x = input[f]
      if (!x || x.state === 'missing') continue
      const scale = x.state === 'present' ? fu.floor + (1 - fu.floor) * Math.min(Math.max(x.severity, 0), 1) : 1
      let logNone = Math.log1p(-leak(f))
      H.forEach((h, i) => { if (h) logNone += Math.log1p(-link(f, C[i]) * scale) })
      l += x.state === 'present' ? Math.log(-Math.expm1(logNone)) : logNone
    }
    return l
  })
  const mx = Math.max(...lw)
  const w = lw.map((v) => Math.exp(v - mx))
  const tot = w.reduce((a, b) => a + b, 0)
  const priorOdds = fu.prior / (1 - fu.prior)
  const posterior: Record<string, number> = {}, bf: Record<string, number> = {}
  C.forEach((c, i) => {
    const p = sets.reduce((a, H, j) => a + (H[i] ? w[j] : 0), 0) / tot
    posterior[c] = p
    const q = Math.min(Math.max(p, 1e-15), 1 - 1e-15)
    bf[c] = q / (1 - q) / priorOdds
  })
  let best = -1
  sets.forEach((H, j) => { if (H.some(Boolean) && (best < 0 || lw[j] > lw[best])) best = j })
  return { posterior, bf, explanation: C.filter((_, i) => sets[best][i]) }
}

export const level = (bf: number, fu: Fusion) => (bf >= fu.bf_alarm ? 'ALARM' : bf >= fu.bf_watch ? 'WATCH' : 'none')
