import { useEffect, useMemo, useState } from 'react'
import type { Farm } from '../useFarm'
import { BLOCKS, REFS } from '../content/flow'
import { buildScenarios } from '../content/scenarios'
import { LinkTable, NetworkSandbox, NetworkView } from '../components/NetworkPanel'
import { Limits } from './Limits'
import { CLS_COLOR, dayTime, fmtBf, hhmm, FINDING_LABEL } from '../format'
import type { AlarmState } from '../types'

const TABS = ['Data flow', 'Walkthroughs', 'Live engine', 'Limits & research'] as const

function tabFromHash(): number {
  const m = location.hash.match(/tab=(\d)/)
  return m ? Math.min(Math.max(Number(m[1]) - 1, 0), TABS.length - 1) : 0
}

export function Extended({ farm }: { farm: Farm }) {
  const [tab, setTab] = useState(tabFromHash)
  const [block, setBlock] = useState('episodes')
  const scenarios = useMemo(() => (farm.replay ? buildScenarios(farm.replay) : []), [farm.replay])
  const [sc, setSc] = useState(0)
  const [step, setStep] = useState(0)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input,select,textarea')) return
      const k = e.key.toLowerCase()
      if ('1234'.includes(k) && k) { setTab(Number(k) - 1); return }
      const qi = ['q', 'w', 'e'].indexOf(k)
      if (qi >= 0 && qi < scenarios.length) { setTab(1); setSc(qi); setStep(0); return }
      if (tab !== 1 || !scenarios[sc]) return
      const n = scenarios[sc].steps.length
      if (k === 'arrowright' || k === ' ') { e.preventDefault(); setStep((s) => Math.min(s + 1, n - 1)) }
      if (k === 'arrowleft') { e.preventDefault(); setStep((s) => Math.max(s - 1, 0)) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab, sc, scenarios])

  return (
    <main className="ext">
      <nav className="tabs">
        {TABS.map((t, i) => <button key={t} className={tab === i ? 'on' : ''} onClick={() => setTab(i)}><kbd>{i + 1}</kbd> {t}</button>)}
        <span className="muted small keys">Q/W/E scenario · ←/→ or space step</span>
      </nav>
      {tab === 0 && <DataFlow block={block} setBlock={setBlock} farm={farm} />}
      {tab === 1 && <Walkthroughs farm={farm} scenarios={scenarios} sc={sc} setSc={(i) => { setSc(i); setStep(0) }} step={step} setStep={setStep} />}
      {tab === 2 && <Engine farm={farm} />}
      {tab === 3 && <Limits meta={farm.meta} replay={farm.replay} />}
    </main>
  )
}

function DataFlow({ block, setBlock, farm }: { block: string; setBlock: (b: string) => void; farm: Farm }) {
  const b = BLOCKS.find((x) => x.id === block)!
  const lanes = [['in', 'Ingest'], ['detect', 'Detect'], ['decide', 'Decide'], ['out', 'Report']] as const
  return (
    <div className="flow">
      <div className="flow-lanes">
        {lanes.map(([l, name]) => (
          <div key={l} className="lane">
            <div className="lane-h">{name}</div>
            {BLOCKS.filter((x) => x.lane === l).map((x) => (
              <button key={x.id} className={`blk ${x.id === block ? 'on' : ''} ${x.id === 'v23' ? 'blk-warn' : ''}`} onClick={() => setBlock(x.id)}>
                <b>{x.title}</b><span>{x.sub}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      <div className="panel blk-detail">
        <h2>{b.title}</h2>
        <h3>What</h3><p>{b.what}</p>
        <h3>Why</h3><p>{b.why}</p>
        <h3>How</h3><ul>{b.how.map((h, i) => <li key={i}>{h}</li>)}</ul>
        {b.id === 'network' && farm.meta && <><h3>Links and leaks ({farm.meta.config ?? 'engine config'})</h3><LinkTable fu={farm.meta.fusion} /></>}
        <h3>Research</h3>
        <ul className="refs">{b.refs.map((r) => { const [t, u] = REFS[r] ?? [r, '']; return <li key={r}><span className="mono">{r}</span> {u ? <a href={u} target="_blank" rel="noreferrer">{t}</a> : t}</li> })}</ul>
      </div>
    </div>
  )
}

function Walkthroughs({ farm, scenarios, sc, setSc, step, setStep }: {
  farm: Farm; scenarios: ReturnType<typeof buildScenarios>; sc: number; setSc: (i: number) => void; step: number; setStep: (i: number) => void
}) {
  if (!farm.replay) return <p className="muted">Loading the recorded run…</p>
  const s = scenarios[sc]
  if (!s) return <p className="muted">The recorded run has none of the expected episodes. Re-generate the snapshot with <code>python -m runtime.webui</code>.</p>
  const st = s.steps[step]
  return (
    <div className="walk">
      <div className="walk-pick">
        {scenarios.map((x, i) => (
          <button key={x.key} className={i === sc ? 'on' : ''} onClick={() => setSc(i)}><kbd>{x.key.toUpperCase()}</kbd> <b>{x.title}</b><span>{x.summary}</span></button>
        ))}
        <p className="muted small">Steps are generated from the logged events in out/alarm_log.jsonl. Only the narration is written by hand.</p>
      </div>
      <div className="panel walk-step">
        <div className="steps">{s.steps.map((_, i) => <button key={i} className={i === step ? 'on' : i < step ? 'done' : ''} onClick={() => setStep(i)}>{i + 1}</button>)}</div>
        <div className="muted mono">{dayTime(st.t, true)}</div>
        <h2>{st.title}</h2>
        {st.body.map((p, i) => <p key={i}>{p}</p>)}
        {st.net && farm.meta && <NetworkView fu={farm.meta.fusion} input={st.net.input} label={st.net.label} />}
        {st.row && (
          <div className="row-json">
            <pre>{JSON.stringify(st.row, null, 2)}</pre>
          </div>
        )}
        <div className="walk-nav">
          <button disabled={step === 0} onClick={() => setStep(step - 1)}>← back</button>
          <button disabled={step === s.steps.length - 1} onClick={() => setStep(step + 1)}>next →</button>
        </div>
      </div>
    </div>
  )
}

const STATES: AlarmState[] = ['WATCH', 'ALARM', 'CONFIRMED', 'RETRACTED']

function Engine({ farm }: { farm: Farm }) {
  const { status, alarms, watchdog, feed, meta, mode } = farm
  const [states, setStates] = useState<Set<AlarmState>>(new Set(STATES))
  const [withSite, setWithSite] = useState(false)
  const list = alarms.filter((a) => states.has(a.state) && (withSite || !a.suppressed)).slice(-60).reverse()
  return (
    <div className="engine">
      <section className="panel">
        <h2>Per-tractor network (latest batch)</h2>
        {mode !== 'live' ? <p className="muted">Per-batch findings and Bayes factors are only in LIVE mode; the recorded run does not store them. Start the evaluator (<code>docker compose up evaluator webui</code>).</p> : (
          <table className="eng-t">
            <thead><tr><th>tractor</th><th>gate</th><th>present (severity)</th><th>no information</th><th>explanation</th><th>top BF</th></tr></thead>
            <tbody>{status?.tractors.map((t) => (
              <tr key={t.id}>
                <td>{t.id}</td><td>{t.working ? 'WORKING' : 'PARKED'}</td>
                <td>{Object.entries(t.present ?? {}).map(([f, s]) => <span key={f} className="chip-f" title={FINDING_LABEL[f]}>{f} {s}</span>)}</td>
                <td className="muted small">{t.missing?.join(' ')}</td>
                <td>{t.explanation?.join(' + ') || '∅'}</td>
                <td className="mono small">{t.top_bf?.map((b) => `${b.cause} ${fmtBf(b.bf)}`).join(' · ')}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {status?.open_episodes && status.open_episodes.length > 0 && (
          <><h3>Open episodes</h3><ul>{status.open_episodes.map((e) => <li key={e.episode} className="mono small">{e.episode} {e.track} since {hhmm(e.start)} {e.tractors.join(',')} provisional {e.provisional ?? '—'}</li>)}</ul></>
        )}
      </section>

      <section className="panel">
        <h2>All alarm states</h2>
        <div className="filters">
          {STATES.map((s) => <label key={s}><input type="checkbox" checked={states.has(s)} onChange={() => { const n = new Set(states); if (n.has(s)) n.delete(s); else n.add(s); setStates(n) }} /> {s}</label>)}
          <label><input type="checkbox" checked={withSite} onChange={(e) => setWithSite(e.target.checked)} /> include suppressed site multipath</label>
        </div>
        <table className="eng-t">
          <thead><tr><th>id</th><th>class</th><th>state</th><th>start</th><th>end</th><th>tractors</th><th>P</th><th>reason</th></tr></thead>
          <tbody>{list.map((a) => (
            <tr key={a.id}><td className="mono small">{a.id}</td><td style={{ color: CLS_COLOR[a.cls] }}>{a.cls}</td><td className={`st-${a.state}`}>{a.state}</td>
              <td className="mono small">{dayTime(a.start, true)}</td><td className="mono small">{a.end ? hhmm(a.end, true) : 'open'}</td>
              <td>{a.tractors.join(',')}</td><td className="mono">{a.confidence.toFixed(2)}</td><td className="small">{a.reason ?? ''}</td></tr>
          ))}</tbody>
        </table>
      </section>

      <section className="panel">
        <h2>Watchdog runs <span className="muted">every {meta ? meta.watchdog_every_s / 60 : 5} min of data time</span></h2>
        <ul className="hist">{watchdog.slice(-12).reverse().map((w, i) => (
          <li key={i} className="mono small">{dayTime(w.t_data)} {w.final ? 'FINAL ' : ''}touched {w.n_touched}{w.actions.length ? ` · ${w.actions.slice(0, 3).join('; ')}` : ''}</li>
        ))}</ul>
        <h2>Feed</h2>
        <ul className="hist">{feed.slice(-25).reverse().map((e, i) => (
          <li key={i} className="mono small">{dayTime(e.t_data_iso, true)} {e.kind === 'watchdog' ? `WATCHDOG touched ${e.n_touched ?? 0}` : `${e.alarm_id} ${e.from ?? '∅'}→${e.to} ${e.reason} ${e.note ?? ''}`}</li>
        ))}</ul>
      </section>

      {meta && <section className="panel"><h2>Network sandbox <span className="muted">engine tables from {meta.config ?? 'the engine config'}; click a finding to cycle absent → present → no information</span></h2><NetworkSandbox fu={meta.fusion} /></section>}
    </div>
  )
}
