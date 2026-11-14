import { useCallback, useMemo } from 'react'
import type { Alarm, Meta } from '../types'
import { CLASSES } from '../types'
import { CLS_COLOR, CLS_LABEL } from '../format'
import { ms } from '../demo'

const W = 1000, LANE = 18, TOP = 18

function Bars({ alarms, x, nowMin, onSelect }: { alarms: Alarm[]; x: (t: number) => number; nowMin: number; onSelect: (id: string) => void }) {
  const shown = alarms.filter((a) => a.state === 'ALARM' || a.state === 'CONFIRMED')
  return <>{CLASSES.map((c, i) => {
    const y = TOP + i * LANE
    return shown.filter((a) => a.cls === c).map((a) => {
      const s = x(ms(a.start)), e = x(a.end ? ms(a.end) : nowMin)
      return (
        <rect key={a.id} x={s} y={y + 2} width={Math.max(e - s, a.suppressed ? 0.6 : 2)} height={LANE - 5}
          fill={CLS_COLOR[c]} opacity={a.suppressed ? 0.45 : a.state === 'CONFIRMED' ? 1 : 0.6}
          onClick={() => !a.suppressed && onSelect(a.id)} className={a.suppressed ? '' : 'tl-bar'}>
          <title>{`${a.id} ${a.state} ${a.start.slice(5, 19)}–${(a.end ?? 'open').slice(5, 19)} ${a.tractors.join(',')}${a.suppressed ? ' (site multipath, suppressed alert)' : ''}`}</title>
        </rect>
      )
    })
  })}</>
}

export function Timeline({ meta, alarms, now, onSelect }: { meta: Meta | null; alarms: Alarm[]; now: number; onSelect: (id: string) => void }) {
  const t0 = meta ? ms(meta.data_start) : 0, t1 = meta ? ms(meta.data_end) : 1
  const x = useCallback((t: number) => ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * W, [t0, t1])
  const nowMin = Math.floor(now / 60000) * 60000      // open bars grow per data minute, not per render
  const bars = useMemo(() => <Bars alarms={alarms} x={x} nowMin={nowMin} onSelect={onSelect} />, [alarms, x, nowMin, onSelect])
  if (!meta) return null
  const H = TOP + LANE * CLASSES.length + 4
  const w0 = x(ms(meta.window.start)), w1 = x(ms(meta.window.end))
  const days: number[] = []
  for (let t = t0; t <= t1; t += 6 * 3600e3) days.push(t)
  return (
    <div className="timeline">
      <svg viewBox={`0 0 ${W + 110} ${H}`} preserveAspectRatio="none" role="img" aria-label="72 hour detection timeline">
        <g transform="translate(110,0)">
          <rect x={0} y={TOP - 4} width={w0} height={H - TOP + 4} className="tl-dev" />
          <rect x={w0} y={TOP - 4} width={w1 - w0} height={H - TOP + 4} className="tl-eval" />
          <text x={4} y={11} className="tl-lbl">development (labelled)</text>
          <text x={w0 + 4} y={11} className="tl-lbl">evaluation window (scored)</text>
          {days.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={TOP - 4} y2={H} className="tl-grid" />
              <text x={x(t) + 2} y={H - 2} className="tl-tick">{new Date(t).toISOString().slice(5, 13).replace('T', ' ')}h</text>
            </g>
          ))}
          {bars}
          {Number.isFinite(now) && <line x1={x(now)} x2={x(now)} y1={TOP - 6} y2={H} className="tl-now" />}
        </g>
        {CLASSES.map((c, i) => <text key={c} x={104} y={TOP + i * LANE + 12} textAnchor="end" className="tl-lane">{CLS_LABEL[c]}</text>)}
      </svg>
    </div>
  )
}
