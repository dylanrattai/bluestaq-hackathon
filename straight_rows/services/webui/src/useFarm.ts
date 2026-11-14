// One hook feeds both pages. LIVE: polls the evaluator API (proxied by Vite, see vite.config.ts).
// DEMO: if the API is unreachable, replays public/demo/snapshot.json and keeps probing for the API.
import { useCallback, useEffect, useRef, useState } from 'react'
import { demoAlarms, demoFeed, demoPath, demoStatus, demoWatchdog, matchDetection, ms, prepare, trail, type Replay } from './demo'
import { normAlarm, normAlarms, normFeed, normStatus, normWatchdog } from './normalize'
import type { Alarm, FeedItem, HistPt, Meta, Mode, Snapshot, Status, WatchdogRun } from './types'

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
  trails: Record<string, HistPt[]>            // C/N0 history for the card sparklines
  paths: Record<string, [number, number, number][]>   // map paths [t ms, lat, lon], 10 s
  replay: Replay | null            // recorded run, used by DEMO and by the walkthroughs in both modes
  now: number                      // data time, ms
  playback: Playback
  forcedReplay: boolean            // API is live but the user rewound: showing the recorded run
  loadAlarm: (a: Alarm) => Promise<Alarm>
}

export interface Playback {
  live: boolean                    // controls drive the evaluator (POST /webui/control) instead of the local clock
  playing: boolean
  setPlaying: (p: boolean) => void
  speed: number | null             // data seconds per wall second; null = max
  setSpeed: (v: number | null) => void
  seek: (t: number) => void        // LIVE: forward = skip ahead, backward = switch to the recorded run
  seekTarget: number | null
  backToLive: () => void
}

async function postJson(url: string, body: unknown) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  return r.json() as Promise<Record<string, unknown>>
}

const DEMO_MAX = 7200

export function useFarm(): Farm {
  const [apiMode, setMode] = useState<Mode>('connecting')
  const [forcedReplay, setForcedReplay] = useState(false)
  const [seekTarget, setSeekTarget] = useState<number | null>(null)
  const mode: Mode = apiMode === 'live' && forcedReplay ? 'demo' : apiMode
  const [error, setError] = useState<string | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [alarms, setAlarms] = useState<Alarm[]>([])
  const [watchdog, setWatchdog] = useState<WatchdogRun[]>([])
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [replay, setReplay] = useState<Replay | null>(null)
  const [trails, setTrails] = useState<Record<string, HistPt[]>>({})
  const [paths, setPaths] = useState<Record<string, [number, number, number][]>>({})
  const [demoT, setDemoT] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(600)            // data seconds per wall second
  const liveTrails = useRef<Record<string, HistPt[]>>({})

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
    const id = setInterval(() => { if (apiMode !== 'live') void probe() }, PROBE_MS)
    return () => { dead = true; clearInterval(id) }
  }, [apiMode])

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
        const tNow = ms(s.data_time)
        for (const t of s.tractors) {
          if (t.lat == null || t.lon == null) continue
          const tr = (liveTrails.current[t.id] ??= [])
          const last = tr[tr.length - 1]
          if (last && tNow < last.t) tr.length = 0          // replay restarted
          if (!last || last.t !== tNow) tr.push({ t: tNow, lat: t.lat, lon: t.lon, cn0: t.cn0 })
          if (tr.length > TRAIL_MAX) tr.splice(0, tr.length - TRAIL_MAX)
        }
        setTrails({ ...liveTrails.current })
        setPaths(Object.fromEntries(s.tractors.map((t) => [t.id, (t.trail ?? []).map(([ts, la, lo]) => [ts * 1000, la, lo] as [number, number, number])])))
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
    if (!forcedReplay) setMeta(replay.snap.meta)
    setDemoT((t) => (t ? t : replay.t0 + 36 * 3600e3))   // start mid-run (05-05 12:00Z) so there is history to see
  }, [mode, replay, forcedReplay])
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
    setPaths(Object.fromEntries(replay.snap.meta.tractors.map((tr) => [tr, demoPath(replay, tr, demoT)])))
  }, [mode, replay, demoT])

  const loadAlarm = useCallback(async (a: Alarm): Promise<Alarm> => {
    if (mode === 'live') {
      try { return normAlarm(await getJson<Record<string, unknown>>(`/alarms/${encodeURIComponent(a.id)}`)) } catch { return a }
    }
    const d = replay ? matchDetection(replay.snap.submission, a) : undefined
    const fin = replay?.final.get(a.id)
    return { ...a, evidence: d?.evidence ?? fin?.evidence ?? a.evidence, conf: fin?.conf ?? a.conf }
  }, [mode, replay])

  const now = mode === 'demo' ? demoT : status ? ms(status.data_time) : NaN
  // a skip-ahead target is done once data time reaches it
  useEffect(() => { if (seekTarget != null && (mode !== 'live' || now >= seekTarget)) setSeekTarget(null) }, [seekTarget, now, mode])

  const live = mode === 'live'
  const liveSpeed = status ? (status.speed === 'max' ? null : Number(status.speed)) : null
  const playback: Playback = {
    live,
    playing: live ? true : playing,
    setPlaying,
    speed: live ? liveSpeed : speed >= DEMO_MAX ? null : speed,
    setSpeed: (v) => {
      if (live) void postJson('/webui/control', { speed: v ?? 'max' })
      else setSpeed(v ?? DEMO_MAX)
    },
    seek: (t) => {
      if (!live) { setDemoT(t); return }
      if (t > now) { setSeekTarget(t); void postJson('/webui/control', { seek: new Date(t).toISOString() }) }
      else { setDemoT(t); setForcedReplay(true); setPlaying(true) }
    },
    seekTarget,
    backToLive: () => { setForcedReplay(false); setDemoT(0) },
  }
  return {
    mode, error, meta, status, alarms, watchdog, feed, trails, paths, replay, now, playback, forcedReplay,
    loadAlarm,
  }
}
