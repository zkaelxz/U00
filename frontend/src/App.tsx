import { useEffect, useState } from 'react'

import { api } from './api/client'
import type { MetaResponse } from './api/types'
import { DramaDetailPanel } from './components/DramaDetailPanel'
import { LibraryList } from './components/LibraryList'

function ApiStatus() {
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [down, setDown] = useState(false)

  useEffect(() => {
    api.meta().then(setMeta, () => setDown(true))
  }, [])

  if (down) return <span className="badge bad">API unreachable</span>
  if (!meta) return <span className="badge">Connecting…</span>
  return (
    <span className="badge ok" data-testid="api-status">
      API v{meta.api_version} · {meta.environment}
    </span>
  )
}

export default function App() {
  const [selectedId, setSelectedId] = useState<number | null>(null)

  return (
    <>
      <header>
        <h1>Baihe Studio</h1>
        <ApiStatus />
      </header>
      <p className="muted">
        Preview of the new React frontend (read-only). Everything else still lives in the
        Streamlit app.
      </p>
      <main>
        <LibraryList selectedId={selectedId} onSelect={setSelectedId} />
        {selectedId !== null && <DramaDetailPanel key={selectedId} dramaId={selectedId} />}
      </main>
    </>
  )
}
