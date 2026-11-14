import type { Alarm, TractorStatus } from '../types'
import { CLS_COLOR, CLS_LABEL, TRACTOR_COLOR } from '../format'

const fixClass = (f: string | null) => (f === 'RTK_FIXED' ? 'ok' : f === 'RTK_FLOAT' || f === 'DGPS' ? 'warn' : f ? 'bad' : '')

export function TractorCards({ tractors, active, onPick }: { tractors: TractorStatus[]; active: Alarm[]; onPick: (t: TractorStatus) => void }) {
  return (
    <div className="cards">
      {tractors.map((t) => {
        const mine = active.filter((a) => a.tractors.includes(t.id))
        return (
          <button key={t.id} className="card tractor" onClick={() => onPick(t)} style={{ borderLeftColor: TRACTOR_COLOR[t.id] }}>
            <div className="row">
              <strong>{t.id}</strong>
              <span className={`tag ${t.working ? 'ok' : 'muted'}`}>{t.working ? 'WORKING' : 'PARKED'}</span>
            </div>
            <div className="kv">
              <span>C/N0</span><b className="mono">{t.cn0 ?? '—'}<small> dB-Hz</small></b>
              <span>min</span><b className="mono">{t.cn0_min ?? '—'}</b>
              <span>sats</span><b className="mono">{t.sats ?? '—'}</b>
              <span>fix</span><b className={`mono ${fixClass(t.fix)}`}>{t.fix ?? '—'}</b>
              <span>link</span><b className={t.link_ok === false ? 'bad' : ''}>{t.link_ok == null ? '—' : t.link_ok ? 'ok' : 'DOWN'}</b>
            </div>
            <div className="chips">
              {mine.length === 0 ? <span className="muted">no active detection</span> : mine.map((a) => (
                <span key={a.id} className="chip" style={{ background: CLS_COLOR[a.cls] }}>{CLS_LABEL[a.cls]}</span>
              ))}
            </div>
          </button>
        )
      })}
    </div>
  )
}
