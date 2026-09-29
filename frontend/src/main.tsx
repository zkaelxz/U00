import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installBootFallback, renderBootFallback } from './bootFallback'
import { RouteErrorBoundary } from './components/ErrorBoundary'

const rootEl = document.getElementById('root')!
// Installed before the first render so a crash in it still shows a message.
installBootFallback(rootEl)

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
