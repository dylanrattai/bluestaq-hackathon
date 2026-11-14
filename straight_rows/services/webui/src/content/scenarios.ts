// The three walkthroughs are real episodes from the recorded run (bundled in public/demo/snapshot.json by
// `python -m runtime.webui`). States, times, tractors and Bayes factors come from the logged events; only the
// narration is written here. The noisy-OR panel uses the engine's own tables on an illustrative single-tractor
// finding set (the engine does not log per-batch findings), and says so.
import { ms, type Replay } from '../demo'
import type { Detection, HistoryEvent } from '../types'
import { dayTime, hhmm } from '../format'
import type { FindingInput } from '../noisyor'

export const STAGES = ['ingest', 'grid', 'detect', 'episode', 'diagnose', 'lifecycle', 'watchdog', 'output'] as const
export type Stage = typeof STAGES[number]
export const STAGE_NAME: Record<Stage, string> = {
  ingest: '01 INGEST', grid: '02 GRID', detect: '03 DETECT', episode: '04 EPISODE', diagnose: '05 DIAGNOSE',
  lifecycle: '06 LIFECYCLE', watchdog: '07 WATCHDOG', output: '08 OUTPUT',
}

export interface Step {
  t: string
  stages: Stage[]
  title: string
  body: string[]
  badge: string                      // NORMAL | WATCH | ALARM | CONFIRMED | RETRACTED
  cls?: string
  bf?: number | null                 // logged Bayes factor at this step
  span?: { start: string; end: string | null; tractors: string[] }
  spans?: { id: string; start: string; end: string | null; cls: string; badge: string }[]   // several alarms at once
  alarmId?: string                   // the alarm this step is about (confidence panel)
  net?: { label: string; input: Record<string, FindingInput> }
  row?: Detection
  mark?: [string, string]
}
export interface Scenario {
  key: string; title: string; outcome: string; oc: string
  t0: string; t1: string; channel: 'cn0' | 'xte'; chLabel: string
  logIds: string[]                    // alarm ids whose log lines the panel shows
  steps: Step[]
}

const P = (severity = 0.5): FindingInput => ({ state: 'present', severity })
const fset = (pres: Record<string, number>) => {
  const out: Record<string, FindingInput> = {}
  for (const f of ['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'])
    out[f] = f in pres ? P(pres[f]) : f === 'F11' ? { state: 'missing', severity: 0 } : { state: 'absent', severity: 0 }
  return out
}

const REASON_TEXT: Record<string, string> = {
  RAISED: 'A new episode crossed its opening rule, and the diagnosis network ran once over the episode-level findings.',
  ESCALATED: 'The provisional class reached BF ≥ 20 inside the most probable explanation, so the alarm went from WATCH to ALARM.',
  WEAKENED: 'The provisional class fell below BF 20, so the alarm went from ALARM to WATCH.',
  RECLASSIFIED: 'Re-running the network on the grown episode changed the most probable class.',
  JOINED: 'Another tractor\'s episode started within 5 min of the farm onset and joined it. The farm-level decision is recomputed: per-tractor log-likelihoods are summed and shared findings count once.',
  RELOCALISED: 'The watchdog moved a boundary with an exact single-change fit on 1 Hz data (a step or a hinge), using only data up to now.',
  CONFIRMED: 'The alarm survived a watchdog pass: at least 2 batches long, not entirely parked, and clipped to working time.',
  UNATTRIBUTED: 'The episode closed with no class at BF ≥ 20, so it is logged and never exported.',
  MERGED: 'A sub-episode was absorbed into its parent because it had the same class as the parent episode.',
  CLOSED: 'The episode recovered and the alarm closed.',
}
const REASON_STAGES: Record<string, Stage[]> = {
  RAISED: ['episode', 'diagnose', 'lifecycle'], ESCALATED: ['diagnose', 'lifecycle'], WEAKENED: ['diagnose', 'lifecycle'],
  RECLASSIFIED: ['diagnose', 'lifecycle'], JOINED: ['episode', 'diagnose', 'lifecycle'], RELOCALISED: ['watchdog'],
  CONFIRMED: ['watchdog'], UNATTRIBUTED: ['diagnose', 'lifecycle'], MERGED: ['watchdog'], CLOSED: ['lifecycle'],
}

function historySteps(h: HistoryEvent[], alarmId?: string): Step[] {
  return h.map((e) => ({
    alarmId: alarmId ?? (e as HistoryEvent & { alarm_id?: string }).alarm_id,
    t: e.t_data_iso,
    stages: REASON_STAGES[e.reason] ?? ['lifecycle'],
    title: `${e.from ?? 'NEW'} → ${e.to} · ${e.reason}`,
    body: [REASON_TEXT[e.reason] ?? '', e.note ? `Engine note: “${e.note}”` : ''].filter(Boolean),
    badge: e.to, cls: e.cls, bf: e.bf,
    span: { start: e.start, end: e.end, tractors: e.tractors },
  }))
}

const alarmEvents = (r: Replay, id: string) =>
  r.snap.events.filter((e) => e.kind === 'alarm' && e.alarm_id === id) as unknown as HistoryEvent[]
const addMin = (iso: string, m: number) => new Date(ms(iso) + m * 60e3).toISOString()

export function buildScenarios(r: Replay): Scenario[] {
  const ev = r.snap.events
  const isSite = (e: { note?: string }) => (e.note ?? '').includes('site multipath')
  const out: Scenario[] = []

  // Q: a quiet stretch in the evaluation window
  {
    const a = '2026-05-06T03:00:00+00:00', b = '2026-05-06T12:50:00+00:00'
    const span = ev.filter((e) => ms(e.t_data_iso) >= ms(a) && ms(e.t_data_iso) < ms(b))
    const wd = span.filter((e) => e.kind === 'watchdog')
    const site = span.filter((e) => e.kind === 'alarm' && isSite(e))
    const other = span.filter((e) => e.kind === 'alarm' && !isSite(e))
    const next = ev.find((e) => e.kind === 'alarm' && ms(e.t_data_iso) >= ms(b) && !isSite(e))
    out.push({
      key: 'q', title: 'Quiet stretch', outcome: 'NO EPISODE OPENS', oc: '#7dbbe0', t0: a, t1: b, channel: 'cn0',
      chLabel: 'Mean C/N0 (dB-Hz), 1-min samples', logIds: [],
      steps: [
        { t: addMin(a, 30), stages: ['ingest', 'grid'], badge: 'NORMAL', title: 'Telemetry in, 30-second batches out', body: [
          'Each tractor sends one row per second: C/N0, satellites, fix, link, cross-track error, speed and position. The space-weather and RFI feeds arrive on their own schedules.',
          'Everything is averaged into 30 s batches, because 1 Hz samples are strongly autocorrelated. A gate marks each tractor WORKING or PARKED.',
        ] },
        { t: addMin(a, 180), stages: ['detect'], badge: 'NORMAL', title: 'The CUSUMs stay below h', body: [
          'Each tractor\'s C/N0 level, log-variance, satellites and cross-track error are compared against a trailing 60 min reference of clean batch means.',
          'With k = 0.5 and h ≈ 6.80 (from Siegmund\'s approximation, not tuned), the design is one false alarm per 24 h per stream. Here, nothing crosses.',
        ], net: { label: 'A clean batch: every finding absent (engine tables, one tractor)', input: fset({}) } },
        { t: addMin(a, 360), stages: ['watchdog'], badge: 'NORMAL', title: `${wd.length} watchdog passes, ${wd.filter((e) => (e.n_touched ?? 0) > 0).length} touched anything`, body: [
          'Every 5 min of data time the watchdog re-checks recent alarms with hindsight. In a quiet stretch it has nothing to refine, merge or retract.',
        ], net: { label: 'A clean batch: every finding absent (engine tables, one tractor)', input: fset({}) } },
        { t: addMin(b, -1), stages: ['detect', 'output'], badge: 'NORMAL', title: `…but ${site.length} site-multipath reports`, body: [
          `Over the same ${((ms(b) - ms(a)) / 3600e3).toFixed(1)} h, the v2.3 site map turned ${site.length} min-C/N0 dips at learned headland cells into one-tractor MULTIPATH reports (the grey ticks on the chart).`,
          `Operators never see them as alarms, but they are exported. "Quiet" means no signal, position or equipment episode: ${other.length} other alarm events. ${next ? `It ends when ${next.alarm_id} (${next.cls}) is raised at ${dayTime(next.t_data_iso)}.` : ''}`,
        ] },
      ],
    })
  }

  // W: a retracted alarm, and the churn that came with it
  {
    const id = 'A-SPOOFING-2'
    const h = alarmEvents(r, id)
    if (h.length) {
      const ep = h.find((e) => e.episode)?.episode
      const again = ep ? [...new Set(ev.filter((e) => e.kind === 'alarm' && e.episode === ep && e.alarm_id !== id).map((e) => e.alarm_id!))] : []
      const lastAgain = ep ? ev.filter((e) => e.kind === 'alarm' && e.episode === ep).at(-1) : undefined
      const net = { label: 'Illustration: weak F8 drift alone on one tractor (engine tables)', input: fset({ F8: 0.25 }) }
      const t1 = lastAgain && ms(lastAgain.t_data_iso) - ms(h[0].t_data_iso) < 4 * 3600e3 ? addMin(lastAgain.t_data_iso, 10) : addMin(h.at(-1)!.t_data_iso, 60)
      out.push({
        key: 'w', title: 'Raised, then thrown away', outcome: `RETRACTED · ${h.at(-1)!.reason}`, oc: '#8b8579',
        t0: addMin(h[0].start, -20), t1, channel: 'xte', chLabel: 'Cross-track error |xte| (m), 1-min samples', logIds: [id, ...again],
        steps: [
          { t: h[0].start, stages: ['detect', 'episode'], badge: 'NORMAL', mark: [h[0].start, 'onset'], title: `T3's position track trips`, body: [
            'T3\'s cross-track drift CUSUM (on the batch median of cross-track error, headland turns masked) fires while its C/N0 looks normal. That is the spoofing fingerprint, so the position track opens episode ' + (ep ?? '') + '.',
            'This is during the labelled dev jamming at 21:00 (TRN-04). A jammer can push the solution sideways too.',
          ], net },
          ...historySteps(h).map((s) => ({ ...s, net })),
          { t: h.at(-1)!.t_data_iso, stages: ['output'], badge: 'RETRACTED', cls: 'SPOOFING', bf: h.at(-1)!.bf, span: { start: h.at(-1)!.start, end: h.at(-1)!.end, tractors: h.at(-1)!.tractors }, net,
            title: 'Never exported, never forgotten', body: [
              `BF ${h.at(-1)!.bf} is just under the ALARM line of 20, so the episode carries no class. It is logged as UNATTRIBUTED and never reaches submission.json.`,
              'The full history stays in alarm_log.jsonl, so anyone can audit why it was raised and why it was dropped.',
            ] },
          { t: lastAgain?.t_data_iso ?? h.at(-1)!.t_data_iso, stages: ['lifecycle', 'watchdog'], badge: 'RETRACTED', cls: 'SPOOFING', bf: h.at(-1)!.bf, net,
            title: `Weakness: re-raised ${again.length} more times`, body: [
              `The episode stayed open and was re-raised and retracted on every watchdog pass, as ${again.slice(0, 3).join(', ')}… An operator watching WATCH alerts would see it flicker. This alarm churn is a known flaw (see Limits).`,
            ] },
        ],
      })
    }
  }

  // E: two causes at once: a jammer switching on inside a storm (dev period, 05-05 evening)
  {
    const fin = [...r.final.values()]
    const sw = fin.find((a) => a.class === 'SPACE_WEATHER' && a.state === 'CONFIRMED' && String(a.start) >= '2026-05-05T18' && String(a.start) < '2026-05-05T20' && (a.tractors as string[]).length >= 2)
    const jam = sw && fin.find((a) => a.class === 'JAMMING' && a.state === 'CONFIRMED' && ms(a.start as string) > ms(sw.start as string) && ms(a.start as string) < ms(sw.end as string ?? '2100'))
    const subEv = sw && ev.find((e) => e.kind === 'alarm' && e.cls === 'SPACE_WEATHER' && e.alarm_id !== sw.alarm_id && ms(e.t_data_iso) > ms(sw.start as string) && ms(e.t_data_iso) < ms(sw.end as string) && (e.note ?? '').includes('episode S-'))
    if (sw && jam) {
      const ids = [sw.alarm_id, ...(subEv ? [subEv.alarm_id!] : []), jam.alarm_id]
      const subEp = subEv?.episode
      const churn = subEp ? new Set(ev.filter((e) => e.kind === 'alarm' && e.episode === subEp).map((e) => e.alarm_id)).size - 1 : 0
      const stateAt = (t: string) => ids.map((id) => {
        const es = ev.filter((e) => e.kind === 'alarm' && e.alarm_id === id && ms(e.t_data_iso) <= ms(t))
        const last = es.at(-1)
        if (!last) return null
        const f = r.final.get(id)
        const end = last.to === 'RETRACTED' ? last.end : f?.end && ms(f.end) <= ms(t) ? f.end : null
        return { id, start: last.start, end, cls: last.cls, badge: last.to }
      }).filter((x): x is NonNullable<typeof x> => x != null)
      const NOTE: Record<string, string> = {
        [`${sw.alarm_id}|RAISED`]: 'A farm episode opens on all three tractors at once: C/N0 drops that are nearly equal across machines (F3), extra scintillation (F2), and the space-weather indices active (F9). The class is decided once for the whole farm.',
        [`${jam.alarm_id}|RAISED`]: 'The v2.3 storm-jam rule fires: 1-min mean C/N0 stepped ≥ 6 dB further down on a floor already ≤ 40 dB-Hz, on ≥ 2 tractors for ≥ 3 minutes. That opens its own JAMMING detection next to the storm. It is rule-based, so there is no Bayes factor.',
        [`${jam.alarm_id}|CONFIRMED`]: 'Two confirmed detections now overlap on the same tractors: SPACE_WEATHER (since the storm onset) and JAMMING (since the step). Each keeps its own span, class and action.',
        [`${jam.alarm_id}|CLOSED`]: 'The jammer stops: every tractor is back within 3 dB of its pre-step reference for 2 minutes. The storm detection carries on.',
        ...(subEv ? {
          [`${subEv.alarm_id}|RAISED`]: 'Inside the open storm, the in-event step CUSUM fires on ≥ 2 tractors within 2 batches: an abrupt extra drop stacked on top of the storm. It opens a sub-episode, classified against the in-event reference.',
          [`${subEv.alarm_id}|MERGED`]: 'The sub-episode\'s own network call was SPACE_WEATHER, the same class as its parent, so it is absorbed. On its own, the physics table would have lost the jammer inside the storm. That is why the storm-jam rule exists, and it is a known weakness.',
        } : {}),
      }
      const steps: Step[] = ids.flatMap((id) => historySteps(alarmEvents(r, id), id))
        .filter((st) => st.badge !== 'RETRACTED' || st.title.includes('MERGED'))
        .sort((x, y) => ms(x.t) - ms(y.t))
        .filter((st, i, arr) => !(st.title.includes('MERGED') && arr.findIndex((z) => z.title.includes('MERGED')) !== i))
        .map((st) => {
          const key = `${st.alarmId}|${st.title.split(' · ')[1]}`
          return { ...st, body: NOTE[key] ? [NOTE[key], ...st.body.filter((b) => b.startsWith('Engine note'))] : st.body, spans: stateAt(st.t) }
        })
      const swNet = { label: 'Illustration: the storm\'s findings on one tractor (engine tables)', input: fset({ F1: 0.5, F2: 0.5, F3: 0.5, F9: 0.75 }) }
      const rows = [sw, jam].map((a) => ({ start: a.start, end: a.end, tractors: a.tractors, class: a.class, action: r.snap.meta.actions[a.class as string], confidence: a.confidence, evidence: a.evidence }))
      out.push({
        key: 'e', title: 'Jamming inside a storm', outcome: 'TWO OVERLAPPING DETECTIONS', oc: '#9d85f2',
        t0: addMin(sw.start as string, -15), t1: addMin(sw.end as string, 15), channel: 'cn0', chLabel: 'Mean C/N0 (dB-Hz), 1-min samples', logIds: ids,
        steps: [
          { t: addMin(sw.start as string, -5), stages: ['ingest', 'detect'], badge: 'NORMAL', title: 'Normal baseline', body: [
            'All three tractors sit near their usual C/N0 with RTK fix. This is the dev period (05-05), so the outcome can be checked against the labels.',
          ] },
          ...steps.map((st) => ({ ...st, net: st.alarmId === jam.alarm_id ? undefined : swNet })),
          { t: sw.end as string, stages: ['watchdog', 'output'], badge: 'CONFIRMED', cls: 'SPACE_WEATHER', alarmId: jam.alarm_id, spans: stateAt(addMin(sw.end as string, 1)),
            title: 'Two causes, two detections', body: [
              `The storm ends at ${hhmm(sw.end as string)}. The result is two overlapping detections on the same tractors, each with its own class, span and action: ${rows.map((x) => `${x.class} ${hhmm(x.start as string)}–${hhmm(x.end as string)} (${x.action})`).join(' and ')}.`,
              'The jamming confidence does not hold the storm\'s signs against jamming: F3 and F9 are "explained by" the confirmed storm (see the confidence panel). This is the dev period, so the rows are not in submission.json. The dev check matched both labelled episodes (TRN-02 storm, TRN-05 jam inside it).',
              churn > 0 ? `Weakness: after the merge, the same sub-episode was re-raised and absorbed ${churn} more times (alarm churn).` : '',
            ].filter(Boolean), row: rows as unknown as Detection },
        ],
      })
    }
  }
  return out
}

export const fmtLog = (e: HistoryEvent & { alarm_id?: string }) =>
  `${hhmm(e.t_data_iso, true)}  ${e.to.padEnd(10)} ${e.cls.padEnd(15)} ${e.tractors.join(',').padEnd(9)} ${e.alarm_id ?? ''} ${e.reason !== 'RAISED' ? e.reason : ''}`
