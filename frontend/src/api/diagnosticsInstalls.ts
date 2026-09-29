// Diagnostics actions that run as background jobs on the server
// (api/routers/diagnostics_installs_routes.py): the Deno install. Starting
// it is PC only (pcOnlyFetch); the status read is admin.diagnostics and is
// polled while the job runs.
import type { DiagnosticsDenoStatus, DiagnosticsJobStarted } from '../types/diagnosticsInstalls'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/diagnostics'

export const getDenoStatus = (f?: Fetch) => getJson<DiagnosticsDenoStatus>(`${BASE}/deno`, f)

// The download URL is the server's own; nothing but the confirm is sent.
export const installDeno = (f?: Fetch) =>
  postJson<DiagnosticsJobStarted>(`${BASE}/deno/install`, { confirm: true }, pcOnlyFetch(f))
