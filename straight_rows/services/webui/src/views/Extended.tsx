import { useEffect, useMemo, useState } from 'react'
import type { Farm } from '../useFarm'
import type { AlarmState, Fusion, HistoryEvent } from '../types'
import { BLOCKS, REFS } from '../content/flow'
import { buildScenarios, fmtLog, STAGE_NAME, STAGES, type Scenario, type Step } from '../content/scenarios'
import { Badge, BfGauge, FindingChips, LinkTable, NetworkSandbox, Posterior } from '../components/NetworkPanel'
import { Research } from './Limits'
import { ConfidenceExplain } from '../components/Confidence'
import type { Alarm } from '../types'
import { Fragment } from 'react'
import { evaluate } from '../noisyor'
import { CLS_LABEL, CV, dayTime, fmtBf, hhmm, STATE_C, TRACTOR_COLOR } from '../format'
import { ms, samples, trackIndex, type Replay } from '../demo'

/* ============================== pipeline diagram ============================== */
const NW = 200, NH = 64
const POS: Record<string, { x: number; y: number; n: string; t: string; s: string }> = {
  ingest: { x: 20, y: 150, n: '01', t: 'Telemetry + SDA', s: '1 Hz × 3 · Kp Dst S4 RFI' },
  grid: { x: 245, y: 60, n: '02', t: '30 s grid + gate', s: 'WORKING / PARKED' },
  cusum: { x: 245, y: 240, n: '03', t: 'Reference + CUSUM', s: 'k 0.5 · h ≈ 6.80' },
  findings: { x: 470, y: 150, n: '04', t: 'Findings F1–F12', s: 'present · absent · n/a' },
  episodes: { x: 695, y: 40, n: '05', t: 'Episodes first', s: 'signal · position · dip' },
  network: { x: 695, y: 150, n: '06', t: 'Noisy-OR diagnosis', s: '64 cause sets · BF 3/20' },
  v23: { x: 695, y: 260, n: '07', t: 'v2.3 rules', s: 'site MP · storm-jam ⚠' },
  lifecycle: { x: 920, y: 0, n: '08', t: 'Live alarms', s: 'WATCH → ALARM' },
  watchdog: { x: 920, y: 100, n: '09', t: 'Watchdog · 5 min', s: 'refine · merge · confirm' },
  confidence: { x: 920, y: 200, n: '10', t: 'Confidence', s: 'reporting only' },
  outputs: { x: 920, y: 300, n: '11', t: 'API + submission', s: '/status /alarms · json' },
}
const EDGES: [string, string][] = [['ingest', 'grid'], ['grid', 'cusum'], ['grid', 'findings'], ['cusum', 'findings'], ['findings', 'episodes'],
  ['episodes', 'network'], ['findings', 'v23'], ['network', 'lifecycle'], ['v23', 'lifecycle'], ['lifecycle', 'watchdog'], ['watchdog', 'confidence'], ['confidence', 'outputs']]
function edgePath(a: string, b: string) {
  const A = POS[a], B = POS[b]
  if (A.x === B.x) { const x = A.x + NW / 2; return `M${x},${A.y + NH} L${x},${B.y}` }
  const x1 = A.x + NW, y1 = A.y + NH / 2, x2 = B.x, y2 = B.y + NH / 2, mx = (x1 + x2) / 2
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`
}
function Pipeline({ sel, onPick }: { sel: string; onPick: (id: string) => void }) {
  return (
    <svg className="pipe" viewBox="-6 -10 1132 404" style={{ width: '100%', display: 'block' }}>
      {[['INPUTS', 20], ['WHEN · DETECT', 245], ['SIGNS', 470], ['WHAT · DECIDE', 695], ['REAL? · REPORT', 920]].map(([l, x]) =>
        <text key={l} className="grp" x={x} y={390}>{l}</text>)}
      {EDGES.map(([a, b]) => <g key={a + b}><path className="edge" d={edgePath(a, b)} /><path className="flow" d={edgePath(a, b)} /></g>)}
      {Object.entries(POS).map(([id, n]) => (
        <g key={id} className={`node ${sel === id ? 'sel' : ''} ${id === 'v23' ? 'warn' : ''}`} transform={`translate(${n.x},${n.y})`} onClick={() => onPick(id)}>
          <rect width={NW} height={NH} rx={12} />
          <text className="n" x={14} y={20}>{n.n}</text><text className="t" x={38} y={21}>{n.t}</text>
          <text className="s" x={14} y={46}>{n.s}</text>
        </g>
      ))}
    </svg>
  )
}

function DataFlow({ farm }: { farm: Farm }) {
  const [sel, setSel] = useState('network')
  const b = BLOCKS.find((x) => x.id === sel)!
  return (
    <section>
      <div className="label">From satellite signal to a decision</div>
      <h2>One pipeline, three questions: <span style={{ color: 'var(--gold)' }}>when did something change, what caused it, was it real?</span></h2>
      <p className="lede">Telemetry runs left to right. Change detectors decide <em>when</em> something changed, episodes and the noisy-OR network decide <em>what</em> caused it, and the watchdog decides whether it was <em>real</em> before anything is exported. The dashed block holds the dev-specific v2.3 rules. Click any block.</p>
      <div className="flowgrid">
        <div className="panel" style={{ padding: 14 }}><Pipeline sel={sel} onPick={setSel} /></div>
        <div className="panel detail">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
            <div className="label">Stage {POS[sel].n}</div><h3>{b.title}</h3>
            <span className="cite mono dim" style={{ marginLeft: 'auto', fontSize: 12 }}>{b.refs.map((r) => REFS[r]?.[0].split(',')[0]).filter(Boolean).slice(0, 3).join(' · ')}</span>
          </div>
          <div className="cols">
            <div><div className="k">WHAT</div><p>{b.what}</p></div>
            <div><div className="k">WHY</div><p>{b.why}</p></div>
            <div><div className="k">HOW</div><ul>{b.how.map((h, i) => <li key={i}>{h}</li>)}</ul></div>
          </div>
          {sel === 'network' && farm.meta && <><div className="k">LINKS AND LEAKS · LIVE FROM THE ENGINE CONFIG</div><LinkTable fu={farm.meta.fusion} /></>}
          <div className="k">RESEARCH</div>
          <ul className="refs">{b.refs.map((r) => { const [t, u] = REFS[r] ?? [r, '']; return <li key={r}><span className="mono">{r}</span>{u ? <a href={u} target="_blank" rel="noreferrer">{t}</a> : t}</li> })}</ul>
        </div>
      </div>
    </section>
  )
}

/* ============================== walkthrough ============================== */
function Chart({ s, step, k, replay }: { s: Scenario; step: Step; k: number; replay: Replay }) {
  const spans = step.spans ?? (step.span ? [{ id: '', start: step.span.start, end: step.span.end, cls: step.cls ?? '', badge: step.badge }] : [])
  const W = 900, H = 320, L = 46, R = 12, TP = 28 + Math.max(0, spans.length - 1) * 22, B = 44
  const a = ms(s.t0), b = ms(s.t1), now = ms(step.t)
  const i0 = trackIndex(replay, a), i1 = trackIndex(replay, b)
  const ids = replay.snap.meta.tractors
  const data = useMemo(() => Object.fromEntries(ids.map((id) => {
    const xs = samples(replay, id), pts: [number, number][] = []
    for (let i = i0; i <= i1; i++) {
      const v = s.channel === 'xte' ? (xs[i].xte != null ? Math.abs(xs[i].xte!) : null) : xs[i].cn0
      if (v != null) pts.push([replay.trackT0 + i * replay.trackStep, v])
    }
    return [id, pts]
  })), [replay, ids, i0, i1, s.channel])
  const all = Object.values(data).flat().map((p) => p[1])
  const [y0, y1] = s.channel === 'xte' ? [0, Math.max(1, Math.ceil(Math.max(...all, 0) * 1.1))] : [15, 50]
  const X = (t: number) => L + ((t - a) / (b - a)) * (W - L - R)
  const Y = (v: number) => TP + (1 - (Math.max(y0, Math.min(y1, v)) - y0) / (y1 - y0)) * (H - TP - B)
  const span = (b - a) / 60e3, stepMin = span > 300 ? 60 : span > 90 ? 15 : 10
  const sites = replay.snap.events.filter((e) => e.kind === 'alarm' && (e.note ?? '').includes('site multipath') && ms(e.t_data_iso) >= a && ms(e.t_data_iso) <= b)
  return (
    <svg viewBox={`0 0 ${W} ${H}`}>
      {[0, 1, 2, 3, 4, 5].map((i) => { const v = y0 + ((y1 - y0) * i) / 5, y = Y(v); return <g key={i}><line x1={L} x2={W - R} y1={y} y2={y} stroke="rgba(237,230,214,.06)" /><text x={L - 8} y={y + 4} textAnchor="end" fill="#6b645a" fontSize={10.5} fontFamily="ui-monospace,monospace">{+v.toFixed(1)}</text></g> })}
      {Array.from({ length: Math.floor(span / stepMin) + 1 }, (_, i) => Math.ceil(a / (stepMin * 60e3)) * stepMin * 60e3 + i * stepMin * 60e3).filter((t) => t <= b).map((t) =>
        <text key={t} x={X(t)} y={H - 6} textAnchor="middle" fill="#6b645a" fontSize={10.5} fontFamily="ui-monospace,monospace">{new Date(t).toISOString().slice(11, 16)}</text>)}
      {span <= 240 && Array.from({ length: Math.floor(span / 5) + 1 }, (_, i) => Math.ceil(a / 300e3) * 300e3 + i * 300e3).filter((t) => t <= b).map((t) =>
        <path key={t} d={`M${X(t)},${H - B + 4} l5,5 l-5,5 l-5,-5z`} fill={t <= now ? '#d9a441' : 'none'} stroke="#d9a441" strokeOpacity={0.6} />)}
      {sites.map((e, i) => <line key={i} x1={X(ms(e.t_data_iso))} x2={X(ms(e.t_data_iso))} y1={H - B - 8} y2={H - B} stroke="#8b8579" strokeWidth={1.2} opacity={ms(e.t_data_iso) <= now ? 0.9 : 0.25} />)}
      {spans.map((sp, i) => {
        const o = ms(sp.start), c = sp.end ? Math.min(ms(sp.end), b) : now
        const col = sp.badge === 'RETRACTED' ? '#8b8579' : CV[sp.cls] ?? '#d9a441'
        const lbl = sp.badge + (sp.badge !== 'WATCH' && sp.cls ? ` · ${sp.cls}` : '') + (sp.badge === 'RETRACTED' || sp.badge === 'WATCH' ? ` (${sp.cls === 'SPACE_WEATHER' ? 'sub-episode' : sp.cls})` : '')
        const ly = 4 + i * 22
        return <g key={i}>
          <rect x={X(o)} y={TP} width={Math.max(3, X(c) - X(o))} height={H - TP - B} fill={col} fillOpacity={sp.badge === 'WATCH' ? 0.07 : 0.12}
            stroke={sp.badge === 'RETRACTED' || sp.badge === 'WATCH' ? col : 'none'} strokeDasharray="4 4" />
          <line x1={X(o)} x2={X(o)} y1={ly + 18} y2={TP} stroke={col} strokeOpacity={0.6} />
          <rect x={X(o)} y={ly} width={lbl.length * 6.7 + 16} height={18} rx={5} fill={sp.badge === 'RETRACTED' ? '#8b8579' : sp.badge === 'WATCH' ? STATE_C.WATCH : col} fillOpacity={0.92} />
          <text x={X(o) + 7} y={ly + 13} fill="#0a0a09" fontSize={10.5} fontWeight={800} fontFamily="ui-monospace,monospace">{lbl}</text>
        </g>
      })}
      {s.steps.slice(0, k + 1).filter((x) => x.mark).map((x, i) => { const xx = X(ms(x.mark![0])); return <g key={i}><line x1={xx} x2={xx} y1={TP} y2={H - B} stroke="rgba(237,230,214,.3)" strokeDasharray="2 4" /><text x={xx + 4} y={H - B - 12} fill="#9a9182" fontSize={10} fontFamily="ui-monospace,monospace">{x.mark![1]}</text></g> })}
      {[...ids].reverse().map((id) => {
        const pl = (arr: [number, number][]) => arr.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ')
        const past = data[id].filter((p) => p[0] <= now), fut = data[id].filter((p) => p[0] >= now), last = past.at(-1)
        return <g key={id}>
          <polyline points={pl(fut)} fill="none" stroke={TRACTOR_COLOR[id]} strokeOpacity={0.13} strokeWidth={1.4} />
          <polyline points={pl(past)} fill="none" stroke={TRACTOR_COLOR[id]} strokeWidth={1.8} />
          {last && <><circle cx={X(last[0])} cy={Y(last[1])} r={3.5} fill={TRACTOR_COLOR[id]} /><text x={X(last[0]) + 7} y={Y(last[1]) + 4} fill={TRACTOR_COLOR[id]} fontSize={11} fontWeight={700} fontFamily="ui-monospace,monospace">{id}</text></>}
        </g>
      })}
      <line x1={X(now)} x2={X(now)} y1={TP - 4} y2={H - B} stroke="#d9a441" strokeWidth={1.5} />
    </svg>
  )
}

function Walkthrough({ farm, sc, setSc, k: kRaw, setK, scenarios }: { farm: Farm; sc: number; setSc: (i: number) => void; k: number; setK: (i: number) => void; scenarios: Scenario[] }) {
  const replay = farm.replay, fu = farm.meta?.fusion
  if (!replay || !fu) return <p className="lede">Loading the recorded run…</p>
  const s = scenarios[sc]
  const k = s ? Math.min(kRaw, s.steps.length - 1) : 0
  if (!s) return <p className="lede">The recorded run lacks the expected episodes; regenerate it with <code>python -m runtime.webui</code>.</p>
  const step = s.steps[k]
  const net = step.net ?? [...s.steps.slice(0, k)].reverse().find((x) => x.net)?.net
  const r = net ? evaluate(fu as Fusion, net.input) : null
  const lead = step.cls && step.cls !== 'OTHER' ? step.cls : r ? fu.causes.reduce((a, c) => (r.bf[c] > r.bf[a] ? c : a), fu.causes[0]) : null
  const bf = step.bf ?? (r && lead ? r.bf[lead] : null)
  const order = STAGES as readonly string[]
  const maxI = Math.max(...step.stages.map((x) => order.indexOf(x)))
  const logs = replay.snap.events.filter((e) => e.kind === 'alarm' && s.logIds.includes(e.alarm_id ?? '') && ms(e.t_data_iso) <= ms(step.t)) as unknown as (HistoryEvent & { alarm_id: string })[]
  const go = (d: number) => { if (k + d >= s.steps.length) { if (sc < scenarios.length - 1) setSc(sc + 1) } else if (k + d >= 0) setK(k + d) }
  return (
    <section>
      <div className="scen">{scenarios.map((x, i) => (
        <button key={x.key} className={i === sc ? 'on' : ''} onClick={() => setSc(i)}><span className="num">{x.key.toUpperCase()}</span>
          <span><div className="tt">{x.title}</div><div className="oc" style={{ color: x.oc }}>{x.outcome}</div></span></button>
      ))}</div>
      <div className="wgrid">
        <div>
          <div className="panel narr">
            <div className="stage"><b>{step.stages.map((x) => STAGE_NAME[x]).join(' · ')}</b><span className="label">data time {dayTime(step.t, true)}</span></div>
            <h3>{step.title}</h3>
            {step.body.map((p, i) => <p key={i}>{p}</p>)}
            {step.row && <pre>{Array.isArray(step.row) ? `// the rows the engine builds for these detections (dev period, so not in submission.json)\n${JSON.stringify(step.row, null, 2)}` : `// submission.json (real exported row)\n${JSON.stringify(step.row, null, 2)}`}</pre>}
            <div className="ctl">
              <button disabled={k === 0} onClick={() => go(-1)}>← Back</button>
              <button className="pri" onClick={() => go(1)}>{k === s.steps.length - 1 ? (sc < scenarios.length - 1 ? 'Next scenario →' : 'Done') : 'Next →'}</button>
              <div className="dots">{s.steps.map((_, i) => <i key={i} className={i === k ? 'on' : i < k ? 'done' : ''} onClick={() => setK(i)} />)}</div>
              <span className="spacer" /><span className="label">step {k + 1} / {s.steps.length}</span>
            </div>
          </div>
          <div className="panel chart">
            <div className="label" style={{ margin: '4px 0 6px' }}>{s.chLabel} · recorded telemetry · ◆ watchdog pass · grey ticks = site-multipath reports</div>
            <Chart s={s} step={step} k={k} replay={replay} />
          </div>
        </div>
        <div className="eng">
          <div className="panel" style={{ padding: 10 }}>
            <div className="strip">{STAGES.map((x, i) => <div key={x} className={step.stages.includes(x) ? 'on' : i < maxI ? 'done' : ''}><b>{STAGE_NAME[x].slice(0, 2)}</b>{STAGE_NAME[x].slice(3)}</div>)}</div>
          </div>
          <div className="panel">
            <div className="label">Findings fed to the diagnosis network</div>
            {net ? <><FindingChips input={net.input} /><div className="note">{net.label}. The engine logs decisions, not per-batch findings.</div></>
              : <div className="note">Diagnosis runs once an episode opens.</div>}
            {r && <><div className="label" style={{ marginTop: 14 }}>Noisy-OR posterior · P(cause | findings)</div><Posterior fu={fu} r={r} /></>}
          </div>
          <div className="panel">
            <div className="label">Bayes factor · {lead ? CLS_LABEL[lead] ?? lead : 'leading cause'} {step.bf != null ? '· logged by the engine' : r ? '· illustration' : ''}</div>
            <BfGauge bf={bf} fu={fu} />
            <div className="state"><span className="label">Alarm state</span><Badge state={step.badge} cls={step.cls} />
              <span className="label">{bf != null ? `BF ${fmtBf(bf)}` : ''}</span></div>
            <div className="label" style={{ marginTop: 12 }}>alarm_log.jsonl</div>
            <div className="log">{logs.length ? logs.slice(-14).map((e, i) => <div key={i}><span className="t">{fmtLog(e).slice(0, 10)}</span>{fmtLog(e).slice(10)}</div>)
              : <div className="t">(no state changes, nothing written)</div>}</div>
          </div>
          {step.alarmId && replay.final.get(step.alarmId) && (() => {
            const f = replay.final.get(step.alarmId)!
            return <div className="panel"><div className="label">{step.alarmId} · {String(f.class)} · final P {Number(f.confidence).toFixed(2)}</div>
              <ConfidenceExplain conf={f.conf} cls={String(f.class)} meta={farm.meta} evidence={f.evidence} finalNote /></div>
          })()}
        </div>
      </div>
    </section>
  )
}

/* ============================== live engine ============================== */
const STATES: AlarmState[] = ['WATCH', 'ALARM', 'CONFIRMED', 'RETRACTED']
function Engine({ farm }: { farm: Farm }) {
  const { status, alarms, watchdog, mode, meta } = farm
  const [states, setStates] = useState<Set<AlarmState>>(new Set(STATES))
  const [withSite, setWithSite] = useState(false)
  const list = alarms.filter((a) => states.has(a.state) && (withSite || !a.suppressed)).slice(-80).reverse()
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Record<string, Alarm>>({})
  const toggle = async (a: Alarm) => {
    setOpenId(openId === a.id ? null : a.id)
    const d = await farm.loadAlarm(a)                 // refetch: an open alarm's confidence keeps changing
    setDetail((x) => ({ ...x, [a.id]: d }))
  }
  return (
    <section>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div className="label">Straight from the running engine</div>
        <span className={`pill ${mode === 'live' ? 'live' : 'demo'}`}><span className="led" />{mode === 'live' ? 'LIVE ENGINE' : 'DEMO REPLAY'}</span>
      </div>
      <h2>What the engine is thinking right now</h2>
      <p className="lede">Everything the operator view hides: WATCH signals, retracted alarms with their reason codes, suppressed site-multipath alerts, per-tractor findings and Bayes factors, and every watchdog pass.</p>
      <div className="grid2">
        <div className="panel tblwrap"><div className="label" style={{ padding: '10px 12px' }}>Per tractor · latest batch</div>
          {mode !== 'live'
            ? <div className="empty">Per-batch findings and Bayes factors exist only in LIVE mode; the recorded run doesn't store them. Run <code>docker compose up evaluator webui</code>.</div>
            : <table className="t"><thead><tr><th>TRACTOR</th><th>GATE</th><th>FINDINGS (SEVERITY)</th><th>EXPLANATION</th><th>TOP BF</th></tr></thead>
              <tbody>{status?.tractors.map((t) => (
                <tr key={t.id}><td className="m" style={{ color: TRACTOR_COLOR[t.id] }}>{t.id}</td><td className="m">{t.working ? 'WORKING' : 'PARKED'}</td>
                  <td className="m">{Object.entries(t.present ?? {}).map(([f, v]) => `${f} ${v}`).join(' · ') || '—'}{t.missing?.length ? <div className="dim">n/a: {t.missing.join(' ')}</div> : null}</td>
                  <td className="m">{t.explanation?.join(' + ') || '∅'}</td>
                  <td className="m">{t.top_bf?.map((b) => <div key={b.cause} style={{ color: CV[b.cause] }}>{b.cause} {fmtBf(b.bf)}</div>)}</td></tr>
              ))}</tbody></table>}
          {status?.open_episodes && status.open_episodes.length > 0 && <div className="note" style={{ padding: '0 12px 10px' }}>Open episodes: {status.open_episodes.map((e) => `${e.episode} (${e.track}, ${e.tractors.join(',')}, since ${hhmm(e.start)}, provisional ${e.provisional ?? '—'})`).join(' · ')}</div>}
        </div>
        <div className="panel tblwrap"><div className="label" style={{ padding: '10px 12px' }}>Watchdog passes · every {meta ? meta.watchdog_every_s / 60 : 5} min of data time</div>
          <table className="t"><thead><tr><th>PASS (DATA TIME)</th><th>TOUCHED</th><th>ACTIONS</th></tr></thead>
            <tbody>{[...watchdog].reverse().slice(0, 20).map((w, i) => (
              <tr key={i}><td className="m">{dayTime(w.t_data)}{w.final ? ' FINAL' : ''}</td><td className="m">{w.n_touched}</td><td className="m">{w.actions.slice(0, 4).join('; ') || '—'}</td></tr>
            ))}</tbody></table>
        </div>
      </div>
      <div className="panel tblwrap" style={{ marginTop: 16 }}>
        <div className="label" style={{ padding: '10px 12px' }}>All alarms, every state</div>
        <div className="filters">
          {STATES.map((s) => <label key={s} style={{ color: STATE_C[s] }}><input type="checkbox" checked={states.has(s)} onChange={() => { const n = new Set(states); if (n.has(s)) n.delete(s); else n.add(s); setStates(n) }} />{s}</label>)}
          <label><input type="checkbox" checked={withSite} onChange={(e) => setWithSite(e.target.checked)} />include suppressed site multipath</label>
        </div>
        <table className="t"><thead><tr><th>ID</th><th>STATE</th><th>CLASS</th><th>TRACTORS</th><th>START</th><th>END</th><th>CONFIDENCE</th><th>REASON</th></tr></thead>
          <tbody>{list.map((a) => (<Fragment key={a.id}>
            <tr className="clickrow" onClick={() => void toggle(a)}><td className="m">{openId === a.id ? '▾' : '▸'} {a.id}</td><td className="m" style={{ color: STATE_C[a.state] }}>{a.state}</td><td className="m" style={{ color: CV[a.cls] }}>{a.cls}{a.suppressed ? ' (site)' : ''}</td>
              <td className="m">{a.tractors.join(',')}</td><td className="m">{dayTime(a.start, true)}</td><td className="m">{a.end ? hhmm(a.end, true) : 'open'}</td>
              <td className="m">{a.confidence.toFixed(2)}</td><td className="m">{a.reason ?? ''}</td></tr>
            {openId === a.id && <tr><td colSpan={8} style={{ background: 'rgba(0,0,0,.2)' }}>
              {!detail[a.id] ? <span className="dim">loading…</span> : <>
                <ConfidenceExplain conf={detail[a.id].conf} cls={a.cls} meta={meta} evidence={detail[a.id].evidence} finalNote={mode !== 'live'} />
                {detail[a.id].evidence?.length ? <div className="ev" style={{ marginTop: 8 }}>{detail[a.id].evidence!.map((e, i) => <div key={i}>› {e}</div>)}</div> : null}
              </>}
            </td></tr>}
          </Fragment>))}</tbody></table>
      </div>
      {meta && <div className="panel" style={{ marginTop: 16, padding: '16px 18px' }}>
        <div className="label" style={{ marginBottom: 6 }}>Network sandbox · the engine's own tables{meta.config ? ` from ${meta.config}` : ''}</div>
        <NetworkSandbox fu={meta.fusion} />
      </div>}
    </section>
  )
}

/* ============================== page ============================== */
export function Extended({ farm, tab, setTab }: { farm: Farm; tab: number; setTab: (i: number) => void }) {
  const scenarios = useMemo(() => (farm.replay ? buildScenarios(farm.replay) : []), [farm.replay])
  // deep link: #/extended?tab=2&s=e&step=3
  const [sc, setScRaw] = useState(() => Math.max(0, 'qwe'.indexOf(location.hash.match(/[?&]s=([qwe])/)?.[1] ?? 'q')))
  const [k, setK] = useState(() => Math.max(0, Number(location.hash.match(/[?&]step=(\d+)/)?.[1] ?? 1) - 1))
  const setSc = (i: number) => { setScRaw(i); setK(0) }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input,select,textarea')) return
      const key = e.key.toLowerCase()
      if (key.length === 1 && '1234'.includes(key)) { setTab(Number(key) - 1); return }
      const qi = 'qwe'.indexOf(key)
      if (key.length === 1 && qi >= 0 && qi < scenarios.length) { setTab(1); setScRaw(qi); setK(0); return }
      if (tab !== 1 || !scenarios[sc]) return
      const n = scenarios[sc].steps.length
      if (key === 'arrowright' || key === ' ') {
        e.preventDefault()
        if (k + 1 < n) setK(k + 1); else if (sc < scenarios.length - 1) { setScRaw(sc + 1); setK(0) }
      }
      if (key === 'arrowleft') { e.preventDefault(); setK(Math.max(0, k - 1)) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab, sc, k, scenarios, setTab])
  return (
    <main className="ext">
      {tab === 0 && <DataFlow farm={farm} />}
      {tab === 1 && <Walkthrough farm={farm} sc={sc} setSc={setSc} k={k} setK={setK} scenarios={scenarios} />}
      {tab === 2 && <Engine farm={farm} />}
      {tab === 3 && <Research meta={farm.meta} replay={farm.replay} />}
    </main>
  )
}
