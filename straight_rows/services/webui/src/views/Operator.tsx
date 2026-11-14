import { useMemo, useState, type CSSProperties } from 'react'
import type { Farm } from '../useFarm'
import type { Alarm, HistPt, TractorStatus } from '../types'
import { ACTION_TEXT, CLS_LABEL, CV, dayTime, dur, hhmm } from '../format'
import { ms } from '../demo'
import { FieldMap } from '../components/FieldMap'
import { ConfidenceExplain } from '../components/Confidence'
import type { Meta } from '../types'

const SEVERITY = ['JAMMING', 'SPOOFING', 'SPACE_WEATHER', 'EQUIPMENT_FAULT', 'MULTIPATH']
const fmtD = (t: string | null) => (t ? dayTime(t) : 'now')

function Spark({ pts, color }: { pts: HistPt[]; color: string }) {
  const h = pts.filter((p) => p.cn0 != null).slice(-120)
  if (h.length < 2) return null
  const vs = h.map((p) => p.cn0!), mid = (Math.min(...vs) + Math.max(...vs)) / 2
  const half = Math.max(3, (Math.max(...vs) - Math.min(...vs)) / 2 + 1)   // scale to the data, at least ±3 dB
  const lo = mid - half, hi = mid + half, W = 360, H = 34
  const xy = h.map((p, i) => `${((i / (h.length - 1)) * W).toFixed(1)},${(H - ((Math.max(lo, Math.min(hi, p.cn0!)) - lo) / (hi - lo)) * H).toFixed(1)}`).join(' ')
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="mean C/N0 history">
      <polyline points={`0,${H} ${xy} ${W},${H}`} fill={color} fillOpacity={0.06} stroke="none" />
      <polyline points={xy} fill="none" stroke={color} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function TractorCard({ t, mine, pts, actions, sel, onPick }: { t: TractorStatus; mine: Alarm[]; pts: HistPt[]; actions: Record<string, string>; sel: boolean; onPick: () => void }) {
  const a = mine[0]
  const col = a ? CV[a.cls] : t.working ? CV.OK : '#6b645a'
  return (
    <div className="trk" onClick={onPick} style={sel ? { borderColor: 'var(--gold)' } : undefined} title="highlight on map">
      <div className="row1">
        <div className="id" style={{ color: col, borderColor: `${col}55` }}>{t.id}</div>
        <div className="st" style={{ color: col }}>{a ? CLS_LABEL[a.cls] : t.working ? 'Nominal' : 'Parked'}</div>
        <div className="md">{t.working ? 'WORKING' : 'PARKED'}</div>
      </div>
      <div className="metrics">
        <div className="m"><div className="v">{t.cn0 != null ? t.cn0.toFixed(1) : '—'}</div><div className="k">C/N0 dB-Hz</div></div>
        <div className="m"><div className="v">{t.sats ?? '—'}</div><div className="k">SATELLITES</div></div>
        <div className="m"><div className="v" style={{ fontSize: 13, paddingTop: 2, color: t.link_ok === false ? CV.JAMMING : undefined }}>{t.fix ?? '—'}{t.link_ok === false ? ' · LINK ✕' : ''}</div><div className="k">FIX</div></div>
      </div>
      <Spark pts={pts} color={col} />
      <div className="act">{a
        ? <><b>Do:</b> {ACTION_TEXT[a.cls]} <span className="mono dim" style={{ fontSize: 11 }}>{actions[a.cls]}</span></>
        : 'No action needed.'}</div>
    </div>
  )
}

function AlarmRow({ a, open, now, actions, detail, meta, demo, onToggle }: { a: Alarm; open: boolean; now: number; actions: Record<string, string>; detail?: Alarm; meta: Meta | null; demo: boolean; onToggle: () => void }) {
  const col = CV[a.cls] ?? '#999'
  const ongoing = a.end == null
  return (
    <div className="al" onClick={onToggle}>
      <div><span className="chip" style={{ color: col, borderColor: `${col}55`, background: `${col}14` }}><i />{CLS_LABEL[a.cls] ?? a.cls}</span></div>
      <div>
        <div className="who">{a.tractors.join(' · ')}</div>
        <div className="when">{fmtD(a.start)} → {ongoing ? 'ongoing' : fmtD(a.end)} · {dur((a.end ? ms(a.end) : now) - ms(a.start))}</div>
      </div>
      <div title="confidence: reporting only, never gates a detection">
        <div className="bar"><i style={{ width: `${Math.round(a.confidence * 100)}%` }} /></div>
        <div className="bar-l">confidence {a.confidence.toFixed(2)}</div>
      </div>
      <div className={`stt ${a.state}`}>{a.state === 'CONFIRMED' ? '✓ CONFIRMED' : '● ALARM'}</div>
      {open && (
        <div className="ev" onClick={(e) => e.stopPropagation()}>
          <div>Action <b style={{ color: 'var(--bone)' }}>{actions[a.cls]}</b> · {ACTION_TEXT[a.cls]}</div>
          {!detail ? <div className="dim">loading…</div>
            : detail.evidence?.length ? detail.evidence.map((e, i) => <div key={i}>› {e}</div>)
            : <div className="dim">No evidence lines yet{a.open ? ' (built when the episode closes)' : ''}.</div>}
          {detail && <ConfidenceExplain conf={detail.conf} cls={a.cls} meta={meta} evidence={detail.evidence} finalNote={demo} />}
          {detail?.history && (
            <details><summary>history · {detail.history.length} state changes</summary>
              {detail.history.map((h, i) => <div key={i}><span className="dim">{dayTime(h.t_data_iso, true)}</span> {h.from ?? '∅'} → {h.to} {h.reason} {h.cls}{h.bf != null ? ` BF ${h.bf}` : ''}{h.note ? ` · ${h.note}` : ''}</div>)}
            </details>
          )}
          <div className="dim">{a.id} · alarm ids keep the class they were raised with</div>
        </div>
      )}
    </div>
  )
}

export function Operator({ farm }: { farm: Farm }) {
  const { meta, status, alarms, now, trails, paths } = farm
  const [focus, setFocus] = useState<string | null>(null)
  const [showSites, setShowSites] = useState(false)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [detail, setDetail] = useState<Record<string, Alarm>>({})
  const actions = meta?.actions ?? {}

  // operator list: ALARM and CONFIRMED only; site-multipath reports are suppressed alerts, counted not listed
  const shown = useMemo(() => alarms.filter((a) => (a.state === 'ALARM' || a.state === 'CONFIRMED') && !a.suppressed)
    .sort((x, y) => ms(y.start) - ms(x.start)), [alarms])
  const site = useMemo(() => alarms.filter((a) => a.suppressed && a.state === 'CONFIRMED'), [alarms])
  const act = shown.filter((a) => a.open && a.cls !== 'MULTIPATH')
  const watching = alarms.filter((a) => a.state === 'WATCH' && a.open && !a.suppressed)
  const siteHour = site.filter((a) => ms(a.start) > now - 3600e3).length
  const top = [...act].sort((x, y) => SEVERITY.indexOf(x.cls) - SEVERITY.indexOf(y.cls))[0]
  const rfi = (meta?.rfi ?? []).filter((r) => ms(r.t_avail) <= now && ms(r.t_event) > now - 6 * 3600e3)
  const trs = status?.tractors ?? []
  const affected = [...new Set(act.flatMap((a) => a.tractors))].sort()
  const names = [...new Set([...act].sort((x, y) => SEVERITY.indexOf(x.cls) - SEVERITY.indexOf(y.cls)).map((a) => CLS_LABEL[a.cls]))]

  const toggle = async (a: Alarm) => {
    const n = new Set(open)
    if (n.has(a.id)) n.delete(a.id); else n.add(a.id)
    setOpen(n)
    if (!detail[a.id]) { const d = await farm.loadAlarm(a); setDetail((x) => ({ ...x, [a.id]: d })) }
  }

  const t0 = meta ? ms(meta.data_start) : 0, t1 = meta ? ms(meta.data_end) : 1
  const pct = (t: number) => Math.max(0, Math.min(100, ((t - t0) / (t1 - t0)) * 100))
  const ticks: number[] = []
  for (let t = t0; t <= t1; t += 12 * 3600e3) ticks.push(t)

  return (
    <main className="op">
      <section className="panel" id="mapWrap">
        <FieldMap meta={meta} tractors={trs} paths={paths} rfi={rfi} alarms={shown} now={now} focus={focus} showSites={showSites}
          onPick={(id) => setFocus(focus === id ? null : id)} />
        <div className={`farmwide ${act.some((a) => a.cls === 'SPACE_WEATHER') ? 'on' : ''}`} />
        <div className="hud tl glass">
          <div className="label">Fleet</div>
          <div className="big" style={{ color: top ? CV[top.cls] : undefined }}>{top ? names.join(' + ') : 'Nominal'}</div>
          <div className="sm">{top ? `${affected.length} of ${trs.length || 3} tractors affected` : `${trs.length || 3} tractors · last 20 min of paths`}</div>
        </div>
        <div className="hud tr glass">
          <div className="label">Data time (UTC)</div>
          <div className="big mono" style={{ fontSize: 20 }}>{Number.isFinite(now) ? new Date(now).toISOString().slice(11, 19) + 'Z' : '—'}</div>
          <div className="sm">{status ? `replay ${Math.round(status.progress * 100)}% of 72 h` : ''}</div>
        </div>
        <div className="hud bl glass legend">
          <span><i style={{ background: CV.OK }} />Nominal</span><span><i style={{ background: CV.SPACE_WEATHER }} />Space weather</span>
          <span><i style={{ background: CV.JAMMING }} />Jamming</span><span><i style={{ background: CV.SPOOFING }} />Spoofing</span>
          <span><i style={{ background: CV.EQUIPMENT_FAULT }} />Equipment</span><span><i style={{ background: CV.MULTIPATH }} />Multipath site</span>
          <label><input type="checkbox" checked={showSites} onChange={(e) => setShowSites(e.target.checked)} />learned multipath sites</label>
        </div>
      </section>

      <aside className="side">
        <div className={`panel hero ${top ? 'bad' : ''}`} style={top ? ({ '--glowc': CV[top.cls], '--glow': `${CV[top.cls]}33` } as CSSProperties) : undefined}>
          <div className="ring"><i /></div>
          <div className="label">Farm status</div>
          <div className="state" style={{ color: top ? CV[top.cls] : undefined }}>{top ? names.join(' + ') : 'All clear'}</div>
          <div className="why">{top
            ? `Affecting ${affected.join(', ')} · ${top.state === 'CONFIRMED' ? 'confirmed by watchdog' : 'awaiting watchdog'}`
            : 'No active GNSS alarms. Every tractor has a trustworthy position.'}</div>
          {top && <div className="do"><b>Do:</b> {ACTION_TEXT[top.cls]} <span className="mono dim" style={{ fontSize: 11 }}>{actions[top.cls]}</span></div>}
          {watching.length > 0 && (
            <div className="watch">On WATCH (BF ≥ 3, below 20): {watching.map((a) => `${a.cls === 'OTHER' ? 'unattributed' : CLS_LABEL[a.cls]} on ${a.tractors.join(',')}`).join(' · ')}</div>
          )}
          <div className="site">
            <b style={{ color: 'var(--bone)' }}>{site.length}</b> site-multipath reports so far ({siteHour} in the last hour): suppressed here, exported as
            MULTIPATH. Learned from the dev days; our weakest output. <a href="#/extended?tab=4">Why →</a>
          </div>
          {rfi.length > 0 && <div className="site">{rfi.map((r) => <div key={r.id}><b style={{ color: CV.JAMMING }}>{r.id}</b> RFI report · event {hhmm(r.t_event)}, available {hhmm(r.t_avail)} · confidence {r.confidence}{r.farm_wide ? ' · farm-wide' : ''}</div>)}</div>}
        </div>
        {trs.map((t) => (
          <TractorCard key={t.id} t={t} pts={trails[t.id] ?? []} actions={actions} sel={focus === t.id} onPick={() => setFocus(focus === t.id ? null : t.id)}
            mine={act.filter((a) => a.tractors.includes(t.id)).sort((x, y) => SEVERITY.indexOf(x.cls) - SEVERITY.indexOf(y.cls))} />
        ))}
        {farm.mode === 'demo' && <div className="kbd">DEMO: recorded run; map paths at 10 s, cards sampled every minute, WORKING approximated from speed.</div>}
      </aside>

      <section className="panel alarms wide">
        <div className="panel-h"><span className="label">Detections</span><span className="label">{shown.length} total · {act.length} active</span></div>
        {shown.length ? shown.map((a) => (
          <AlarmRow key={a.id} a={a} open={open.has(a.id)} now={now} actions={actions} detail={detail[a.id]} meta={meta} demo={farm.mode !== 'live'} onToggle={() => void toggle(a)} />
        )) : <div className="empty">No detections yet.</div>}
      </section>

      <section className="panel tl wide">
        <div className="panel-h" style={{ padding: '0 0 4px' }}>
          <span className="label">72-hour timeline</span>
          <span className="label" style={{ color: 'var(--dim)' }}>hatched = alarm awaiting watchdog · thin ticks = site multipath · gold band = scored evaluation window</span>
        </div>
        {(trs.length ? trs.map((t) => t.id) : meta?.tractors ?? []).map((id) => (
          <div className="lane" key={id}>
            <div className="l">{id}</div>
            <div className="track">
              {meta && <div className="evalw" style={{ left: `${pct(ms(meta.window.start))}%`, width: `${pct(ms(meta.window.end)) - pct(ms(meta.window.start))}%` }} />}
              {site.filter((a) => a.tractors.includes(id)).map((a) => (
                <div key={a.id} className="tseg site" style={{ left: `${pct(ms(a.start))}%`, background: CV.MULTIPATH }} />
              ))}
              {shown.filter((a) => a.tractors.includes(id)).map((a) => {
                const s = pct(ms(a.start)), e = pct(a.end ? ms(a.end) : now)
                return <div key={a.id} className={`tseg ${a.state}`} title={`${CLS_LABEL[a.cls]} ${fmtD(a.start)} – ${fmtD(a.end)}`} onClick={() => void toggle(a)}
                  style={{ left: `${s}%`, width: `${Math.max(e - s, 0.3)}%`, background: CV[a.cls] }} />
              })}
              {Number.isFinite(now) && <div className="now" style={{ left: `${pct(now)}%` }} />}
            </div>
          </div>
        ))}
        <div className="ticks">{ticks.map((t) => { const s = new Date(t).toISOString(); return <span key={t} style={{ left: `${pct(t)}%` }}>{s.slice(5, 10)} {s.slice(11, 13)}Z</span> })}</div>
      </section>
      <footer>Operator view shows ALARM and CONFIRMED only · WATCH signals, retracted alarms and suppressed site-multipath alerts live in the judge view · times UTC</footer>
    </main>
  )
}
