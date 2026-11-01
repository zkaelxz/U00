import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './segmented.css'
import './frost.css'
import './soft.css'
import App from './App.tsx'
import { installBootFallback, renderBootFallback } from './bootFallback'
import { installChunkReload } from './chunkReload'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import { installCapture } from './report/capture'
import { applyDisplay, loadDisplay } from './display'
import { shareRedirect } from './shareTarget'
import { applyTheme, loadTheme } from './theme'

// The saved theme (index.html already set it before paint; this covers dev/HMR).
applyTheme(loadTheme())
applyDisplay(loadDisplay())

// The app is a hash router; the installed app's share entry opens /share, so move to the hash route before the first render.
const shared = shareRedirect(window.location.pathname, window.location.search)
if (shared) window.history.replaceState(null, '', `/${shared}`)

const rootEl = document.getElementById('root')!
// Installed before the first render so a crash in it still shows a message.
installBootFallback(rootEl)
// Also before the first render, so early errors are kept for "Report a problem".
installCapture()
installChunkReload()

try {
  // React replaces index.html's static "didn't load" note on first render.
  // The outer boundary catches a crash outside App's page boundary (the header).
  createRoot(rootEl).render(
    <StrictMode>
      <RouteErrorBoundary>
        <App />
      </RouteErrorBoundary>
    </StrictMode>,
  )
} catch (error) {
  renderBootFallback(rootEl, error)
}
