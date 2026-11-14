// Drawn farm map (no tiles, no Leaflet): field outline with row texture, tractor paths coloured by what the
// engine concluded at each moment, tractor markers, available RFI reports and, optionally, the learned
// multipath sites. Paths come from /status `trail` (LIVE) or the snapshot `path` (DEMO), both at 10 s.
import { useMemo } from 'react'
import type { Alarm, Meta, Rfi, TractorStatus } from '../types'
import { CV, TRACTOR_COLOR } from '../format'
import { ms } from '../demo'

export type PathPt = [number, number, number]       // [t ms, lat, lon]
const W = 1000

/** colour of what the engine concluded about tractor `id` at time t (ALARM/CONFIRMED, not multipath) */
export function colorAt(id: string, t: number, alarms: Alarm[]): string {
  let best: Alarm | null = null
  for (const a of alarms) {
    if (a.cls === 'MULTIPATH' || !a.tractors.includes(id)) continue
    const s = ms(a.start), e = a.end ? ms(a.end) : Infinity
    if (s <= t && t <= e && (!best || a.confidence > best.confidence)) best = a
  }
  return best ? CV[best.cls] : CV.OK
}

export function FieldMap({ meta, tractors, paths, rfi, alarms, now, focus, showSites, onPick }: {
  meta: Meta | null
  tractors: TractorStatus[]
  paths: Record<string, PathPt[]>
  rfi: Rfi[]
  alarms: Alarm[]
  now: number
  focus: string | null
  showSites: boolean
  onPick: (id: string) => void
}) {
  const field = meta?.fields[0]
  // fixed frame: the field plus 25% (parked tractors sit just south of it), equirectangular at the field's latitude
  const frame = useMemo(() => {
    const lat = field?.lat ?? [46.9001, 46.9071], lon = field?.lon ?? [-97.2039, -97.1946]
    const pLat = (lat[1] - lat[0]) * 0.25, pLon = (lon[1] - lon[0]) * 0.25
    const la0 = lat[0] - pLat, la1 = lat[1] + pLat, lo0 = lon[0] - pLon, lo1 = lon[1] + pLon
    const k = Math.cos((((la0 + la1) / 2) * Math.PI) / 180)
    const sx = W / ((lo1 - lo0) * k), H = (la1 - la0) * sx
    const mPerPx = ((lo1 - lo0) * k * 111320) / W
    return { x: (lo: number) => (lo - lo0) * k * sx, y: (la: number) => (la1 - la) * sx, H, mPerPx }
  }, [field])
  const { x, y, H, mPerPx } = frame

  const segs = useMemo(() => Object.entries(paths).map(([id, pts]) => {
    const out: { c: string; d: string }[] = []
    let cur: { c: string; p: string[] } | null = null
    let prev: PathPt | null = null
    for (const p of pts) {
      const c = colorAt(id, p[0], alarms)
      const X = x(p[2]).toFixed(1), Y = y(p[1]).toFixed(1)
      const gap = prev && (p[0] - prev[0] > 60e3 || Math.hypot(x(p[2]) - x(prev[2]), y(p[1]) - y(prev[1])) * mPerPx > 120)
      if (!cur || gap) { if (cur) out.push({ c: cur.c, d: cur.p.join(' ') }); cur = { c, p: [`M${X},${Y}`] } }
      else if (c !== cur.c) { out.push({ c: cur.c, d: cur.p.join(' ') + ` L${X},${Y}` }); cur = { c, p: [`M${X},${Y}`] } }
      else cur.p.push(`L${X},${Y}`)
      prev = p
    }
    if (cur) out.push({ c: cur.c, d: cur.p.join(' ') })
    return { id, segs: out.filter((s) => s.d.includes('L')) }
  }), [paths, alarms, x, y, mPerPx])

  const scaleM = 200, scalePx = scaleM / mPerPx
  return (
    <svg className="mapsvg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="farm map">
      <defs>
        <radialGradient id="vign" cx="50%" cy="45%" r="70%"><stop offset="0" stopColor="#151815" /><stop offset="1" stopColor="#0b0c0a" /></radialGradient>
        <pattern id="rows" width="9" height="9" patternUnits="userSpaceOnUse"><line x1="4.5" y1="0" x2="4.5" y2="9" stroke="#2c3a21" strokeWidth="1" /></pattern>
      </defs>
      <rect width={W} height={H} fill="url(#vign)" />
      {field && (() => {
        const fx = x(field.lon[0]), fy = y(field.lat[1]), fw = x(field.lon[1]) - fx, fh = y(field.lat[0]) - fy
        return <g>
          <rect x={fx} y={fy} width={fw} height={fh} fill="#1a2215" />
          <rect x={fx} y={fy} width={fw} height={fh} fill="url(#rows)" opacity={0.8} />
          <rect x={fx} y={fy} width={fw} height={fh} fill="none" stroke="#ede6d6" strokeOpacity={0.35} strokeDasharray="4 6" />
          <text x={fx + 4} y={fy - 8} className="flabel-svg">FIELD · {meta!.fields.map((f) => f.tractor).join(' ')} · OUTLINE FROM TRACKS</text>
          <text x={fx + fw} y={fy - 8} textAnchor="end" className="flabel-svg">N HEADLAND</text>
          <text x={fx + fw} y={fy + fh + 16} textAnchor="end" className="flabel-svg">S HEADLAND</text>
        </g>
      })()}
      {showSites && meta?.site_multipath?.sites.map((s, i) => (
        <rect key={i} x={x(s.lon[0])} y={y(s.lat[1])} width={Math.max(2, x(s.lon[1]) - x(s.lon[0]))} height={Math.max(2, y(s.lat[0]) - y(s.lat[1]))}
          fill={CV.MULTIPATH} fillOpacity={Math.min(0.2 + (s.rate ?? 0) * 0.6, 0.8)}>
          <title>{`Learned multipath site: ${s.dips}/${s.passes} dev passes dipped (${Math.round((s.rate ?? 0) * 100)}%)`}</title>
        </rect>
      ))}
      {rfi.filter((r) => r.lat != null && r.lon != null).map((r) => {
        const cx = x(r.lon!), cy = y(r.lat!), rad = (r.radius_km * 1000) / mPerPx
        return <g key={r.id}>
          <circle cx={cx} cy={cy} r={rad} fill={CV.JAMMING} fillOpacity={0.06} stroke={CV.JAMMING} strokeWidth={1.5} strokeDasharray="6 6" />
          <text x={Math.min(Math.max(cx, 60), W - 60)} y={Math.min(Math.max(cy, 20), H - 30)} textAnchor="middle" className="flabel-svg" style={{ fill: '#ff9a93' }}>⌁ {r.id} · conf {r.confidence}</text>
          <title>{`${r.id} · event ${r.t_event.slice(5, 16)}Z · available ${r.t_avail.slice(11, 16)}Z · ${r.text}`}</title>
        </g>
      })}
      {segs.map(({ id, segs }) => (
        <g key={id} opacity={focus && focus !== id ? 0.35 : 1}>
          {segs.map((s, i) => <path key={i} d={s.d} fill="none" stroke={s.c} strokeWidth={focus === id ? 3.5 : 2.4} strokeLinejoin="round" strokeLinecap="round" opacity={0.9} />)}
        </g>
      ))}
      {tractors.filter((t) => t.lat != null && t.lon != null).map((t) => {
        const c = colorAt(t.id, now, alarms), alert = c !== CV.OK, cx = x(t.lon!), cy = y(t.lat!)
        return (
          <g key={t.id} className="tmk" onClick={() => onPick(t.id)} style={{ cursor: 'pointer' }}>
            {alert && <circle cx={cx} cy={cy} r={14} fill="none" stroke={c} strokeWidth={2}>
              <animate attributeName="r" from="12" to="26" dur="1.6s" repeatCount="indefinite" />
              <animate attributeName="opacity" from="0.9" to="0" dur="1.6s" repeatCount="indefinite" />
            </circle>}
            <circle cx={cx} cy={cy} r={focus === t.id ? 17 : 14} fill={c} stroke="#0a0a09" strokeWidth={2.5} style={{ filter: `drop-shadow(0 0 8px ${c})` }} />
            <circle cx={cx} cy={cy} r={(focus === t.id ? 17 : 14) + 2} fill="none" stroke={TRACTOR_COLOR[t.id] ?? c} strokeOpacity={0.5} strokeWidth={1.2} />
            <text x={cx} y={cy + 4} textAnchor="middle" className="tmk-t">{t.id}</text>
          </g>
        )
      })}
      <g transform={`translate(${W - scalePx - 24},${H - 22})`}>
        <line x1={0} x2={scalePx} y1={0} y2={0} stroke="#9a9182" strokeWidth={1.5} />
        <line x1={0} x2={0} y1={-4} y2={4} stroke="#9a9182" /><line x1={scalePx} x2={scalePx} y1={-4} y2={4} stroke="#9a9182" />
        <text x={scalePx / 2} y={-8} textAnchor="middle" className="flabel-svg">{scaleM} m</text>
      </g>
      <text x={W - 24} y={30} textAnchor="end" className="flabel-svg">N ↑</text>
    </svg>
  )
}
