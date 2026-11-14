// "Why is the confidence what it is?" — renders the engine's own breakdown (alarm.extra.conf in the API,
// alarms[].conf in the snapshot) with the rules from /webui/meta.confidence. Reporting only: never gates a detection.
import type { ConfInfo, Meta } from '../types'
import { CLS_LABEL, CV, FINDING_LABEL } from '../format'

type HypItem = { fid: string; present?: boolean; pts: number; why: string }
type TabGroup = { name: string; state: string; seen: string[]; lr: number }

const ptsColor = (p: number) => (p > 0 ? 'var(--gold)' : p < 0 ? 'var(--jam)' : 'var(--dim)')
const fmtPts = (p: number) => (p > 0 ? `+${p}` : `${p}`)

function linkOf(meta: Meta | null, fid: string, cls: string) {
  return meta?.fusion.links[fid]?.[cls]
}

function Hypothesis({ conf, cls, meta }: { conf: ConfInfo; cls: string; meta: Meta | null }) {
  const br = conf.br as { points: number; items: HypItem[] }
  const hp = meta?.confidence?.hypothesis ?? { scale: 0.5, cap_group: 3, clip: [0.05, 0.95] as [number, number] }
  const raw = 1 / (1 + Math.exp(-hp.scale * br.points))
  const groups = meta?.confidence?.groups ?? {}
  const groupOf = (fid: string) => Object.entries(groups).find(([, g]) => g.findings.includes(fid))
  // per-group sums, to show where the ±cap bit
  const sums = new Map<string, number>()
  for (const it of br.items) { const g = groupOf(it.fid); if (g && it.pts) sums.set(g[0], (sums.get(g[0]) ?? 0) + it.pts) }
  const capped = [...sums.entries()].filter(([, v]) => Math.abs(v) > hp.cap_group)
  return (
    <div className="conf">
      <div className="conf-head">
        <span className="conf-p">P = {conf.p.toFixed(2)}</span>
        <span className="mono">from <b style={{ color: ptsColor(br.points) }}>{fmtPts(br.points)}</b> points for “{CLS_LABEL[cls] ?? cls}” (0 = undecided)</span>
      </div>
      <div className="conf-formula mono">P = 1 / (1 + e<sup>−{hp.scale} × {br.points}</sup>) = {raw.toFixed(3)}{raw < hp.clip[0] || raw > hp.clip[1] ? ` → clipped to [${hp.clip[0]}, ${hp.clip[1]}]` : ''}</div>
      <table className="conf-t">
        <tbody>
          {br.items.map((it, i) => {
            const isF = /^F\d+$/.test(it.fid)
            const lc = isF ? linkOf(meta, it.fid, cls) : undefined
            const m = it.why.match(/(?:points to|leans) (\w+)/)
            const rl = m && isF ? linkOf(meta, it.fid, m[1]) : undefined
            return (
              <tr key={i}>
                <td className="mono" style={{ color: ptsColor(it.pts), width: 44 }}>{fmtPts(it.pts)}</td>
                <td className="mono">{it.fid}</td>
                <td>{isF ? FINDING_LABEL[it.fid] : it.fid === 'rival' ? 'rival class' : it.why}</td>
                <td className="mono dim">{isF ? (it.present ? 'present' : 'absent') : ''}</td>
                <td className="conf-why">{it.why.startsWith('explained')
                  ? <>not counted against {CLS_LABEL[cls] ?? cls}: {it.why} (explaining away)</>
                  : <>{it.why}{lc != null ? ` · P(${it.fid} | ${cls}) = ${lc}` : isF ? ` · no ${cls} link` : ''}{rl != null ? ` · P(${it.fid} | ${m![1]}) = ${rl}` : ''}</>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {capped.length > 0 && <div className="note">Group cap ±{hp.cap_group}: {capped.map(([g, v]) => `${groups[g]?.name ?? g} summed ${fmtPts(v)} → ${fmtPts(Math.sign(v) * hp.cap_group)}`).join(' · ')} (correlated views of one signal count once).</div>}
      <div className="note">Rules: a sign present with P(sign | class) ≥ 0.8 scores +2, ≥ 0.5 +1; an expected sign missing scores −1 (−2 if ≥ 0.95); a sign that points to another class scores −1/−2 unless a confirmed overlapping detection of that class already explains it. A rival class within BF ratio {meta?.confidence?.hypothesis?.rival_ratio ?? 0.5} costs {meta?.confidence?.hypothesis?.rival_pts ?? 1}. Reporting only: confidence never decides a detection.</div>
    </div>
  )
}

function Table({ conf, cls, meta }: { conf: ConfInfo; cls: string; meta: Meta | null }) {
  const br = conf.br as { prior: number; groups: Record<string, TabGroup> }
  const tab = meta?.confidence?.table?.classes?.[cls]?.groups ?? {}
  return (
    <div className="conf">
      <div className="conf-head"><span className="conf-p">P = {conf.p.toFixed(2)}</span><span className="mono">start from prior {br.prior.toFixed(2)}, × one likelihood ratio per sign group</span></div>
      <table className="conf-t"><tbody>
        {Object.entries(br.groups).map(([g, x]) => {
          const t = tab[g] as { k1?: number; n1?: number; k0?: number; n0?: number } | undefined
          return (
            <tr key={g}>
              <td className="mono" style={{ color: x.lr > 1 ? 'var(--gold)' : x.lr < 1 ? 'var(--jam)' : 'var(--dim)', width: 64 }}>×{x.lr.toFixed(2)}</td>
              <td>{x.name}</td>
              <td className="mono dim">{x.state}{x.seen.length ? ` [${x.seen.join(',')}]` : ''}</td>
              <td className="conf-why">{t?.n1 != null ? `present on ${t.k1}/${t.n1} dev ${cls} episodes vs ${t.k0}/${t.n0} others` : ''}</td>
            </tr>
          )
        })}
      </tbody></table>
      <div className="note">Reporting only: confidence never decides a detection.</div>
    </div>
  )
}

export function ConfidenceExplain({ conf, cls, meta, evidence, finalNote }: { conf?: ConfInfo | null; cls: string; meta: Meta | null; evidence?: string[]; finalNote?: boolean }) {
  const br = conf?.br as Record<string, unknown> | undefined
  return (
    <div>
      <div className="label" style={{ color: CV[cls] ?? 'var(--muted)', margin: '10px 0 6px' }}>Why this confidence{finalNote ? ' · as computed when the run finished' : ''}</div>
      {conf && br && Array.isArray(br.items) ? <Hypothesis conf={conf} cls={cls} meta={meta} />
        : conf && br && br.groups ? <Table conf={conf} cls={cls} meta={meta} />
        : <div className="note">{evidence?.find((e) => e.startsWith('P=')) ?? 'No breakdown recorded for this alarm yet (it is computed as the episode develops and when it closes).'}</div>}
    </div>
  )
}
