// Diagnostics > Setup "Install browser support"
// (api/routers/diagnostics_browser_routes.py). Starting it is PC only
// (pcOnlyFetch); the status read is admin.diagnostics and is polled while
// the job runs.
import type { BrowserInstallStatus } from '../types/browserInstall'
import type { DiagnosticsJobStarted } from '../types/diagnosticsInstalls'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export const getBrowserInstallStatus = (f?: Fetch) =>
  getJson<BrowserInstallStatus>('/api/diagnostics/browser', f)

// The command and folder are the server's own; nothing but the confirm is sent.
export const installBrowser = (f?: Fetch) =>
  postJson<DiagnosticsJobStarted>('/api/diagnostics/browser/install', { confirm: true }, pcOnlyFetch(f))
