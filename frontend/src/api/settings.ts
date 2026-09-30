import type {
  EndpointName,
  EndpointUrlResult,
  EngineKeyResult,
  SettingsOverview,
  SettingsPreferences,
  SettingsToggleKey,
  SettingsUpdate,
} from '../types/settings'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const TOGGLES: { key: SettingsToggleKey; label: string }[] = [
  { key: 'gpu_limit_enabled', label: 'Limit GPU jobs to one at a time' },
  { key: 'notify_on_completion', label: 'Notify when a job finishes' },
  { key: 'use_gpu', label: 'Use the GPU for transcription' },
  { key: 'gemini_free_tier', label: 'Gemini free tier (slower, rate-limited)' },
  { key: 'bulk_auto_resume', label: 'Resume batches on start' },
]

// A toggle update carries one known boolean, never a key or URL.
export function buildUpdate(key: SettingsToggleKey, value: boolean): SettingsUpdate {
  return { [key]: value === true }
}

export const getSettings = (f?: Fetch) => getJson<SettingsOverview>('/api/settings', f)
export const updateSetting = (key: SettingsToggleKey, value: boolean, f?: Fetch) =>
  postJson<SettingsOverview>('/api/settings', buildUpdate(key, value), f)

// Persisted preferences (PC only). The patch holds only changed fields.
export const updatePreferences = (patch: Partial<SettingsPreferences>, f?: Fetch) =>
  postJson<SettingsOverview>('/api/settings', patch, f)

// Write-only key endpoints (Slice 24). The value goes in the body only,
// never the URL; the response is {engine, configured}, never the key.
const keyPath = (engine: string) => `/api/settings/keys/${encodeURIComponent(engine)}`
export const setEngineKey = (engine: string, value: string, f?: Fetch) =>
  postJson<EngineKeyResult>(keyPath(engine), { value, confirm: true }, f)
export const clearEngineKey = (engine: string, f?: Fetch) =>
  postJson<EngineKeyResult>(`${keyPath(engine)}/clear`, { confirm: true }, f)

// Endpoint URLs (Ollama, LibreTranslate, GPT-SoVITS): saved to .env on the
// PC behind the same guard as keys.
const endpointPath = (name: EndpointName) => `/api/settings/endpoints/${encodeURIComponent(name)}`
export const setEndpointUrl = (name: EndpointName, url: string, f?: Fetch) =>
  postJson<EndpointUrlResult>(endpointPath(name), { url, confirm: true }, f)
export const clearEndpointUrl = (name: EndpointName, f?: Fetch) =>
  postJson<EndpointUrlResult>(`${endpointPath(name)}/clear`, { confirm: true }, f)
