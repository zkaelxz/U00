export type SettingsToggleKey =
  | 'gpu_limit_enabled'
  | 'unload_ollama_before_transcribe'
  | 'notify_on_completion'
  | 'use_gpu'
  | 'gemini_free_tier'
  | 'bulk_auto_resume'
  | 'offer_provider_models'
  | 'remote_uploads'

// Settings > Timing and splitting (the three sections of lib/settings_schema.py), all numbers.
// Optional here so a preferences object built before these rows existed still type-checks;
// the server always sends every one. tests/test_timing_settings.py keeps this list in step.
export interface TimingPreferences {
  sentence_split_min_silence_ms?: number
  sensitive_vad_threshold?: number
  difficult_no_speech_threshold?: number
  difficult_log_prob_threshold?: number
  qwen_vad_threshold?: number
  qwen_vad_min_silence_ms?: number
  qwen_vad_min_speech_ms?: number
  qwen_vad_pad_ms?: number
  qwen_long_vad_threshold?: number
  qwen_long_vad_min_speech_ms?: number
  qwen_long_vad_pad_ms?: number
  qwen_merge_gap_seconds?: number
  qwen_merge_gap_mixed_seconds?: number
  qwen_span_cap_seconds?: number
  qwen_span_cap_search_seconds?: number
  qwen_context_pad_seconds?: number
  qwen_long_merge_gap_seconds?: number
  qwen_long_window_seconds?: number
  qwen_long_cut_search_seconds?: number
  asmr_start_threshold?: number
  asmr_stop_threshold?: number
  speech_coverage_vad_threshold?: number
  split_max_seconds?: number
  split_max_cjk_chars?: number
  split_min_piece_seconds?: number
  split_min_piece_cjk_chars?: number
  split_min_piece_words?: number
  quick_split_seconds?: number
  realign_min_seconds?: number
  realign_pause_seconds?: number
  stock_phrase_isolation_seconds?: number
  repeat_loop_min_repeats?: number
  retime_window_pad_seconds?: number
  gap_fill_min_gap_seconds?: number
  scene_break_gap_seconds?: number
  sentence_group_gap_seconds?: number
  sentence_group_max_lines?: number
  sentence_group_max_chars?: number
  sentence_group_max_seconds?: number
}

// Persisted PC-side preferences (GET/POST /api/settings). Paths are paths
// only; a cookies file's contents never cross the API.
export interface SettingsPreferences extends TimingPreferences {
  default_engine: string
  default_locale: string
  default_style_note: string
  scene_aware_batches: boolean // translate batches start at scene breaks
  translate_reply_guard: string // auto | on | off
  episode_summary_engine: string
  monthly_cap_usd: number | null // null: BAIHE_MONTHLY_CAP_USD from .env applies
  max_upload_mb: number // media/backup upload cap; BAIHE_MAX_UPLOAD_MB wins when set
  ollama_num_ctx_override: number // 0: sized from the prompt
  keep_free_vram_gb: number // 0: off
  keep_free_ram_gb: number // 0: off
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

export interface OcrBackendStatus {
  installed: boolean
  reason: string | null
  package: string | null
}

interface SettingsChoices {
  engines: string[]
  locales: string[]
  summary_engines: string[]
  ocr_backends: string[]
  ocr_backend_status?: Record<string, OcrBackendStatus>
  cookie_browsers: string[]
}

export type EndpointName = 'ollama_url'

// engine_keys maps a setting name to "is a key/endpoint configured"; the
// API never returns a key, and neither does this type. endpoints carries a
// URL only when it has no user name, password, query or fragment.
export interface SettingsOverview extends Record<SettingsToggleKey, boolean> {
  engine_keys: Record<string, boolean>
  gpu_max_parallel: number // 1..4; 1 = one GPU job at a time
  preferences: SettingsPreferences
  endpoints: Record<EndpointName, string | null>
  upload_max_mb_from_env: boolean // true: the environment sets the limit, the saved one is ignored
  effective_upload_max_mb: number
  monthly_cap_env_usd: number
  effective_monthly_cap_usd: number
  // Full month vs what the cap counts since an active reset (UTC ISO time).
  month_spend_usd: number
  month_spend_counted_usd: number
  month_spend_reset_at: string | null
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

interface MonthSpendStatus {
  month_spend_usd: number
  month_spend_counted_usd: number
  month_spend_reset_at: string | null
}

export interface MonthCounterResetResult {
  before: MonthSpendStatus
  after: MonthSpendStatus
}

// GET /api/settings/schema: the declared settings the page builds forms from.
// Metadata only; values come from the overview.
type SettingsSchemaType = 'bool' | 'int' | 'float' | 'text' | 'choice' | 'path' | 'json'

interface SettingsSchemaChoice {
  value: string
  label: string
}

export interface SettingsSchemaRow {
  key: string
  type: SettingsSchemaType
  default: unknown
  scope: string
  label: string
  help: string
  unit: string
  placeholder: string
  tab: string
  section: string
  custom: boolean // a hand-written card owns this key's field
  dev_only: boolean
  choices: SettingsSchemaChoice[] | null
  min: number | null
  max: number | null
  max_len: number | null
  multiline: boolean
}
