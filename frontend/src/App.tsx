import { useEffect, useState } from 'react'

import { api } from './api/client'
import type { MetaResponse } from './api/types'
import { DramaDetailPanel } from './components/DramaDetailPanel'
import { LibraryList } from './components/LibraryList'
import { DiagnosticsPage, DramaPage, SettingsPage } from './pages/Placeholders'
import { routeHref, useRoute } from './router'

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

function Library() {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  return (
    <main>
      <LibraryList selectedId={selectedId} onSelect={setSelectedId} />
      {selectedId !== null && <DramaDetailPanel key={selectedId} dramaId={selectedId} />}
    </main>
  )
}

export default function App() {
  const route = useRoute()

  return (
    <>
      <header>
        <h1>Baihe Studio</h1>
        <nav>
          <a href={routeHref({ name: 'library' })}>Library</a>
          <a href={routeHref({ name: 'settings' })}>Settings</a>
          <a href={routeHref({ name: 'diagnostics' })}>Diagnostics</a>
        </nav>
        <ApiStatus />
      </header>
      <p className="muted">
        Preview of the new React frontend (read-only). Everything else still lives in the
        Streamlit app.
      </p>
      {route.name === 'library' && <Library />}
      {route.name === 'drama' && <DramaPage key={route.id} id={route.id} stage={route.stage} />}
      {route.name === 'settings' && <SettingsPage />}
      {route.name === 'diagnostics' && <DiagnosticsPage />}
    </>
  )
}
