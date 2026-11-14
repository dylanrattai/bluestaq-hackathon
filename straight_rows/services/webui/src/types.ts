// Shapes the UI works with after normalisation (see normalize.ts). Field names follow the evaluator's API
// (runtime/api.py, runtime/engine.py snapshot(), diagnose/lifecycle.py Alarm.summary/full).

export type AlarmState = 'WATCH' | 'ALARM' | 'CONFIRMED' | 'RETRACTED'
export type Cls = 'SPACE_WEATHER' | 'JAMMING' | 'SPOOFING' | 'EQUIPMENT_FAULT' | 'MULTIPATH' | 'OTHER'
export const CLASSES: Cls[] = ['SPACE_WEATHER', 'JAMMING', 'SPOOFING', 'EQUIPMENT_FAULT', 'MULTIPATH']

export interface HistoryEvent {
  t_data_iso: string
  from: string | null
  to: string
  reason: string
  cls: string
  bf: number | null
  confidence: number
  tractors: string[]
  start: string
  end: string | null
  note?: string
  episode?: string
}

export interface Alarm {
  id: string
  cls: Cls
  state: AlarmState
  start: string
  end: string | null          // null while open
  tractors: string[]
  confidence: number
  reason: string | null
  track: string               // signal | sub | position | dip | site | stormjam
  open: boolean
  unattributed: boolean
  suppressed: boolean         // v2.3 site multipath: ISA-18.2 alert, hidden from /alarms by default but exported
  episode: string | null
  evidence?: string[]
  history?: HistoryEvent[]
  conf?: ConfInfo | null      // engine's confidence breakdown (extra.conf)
}

export interface ConfInfo { p: number; br: unknown; line?: string }

export interface TopBf { cause: string; bf: number; posterior: number }

export interface TractorStatus {
  id: string
  working: boolean
  lat: number | null
  lon: number | null
  cn0: number | null
  cn0_min: number | null
  sats: number | null
  fix: string | null
  speed: number | null
  link_ok: boolean | null
  xte?: number | null
  trail?: [number, number, number][] | null    // [epoch s, lat, lon], last 20 min at 10 s (webui hook)
  present: Record<string, number> | null      // finding -> severity (live only)
  missing: string[] | null
  explanation: string[] | null
  map_set: string[] | null
  top_bf: TopBf[] | null
}

export interface OpenEpisode {
  episode: string
  track: string
  start: string
  tractors: string[]
  provisional?: string | null
  groups?: [string, string[], number][]
}

export interface WatchdogRun {
  t_data: string
  final: boolean
  n_touched: number
  alarms: string[]
  actions: string[]
}

export interface Status {
  data_time: string
  progress: number
  speed: string | number
  done: boolean
  error: string | null
  tractors: TractorStatus[]
  open_episodes: OpenEpisode[] | null
  last_watchdog: WatchdogRun | null
}

export interface FeedItem {
  t_data_iso: string
  kind: 'alarm' | 'watchdog'
  alarm_id?: string
  cls?: string
  from?: string | null
  to?: string
  reason?: string
  tractors?: string[]
  note?: string
  bf?: number | null
  actions?: string[]
  n_touched?: number
}

export interface Rfi {
  id: string
  t_event: string
  t_avail: string
  lat: number | null
  lon: number | null
  radius_km: number
  confidence: number
  farm_wide: boolean
  text: string
}

export interface Fusion {
  causes: string[]
  prior: number
  floor: number
  bf_watch: number
  bf_alarm: number
  leak_default: number
  leak_f9: number
  leaks: Record<string, number>
  links: Record<string, Record<string, number>>
}

export interface Site { lat: [number, number]; lon: [number, number]; rate: number | null; passes: number | null; dips: number | null; name?: string }

export interface Meta {
  version: string
  config: string | null
  data_start: string
  data_end: string
  window: { start: string; end: string }
  batch_s: number
  watchdog_every_s: number
  tractors: string[]
  fields: { tractor: string; source: string; lat: [number, number]; lon: [number, number] }[]
  rfi: Rfi[]
  fusion: Fusion
  episodes: { open_m: number; open_n: number; close_batches: number; phi_present: number }
  actions: Record<string, string>
  site_multipath: {
    n_cells_seen: number; n_sites: number; site_passes: number; site_dips: number
    north_headland_rate: number; south_headland_rate: number; dev_end: string; sites: Site[]
  } | null
  site_cap: number
  confidence?: {
    method: string
    groups: Record<string, { name: string; findings: string[] }>
    hypothesis?: { scale: number; cap_group: number; rival_ratio: number; rival_pts: number; clip: [number, number]; quorum: number }
    table?: { detection_prior?: { p: number; hits: number; n: number }; lr_weight?: number; clip?: [number, number]; classes?: Record<string, { prior: number; groups: Record<string, unknown> }> }
  }
  scores: {
    headline: string | null
    lenient: string | null
    versions: { version: string; score: string; macro_f1: string; found: string; extras: string; onset: string }[]
    classes: { class: string; truth: string; found: string; extra: string; missed: string; precision: string; recall: string; f1: string }[]
  } | null
}

export interface Detection {
  start: string
  end: string
  tractors: string[]
  class: Cls
  action: string
  confidence: number
  evidence: string[]
}

// What runtime/webui.py writes to public/demo/snapshot.json
export interface Snapshot {
  meta: Meta
  track: { t: string[] } & Record<string, unknown>
  events: (FeedItem & HistoryEvent & { kind: 'alarm' | 'watchdog'; final?: boolean })[]
  path?: { t0: number; step_s: number; lat0: number; lon0: number } & Record<string, unknown>   // 10 s map path, micro-degree offsets
  alarms: (Record<string, unknown> & { alarm_id: string; end: string | null; track: string; suppressed: boolean; evidence: string[]; conf?: ConfInfo })[]   // final state at the end of the run
  submission: Detection[]
}

export interface HistPt { t: number; lat: number; lon: number; cn0: number | null }

export type Mode = 'connecting' | 'live' | 'demo'
