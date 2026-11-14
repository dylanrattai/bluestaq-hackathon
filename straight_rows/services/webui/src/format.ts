export const TRACTOR_COLOR: Record<string, string> = { T1: '#4cc9f0', T2: '#f9c74f', T3: '#f28482' }
export const CLS_COLOR: Record<string, string> = {
  SPACE_WEATHER: 'var(--c-sw)', JAMMING: 'var(--c-jam)', SPOOFING: 'var(--c-spoof)',
  EQUIPMENT_FAULT: 'var(--c-equip)', MULTIPATH: 'var(--c-mp)', OTHER: 'var(--c-other)',
}
export const CLS_LABEL: Record<string, string> = {
  SPACE_WEATHER: 'Space weather', JAMMING: 'Jamming', SPOOFING: 'Spoofing', EQUIPMENT_FAULT: 'Equipment fault',
  MULTIPATH: 'Multipath', OTHER: 'Unattributed',
}
export const FINDING_LABEL: Record<string, string> = {
  F1: 'C/N0 level drop', F2: 'C/N0 oscillation', F3: 'Equal drop on all tractors', F4: 'Unequal drop (≥2 tractors)',
  F5: 'Short dip', F6: 'Fix/satellites degraded, C/N0 normal', F7: 'Correction link down', F8: 'Cross-track drift',
  F9: 'Space weather active', F10: 'RFI report covers tractor', F11: 'Stacked step (opens a sub-episode)',
  F12: 'Machine-specific',
}

const pad = (n: number) => String(n).padStart(2, '0')
export function hhmm(t: number | string | null | undefined, sec = false): string {
  if (t == null) return '—'
  const d = new Date(typeof t === 'string' ? Date.parse(t) : t)
  if (Number.isNaN(d.getTime())) return '—'
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${sec ? ':' + pad(d.getUTCSeconds()) : ''}Z`
}
export function dayTime(t: number | string | null | undefined, sec = false): string {
  if (t == null) return '—'
  const d = new Date(typeof t === 'string' ? Date.parse(t) : t)
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
  bf == null ? '—' : bf >= 1e4 ? bf.toExponential(1) : bf >= 100 ? bf.toFixed(0) : bf.toFixed(bf < 10 ? 2 : 1)
