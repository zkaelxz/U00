import { useEffect, useState } from 'react'

import { api } from './api/client'
import type { MetaResponse } from './api/types'
import DiagnosticsPage from './pages/Diagnostics'
import LibraryPage from './pages/Library'
import SettingsPage from './pages/Settings'
import TranslatePage from './pages/Translate'
import WorkspaceShell from './pages/workspace/WorkspaceShell'
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

export default function App() {
  const route = useRoute()

  return (
    <>
      <header>
        <h1>Baihe Studio</h1>
        <nav>
          <a href={routeHref({ name: 'library' })}>Library</a>
          <a href={routeHref({ name: 'translate' })}>Translate</a>
          <a href={routeHref({ name: 'settings' })}>Settings</a>
          <a href={routeHref({ name: 'diagnostics' })}>Diagnostics</a>
        </nav>
        <ApiStatus />
      </header>
      <p className="muted">
        Preview of the new React frontend. The Workspace stages still live in the Streamlit app.
      </p>
      {route.name === 'library' && <LibraryPage />}
      {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
      {route.name === 'settings' && <SettingsPage />}
      {route.name === 'translate' && <TranslatePage />}
      {route.name === 'diagnostics' && <DiagnosticsPage />}
    </>
  )
}
