import { useState } from 'react'
import type { Farm } from '../useFarm'
import { ms } from '../demo'
import { dayTime } from '../format'

const N = 3600                                       // slowest 1×, fastest 3600× (1 h of data per second), then MAX
const STEPS = 100                                    // slider position STEPS = MAX
const toSpeed = (p: number) => (p >= STEPS ? null : Math.max(1, Number((10 ** ((p / (STEPS - 1)) * Math.log10(N))).toPrecision(2))))
const toPos = (v: number | null) => (v == null ? STEPS : Math.round((Math.log10(Math.max(1, v)) / Math.log10(N)) * (STEPS - 1)))
const speedLabel = (v: number | null) => (v == null ? 'MAX' : `${v}×`)
const perSec = (v: number | null) => (v == null ? 'as fast as the engine runs' : v < 60 ? `${v} s of data per second` : v < 3600 ? `${+(v / 60).toFixed(1)} min of data per second` : `${+(v / 3600).toFixed(1)} h of data per second`)

export function PlayBar({ farm }: { farm: Farm }) {
  const { playback: pb, meta, now, replay } = farm
  const [dragT, setDragT] = useState<number | null>(null)
  const [dragS, setDragS] = useState<number | null>(null)
  const t0 = meta ? ms(meta.data_start) : replay?.t0 ?? 0
  const t1 = meta ? ms(meta.data_end) : replay?.t1 ?? 1
  if (!meta && !replay) return null
  const shownT = dragT ?? pb.seekTarget ?? now
  const commitT = () => { if (dragT != null) { pb.seek(dragT); setDragT(null) } }
  const commitS = () => { if (dragS != null) { pb.setSpeed(toSpeed(dragS)); setDragS(null) } }
  const sp = dragS != null ? toSpeed(dragS) : pb.speed
  const pct = (t: number) => ((t - t0) / (t1 - t0)) * 100
  const w0 = meta ? pct(ms(meta.window.start)) : 66.7, w1 = meta ? pct(ms(meta.window.end)) : 100
  const doneAt = Number.isFinite(now) ? pct(now) : 0
  const going = dragT != null ? (pb.live ? (dragT > now ? 'release to skip ahead' : 'release to rewind into the recorded run') : 'release to jump') : null
  return (
    <div className="playbar">
      {!pb.live && <button className="pb-btn" onClick={() => pb.setPlaying(!pb.playing)} title={pb.playing ? 'pause' : 'play'}>{pb.playing ? '❚❚' : '▶'}</button>}
      <div className="pb-time">
        <div className="pb-lbl"><span className="label">Data time</span><b className="mono">{dayTime(shownT, true)}</b>
          <span className="pb-hint">{going ?? (pb.seekTarget != null ? `skipping ahead to ${dayTime(pb.seekTarget)}…` : pb.live ? 'drag later to skip ahead · earlier to rewind (recorded run)' : 'drag to jump anywhere in the 72 h')}</span>
        </div>
        <input type="range" min={t0} max={t1} step={60000} value={Number.isFinite(shownT) ? shownT : t0} aria-label="data time"
          style={{ background: `linear-gradient(90deg, rgba(217,164,65,.55) 0 ${doneAt}%, #2b2925 ${doneAt}% ${w0}%, rgba(217,164,65,.14) ${w0}% ${w1}%)` }}
          onChange={(e) => setDragT(Number(e.target.value))} onPointerUp={commitT} onKeyUp={commitT} onBlur={commitT} />
        <div className="pb-ticks"><span>{dayTime(t0)}</span><span style={{ left: `${w0}%` }}>scored window ▸</span><span>{dayTime(t1)}</span></div>
      </div>
      <div className="pb-speed">
        <div className="pb-lbl"><span className="label">Speed</span><b className="mono">{speedLabel(sp)}</b><span className="pb-hint">{perSec(sp)}</span></div>
        <input type="range" min={0} max={STEPS} step={1} value={dragS ?? toPos(pb.speed)} aria-label="playback speed"
          onChange={(e) => setDragS(Number(e.target.value))} onPointerUp={commitS} onKeyUp={commitS} onBlur={commitS} />
        <div className="pb-ticks"><span>1×</span><span style={{ left: `${(toPos(60) / STEPS) * 100}%` }}>60×</span><span style={{ left: `${(toPos(600) / STEPS) * 100}%` }}>600×</span><span>MAX</span></div>
      </div>
      {farm.forcedReplay && <button className="btn" onClick={pb.backToLive}>● Back to live</button>}
    </div>
  )
}
