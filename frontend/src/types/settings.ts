export type SettingsToggleKey =
  | 'gpu_limit_enabled'
  | 'notify_on_completion'
  | 'use_gpu'
  | 'gemini_free_tier'

// engine_keys maps a setting name to "is a key/endpoint configured"; the
// API never returns a value, and neither does this type.
export interface SettingsOverview extends Record<SettingsToggleKey, boolean> {
  engine_keys: Record<string, boolean>
}

// Exactly the four non-secret booleans the API accepts (extra="forbid").
export type SettingsUpdate = Partial<Record<SettingsToggleKey, boolean>>
