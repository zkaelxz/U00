import { useEffect, useState } from 'react'

import { api } from './api/client'
import type { MetaResponse } from './api/types'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import ComicPage from './pages/Comic'
import DiagnosticsPage from './pages/Diagnostics'
import LibraryPage from './pages/Library'
import ReaderPage from './pages/Reader'
import SettingsPage from './pages/Settings'
import SourcesPage from './pages/Sources'
import TranslatePage from './pages/Translate'
import WorkspaceShell from './pages/workspace/WorkspaceShell'
import { ReportProblemButton } from './report/ReportProblem'
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
  ['Library', { name: 'library' }, ['library', 'drama', 'read', 'comic']],
  ['Translate', { name: 'translate' }, ['translate']],
  ['Sources', { name: 'sources' }, ['sources']],
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
        <ReportProblemButton />
        <ApiStatus />
      </header>
      {/* Header and nav stay outside the boundary so a crashed page can still be left. */}
      <RouteErrorBoundary>
        {route.name === 'library' && <LibraryPage />}
        {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
        {route.name === 'read' && <ReaderPage key={route.id} id={route.id} page={route.page} />}
        {route.name === 'comic' && <ComicPage key={route.id} id={route.id} page={route.page} />}
        {route.name === 'settings' && <SettingsPage />}
        {route.name === 'translate' && <TranslatePage />}
        {route.name === 'sources' && <SourcesPage />}
        {route.name === 'diagnostics' && <DiagnosticsPage />}
      </RouteErrorBoundary>
    </>
  )
}
