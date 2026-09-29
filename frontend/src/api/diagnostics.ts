// Diagnostics: the read-only overview plus the API batch 1 gaps
// (api/routers/diagnostics_gaps_routes.py). Install, upgrade, reset and the
// model-cache and bug-bundle deletes are PC only and go through pcOnlyFetch (X-Baihe-Local; a 403 marks the tab remote).
import type {
  BugBundleDeleteResult,
  DiagnosticsBugBundle,
  DiagnosticsCacheDeleteResult,
  DiagnosticsGpuTorchSetupResult,
  DiagnosticsGpuTorchStatus,
  DiagnosticsInstallPresets,
  DiagnosticsInstallResult,
  DiagnosticsJobHistoryItem,
  DiagnosticsLogTail,
  DiagnosticsModelCache,
  DiagnosticsPackageUpdates,
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

// Packages grouped by task, approx. sizes, PyPI links and install caveats.
export const getInstallPresets = (f?: Fetch) => getJson<DiagnosticsInstallPresets>(`${BASE}/install-presets`, f)

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

// target: the version the user confirmed (the last update check's); 409 if that check changed.
export const upgradeDependency = (name: string, target: string, f?: Fetch) =>
  postJson<DiagnosticsInstallResult>(
    `${BASE}/dependencies/${encodeURIComponent(name)}/upgrade`, { confirm: true, target }, pcOnlyFetch(f),
  )

// Asks PyPI on the server (only when called; cached there, and Update installs
// exactly the target it reports).
export const checkPackageUpdates = (f?: Fetch) =>
  postJson<DiagnosticsPackageUpdates>(`${BASE}/package-updates/check`, {}, f)

export const getGpuTorch = (f?: Fetch) => getJson<DiagnosticsGpuTorchStatus>(`${BASE}/gpu-torch`, f)

// The same status plus a CUDA check: the server imports torch in a fresh Python
// (a few seconds; 409 while a job or another check runs).
export const checkGpuTorch = (f?: Fetch) => postJson<DiagnosticsGpuTorchStatus>(`${BASE}/gpu-torch/check`, {}, f)

// Synchronous like installDependency: ~2.5 GB for the CUDA build.
export const setupGpuTorch = (variant: 'cu128' | 'cpu', f?: Fetch) =>
  postJson<DiagnosticsGpuTorchSetupResult>(`${BASE}/gpu-torch/setup`, { confirm: true, variant }, pcOnlyFetch(f))

export const resetLibrary = (f?: Fetch) =>
  postJson<DiagnosticsResetResult>(
    `${BASE}/reset-library`, { confirm: true, confirm_text: RESET_WORD }, pcOnlyFetch(f),
  )

// Model cache (Q14): only names the cache listing returned are accepted.
export const deleteHfRevision = (revision: string, f?: Fetch) =>
  postJson<DiagnosticsCacheDeleteResult>(
    `${BASE}/model-cache/hf/${encodeURIComponent(revision)}/delete`, { confirm: true }, pcOnlyFetch(f),
  )

export const deletePiperVoice = (voice: string, f?: Fetch) =>
  postJson<DiagnosticsCacheDeleteResult>(
    `${BASE}/model-cache/piper/${encodeURIComponent(voice)}/delete`, { confirm: true }, pcOnlyFetch(f),
  )

// Saved bug-reproduction bundles (a line's "What happened here?" snapshot).
export const getBugBundles = (f?: Fetch) => getJson<DiagnosticsBugBundle[]>(`${BASE}/bug-bundles`, f)

export const deleteBugBundle = (id: number, f?: Fetch) =>
  postJson<BugBundleDeleteResult>(`${BASE}/bug-bundles/${id}/delete`, { confirm: true }, pcOnlyFetch(f))
