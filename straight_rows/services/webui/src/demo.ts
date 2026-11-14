// DEMO mode: replays a finished run (out/alarm_log.jsonl + out/submission.json, bundled by
// `python -m runtime.webui`) on a client-side clock. Alarm states at time t are the last logged event <= t,
// so the replay shows exactly what the engine logged, never anything it did not.
import { normAlarm } from './normalize'
import type { Alarm, Detection, FeedItem, HistPt, Snapshot, Status, TractorStatus, WatchdogRun } from './types'

export const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN)

export interface Replay {
  snap: Snapshot
  final: Map<string, Snapshot['alarms'][number]>
  t0: number
  t1: number
  evT: number[]
  trackT0: number
  trackStep: number
}

export function prepare(snap: Snapshot): Replay {
  const evT = snap.events.map((e) => ms(e.t_data_iso))
  const tt = snap.track.t
  return { snap, final: new Map(snap.alarms.map((a) => [a.alarm_id, a])), t0: ms(snap.meta.data_start), t1: ms(snap.meta.data_end), evT, trackT0: ms(tt[0]), trackStep: ms(tt[1]) - ms(tt[0]) }
}

function cutoff(r: Replay, t: number): number {
  let lo = 0, hi = r.evT.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (r.evT[m] <= t) lo = m + 1; else hi = m }
  return lo
}

export type Sample = { lat: number | null; lon: number | null; cn0: number | null; cn0_min: number | null; sats: number | null; fix: string | null; speed: number | null; link_ok: boolean | null; xte?: number | null }
export const samples = (r: Replay, tr: string) => r.snap.track[tr] as Sample[]

export function trackIndex(r: Replay, t: number) {
  return Math.max(0, Math.min(r.snap.track.t.length - 1, Math.floor((t - r.trackT0) / r.trackStep)))
}

export function demoStatus(r: Replay, t: number, wd: WatchdogRun | null): Status {
  const i = trackIndex(r, t)
  const tractors: TractorStatus[] = r.snap.meta.tractors.map((id) => {
    const x = samples(r, id)[i]
    return {
      id, ...x,
      working: (x.speed ?? 0) > 0.5,    // DEMO approximation of the working gate (the engine's gate also needs 30 s / 5 min holds)
      present: null, missing: null, explanation: null, map_set: null, top_bf: null,
    }
  })
  return {
    data_time: new Date(t).toISOString(), progress: (t - r.t0) / (r.t1 - r.t0), speed: 'demo', done: t >= r.t1,
    error: null, tractors, open_episodes: null, last_watchdog: wd,
  }
}

export function demoAlarms(r: Replay, t: number): Alarm[] {
  const k = cutoff(r, t)
  const by = new Map<string, Record<string, unknown>>()
  const hist = new Map<string, Record<string, unknown>[]>()
  for (let j = 0; j < k; j++) {
    const e = r.snap.events[j]
    if (e.kind !== 'alarm' || !e.alarm_id) continue
    const h = hist.get(e.alarm_id) ?? []
    h.push(e as unknown as Record<string, unknown>)
    hist.set(e.alarm_id, h)
    by.set(e.alarm_id, e as unknown as Record<string, unknown>)
  }
  return [...by.entries()].map(([id, e]) => {
    const h = hist.get(id)!
    const fin = r.final.get(id)
    // the log has no event when an episode closes normally, so the end comes from the run's final state
    // and is shown only once data time has passed it
    const retracted = e.to === 'RETRACTED'
    const end = retracted ? (e.end as string) : fin?.end && ms(fin.end) <= t ? fin.end : (e.end as string | null) && ms(e.end as string) <= t ? (e.end as string) : null
    return normAlarm({ alarm_id: id, class: e.cls, state: e.to, start: e.start, end, tractors: e.tractors,
      confidence: end && !retracted && fin ? fin.confidence : e.confidence, reason: retracted ? e.reason : null, open: end == null && !retracted, history: h,
      track: fin?.track, suppressed: fin?.suppressed, evidence: fin?.evidence, conf: fin?.conf,
      episode: h.find((x) => x.episode)?.episode ?? null })
  })
}

export function demoWatchdog(r: Replay, t: number, n = 20): WatchdogRun[] {
  const k = cutoff(r, t)
  const out: WatchdogRun[] = []
  for (let j = k - 1; j >= 0 && out.length < n; j--) {
    const e = r.snap.events[j]
    if (e.kind === 'watchdog') out.push({ t_data: e.t_data_iso, final: Boolean(e.final), n_touched: e.n_touched ?? 0, alarms: [], actions: e.actions ?? [] })
  }
  return out.reverse()
}

export function demoFeed(r: Replay, t: number, n = 100): FeedItem[] {
  const k = cutoff(r, t)
  return r.snap.events.slice(Math.max(0, k - n), k) as FeedItem[]
}

/** Submitted row for an alarm (eval window only): same class, overlapping span, shared tractor. */
export function matchDetection(dets: Detection[], a: Alarm): Detection | undefined {
  const s = ms(a.start), e = a.end ? ms(a.end) : Infinity
  return dets.find((d) => d.class === a.cls && ms(d.start) < e && ms(d.end) > s && d.tractors.some((x) => a.tractors.includes(x)))
}

export function trail(r: Replay, tr: string, t: number, spanMs = 2 * 3600e3): HistPt[] {
  const i1 = trackIndex(r, t), i0 = trackIndex(r, t - spanMs)
  const out: HistPt[] = []
  const xs = samples(r, tr)
  for (let i = i0; i <= i1; i++) {
    const x = xs[i]
    if (x.lat != null && x.lon != null) out.push({ t: r.trackT0 + i * r.trackStep, lat: x.lat, lon: x.lon, cn0: x.cn0 })
  }
  return out
}

/** Map path for the last `spanMs` from the snapshot's 10 s path (falls back to the 1-min track). */
export function demoPath(r: Replay, tr: string, t: number, spanMs = 20 * 60e3): [number, number, number][] {
  const p = r.snap.path
  const enc = p?.[tr] as { lat: (number | null)[]; lon: (number | null)[] } | undefined
  if (!p || !enc) return trail(r, tr, t, spanMs).map((x) => [x.t, x.lat, x.lon])
  const stepMs = p.step_s * 1000, t0 = p.t0 * 1000
  const i1 = Math.min(enc.lat.length - 1, Math.floor((t - t0) / stepMs)), i0 = Math.max(0, Math.floor((t - spanMs - t0) / stepMs))
  const out: [number, number, number][] = []
  for (let i = i0; i <= i1; i++) {
    const la = enc.lat[i], lo = enc.lon[i]
    if (la != null && lo != null) out.push([t0 + i * stepMs, p.lat0 + la / 1e6, p.lon0 + lo / 1e6])
  }
  return out
}
