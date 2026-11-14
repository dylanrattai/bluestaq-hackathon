import { useEffect, useState } from 'react'
import { useFarm } from './useFarm'
import { EXT_TABS, Header } from './components/Header'
import { Operator } from './views/Operator'
import { Extended } from './views/Extended'
import { PlayBar } from './components/PlayBar'

const pageOf = () => (location.hash.startsWith('#/extended') ? 'extended' : 'operator')
const tabOf = () => {
  const m = location.hash.match(/tab=(\d)/)
  return m ? Math.min(Math.max(Number(m[1]) - 1, 0), EXT_TABS.length - 1) : 0
}

function App() {
  const farm = useFarm()
  const [page, setPage] = useState<'operator' | 'extended'>(pageOf)
  const [tab, setTab] = useState(tabOf)
  useEffect(() => {
    const on = () => { setPage(pageOf()); if (/tab=\d/.test(location.hash)) setTab(tabOf()) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return (
    <>
      <Header farm={farm} page={page} tab={tab} setTab={setTab} />
      <PlayBar farm={farm} />
      {page === 'operator' ? <Operator farm={farm} /> : <Extended farm={farm} tab={tab} setTab={setTab} />}
    </>
  )
}

export default App
