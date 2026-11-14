// The three walkthroughs are real episodes from the recorded run (out/alarm_log.jsonl + out/submission.json,
// bundled as public/demo/snapshot.json). Steps are generated from the logged events; only the narration is written here.
import { matchDetection, ms, type Replay } from '../demo'
import { normAlarm } from '../normalize'
import type { Detection, HistoryEvent } from '../types'
import { dayTime, fmtBf, hhmm } from '../format'
import type { FindingInput } from '../noisyor'

export interface Step {
  t: string
  title: string
  body: string[]
  net?: { label: string; input: Record<string, FindingInput> }
  row?: Detection
}
export interface Scenario { key: string; title: string; summary: string; steps: Step[] }

const P = (severity = 0.5): FindingInput => ({ state: 'present', severity })
const A: FindingInput = { state: 'absent', severity: 0 }
const M: FindingInput = { state: 'missing', severity: 0 }
const fset = (pres: Record<string, number>, missing: string[] = ['F11']) => {
  const out: Record<string, FindingInput> = {}
  for (const f of ['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12']) out[f] = f in pres ? P(pres[f]) : missing.includes(f) ? M : A
  return out
}

const REASON_TEXT: Record<string, string> = {
  RAISED: 'A new episode crossed the opening rule and the network gave this provisional class.',
  ESCALATED: 'The provisional class reached BF ≥ 20 inside the explanation set: WATCH → ALARM.',
  WEAKENED: 'The provisional class fell below BF 20: ALARM → WATCH.',
  RECLASSIFIED: 'Re-running the network on the grown episode changed the most probable class.',
  JOINED: 'Another tractor\'s episode started within 5 min of the farm onset and joined it. The farm-level BF is recomputed over all members.',
  RELOCALISED: 'The watchdog moved a boundary with an exact single-change fit on 1 Hz data (data ≤ now only).',
  CONFIRMED: 'The alarm survived a watchdog pass: ≥ 2 batches, not entirely parked, clipped to working time.',
  UNATTRIBUTED: 'The episode closed with no class at BF ≥ 20, so it is logged and never exported.',
  MERGED: 'A sub-episode was absorbed into its parent because it had the same class as the parent episode.',
  CLOSED: 'The episode recovered and the alarm closed.',
  NOT_SUSTAINED: 'Shorter than 2 batches after refinement, so it was retracted.',
  PARKED: 'Entirely parked, so it was retracted.',
}

function historySteps(h: HistoryEvent[]): Step[] {
  return h.map((e) => ({
    t: e.t_data_iso,
    title: `${e.from ?? '∅'} → ${e.to} · ${e.reason}`,
    body: [
      REASON_TEXT[e.reason] ?? '',
      `Class ${e.cls} · tractors ${e.tractors.join(', ')} · span ${hhmm(e.start, true)}–${e.end ? hhmm(e.end, true) : 'open'}` +
        (e.bf != null ? ` · logged BF ${fmtBf(e.bf)}` : ''),
      e.note ? `Engine note: “${e.note}”` : '',
    ].filter(Boolean),
  }))
}

function alarmEvents(r: Replay, id: string) {
  return r.snap.events.filter((e) => e.kind === 'alarm' && e.alarm_id === id) as unknown as HistoryEvent[]
}

export function buildScenarios(r: Replay): Scenario[] {
  const ev = r.snap.events
  const inSpan = (a: string, b: string) => ev.filter((e) => ms(e.t_data_iso) >= ms(a) && ms(e.t_data_iso) < ms(b))
  const out: Scenario[] = []

  // Q: a quiet stretch in the evaluation window
  {
    const a = '2026-05-06T03:00:00+00:00', b = '2026-05-06T12:50:00+00:00'
    const span = inSpan(a, b)
    const wd = span.filter((e) => e.kind === 'watchdog')
    const site = span.filter((e) => e.kind === 'alarm' && (e.note ?? '').includes('site multipath'))
    const other = span.filter((e) => e.kind === 'alarm' && !(e.note ?? '').includes('site multipath'))
    const next = ev.find((e) => e.kind === 'alarm' && ms(e.t_data_iso) >= ms(b) && !(e.note ?? '').includes('site multipath'))
    out.push({
      key: 'q', title: 'Quiet stretch', summary: `${dayTime(a)}–${hhmm(b)}, evaluation window`,
      steps: [
        { t: a, title: 'Nothing wrong: the CUSUMs stay below h', body: [
          'Each tractor\'s C/N0 level, log-variance, satellites and cross-track error are tracked against a trailing 60 min reference of clean batch means.',
          'With k = 0.5 and h ≈ 6.80, the designed false-alarm rate is one per 24 h per stream. A quiet stretch should look exactly like this: no signal or position episode opens.',
        ], net: { label: 'A clean batch: every finding absent', input: fset({}) } },
        { t: a, title: `${wd.length} watchdog passes, ${wd.filter((e) => (e.n_touched ?? 0) > 0).length} touched anything`, body: [
          'Every 5 min of data time the watchdog re-checks recent alarms with hindsight. In a quiet stretch it has nothing to refine, merge or retract.',
        ] },
        { t: a, title: `…but ${site.length} site-multipath reports`, body: [
          `Over the same ${((ms(b) - ms(a)) / 3600e3).toFixed(1)} h, the v2.3 site map turned ${site.length} min-C/N0 dips at learned headland cells into one-tractor MULTIPATH reports.`,
          'Operators never see them as alarms (they are suppressed alerts), but they are exported. "Quiet" here means no signal, position or equipment episode. It does not mean zero output. See Limits.',
        ] },
        { t: b, title: other.length ? `${other.length} other alarm events in the span` : 'No other alarm event in the span', body: [
          next ? `The quiet stretch ends when ${next.alarm_id} is raised at ${dayTime(next.t_data_iso)} (${next.cls}).` : '',
        ].filter(Boolean) },
      ],
    })
  }

  // W: a retracted alarm (and the churn that came with it)
  {
    const id = 'A-SPOOFING-2'
    const h = alarmEvents(r, id)
    const ep = h.find((e) => e.episode)?.episode
    const again = ep ? new Set(ev.filter((e) => e.kind === 'alarm' && e.episode === ep && e.alarm_id !== id).map((e) => e.alarm_id)) : new Set()
    out.push({
      key: 'w', title: 'Retracted alarm', summary: `${id} · ${h[0] ? dayTime(h[0].t_data_iso) : ''} · ${h.at(-1)?.reason ?? ''}`,
      steps: [
        { t: h[0]?.t_data_iso ?? '', title: 'A position episode opens on T3', body: [
          'T3\'s cross-track drift CUSUM fires while its C/N0 stays normal, which is the spoofing fingerprint. The position track opens episode ' + (ep ?? '') + '.',
          'The network puts SPOOFING at BF 19.98: strong enough for WATCH (≥ 3), but just short of ALARM (≥ 20).',
        ], net: { label: 'F8 drift alone, low severity (illustration)', input: fset({ F8: 0.25 }) } },
        ...historySteps(h),
        { t: h.at(-1)?.t_data_iso ?? '', title: 'Why it was not exported', body: [
          'Below BF 20 the episode carries no class, so it is logged as UNATTRIBUTED and never reaches submission.json.',
          'This overlaps the labelled jamming at 21:00 (TRN-04). The v2.3 spoof-under-jam rule exists so a jammer\'s side effects are not reported as spoofing.',
        ] },
        { t: h.at(-1)?.t_data_iso ?? '', title: `Weakness: the same episode re-raised ${again.size} more times`, body: [
          `The episode stayed open and was re-raised and retracted every 5 min as ${[...again].slice(0, 3).join(', ')}… An operator would see the same WATCH flicker. This is alarm churn, a known flaw.`,
        ] },
      ],
    })
  }

  // E: a confirmed multi-tractor detection in the evaluation window
  {
    const id = 'A-JAMMING-5'
    const h = alarmEvents(r, id)
    const last = h.at(-1)
    const row = last ? matchDetection(r.snap.submission, normAlarm({ ...last, alarm_id: id, class: last.cls, state: last.to })) : undefined
    out.push({
      key: 'e', title: 'Confirmed multi-tractor', summary: `${id} · ${h[0] ? dayTime(h[0].t_data_iso) : ''} · JAMMING on T1–T3`,
      steps: [
        ...historySteps(h).map((s, i) => i === 0 ? { ...s, net: { label: 'One tractor: F1 (L2 drop) + F4 unequal drop', input: fset({ F1: 0.5, F4: 0.5 }) } } : s),
        { t: row?.start ?? '', title: 'Exported row', body: [
          'Only CONFIRMED detections in the evaluation window are exported. The action comes from the fixed class → action table.',
          'There was no RFI report for this window, so the context group counts against it: P = 0.89, versus 0.97 for detections that had one.',
        ], row },
      ],
    })
  }
  return out.filter((s) => s.steps.length > 1)
}
