import type { EngineKeyResult, SettingsOverview, SettingsToggleKey, SettingsUpdate } from '../types/settings'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const TOGGLES: { key: SettingsToggleKey; label: string }[] = [
  { key: 'gpu_limit_enabled', label: 'Limit GPU jobs to one at a time' },
  { key: 'notify_on_completion', label: 'Notify when a job finishes' },
  { key: 'use_gpu', label: 'Use the GPU for transcription' },
  { key: 'gemini_free_tier', label: 'Gemini free tier (slower, rate-limited)' },
]

// The body only ever carries one known boolean, never a key, URL or path.
export function buildUpdate(key: SettingsToggleKey, value: boolean): SettingsUpdate {
  return { [key]: value === true }
}

export const getSettings = (f?: Fetch) => getJson<SettingsOverview>('/api/settings', f)
export const updateSetting = (key: SettingsToggleKey, value: boolean, f?: Fetch) =>
  postJson<SettingsOverview>('/api/settings', buildUpdate(key, value), f)

// Write-only key endpoints (Slice 24). The value goes in the body only,
// never the URL; the response is {engine, configured}, never the key.
const keyPath = (engine: string) => `/api/settings/keys/${encodeURIComponent(engine)}`
export const setEngineKey = (engine: string, value: string, f?: Fetch) =>
  postJson<EngineKeyResult>(keyPath(engine), { value, confirm: true }, f)
export const clearEngineKey = (engine: string, f?: Fetch) =>
  postJson<EngineKeyResult>(`${keyPath(engine)}/clear`, { confirm: true }, f)
