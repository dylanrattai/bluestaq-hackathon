import type { Farm } from '../useFarm'

export const EXT_TABS = ['DATA FLOW', 'WALKTHROUGH', 'LIVE ENGINE', 'RESEARCH & LIMITS'] as const

export function Brand({ sub }: { sub: string }) {
  return (
    <a className="brand" href="#/">
      <svg viewBox="0 0 32 32" aria-hidden="true">
        <path d="M3 22 C10 12,20 7,30 6" stroke="#ede6d6" strokeWidth="3" fill="none" strokeLinecap="round" />
        <path d="M5 25 C12 17,21 12,29 11" stroke="#d9a441" strokeWidth="3" fill="none" strokeLinecap="round" />
        <path d="M8 28 C14 22,22 17,28 16" stroke="#b5763a" strokeWidth="3" fill="none" strokeLinecap="round" />
      </svg>
      <span className="wm">STRAIGHT<b> ROWS</b></span>
      <span className="sub">{sub}</span>
    </a>
  )
}

export function Header({ farm, page, tab, setTab }: { farm: Farm; page: 'operator' | 'extended'; tab: number; setTab: (i: number) => void }) {
  const { mode, status, meta, now } = farm
  const iso = Number.isFinite(now) ? new Date(now).toISOString() : ''
  return (
    <header className={`hdr ${page === 'extended' ? 'ext-hdr' : ''}`}>
      <Brand sub={page === 'operator' ? 'GNSS INTEGRITY · CASSELTON, ND' : 'HOW IT WORKS'} />
      {page === 'extended' && (
        <nav className="seg">
          {EXT_TABS.map((t, i) => <button key={t} className={tab === i ? 'on' : ''} onClick={() => setTab(i)}><span>{i + 1}</span>{t}</button>)}
        </nav>
      )}
      <span className="spacer" />
      {page === 'extended' && <span className="kbd">1–4 tabs · Q W E scenario · ← → step</span>}
      <span className="pill" title="data time (UTC)"><span className="led" style={{ background: 'var(--gold)' }} />
        <span>{iso ? `${iso.slice(5, 10)} ${iso.slice(11, 19)}Z` : '—'}{status ? ` · ${Math.round(status.progress * 100)}%` : ''}</span></span>
      <span className={`pill ${mode === 'live' ? 'live' : mode === 'demo' ? 'demo' : ''}`} title={farm.error ?? ''}>
        <span className="led" /><span>{mode === 'live' ? (status?.done ? 'LIVE · REPLAY DONE' : 'LIVE ENGINE') : farm.forcedReplay ? 'RECORDED REPLAY' : mode === 'demo' ? 'DEMO REPLAY' : 'CONNECTING'}{meta ? ` · ${meta.version}` : ''}</span>
      </span>
      {page === 'operator'
        ? <a className="btn" href="#/extended">How it works →</a>
        : <a className="btn ghost" href="#/">← Operator view</a>}
    </header>
  )
}
