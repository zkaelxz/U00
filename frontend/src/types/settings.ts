export type SettingsToggleKey =
  | 'gpu_limit_enabled'
  | 'notify_on_completion'
  | 'use_gpu'
  | 'gemini_free_tier'
  | 'bulk_auto_resume'

// Persisted PC-side preferences (GET/POST /api/settings). Paths are paths
// only; a cookies file's contents never cross the API.
export interface SettingsPreferences {
  default_engine: string
  default_locale: string
  default_style_note: string
  episode_summary_engine: string
  monthly_cap_usd: number | null // null: BAIHE_MONTHLY_CAP_USD from .env applies
  ollama_num_ctx_override: number // 0: sized from the prompt
  whisper_model_path: string
  ocr_backend: string
  ocr_prefer_paddle_vl_manga: boolean
  tesseract_cmd: string
  cookies_browser: string | null
  cookies_file: string
  lncrawl_cmd: string
  // The four paths above come back only to the PC itself; other devices get
  // '' there and only these flags.
  whisper_model_path_configured: boolean
  tesseract_cmd_configured: boolean
  cookies_file_configured: boolean
  lncrawl_cmd_configured: boolean
}

export type PreferenceKey = keyof SettingsPreferences

export interface SettingsChoices {
  engines: string[]
  locales: string[]
  summary_engines: string[]
  ocr_backends: string[]
  cookie_browsers: string[]
}

export type EndpointName = 'ollama_url' | 'libretranslate_url' | 'gpt_sovits_url'

// engine_keys maps a setting name to "is a key/endpoint configured"; the
// API never returns a key, and neither does this type. endpoints carries a
// URL only when it has no user name, password, query or fragment.
export interface SettingsOverview extends Record<SettingsToggleKey, boolean> {
  engine_keys: Record<string, boolean>
  gpu_max_parallel: number // 1..4; 1 = one GPU job at a time
  preferences: SettingsPreferences
  endpoints: Record<EndpointName, string | null>
  monthly_cap_env_usd: number
  effective_monthly_cap_usd: number
  choices: SettingsChoices
}

// The toggles plus any subset of the preferences (extra="forbid" server side).
export type SettingsUpdate = Partial<Record<SettingsToggleKey, boolean>> &
  Partial<SettingsPreferences> & { gpu_max_parallel?: number }

// Result of a write-only key set/clear: never carries the key itself.
export interface EngineKeyResult {
  engine: string
  configured: boolean
}

export interface EndpointUrlResult {
  name: EndpointName
  url: string | null
  configured: boolean
}
