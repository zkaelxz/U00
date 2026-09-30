// Diagnostics actions that run as background jobs on the server
// (api/routers/diagnostics_installs_routes.py): the Deno install and
// "Test first" for an update. Starting either is PC only (pcOnlyFetch);
// the status reads are admin.diagnostics and are polled while a job runs.
import type {
  DiagnosticsDenoStatus, DiagnosticsJobStarted, DiagnosticsUpgradeCheckState,
} from '../types/diagnosticsInstalls'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/diagnostics'

export const getDenoStatus = (f?: Fetch) => getJson<DiagnosticsDenoStatus>(`${BASE}/deno`, f)

// The download URL is the server's own; nothing but the confirm is sent.
export const installDeno = (f?: Fetch) =>
  postJson<DiagnosticsJobStarted>(`${BASE}/deno/install`, { confirm: true }, pcOnlyFetch(f))

export const getUpgradeCheck = (f?: Fetch) => getJson<DiagnosticsUpgradeCheckState>(`${BASE}/upgrade-check`, f)

// target: the version the last "Check for updates" offered (409 if that changed).
export const testUpgrade = (name: string, target: string, f?: Fetch) =>
  postJson<DiagnosticsJobStarted>(
    `${BASE}/dependencies/${encodeURIComponent(name)}/test-upgrade`, { confirm: true, target }, pcOnlyFetch(f),
  )
