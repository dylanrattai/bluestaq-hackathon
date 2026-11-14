import { useMemo } from 'react'
import type { Meta } from '../types'
import type { Replay } from '../demo'
import { REFS } from '../content/flow'
import { CLS_LABEL, CV } from '../format'

const SIG: [string, string[]][] = [
  ['SPACE_WEATHER', ['C/N0 drops on every working tractor at once, roughly equally (F1 on ≥ 2, F3)', 'Scintillation: extra C/N0 oscillation (F2); fixes slip to float or DGPS', 'Kp ≥ 7, Dst ≤ −100 nT or S4 ≥ 0.3 (F9). An advisory alone doesn\'t count', 'Ramp onset (hinge fit)']],
  ['JAMMING', ['Drop is unequal across tractors (F4: > 6 dB spread), set by distance to the emitter', 'Fix degrades with the signal; pulsed sources make short dips (F5)', 'RFI report usable only from event + 30 min (F10), and often late or missing', 'Step onset and step recovery']],
  ['SPOOFING', ['C/N0, satellites and fix all look healthy', 'Cross-track error drifts on one machine (F8)', 'Other machines unaffected (F12)', 'Ramp onset (hinge fit). Along-track drag is invisible']],
  ['EQUIPMENT_FAULT', ['One machine only (F12)', 'Correction link down (F7), or fix/satellites degraded while C/N0 is normal (F6)', 'A fix drop ≤ 4 min after link loss is the link\'s fault', 'Step onset']],
  ['MULTIPATH', ['Min-C/N0 dip ≥ 5 dB lasting 5–60 s', 'Recurs at the north headland (95% of dev passes)', 'Other machines, mean C/N0, fix and cross-track unaffected', 'Looks like any headland-turn dip without the learned site map']],
]
const ALG: [string, string, string][] = [
  ['Change detection', 'Tabular CUSUM on batch means, onset backtracked to the last reset; h from Siegmund\'s approximation', 'Page (1954); R2, R3'],
  ['Autocorrelation', '30 s batch means + AR(1) σ inflation (capped at 3σ)', 'Runger & Willemain (1995); R8, R9'],
  ['Short dips', 'Finite moving-average transient test against a lagged local median', 'Egea-Roca et al. (R4)'],
  ['Spoofing', 'Cumulative drift monitor on cross-track error, turns masked', 'Kujur, Khanafseh & Pervan (R10)'],
  ['Persistence', '2-of-3 batches to open an episode, 10 quiet batches to close', 'Abraham, M-of-N (R28)'],
  ['Attribution', 'Leaky noisy-OR, exact 2⁶ enumeration, explaining away', 'Heckerman (1989); Wellman & Henrion (1993)'],
  ['Thresholds', 'Bayes factors on the Kass & Raftery scale: WATCH 3, ALARM 20', 'Kass & Raftery (1995)'],
  ['Boundaries', 'Exact single change point: a step, or a continuous hinge', 'Fearnhead et al. (2019); R31'],
  ['Alarm design', 'Alert vs alarm tiers; suppressed nuisance alerts', 'ISA-18.2 (R29)'],
]

function useNumbers(r: Replay | null) {
  return useMemo(() => {
    if (!r) return null
    const sub = r.snap.submission
    const mp = sub.filter((d) => d.class === 'MULTIPATH').length
    const ev = r.snap.events.filter((e) => e.kind === 'alarm')
    const siteRaised = new Set(ev.filter((e) => (e.note ?? '').includes('site multipath')).map((e) => e.alarm_id)).size
    const byEp = new Map<string, Set<string>>()
    for (const e of ev) if (e.episode && e.alarm_id) { if (!byEp.has(e.episode)) byEp.set(e.episode, new Set()); byEp.get(e.episode)!.add(e.alarm_id) }
    const churn = [...byEp.entries()].filter(([, s]) => s.size >= 5).map(([ep, s]) => [ep, s.size] as const)
    const confs = sub.filter((d) => d.class !== 'MULTIPATH').map((d) => d.confidence)
    return { n: sub.length, mp, nonMp: sub.length - mp, siteRaised, churn, confs }
  }, [r])
}

export function Research({ meta, replay }: { meta: Meta | null; replay: Replay | null }) {
  const n = useNumbers(replay)
  const sm = meta?.site_multipath
  const sc = meta?.scores
  const mpRow = sc?.classes.find((c) => c.class === 'MULTIPATH')
  const score = (s: string | null | undefined) => s?.match(/[\d.]+ \/ 40/)?.[0]
  return (
    <section className="limits">
      <div className="label">Why each cause looks different</div>
      <h2>The physics we detect on, and where it breaks</h2>
      <p className="lede">Each cause leaves a different fingerprint across tractors, channels and context. The diagnosis network encodes these fingerprints as link probabilities, and the watchdog uses their shapes (step or ramp) to place boundaries. Below them: what we can't do yet, starting with multipath.</p>
      <div className="sig">{SIG.map(([c, l]) => (
        <div key={c} className="panel" style={{ borderTop: `3px solid ${CV[c]}` }}>
          <div className="label" style={{ color: CV[c] }}>Cause</div><h4>{CLS_LABEL[c]}</h4>
          <ul>{l.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      ))}</div>

      <div className="panel warnpanel" style={{ padding: '18px 22px', marginBottom: 16 }}>
        <div className="label" style={{ color: 'var(--spoof)' }}>Weakness 1 · multipath</div>
        {n && <p className="lead">{n.mp} of the {n.n} exported evaluation-window detections ({((n.mp / n.n) * 100).toFixed(1)}%) are MULTIPATH site reports. The other {n.nonMp} cover every other class, and the organisers say the window holds 8 episodes.</p>}
        <ul>
          <li><b>The site map is learned from the dev days, at this farm only.</b> {sm && <>{sm.n_sites} grid cells out of {sm.n_cells_seen} visited. The north headland dipped on {Math.round(sm.north_headland_rate * 100)}% of dev passes and the south headland on {Math.round(sm.south_headland_rate * 100)}%.</>} Any min-C/N0 dip ≥ 5 dB for 5–60 s at a mapped cell becomes a one-tractor MULTIPATH report. Nothing is learned on the evaluation day, and a new obstruction elsewhere isn't on the map.</li>
          <li><b>Recurrence is not attribution.</b> {meta?.confidence?.method === 'hypothesis'
            ? 'A report\'s confidence counts the site\'s dev dip rate as a witness (+2 at ≥ 90%, +1 at ≥ 70%) plus the short dip itself.'
            : 'A report\'s confidence is the cell\'s dev dip rate, (dips + 1)/(passes + 2) capped at 0.95.'} That says the place often dips, not that this dip was multipath rather than, say, a short jammer pulse at the headland.</li>
          <li><b>Labelled multipath looks like a headland turn.</b> The two labelled dev multipath episodes are indistinguishable from the ~1,000 turn dips: the best feature pair separates them 89% of the time, short of the 95% we required. The generic short-dip track also treats turn batches as no information, so generic multipath at a turn is invisible by design. The site map exists to work around that.</li>
          <li><b>The score depends on the recurrent-multipath exemption.</b> The dev check exempts short (≤ 3 min) MULTIPATH reports unless they duplicate a labelled window. {mpRow && <>Even so, MULTIPATH is the only class below F1 1.00 on dev (precision {mpRow.precision}, F1 {mpRow.f1}, {mpRow.extra} extras).</>} {sc?.lenient && <>The strict and lenient readings of the duplicate rule give {score(sc.headline)} and {score(sc.lenient)}.</>} If the official scorer reads it differently, most of these reports become false positives.</li>
          <li><b>Hidden from operators, present in the export.</b> Site reports are ISA-18.2 suppressed alerts. They are absent from <code>/alarms</code> unless <code>include_suppressed=1</code> is set, and the operator view only counts them. {n && <>The 72 h replay raised {n.siteRaised}.</>} A cap of {meta?.site_cap ?? 500} exported detections drops site reports first.</li>
          <li><b>Boundary effects.</b> A short dip straddling a 30 s batch boundary moves two batch means. It can then open a 2-batch signal episode and drop out of the short-dip track (<code>out/dips.csv</code> lists these).</li>
        </ul>
      </div>

      <div className="panel" style={{ padding: '18px 22px', marginBottom: 16 }}>
        <div className="label">Other known weaknesses</div>
        <ul>
          <li><b>Scores are in-sample.</b> The v2.3 rules were written with the dev labels visible and are scored on the same 12 episodes. The only out-of-sample number is for v2.1-trained: leave-one-episode-out 23.0 versus 31.8 in-sample. We have none for v2.3.</li>
          {meta?.confidence?.method === 'hypothesis'
            ? <li><b>Confidence is a points score, not a calibrated probability.</b> Each finding scores for or against the class from the link table (±1 or ±2, capped at ±{meta.confidence.hypothesis?.cap_group} per sign group), and P = 1/(1 + e^(−{meta.confidence.hypothesis?.scale}·points)), clipped to [{meta.confidence.hypothesis?.clip.join(', ')}]. The point values and scale are hand-set, not fitted. {n && <>Non-multipath exports in the evaluation window: {n.confs.map((c) => c.toFixed(2)).join(', ')}.</>} Open any detection to see its breakdown.</li>
            : <li><b>Confidence saturates.</b> The detection prior is {meta?.confidence?.table?.detection_prior?.p?.toFixed(2) ?? '0.92'} ({meta?.confidence?.table?.detection_prior ? `${meta.confidence.table.detection_prior.hits} of ${meta.confidence.table.detection_prior.n}` : '10 of 10'} dev detections were hits), so {n ? `${n.confs.filter((c) => c >= 0.97).length} of ${n.confs.length}` : 'most'} non-multipath exports sit at the 0.97 clip.</li>}
          <li><b>Alarm churn.</b> An episode sitting just under BF 20 is re-raised and retracted on every watchdog pass. {n && n.churn.length > 0 && <>In the recorded run: {n.churn.map(([ep, k]) => `${ep} produced ${k} alarm ids`).join(', ')}.</>}</li>
          <li><b>Alarm IDs keep their first class.</b> A-OTHER-1 ended as EQUIPMENT_FAULT, and A-EQUIPMENT_FAULT-2 as SPACE_WEATHER. Read the class field.</li>
          <li><b>Storm-jam is rule-based.</b> It logs BF 0 and needs a ≥ 6 dB step on ≥ 2 tractors.</li>
          <li><b>Single-tractor jamming</b> with no oscillation, RFI report or second tractor stays an unattributed WATCH. <b>Wide-area jamming</b> that drops every tractor equally can pass for space weather without SDA context.</li>
          <li><b>Along-track spoofing</b> is invisible in cross-track error. Spoofing that also collapses C/N0 is attributed to the C/N0 cause.</li>
          <li><b>One cause per tractor per signal episode.</b> Only a stacked step splits an episode. <b>Advisories</b> carry no end time ("24–48 h" → 24 h), and RFI reports have no publish time (assumed event + 30 min).</li>
          <li><b>The links are principled guesses</b> on QMR's five-level scale. The trained links overfit under leave-one-episode-out testing, so the physics values ship.</li>
        </ul>
      </div>

      <div className="grid2">
        <div className="panel tblwrap"><div className="label" style={{ padding: '10px 12px' }}>Methods and where they come from</div>
          <table className="t"><thead><tr><th>PROBLEM</th><th>METHOD</th><th>SOURCE</th></tr></thead>
            <tbody>{ALG.map((r) => <tr key={r[0]}><td>{r[0]}</td><td>{r[1]}</td><td className="m">{r[2]}</td></tr>)}</tbody></table>
        </div>
        {sc && <div className="panel tblwrap"><div className="label" style={{ padding: '10px 12px' }}>Dev diagnostic score · development labels, in-sample, not the evaluation window</div>
          <table className="t"><thead><tr><th>VERSION</th><th>/40</th><th>MACRO-F1</th><th>FOUND</th><th>EXTRAS</th></tr></thead>
            <tbody>{sc.versions.map((v) => <tr key={v.version} className={v.version === 'current' ? 'hl' : ''}><td>{v.version === 'current' ? `${meta?.version} (current)` : v.version}</td><td className="m">{v.score}</td><td className="m">{v.macro_f1}</td><td className="m">{v.found}</td><td className="m">{v.extras}</td></tr>)}</tbody></table>
          <table className="t" style={{ marginTop: 8 }}><thead><tr><th>CLASS</th><th>TRUTH</th><th>FOUND</th><th>EXTRA</th><th>PREC</th><th>F1</th></tr></thead>
            <tbody>{sc.classes.map((c) => <tr key={c.class} className={c.class === 'MULTIPATH' ? 'warn' : ''}><td style={{ color: c.class === 'MULTIPATH' ? undefined : CV[c.class] }}>{CLS_LABEL[c.class]}</td><td className="m">{c.truth}</td><td className="m">{c.found}</td><td className="m">{c.extra}</td><td className="m">{c.precision}</td><td className="m">{c.f1}</td></tr>)}</tbody></table>
        </div>}
      </div>

      <div className="panel" style={{ padding: '18px 22px', marginTop: 16 }}>
        <div className="label">References</div>
        <ul className="refs" style={{ columns: 2, columnGap: 32, marginTop: 10 }}>{Object.entries(REFS).map(([k, [t, u]]) => <li key={k}><span className="mono">{k}</span>{u ? <a href={u} target="_blank" rel="noreferrer">{t}</a> : t}</li>)}</ul>
      </div>
    </section>
  )
}
