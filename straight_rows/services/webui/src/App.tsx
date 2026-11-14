import { useEffect, useState } from 'react'
import { useFarm } from './useFarm'
import { Header } from './components/Header'
import { Operator } from './views/Operator'
import { Extended } from './views/Extended'

const pageOf = () => (location.hash.startsWith('#/extended') ? 'extended' : 'operator')

function App() {
  const farm = useFarm()
  const [page, setPage] = useState<'operator' | 'extended'>(pageOf)
  useEffect(() => {
    const on = () => setPage(pageOf())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return (
    <>
      <Header farm={farm} page={page} />
      {page === 'operator' ? <Operator farm={farm} /> : <Extended key={location.hash} farm={farm} />}
    </>
  )
}

export default App
