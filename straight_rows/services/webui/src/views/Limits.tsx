import { useMemo } from 'react'
import type { Meta } from '../types'
import type { Replay } from '../demo'
import { REFS } from '../content/flow'

function useNumbers(r: Replay | null) {
  return useMemo(() => {
    if (!r) return null
    const sub = r.snap.submission
    const mp = sub.filter((d) => d.class === 'MULTIPATH')
    const nonMp = sub.length - mp.length
    const ev = r.snap.events.filter((e) => e.kind === 'alarm')
    const siteRaised = new Set(ev.filter((e) => (e.note ?? '').includes('site multipath')).map((e) => e.alarm_id)).size
    const byEp = new Map<string, Set<string>>()
    for (const e of ev) if (e.episode && e.alarm_id) (byEp.get(e.episode) ?? byEp.set(e.episode, new Set()).get(e.episode)!).add(e.alarm_id)
    const churn = [...byEp.entries()].filter(([, s]) => s.size >= 5).map(([ep, s]) => [ep, s.size] as const)
    const confs = sub.filter((d) => d.class !== 'MULTIPATH').map((d) => d.confidence)
    return { n: sub.length, mp: mp.length, nonMp, siteRaised, churn, confs }
  }, [r])
}

export function Limits({ meta, replay }: { meta: Meta | null; replay: Replay | null }) {
  const n = useNumbers(replay)
  const sm = meta?.site_multipath
  const sc = meta?.scores
  const mpRow = sc?.classes.find((c) => c.class === 'MULTIPATH')
  return (
    <div className="limits">
      <section className="panel warn-panel">
        <h2>Multipath: our weakest output</h2>
        {n && <p className="lead">{n.mp} of the {n.n} exported evaluation-window detections ({((n.mp / n.n) * 100).toFixed(1)}%) are MULTIPATH site reports. The other {n.nonMp} cover every other class. The organisers say the evaluation window holds 8 episodes.</p>}
        <ul>
          <li><b>The site map is learned from the dev days, at this farm only.</b> {sm && <>{sm.n_sites} grid cells out of {sm.n_cells_seen} visited. The north headland dipped on {Math.round(sm.north_headland_rate * 100)}% of dev passes and the south headland on {Math.round(sm.south_headland_rate * 100)}%.</>} Any min-C/N0 dip ≥ 5 dB for 5–60 s at a mapped cell becomes a one-tractor MULTIPATH report. Nothing about the evaluation day is learned, and a new obstruction elsewhere is not on the map.</li>
          <li><b>Recurrence is not attribution.</b> A site report's confidence is the cell's dev dip rate, (dips + 1)/(passes + 2) capped at 0.95. It says the place often dips. It does not say this dip was multipath rather than, for example, a short jammer pulse at the headland.</li>
          <li><b>Labelled multipath looks like a headland turn.</b> The two labelled dev multipath episodes are indistinguishable from the ~1,000 turn dips: the best feature pair separates them 89% of the time, short of the 95% we required. The generic short-dip track also treats turn batches as no information, so generic multipath at a turn is invisible by design. The site map exists to work around that.</li>
          <li><b>The score depends on the recurrent-multipath exemption.</b> The dev check exempts short (≤ 3 min) MULTIPATH reports from extra-detection penalties unless they duplicate a labelled window. {mpRow && <>Even so, MULTIPATH is the only class below F1 1.00 on dev (precision {mpRow.precision}, F1 {mpRow.f1}, {mpRow.extra} extras).</>} {sc?.lenient && <>Strict versus lenient readings of the duplicate rule give {sc.headline?.match(/[\d.]+ \/ 40/)?.[0]} versus {sc.lenient.match(/[\d.]+ \/ 40/)?.[0]}.</>} If the official scorer reads the exemption differently, most of the {n?.mp ?? 'site'} reports become false positives.</li>
          <li><b>Hidden from operators, present in the export.</b> Site reports are ISA-18.2 suppressed alerts. They are not in <code>/alarms</code> without <code>include_suppressed=1</code>, and the operator page only counts them. {n && <>The full 72 h replay raised {n.siteRaised} of them.</>} A cap of {meta?.site_cap ?? 500} exported detections drops site reports first.</li>
          <li><b>Boundary effects.</b> A short dip that straddles a 30 s batch boundary moves two batch means. It can then open a 2-batch signal episode and be excluded from the short-dip track (<code>out/dips.csv</code> lists these).</li>
        </ul>
      </section>

      <section className="panel">
        <h2>Other known weaknesses</h2>
        <ul>
          <li><b>Scores are in-sample.</b> The v2.3 rules (site multipath, spoof-under-jam, storm-jam, storm rules) were written while the dev labels were visible, and they are scored on the same 12 episodes. The only honest out-of-sample number we have is for v2.1-trained: leave-one-episode-out 23.0 versus 31.8 in-sample. We have no LOEO number for v2.3.</li>
          <li><b>Confidence saturates.</b> The detection prior is 0.92 (10 of 10 dev detections were hits), so {n ? `${n.confs.filter((c) => c >= 0.97).length} of ${n.confs.length}` : 'most'} non-multipath exports carry P = 0.97, the clip. It does not separate strong cases from marginal ones. Live, confidence starts at 0 and jumps (0.17/0.22 → 0.97) as sign groups fill in.</li>
          <li><b>Alarm churn.</b> An episode that sits just under BF 20 is re-raised and retracted every watchdog pass. {n && n.churn.length > 0 && <>In the recorded run: {n.churn.map(([ep, k]) => `${ep} → ${k} alarm ids`).join(', ')}.</>}</li>
          <li><b>Alarm IDs keep the class they were raised with.</b> A-OTHER-1 ended as EQUIPMENT_FAULT and A-EQUIPMENT_FAULT-2 as SPACE_WEATHER. Always read the class field.</li>
          <li><b>Storm-jam (P3) is rule-based.</b> It has no Bayes factor (logged BF 0) and relies on a ≥ 6 dB step on ≥ 2 tractors.</li>
          <li><b>Single-tractor jamming</b> with no oscillation, RFI report or second affected tractor stays below ALARM. A lone C/N0 drop is ambiguous and surfaces as an unattributed WATCH.</li>
          <li><b>Wide-area jamming</b> that drops all tractors equally can look like space weather without SDA context.</li>
          <li><b>Along-track spoofing</b> is invisible in cross-track error. Spoofing that also collapses C/N0 is attributed to the C/N0 cause.</li>
          <li><b>One cause per tractor per signal episode.</b> A link fault during a storm is reported as whichever cause wins. Only a stacked step splits an episode.</li>
          <li><b>Advisories have no end time.</b> The duration is parsed from the text ("24–48 h" → 24 h), else 24 h. RFI reports have no publish time, so we assume event + 30 min.</li>
          <li><b>The links are principled guesses.</b> They were not fitted: the trained links overfit (LOEO). The network's BFs are only as good as the QMR five-level scale they use.</li>
        </ul>
      </section>

      {sc && (
        <section className="panel">
          <h2>Dev diagnostic score <span className="muted">(development labels, in-sample, not the evaluation window)</span></h2>
          <table className="scores">
            <thead><tr><th>version</th><th>score /40</th><th>macro-F1</th><th>found</th><th>extras</th><th>onset</th></tr></thead>
            <tbody>{sc.versions.map((v) => <tr key={v.version} className={v.version === 'current' ? 'cur' : ''}><td>{v.version === 'current' ? `${meta?.version} (current)` : v.version}</td><td>{v.score}</td><td>{v.macro_f1}</td><td>{v.found}</td><td>{v.extras}</td><td>{v.onset}</td></tr>)}</tbody>
          </table>
          <table className="scores">
            <thead><tr><th>class</th><th>truth</th><th>found</th><th>extra</th><th>precision</th><th>recall</th><th>F1</th></tr></thead>
            <tbody>{sc.classes.map((c) => <tr key={c.class} className={c.class === 'MULTIPATH' ? 'bad-row' : ''}><td>{c.class}</td><td>{c.truth}</td><td>{c.found}</td><td>{c.extra}</td><td>{c.precision}</td><td>{c.recall}</td><td>{c.f1}</td></tr>)}</tbody>
          </table>
        </section>
      )}

      <section className="panel">
        <h2>Research</h2>
        <ul className="refs">{Object.entries(REFS).map(([k, [t, u]]) => <li key={k}><span className="mono">{k}</span> {u ? <a href={u} target="_blank" rel="noreferrer">{t}</a> : t}</li>)}</ul>
      </section>
    </div>
  )
}
