import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './segmented.css'
import './frost.css'
import App from './App.tsx'
import { installBootFallback, renderBootFallback } from './bootFallback'
import { installChunkReload } from './chunkReload'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import { installCapture } from './report/capture'
import { applyDisplay, loadDisplay } from './display'
import { applyTheme, loadTheme } from './theme'

// The saved theme (index.html already set it before paint; this covers dev/HMR).
applyTheme(loadTheme())
applyDisplay(loadDisplay())

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
