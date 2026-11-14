import { useMemo, useState } from 'react'
import type { Farm } from '../useFarm'
import { FarmMap } from '../components/FarmMap'
import { TractorCards } from '../components/TractorCards'
import { Detections } from '../components/Detections'
import { Timeline } from '../components/Timeline'
import { CLS_COLOR, CLS_LABEL, hhmm } from '../format'
import { ms } from '../demo'

const SEVERITY = ['JAMMING', 'SPOOFING', 'SPACE_WEATHER', 'EQUIPMENT_FAULT', 'MULTIPATH']

export function Operator({ farm }: { farm: Farm }) {
  const { meta, status, alarms, now, trails } = farm
  const [focus, setFocus] = useState<[number, number] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [showSites, setShowSites] = useState(false)

  // operator list: ALARM and CONFIRMED only; site-multipath reports are suppressed alerts, counted not listed
  const shown = useMemo(() => alarms.filter((a) => (a.state === 'ALARM' || a.state === 'CONFIRMED') && !a.suppressed)
    .sort((a, b) => ms(b.start) - ms(a.start)), [alarms])
  const active = shown.filter((a) => a.open)
  const watching = alarms.filter((a) => a.state === 'WATCH' && a.open && !a.suppressed)
  const site = alarms.filter((a) => a.suppressed && a.state === 'CONFIRMED')
  const siteHour = site.filter((a) => ms(a.start) > now - 3600e3).length
  const top = [...active].sort((a, b) => SEVERITY.indexOf(a.cls) - SEVERITY.indexOf(b.cls))[0]
  const rfi = (meta?.rfi ?? []).filter((r) => ms(r.t_avail) <= now)
  const alarmed = new Set(active.flatMap((a) => a.tractors))

  return (
    <main className="op">
      <section className="op-map panel">
        <FarmMap meta={meta} tractors={status?.tractors ?? []} trails={trails} rfi={rfi} alarmed={alarmed} focus={focus} showSites={showSites} />
        <label className="map-opt"><input type="checkbox" checked={showSites} onChange={(e) => setShowSites(e.target.checked)} /> learned multipath sites</label>
      </section>

      <section className="op-side">
        <div className="panel farm-status" style={{ borderColor: top ? CLS_COLOR[top.cls] : 'var(--ok)' }}>
          <div className="muted">Farm status · {hhmm(now)}</div>
          {top ? (
            <>
              <div className="big" style={{ color: CLS_COLOR[top.cls] }}>{CLS_LABEL[top.cls]} · {top.state}</div>
              <div>Action: <b>{meta?.actions[top.cls]}</b> on {top.tractors.join(', ')}</div>
              {active.length > 1 && <div className="muted">{active.length} active detections; most severe shown</div>}
            </>
          ) : (
            <><div className="big ok">No active detection</div><div className="muted">Continue operations</div></>
          )}
          {watching.length > 0 && (
            <div className="watching">On WATCH (BF ≥ 3, not yet 20): {watching.map((a) => `${a.cls === 'OTHER' ? 'unattributed' : a.cls} on ${a.tractors.join(',')}`).join('; ')}</div>
          )}
          <div className="site-note">
            <b>{site.length}</b> site-multipath reports so far ({siteHour} in the last hour). They are suppressed alerts here but exported
            as MULTIPATH · CONTINUE_FALSE_ALARM. They are learned from the dev days and are this system's weakest output: see <a href="#/extended?tab=4">Limits</a>.
          </div>
        </div>
        <TractorCards tractors={status?.tractors ?? []} active={active}
          onPick={(t) => t.lat != null && t.lon != null && setFocus([t.lat, t.lon])} />
        {farm.mode === 'demo' && <p className="muted small">DEMO: tractor state is sampled every 2 min from the recorded telemetry; WORKING is approximated from speed.</p>}
      </section>

      <section className="op-dets panel">
        <h2>Detections <span className="muted">ALARM and CONFIRMED · newest first · click for evidence</span></h2>
        <Detections alarms={shown} actions={meta?.actions ?? {}} now={now} loadAlarm={farm.loadAlarm} selected={selected} onSelect={setSelected} />
      </section>

      <section className="op-tl panel">
        <h2>72 h timeline</h2>
        <Timeline meta={meta} alarms={alarms} now={now} onSelect={setSelected} />
      </section>
    </main>
  )
}
