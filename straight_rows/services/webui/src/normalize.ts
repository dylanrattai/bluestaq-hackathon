// The only place API field names are mapped. If the evaluator's JSON changes, edit here.
import type { Alarm, AlarmState, Cls, FeedItem, OpenEpisode, Status, TractorStatus, WatchdogRun } from './types'

type Raw = Record<string, unknown>
const s = (v: unknown): string | null => (v == null ? null : String(v))
const n = (v: unknown): number | null => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v))
const arr = <T = string>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])

export function normAlarm(r: Raw): Alarm {
  const track = s(r.track) ?? 'signal'
  const note = arr<Raw>(r.history).map((h) => String(h.note ?? '')).join(' ')
  return {
    id: String(r.alarm_id ?? r.id),
    cls: String(r.class ?? r.cls) as Cls,
    state: String(r.state) as AlarmState,
    start: String(r.start),
    end: s(r.end),
    tractors: arr(r.tractors),
    confidence: n(r.confidence) ?? 0,
    reason: s(r.reason),
    track,
    open: Boolean(r.open ?? r.end == null),
    unattributed: Boolean(r.unattributed),
    // site-multipath reports are the ones the engine hides from /alarms unless include_suppressed=1
    suppressed: Boolean(r.suppressed) || track === 'site' || note.includes('suppressed'),
    episode: s(r.episode),
    evidence: r.evidence ? arr(r.evidence) : undefined,
    history: r.history ? arr(r.history) : undefined,
  }
}

export function normAlarms(r: unknown): Alarm[] {
  const list = Array.isArray(r) ? r : arr((r as Raw)?.alarms)
  return (list as Raw[]).map(normAlarm)
}

function normTractor(id: string, v: Raw): TractorStatus {
  return {
    id,
    working: v.state ? v.state === 'WORKING' : Boolean(v.working),
    lat: n(v.lat), lon: n(v.lon),
    cn0: n(v.cn0 ?? v.mean_cn0), cn0_min: n(v.cn0_min), sats: n(v.sats), fix: s(v.fix),
    speed: n(v.speed), link_ok: v.link_ok == null ? null : Boolean(v.link_ok),
    present: (v.present as Record<string, number>) ?? (v.findings as Record<string, number>) ?? null,
    missing: v.missing ? arr(v.missing) : null,
    explanation: v.explanation ? arr(v.explanation) : null,
    map_set: v.map_set ? arr(v.map_set) : null,
    top_bf: (v.top_bf ?? v.top_bfs) ? arr(v.top_bf ?? v.top_bfs) : null,
  }
}

export function normWatchdog(r: Raw): WatchdogRun {
  return {
    t_data: String(r.t_data ?? r.t_data_iso ?? r.time),
    final: Boolean(r.final),
    n_touched: n(r.n_touched) ?? arr(r.alarms ?? r.touched).length,
    alarms: arr(r.alarms ?? r.touched),
    actions: arr(r.actions),
  }
}

export function normStatus(r: Raw): Status {
  const tr = r.tractors
  const tractors = Array.isArray(tr)
    ? (tr as Raw[]).map((v) => normTractor(String(v.id ?? v.tractor), v))
    : Object.entries((tr as Record<string, Raw>) ?? {}).map(([k, v]) => normTractor(k, v))
  return {
    data_time: String(r.data_time),
    progress: n(r.progress) ?? 0,
    speed: (r.speed as string | number) ?? '',
    done: Boolean(r.done),
    error: s(r.error),
    tractors,
    open_episodes: r.open_episodes ? arr<OpenEpisode>(r.open_episodes) : null,
    last_watchdog: r.last_watchdog ? normWatchdog(r.last_watchdog as Raw) : null,
  }
}

export const normFeed = (r: unknown): FeedItem[] => arr<FeedItem>(r)
