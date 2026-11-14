import type { Farm } from '../useFarm'
import { dayTime } from '../format'

const SPEEDS = [60, 300, 600, 1800, 3600]

export function Header({ farm, page }: { farm: Farm; page: 'operator' | 'extended' }) {
  const { mode, status, meta, replay, now, demo } = farm
  const pill = mode === 'live' ? (status?.done ? 'LIVE · replay finished' : 'LIVE') : mode === 'demo' ? 'DEMO · recorded run' : 'CONNECTING'
  return (
    <header className="hdr">
      <div className="hdr-title">
        <strong>Straight Rows</strong>
        <span className="muted">GNSS degradation watchdog {meta ? `· ${meta.version}${meta.config ? ` · ${meta.config}` : ''}` : ''}</span>
      </div>
      <nav className="hdr-nav">
        <a href="#/" className={page === 'operator' ? 'on' : ''}>Operator</a>
        <a href="#/extended" className={page === 'extended' ? 'on' : ''}>Judge view</a>
      </nav>
      <div className="hdr-time">
        <span className="mono">{dayTime(now, true)}</span>
        <div className="bar" title="replay progress"><div style={{ width: `${(status?.progress ?? 0) * 100}%` }} /></div>
      </div>
      <span className={`pill pill-${mode}`} title={farm.error ?? ''}>{pill}</span>
      {mode === 'demo' && replay && (
        <div className="demo-ctl">
          <button onClick={() => demo.setPlaying(!demo.playing)}>{demo.playing ? 'Pause' : 'Play'}</button>
          <select value={demo.speed} onChange={(e) => demo.setSpeed(Number(e.target.value))} title="data seconds per wall second">
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
          <input type="range" min={replay.t0} max={replay.t1} step={30000} value={now || replay.t0}
            onChange={(e) => demo.seek(Number(e.target.value))} aria-label="seek data time" />
        </div>
      )}
    </header>
  )
}
