import type {
  EndpointName,
  EndpointUrlResult,
  EngineKeyResult,
  MonthCounterResetResult,
  SettingsOverview,
  SettingsPreferences,
  SettingsToggleKey,
  SettingsUpdate,
} from '../types/settings'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export const TOGGLES: { key: SettingsToggleKey; label: string }[] = [
  { key: 'gpu_limit_enabled', label: 'Limit GPU jobs running at once' },
  { key: 'notify_on_completion', label: 'Notify when a job finishes' },
  { key: 'use_gpu', label: 'Use the GPU for transcription' },
  { key: 'bulk_auto_resume', label: 'Resume batches on start' },
]

// A toggle update carries one known boolean, never a key or URL.
export function buildUpdate(key: SettingsToggleKey, value: boolean): SettingsUpdate {
  return { [key]: value === true }
}

export const getSettings = (f?: Fetch) => getJson<SettingsOverview>('/api/settings', f)
export const updateSetting = (key: SettingsToggleKey, value: boolean, f?: Fetch) =>
  postJson<SettingsOverview>('/api/settings', buildUpdate(key, value), f)

// How many GPU jobs may run at once while the limit is on (the server clamps too).
export const GPU_MAX_PARALLEL_MAX = 4
export function clampGpuMaxParallel(n: number): number {
  return Number.isFinite(n) ? Math.min(GPU_MAX_PARALLEL_MAX, Math.max(1, Math.round(n))) : 1
}
export const updateGpuMaxParallel = (n: number, f?: Fetch) =>
  postJson<SettingsOverview>('/api/settings', { gpu_max_parallel: clampGpuMaxParallel(n) }, f)

export function gpuMaxParallelHelp(n: number): string {
  const base =
    'How many GPU jobs may run together. A job only joins a running one when the graphics card has at least 2 GB of memory free; without nvidia-smi they run one at a time.'
  return n > 1
    ? `${base} Ollama only runs requests in parallel if the OLLAMA_NUM_PARALLEL environment variable is set, and each parallel slot uses more graphics memory.`
    : base
}

// Persisted preferences (PC only). The patch holds only changed fields.
export const updatePreferences = (patch: Partial<SettingsPreferences>, f?: Fetch) =>
  postJson<SettingsOverview>('/api/settings', patch, pcOnlyFetch(f))

// Write-only key endpoints. The value goes in the body only,
// never the URL; the response is {engine, configured}, never the key.
const keyPath = (engine: string) => `/api/settings/keys/${encodeURIComponent(engine)}`
export const setEngineKey = (engine: string, value: string, f?: Fetch) =>
  postJson<EngineKeyResult>(keyPath(engine), { value, confirm: true }, f)
export const clearEngineKey = (engine: string, f?: Fetch) =>
  postJson<EngineKeyResult>(`${keyPath(engine)}/clear`, { confirm: true }, f)

// Endpoint URLs (Ollama, GPT-SoVITS): saved to .env on the
// PC behind the same guard as keys.
const endpointPath = (name: EndpointName) => `/api/settings/endpoints/${encodeURIComponent(name)}`
export const setEndpointUrl = (name: EndpointName, url: string, f?: Fetch) =>
  postJson<EndpointUrlResult>(endpointPath(name), { url, confirm: true }, f)
export const clearEndpointUrl = (name: EndpointName, f?: Fetch) =>
  postJson<EndpointUrlResult>(`${endpointPath(name)}/clear`, { confirm: true }, f)

// PC only: they change what the monthly cap counts. Nothing is deleted.
export const resetMonthCounter = (f?: Fetch) =>
  postJson<MonthCounterResetResult>('/api/settings/month-counter/reset', {}, pcOnlyFetch(f))
export const undoMonthCounterReset = (f?: Fetch) =>
  postJson<MonthCounterResetResult>('/api/settings/month-counter/undo', {}, pcOnlyFetch(f))
