// Palette and wording shared by both pages (hex, so SVG and alpha suffixes like `${c}55` work)
export const CV: Record<string, string> = {
  SPACE_WEATHER: '#9d85f2', JAMMING: '#e5534b', SPOOFING: '#f08c3a', EQUIPMENT_FAULT: '#e85fc4',
  MULTIPATH: '#8b8579', OTHER: '#6b645a', OK: '#7dbbe0',
}
export const CLS_COLOR = CV
export const STATE_C: Record<string, string> = {
  NORMAL: '#7dbbe0', WATCH: '#d9a441', ALARM: '#e5534b', CONFIRMED: '#d9a441', RETRACTED: '#8b8579', CLOSED: '#9a9182',
}
export const TRACTOR_COLOR: Record<string, string> = { T1: '#ede6d6', T2: '#d9a441', T3: '#7dbbe0' }
export const CLS_LABEL: Record<string, string> = {
  SPACE_WEATHER: 'Space weather', JAMMING: 'Jamming', SPOOFING: 'Spoofing', EQUIPMENT_FAULT: 'Equipment fault',
  MULTIPATH: 'Multipath', OTHER: 'Unattributed',
}
// Plain-language operator instruction per class. The exported action code comes from the engine (meta.actions).
export const ACTION_TEXT: Record<string, string> = {
  SPACE_WEATHER: 'Farm-wide ionospheric storm. Hold RTK-critical passes or run in degraded mode until it clears.',
  JAMMING: 'Likely nearby transmitter. Stop autosteer and report the interference.',
  SPOOFING: 'Position may be faked. Stop, cross-check against an independent reference, and report.',
  EQUIPMENT_FAULT: 'This machine only. Service its antenna, cabling or RTK link; the others continue.',
  MULTIPATH: 'Recurring reflection dip at a known spot. Continue.',
}
export const FINDING_LABEL: Record<string, string> = {
  F1: 'C/N0 level drop', F2: 'C/N0 oscillation', F3: 'equal drop, all tractors', F4: 'unequal drop, ≥2 tractors',
  F5: 'short dip <60 s', F6: 'fix/sats degraded, C/N0 normal', F7: 'correction link down', F8: 'cross-track drift',
  F9: 'space weather active', F10: 'RFI report covers tractor', F11: 'stacked step', F12: 'machine-specific',
}

const pad = (n: number) => String(n).padStart(2, '0')
const toDate = (t: number | string) => new Date(typeof t === 'string' ? Date.parse(t) : t)
export function hhmm(t: number | string | null | undefined, sec = false): string {
  if (t == null) return '—'
  const d = toDate(t)
  if (Number.isNaN(d.getTime())) return '—'
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${sec ? ':' + pad(d.getUTCSeconds()) : ''}Z`
}
export function dayTime(t: number | string | null | undefined, sec = false): string {
  if (t == null) return '—'
  const d = toDate(t)
  if (Number.isNaN(d.getTime())) return '—'
  return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${hhmm(d.getTime(), sec)}`
}
export function dur(ms: number): string {
  if (!Number.isFinite(ms)) return '—'
  const s = Math.round(ms / 1000)
  if (s < 120) return `${s} s`
  const m = Math.round(s / 60)
  return m < 120 ? `${m} min` : `${(m / 60).toFixed(1)} h`
}
export const fmtBf = (bf: number | null | undefined) =>
  bf == null ? '—' : bf >= 1e4 ? bf.toExponential(1) : bf >= 100 ? bf.toFixed(0) : bf < 0.01 ? '<0.01' : bf < 10 ? bf.toFixed(2) : String(+bf.toPrecision(4))   // 19.98 must not read as 20
