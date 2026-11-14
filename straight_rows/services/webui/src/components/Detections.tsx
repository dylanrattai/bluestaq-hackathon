import { useState } from 'react'
import type { Alarm } from '../types'
import { CLS_COLOR, CLS_LABEL, dayTime, dur, hhmm } from '../format'
import { ms } from '../demo'

export function Detections({ alarms, actions, now, loadAlarm, selected, onSelect }: {
  alarms: Alarm[]
  actions: Record<string, string>
  now: number
  loadAlarm: (a: Alarm) => Promise<Alarm>
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const [detail, setDetail] = useState<Record<string, Alarm>>({})
  const toggle = async (a: Alarm) => {
    if (selected === a.id) return onSelect(null)
    onSelect(a.id)
    if (!detail[a.id]) {
      const full = await loadAlarm(a)
      setDetail((d) => ({ ...d, [a.id]: full }))
    }
  }
  if (!alarms.length) return <p className="muted">No ALARM or CONFIRMED detections yet.</p>
  return (
    <ul className="dets">
      {alarms.map((a) => {
        const d = detail[a.id]
        const end = a.end ? ms(a.end) : now
        return (
          <li key={a.id} className={`det ${selected === a.id ? 'sel' : ''}`}>
            <button className="det-head" onClick={() => void toggle(a)}>
              <span className="swatch" style={{ background: CLS_COLOR[a.cls] }} />
              <span className="det-cls">{CLS_LABEL[a.cls]}</span>
              <span className={`tag st-${a.state}`}>{a.state}{a.open ? ' · open' : ''}</span>
              <span className="mono">{dayTime(a.start)}–{a.end ? hhmm(a.end) : 'now'}</span>
              <span className="muted">{dur(end - ms(a.start))}</span>
              <span>{a.tractors.join(', ')}</span>
              <span className="mono" title="confidence (reporting only; never gates a detection)">P {a.confidence.toFixed(2)}</span>
            </button>
            {selected === a.id && (
              <div className="det-body">
                <div className="action">Action: <b>{actions[a.cls] ?? '—'}</b> <span className="muted">· alarm id {a.id} (ids keep the class they were raised with)</span></div>
                {!d ? <p className="muted">loading…</p> : d.evidence?.length ? (
                  <ul className="evidence">{d.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
                ) : <p className="muted">No evidence lines for this alarm{a.open ? ' yet (built when it closes)' : ''}. </p>}
                {d?.history && (
                  <details><summary>History ({d.history.length})</summary>
                    <ul className="hist">{d.history.map((h, i) => (
                      <li key={i} className="mono">{dayTime(h.t_data_iso, true)} {h.from ?? '∅'}→{h.to} {h.reason} {h.cls} {h.bf != null ? `BF ${h.bf}` : ''} {h.note ?? ''}</li>
                    ))}</ul>
                  </details>
                )}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
