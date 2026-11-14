// One hook feeds both pages. LIVE: polls the evaluator API (proxied by Vite, see vite.config.ts).
// DEMO: if the API is unreachable, replays public/demo/snapshot.json and keeps probing for the API.
import { useCallback, useEffect, useRef, useState } from 'react'
import { demoAlarms, demoFeed, demoStatus, demoWatchdog, matchDetection, ms, prepare, trail, type Replay } from './demo'
import { normAlarm, normAlarms, normFeed, normStatus, normWatchdog } from './normalize'
import type { Alarm, FeedItem, Meta, Mode, Snapshot, Status, WatchdogRun } from './types'

const POLL_MS = 2000
const ALARM_POLL_MS = 5000
const PROBE_MS = 10000
const TRAIL_MAX = 600

async function getJson<T>(url: string, timeoutMs = 2500): Promise<T> {
  const c = new AbortController()
  const id = setTimeout(() => c.abort(), timeoutMs)
  try {
    const r = await fetch(url, { signal: c.signal, cache: 'no-store' })
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
    return (await r.json()) as T
  } finally {
    clearTimeout(id)
  }
}

let snapPromise: Promise<Replay | null> | null = null
export function loadSnapshot(): Promise<Replay | null> {
  snapPromise ??= getJson<Snapshot>(`${import.meta.env.BASE_URL}demo/snapshot.json`, 20000).then(prepare).catch(() => null)
  return snapPromise
}

export interface Farm {
  mode: Mode
  error: string | null
  meta: Meta | null
  status: Status | null
  alarms: Alarm[]
  watchdog: WatchdogRun[]
  feed: FeedItem[]
  trails: Record<string, [number, number][]>
  replay: Replay | null            // recorded run, used by DEMO and by the walkthroughs in both modes
  now: number                      // data time, ms
  demo: { playing: boolean; speed: number; setPlaying: (p: boolean) => void; setSpeed: (s: number) => void; seek: (t: number) => void }
  loadAlarm: (a: Alarm) => Promise<Alarm>
}

export function useFarm(): Farm {
  const [mode, setMode] = useState<Mode>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [alarms, setAlarms] = useState<Alarm[]>([])
  const [watchdog, setWatchdog] = useState<WatchdogRun[]>([])
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [replay, setReplay] = useState<Replay | null>(null)
  const [trails, setTrails] = useState<Record<string, [number, number][]>>({})
  const [demoT, setDemoT] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(600)            // data seconds per wall second
  const liveTrails = useRef<Record<string, [number, number][]>>({})

  useEffect(() => { void loadSnapshot().then(setReplay) }, [])

  // decide the mode: try the API, else DEMO; in DEMO keep probing
  useEffect(() => {
    let dead = false
    const probe = async () => {
      try {
        const m = await getJson<Meta>('/webui/meta')
        if (dead) return
        setMeta(m); setMode('live'); setError(null)
      } catch (e) {
        if (dead) return
        setMode((cur) => (cur === 'live' ? cur : 'demo'))
        setError(String(e))
      }
    }
    void probe()
    const id = setInterval(() => { if (mode !== 'live') void probe() }, PROBE_MS)
    return () => { dead = true; clearInterval(id) }
  }, [mode])

  // LIVE polling
  useEffect(() => {
    if (mode !== 'live') return
    let dead = false, fails = 0
    const fast = async () => {
      try {
        const [st, wd, fd] = await Promise.all([getJson<Record<string, unknown>>('/status'), getJson<unknown[]>('/watchdog'), getJson<unknown>('/feed')])
        if (dead) return
        fails = 0
        const s = normStatus(st)
        setStatus(s); setWatchdog((wd as Record<string, unknown>[]).map(normWatchdog)); setFeed(normFeed(fd))
        for (const t of s.tractors) {
          if (t.lat == null || t.lon == null) continue
          const tr = (liveTrails.current[t.id] ??= [])
          const last = tr[tr.length - 1]
          if (!last || last[0] !== t.lat || last[1] !== t.lon) tr.push([t.lat, t.lon])
          if (tr.length > TRAIL_MAX) tr.splice(0, tr.length - TRAIL_MAX)
        }
        setTrails({ ...liveTrails.current })
      } catch (e) {
        if (++fails >= 3 && !dead) { setError(String(e)); setMode('demo') }
      }
    }
    const slow = async () => {
      try {
        const al = await getJson<unknown>('/alarms?include_suppressed=1', 8000)
        if (!dead) setAlarms(normAlarms(al))
      } catch { /* the fast loop decides the mode */ }
    }
    void fast(); void slow()
    const a = setInterval(fast, POLL_MS), b = setInterval(slow, ALARM_POLL_MS)
    return () => { dead = true; clearInterval(a); clearInterval(b) }
  }, [mode])

  // DEMO clock
  useEffect(() => {
    if (mode !== 'demo' || !replay) return
    setMeta(replay.snap.meta)
    setDemoT((t) => (t ? t : replay.t0 + 36 * 3600e3))   // start mid-run (05-05 12:00Z) so there is history to see
  }, [mode, replay])
  useEffect(() => {
    if (mode !== 'demo' || !replay || !playing) return
    // 4 Hz is plenty: at 600x one tick is 2.5 min of data time, and every tick re-renders the timeline
    let last = performance.now()
    const id = setInterval(() => {
      const now = performance.now()
      const dt = now - last
      last = now
      setDemoT((t) => Math.min(replay.t1, t + dt * speed))
    }, 250)
    return () => clearInterval(id)
  }, [mode, replay, playing, speed])
  // DEMO derived state
  useEffect(() => {
    if (mode !== 'demo' || !replay || !demoT) return
    const wd = demoWatchdog(replay, demoT)
    setStatus(demoStatus(replay, demoT, wd[wd.length - 1] ?? null))
    setAlarms(demoAlarms(replay, demoT))
    setWatchdog(wd)
    setFeed(demoFeed(replay, demoT))
    setTrails(Object.fromEntries(replay.snap.meta.tractors.map((tr) => [tr, trail(replay, tr, demoT)])))
  }, [mode, replay, demoT])

  const loadAlarm = useCallback(async (a: Alarm): Promise<Alarm> => {
    if (mode === 'live') {
      try { return normAlarm(await getJson<Record<string, unknown>>(`/alarms/${encodeURIComponent(a.id)}`)) } catch { return a }
    }
    const d = replay ? matchDetection(replay.snap.submission, a) : undefined
    return { ...a, evidence: d?.evidence ?? replay?.final.get(a.id)?.evidence ?? a.evidence }
  }, [mode, replay])

  const now = mode === 'demo' ? demoT : status ? ms(status.data_time) : NaN
  return {
    mode, error, meta, status, alarms, watchdog, feed, trails, replay, now,
    demo: { playing, speed, setPlaying, setSpeed, seek: (t) => setDemoT(t) },
    loadAlarm,
  }
}
