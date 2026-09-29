import { useEffect, useState } from 'react'

import { api } from './api/client'
import type { MetaResponse } from './api/types'
import DiagnosticsPage from './pages/Diagnostics'
import LibraryPage from './pages/Library'
import SettingsPage from './pages/Settings'
import TranslatePage from './pages/Translate'
import WorkspaceShell from './pages/workspace/WorkspaceShell'
import { routeHref, useRoute } from './router'
import type { Route } from './router'

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

// [label, target, route names that count as being on this page]
const NAV: [string, Route, Route['name'][]][] = [
  ['Library', { name: 'library' }, ['library', 'drama']],
  ['Translate', { name: 'translate' }, ['translate']],
  ['Settings', { name: 'settings' }, ['settings']],
  ['Diagnostics', { name: 'diagnostics' }, ['diagnostics']],
]

export default function App() {
  const route = useRoute()

  return (
    <>
      <header className="app-header">
        <h1>Baihe Studio</h1>
        <nav aria-label="Main">
          {NAV.map(([label, target, active]) => (
            <a
              key={label}
              href={routeHref(target)}
              aria-current={active.includes(route.name) ? 'page' : undefined}
            >
              {label}
            </a>
          ))}
        </nav>
        <ApiStatus />
      </header>
      {route.name === 'library' && <LibraryPage />}
      {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
      {route.name === 'settings' && <SettingsPage />}
      {route.name === 'translate' && <TranslatePage />}
      {route.name === 'diagnostics' && <DiagnosticsPage />}
    </>
  )
}
