// Diagnostics: the read-only overview plus the API batch 1 gaps
// (api/routers/diagnostics_gaps_routes.py). Install, upgrade and reset are
// PC only and go through pcOnlyFetch (X-Baihe-Local; a 403 marks the tab remote).
import type {
  DiagnosticsInstallResult,
  DiagnosticsJobHistoryItem,
  DiagnosticsLogTail,
  DiagnosticsModelCache,
  DiagnosticsOverview,
  DiagnosticsPyannoteReadiness,
  DiagnosticsResetResult,
  DiagnosticsSetupChecks,
  DiagnosticsSupportReport,
} from '../types/diagnostics'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/diagnostics'

// The word the server checks for a library reset (diagnostics_gaps_service.RESET_CONFIRM_TEXT).
export const RESET_WORD = 'RESET'
// Server caps (diagnostics_gaps_routes.get_log).
export const LOG_MAX_LINES = 200
export const LOG_KEYWORD_MAX = 100

export const getDiagnostics = (f?: Fetch) => getJson<DiagnosticsOverview>(BASE, f)

export const getSetupChecks = (f?: Fetch) => getJson<DiagnosticsSetupChecks>(`${BASE}/setup-checks`, f)

export const getModelCache = (f?: Fetch) => getJson<DiagnosticsModelCache>(`${BASE}/model-cache`, f)

// checkAccess: the server asks Hugging Face with its saved token (booleans back only).
export const getPyannote = (checkAccess = false, f?: Fetch) =>
  getJson<DiagnosticsPyannoteReadiness>(`${BASE}/pyannote${checkAccess ? '?check_access=true' : ''}`, f)

export const getJobHistory = (f?: Fetch) => getJson<DiagnosticsJobHistoryItem[]>(`${BASE}/job-history`, f)

export function getLog(n: number, keyword = '', f?: Fetch) {
  const lines = Math.max(0, Math.min(LOG_MAX_LINES, Math.round(n)))
  const params = new URLSearchParams({ n: String(lines) })
  const k = keyword.trim().slice(0, LOG_KEYWORD_MAX)
  if (k) params.set('keyword', k)
  return getJson<DiagnosticsLogTail>(`${BASE}/log?${params}`, f)
}

export const getSupportReport = (f?: Fetch) => getJson<DiagnosticsSupportReport>(`${BASE}/support-report`, f)

// Synchronous: the request stays open for the whole pip run (up to an hour for torch).
export const installDependency = (name: string, f?: Fetch) =>
  postJson<DiagnosticsInstallResult>(
    `${BASE}/dependencies/${encodeURIComponent(name)}/install`, { confirm: true }, pcOnlyFetch(f),
  )

export const upgradeDependency = (name: string, f?: Fetch) =>
  postJson<DiagnosticsInstallResult>(
    `${BASE}/dependencies/${encodeURIComponent(name)}/upgrade`, { confirm: true }, pcOnlyFetch(f),
  )

export const resetLibrary = (f?: Fetch) =>
  postJson<DiagnosticsResetResult>(
    `${BASE}/reset-library`, { confirm: true, confirm_text: RESET_WORD }, pcOnlyFetch(f),
  )
