import { useMemo, useState } from 'react'
import type { Fusion } from '../types'
import { evaluate, FINDINGS, level, type FindingInput, type FindingState } from '../noisyor'
import { CLS_COLOR, CLS_LABEL, FINDING_LABEL, fmtBf } from '../format'

const NEXT: Record<FindingState, FindingState> = { absent: 'present', present: 'missing', missing: 'absent' }

export function NetworkView({ fu, input, label }: { fu: Fusion; input: Record<string, FindingInput>; label?: string }) {
  const r = useMemo(() => evaluate(fu, input), [fu, input])
  const max = Math.log10(Math.max(...Object.values(r.bf), fu.bf_alarm * 10))
  return (
    <div className="net">
      {label && <div className="muted small">{label}</div>}
      {fu.causes.map((c) => {
        const bf = r.bf[c]
        const w = Math.max(0, Math.log10(Math.max(bf, 1)) / max) * 100
        const lv = r.explanation.includes(c) ? level(bf, fu) : bf >= fu.bf_watch ? 'not in explanation' : 'none'
        return (
          <div key={c} className="net-row">
            <span className="net-c">{CLS_LABEL[c] ?? c}</span>
            <div className="net-bar">
              <div style={{ width: `${w}%`, background: CLS_COLOR[c] }} />
              <i style={{ left: `${(Math.log10(fu.bf_watch) / max) * 100}%` }} title={`WATCH BF ${fu.bf_watch}`} />
              <i style={{ left: `${(Math.log10(fu.bf_alarm) / max) * 100}%` }} title={`ALARM BF ${fu.bf_alarm}`} />
            </div>
            <span className="mono net-v">BF {fmtBf(bf)}</span>
            <span className={`tag lv-${lv.replace(/ /g, '-')}`}>{lv}</span>
          </div>
        )
      })}
      <div className="muted small">Explanation (most probable non-empty set): {r.explanation.join(' + ') || '∅'}.
        Single tractor, per-batch leaks from {fu.leaks && Object.keys(fu.leaks).length ? 'config' : 'config defaults'}. The engine's farm-level decisions sum tractors, so its logged BFs are larger.</div>
    </div>
  )
}

const PRESETS: Record<string, Record<string, number>> = {
  'Jamming (unequal drop)': { F1: 0.5, F4: 0.5 },
  'Space weather': { F1: 0.5, F2: 0.5, F3: 0.5, F9: 0.75 },
  'Spoofing': { F8: 0.75, F12: 1 },
  'Equipment (link)': { F7: 0.5, F12: 1 },
  'Short dip': { F5: 0.5, F12: 1 },
  'Lone C/N0 drop': { F1: 0.5 },
}

export function NetworkSandbox({ fu }: { fu: Fusion }) {
  const init = () => Object.fromEntries(FINDINGS.map((f) => [f, { state: f === 'F11' ? 'missing' : 'absent', severity: 0.5 } as FindingInput]))
  const [inp, setInp] = useState<Record<string, FindingInput>>(init)
  const preset = (p: Record<string, number>) => {
    const b = init()
    for (const [f, s] of Object.entries(p)) b[f] = { state: 'present', severity: s }
    setInp(b)
  }
  return (
    <div className="sandbox">
      <div className="presets">{Object.entries(PRESETS).map(([k, p]) => <button key={k} onClick={() => preset(p)}>{k}</button>)}</div>
      <div className="sandbox-grid">
        <table className="findings">
          <tbody>
            {FINDINGS.map((f) => (
              <tr key={f}>
                <td className="mono">{f}</td>
                <td className="small">{FINDING_LABEL[f]}</td>
                <td><button className={`fs fs-${inp[f].state}`} onClick={() => setInp({ ...inp, [f]: { ...inp[f], state: NEXT[inp[f].state] } })}>{inp[f].state}</button></td>
                <td>{inp[f].state === 'present' && (
                  <select value={inp[f].severity} onChange={(e) => setInp({ ...inp, [f]: { ...inp[f], severity: Number(e.target.value) } })}>
                    {[0, 0.25, 0.5, 0.75, 1].map((s) => <option key={s} value={s}>L{s * 4}</option>)}
                  </select>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <NetworkView fu={fu} input={inp} />
      </div>
    </div>
  )
}

export function LinkTable({ fu }: { fu: Fusion }) {
  const leak = (f: string) => fu.leaks?.[f] ?? (f === 'F9' ? fu.leak_f9 : fu.leak_default)
  return (
    <table className="links">
      <thead><tr><th /><th>leak</th>{fu.causes.map((c) => <th key={c}>{CLS_LABEL[c] ?? c}</th>)}</tr></thead>
      <tbody>{FINDINGS.filter((f) => fu.links[f]).map((f) => (
        <tr key={f}><td className="mono">{f}</td><td className="mono muted">{leak(f)}</td>
          {fu.causes.map((c) => { const p = fu.links[f][c]; return <td key={c} className="mono" style={{ opacity: p ? 0.35 + p * 0.65 : 0.25 }}>{p ?? '·'}</td> })}</tr>
      ))}</tbody>
    </table>
  )
}
