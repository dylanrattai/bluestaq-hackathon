import { useMemo, useState } from 'react'
import type { Fusion } from '../types'
import { evaluate, FINDINGS, type FindingInput, type FindingState, type NetResult } from '../noisyor'
import { CLS_LABEL, CV, FINDING_LABEL, fmtBf, STATE_C } from '../format'

const NEXT: Record<FindingState, FindingState> = { absent: 'present', present: 'missing', missing: 'absent' }

export function FindingChips({ input, onCycle }: { input: Record<string, FindingInput>; onCycle?: (f: string) => void }) {
  return (
    <div className="finds">
      {FINDINGS.map((f) => {
        const x = input[f]
        const cls = x?.state === 'present' ? 'p' : x?.state === 'absent' ? 'a' : ''
        const txt = `${x?.state === 'present' ? '● ' : x?.state === 'missing' ? '? ' : ''}${f} ${FINDING_LABEL[f]}${x?.state === 'present' ? ` L${Math.round(x.severity * 4)}` : ''}`
        return onCycle
          ? <button key={f} className={`fd ${cls}`} onClick={() => onCycle(f)} title="absent → present → no information">{txt}</button>
          : <span key={f} className={`fd ${cls}`}>{txt}</span>
      })}
    </div>
  )
}

export function Posterior({ fu, r }: { fu: Fusion; r: NetResult }) {
  return (
    <>
      {fu.causes.map((c) => (
        <div className="post" key={c}>
          <span style={{ color: CV[c] }}>{CLS_LABEL[c] ?? c}</span>
          <div className="b"><i style={{ width: `${(r.posterior[c] * 100).toFixed(1)}%`, background: CV[c] }} /></div>
          <span style={{ textAlign: 'right' }}>BF {fmtBf(r.bf[c])}</span>
        </div>
      ))}
      <div className="label" style={{ marginTop: 8 }}>Most probable non-empty set: <span style={{ color: 'var(--bone)' }}>{'{'}{r.explanation.join(', ')}{'}'}</span></div>
    </>
  )
}

const pos = (v: number) => Math.max(0, Math.min(100, ((Math.log10(Math.max(v, 1e-9)) + 1) / 6) * 100))   // 0.1 … 100 000
export function BfGauge({ bf, fu }: { bf: number | null; fu: Fusion }) {
  return (
    <div className="gauge">
      <div className="tr" style={{ background: `linear-gradient(90deg,#2b2925 0 ${pos(fu.bf_watch)}%,rgba(217,164,65,.35) ${pos(fu.bf_watch)}% ${pos(fu.bf_alarm)}%,rgba(229,83,75,.45) ${pos(fu.bf_alarm)}%)` }} />
      {([[0.1, '0.1'], [1, '1'], [fu.bf_watch, `WATCH ${fu.bf_watch}`], [fu.bf_alarm, `ALARM ${fu.bf_alarm}`], [1000, '1000'], [1e5, '10⁵']] as [number, string][]).map(([v, l]) => (
        <div key={l} className="mk" style={{ left: `${pos(v)}%` }}><span>{l}</span></div>
      ))}
      {bf != null && <div className="pt" style={{ left: `${pos(bf)}%` }} />}
    </div>
  )
}

export function Badge({ state, cls }: { state: string; cls?: string }) {
  const c = STATE_C[state] ?? '#9a9182'
  return <span className="badge" style={{ color: c, borderColor: `${c}77`, background: `${c}14` }}>{state}{cls && state !== 'NORMAL' && state !== 'WATCH' ? ` · ${cls}` : ''}</span>
}

const PRESETS: Record<string, Record<string, number>> = {
  'Jamming (unequal)': { F1: 0.5, F4: 0.5 },
  'Space weather': { F1: 0.5, F2: 0.5, F3: 0.5, F9: 0.75 },
  'Spoofing': { F8: 0.75, F12: 1 },
  'Equipment (link)': { F7: 0.5, F12: 1 },
  'Short dip': { F5: 0.5, F12: 1 },
  'Lone C/N0 drop': { F1: 0.5 },
}

export function NetworkSandbox({ fu }: { fu: Fusion }) {
  const init = () => Object.fromEntries(FINDINGS.map((f) => [f, { state: f === 'F11' ? 'missing' : 'absent', severity: 0.5 } as FindingInput]))
  const [inp, setInp] = useState<Record<string, FindingInput>>(init)
  const r = useMemo(() => evaluate(fu, inp), [fu, inp])
  const lead = fu.causes.reduce((a, c) => (r.bf[c] > r.bf[a] ? c : a), fu.causes[0])
  const preset = (p: Record<string, number>) => {
    const b = init()
    for (const [f, s] of Object.entries(p)) b[f] = { state: 'present', severity: s }
    setInp(b)
  }
  return (
    <div className="sandbox">
      <div>
        <div className="label">Findings · click to cycle absent → present → no information</div>
        <div className="presets">{Object.entries(PRESETS).map(([k, p]) => <button key={k} className="fd" onClick={() => preset(p)}>{k}</button>)}</div>
        <FindingChips input={inp} onCycle={(f) => setInp({ ...inp, [f]: { ...inp[f], state: NEXT[inp[f].state] } })} />
        <div className="presets">
          {FINDINGS.filter((f) => inp[f].state === 'present').map((f) => (
            <label key={f} className="fd p">{f} severity{' '}
              <select value={inp[f].severity} onChange={(e) => setInp({ ...inp, [f]: { ...inp[f], severity: Number(e.target.value) } })}>
                {[0, 0.25, 0.5, 0.75, 1].map((s) => <option key={s} value={s}>L{s * 4}</option>)}
              </select>
            </label>
          ))}
        </div>
      </div>
      <div>
        <div className="label">Noisy-OR posterior · P(cause | findings)</div>
        <Posterior fu={fu} r={r} />
        <div className="label" style={{ marginTop: 14 }}>Bayes factor · {CLS_LABEL[lead] ?? lead}</div>
        <BfGauge bf={r.bf[lead]} fu={fu} />
        <div className="note">Exact 2⁶ = 64 cause-set enumeration with the engine's own links and leaks. Single tractor, per-batch: farm-level decisions sum tractors, so the engine's logged BFs are larger.</div>
      </div>
    </div>
  )
}

export function LinkTable({ fu }: { fu: Fusion }) {
  const leak = (f: string) => fu.leaks?.[f] ?? (f === 'F9' ? fu.leak_f9 : fu.leak_default)
  return (
    <table className="links">
      <thead><tr><th /><th>leak</th>{fu.causes.map((c) => <th key={c} style={{ color: CV[c] }}>{(CLS_LABEL[c] ?? c).split(' ')[0]}</th>)}</tr></thead>
      <tbody>{FINDINGS.filter((f) => fu.links[f]).map((f) => (
        <tr key={f}><td style={{ color: 'var(--muted)' }}>{f}</td><td className="dim">{leak(f)}</td>
          {fu.causes.map((c) => { const p = fu.links[f][c]; return <td key={c} style={{ color: p ? 'var(--bone)' : 'var(--dim)', opacity: p ? 0.4 + p * 0.6 : 0.5 }}>{p ?? '·'}</td> })}</tr>
      ))}</tbody>
    </table>
  )
}
