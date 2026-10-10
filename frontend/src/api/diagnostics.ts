// Diagnostics: the read-only overview plus the API batch 1 gaps
// (api/routers/diagnostics_gaps_routes.py). Install, upgrade, reset and the
// model-cache deletes are PC only and go through pcOnlyFetch (X-Baihe-Local; a 403 marks the tab remote).
import type {
  DiagnosticsCacheDeleteResult,
  DiagnosticsGpuTorchStatus,
  DiagnosticsInstallPresets,
  DiagnosticsInstallResult,
  DiagnosticsLogTail,
  DiagnosticsModelCache,
  DiagnosticsModelFolder,
  DiagnosticsPackageUpdates,
  DiagnosticsOverview,
  DiagnosticsPyannoteReadiness,
  DiagnosticsResetResult,
  DiagnosticsSetupChecks,
  DiagnosticsSupportReport,
  PortsOverview,
  RemoteHealth,
  RemoteIpCheckStatus,
  RemoteIpCheckTestResult,
} from '../types/diagnostics'
import type { DiagnosticsJobStarted } from '../types/diagnosticsInstalls'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/diagnostics'

// The word the server checks for a library reset (diagnostics_gaps_service.RESET_CONFIRM_TEXT).
export const RESET_WORD = 'RESET'
// Server caps (diagnostics_gaps_routes.get_log).
const LOG_MAX_LINES = 200
export const LOG_KEYWORD_MAX = 100

export const getDiagnostics = (f?: Fetch) => getJson<DiagnosticsOverview>(BASE, f)

export const getSetupChecks = (f?: Fetch) => getJson<DiagnosticsSetupChecks>(`${BASE}/setup-checks`, f)

// Packages grouped by task, approx. sizes, PyPI links and install caveats.
export const getInstallPresets = (f?: Fetch) => getJson<DiagnosticsInstallPresets>(`${BASE}/install-presets`, f)

// PC only (local_only on the server): a remote tab gets a 403.
export const getPorts = (f?: Fetch) => getJson<PortsOverview>(`${BASE}/ports`, f)

export const getModelCache = (f?: Fetch) => getJson<DiagnosticsModelCache>(`${BASE}/model-cache`, f)

// checkAccess: the server asks Hugging Face with its saved token (booleans back only).
export const getPyannote = (checkAccess = false, f?: Fetch) =>
  getJson<DiagnosticsPyannoteReadiness>(`${BASE}/pyannote${checkAccess ? '?check_access=true' : ''}`, f)


export function getLog(n: number, keyword = '', f?: Fetch) {
  const lines = Math.max(0, Math.min(LOG_MAX_LINES, Math.round(n)))
  const params = new URLSearchParams({ n: String(lines) })
  const k = keyword.trim().slice(0, LOG_KEYWORD_MAX)
  if (k) params.set('keyword', k)
  return getJson<DiagnosticsLogTail>(`${BASE}/log?${params}`, f)
}

export const getSupportReport = (f?: Fetch) => getJson<DiagnosticsSupportReport>(`${BASE}/support-report`, f)

// Starts a background job (answers at once); follow it with getDependencyInstall
// (api/diagnosticsInstalls.ts) and stop it with cancelDependencyInstall.
// acceptRisk: the owner accepted the plan's downgrade of a package Baihe needs
// (the server re-plans and answers 409 without it).
export const installDependency = (name: string, f?: Fetch, acceptRisk = false) =>
  postJson<DiagnosticsJobStarted>(
    `${BASE}/dependencies/${encodeURIComponent(name)}/install`,
    acceptRisk ? { confirm: true, accept_risk: true } : { confirm: true }, pcOnlyFetch(f),
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

// A job like installDependency: ~2.5 GB for the CUDA build.
export const setupGpuTorch = (variant: 'cu128' | 'cpu', f?: Fetch) =>
  postJson<DiagnosticsJobStarted>(`${BASE}/gpu-torch/setup`, { confirm: true, variant }, pcOnlyFetch(f))

export const resetLibrary = (f?: Fetch) =>
  postJson<DiagnosticsResetResult>(
    `${BASE}/reset-library`, { confirm: true, confirm_text: RESET_WORD }, pcOnlyFetch(f),
  )

// Model cache (Q14): only names the cache listing returned are accepted.
export const deleteHfRevision = (revision: string, f?: Fetch) =>
  postJson<DiagnosticsCacheDeleteResult>(
    `${BASE}/model-cache/hf/${encodeURIComponent(revision)}/delete`, { confirm: true }, pcOnlyFetch(f),
  )

export const deleteModelFile = (folder: DiagnosticsModelFolder, name: string, f?: Fetch) =>
  postJson<DiagnosticsCacheDeleteResult>(
    `${BASE}/model-cache/files/${encodeURIComponent(folder)}/${encodeURIComponent(name)}/delete`,
    { confirm: true }, pcOnlyFetch(f),
  )

// The last scheduled remote-access check; reading it starts no check.
export const getRemoteHealth = (f?: Fetch) => getJson<RemoteHealth>(`${BASE}/remote-health`, f)

// Settings > Remote access (PC only). Set and clear sit behind the key-write
// gate like the notification addresses, so they use a plain fetch (a 403 on
// the PC means key writes are off, not "away from the PC"); Test goes through
// pcOnlyFetch. The address goes in the body only and never comes back.
const IP_CHECK = `${BASE}/remote-health/ip-check`

export const getIpCheckStatus = (f?: Fetch) => getJson<RemoteIpCheckStatus>(IP_CHECK, f)

export const setIpCheck = (value: string, f?: Fetch) =>
  postJson<RemoteIpCheckStatus>(IP_CHECK, { value, confirm: true }, f)

export const clearIpCheck = (f?: Fetch) => postJson<RemoteIpCheckStatus>(`${IP_CHECK}/clear`, { confirm: true }, f)

export const testIpCheck = (f?: Fetch) => postJson<RemoteIpCheckTestResult>(`${IP_CHECK}/test`, {}, pcOnlyFetch(f))
