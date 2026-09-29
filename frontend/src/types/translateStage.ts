// Hand-written mirrors of api/schemas.py (Translate stage: translate-run,
// glossary and characters models). Booleans and numbers only, never keys.

import type { TranslateEngine } from './translate'

export interface TranslateRunConfig {
  drama_id: number
  translation_engine: string
  engines: TranslateEngine[]
  style_presets: { key: string; label: string }[]
  default_style_preset: string
  locales: string[]
  defaults: { context_window: number; context_window_ahead: number; batch_size: number }
  project_instructions: string | null
  series_instructions: string | null
  line_count: number
  untranslated_count: number
  monthly_cap_usd: number
  month_spend: number
  cap_applies_by_engine: Record<string, boolean>
  bulk_supported_engines: string[]
}

export interface TranslateRunEstimate {
  engine: string
  model: string | null
  estimated_usd: number | null
  target_line_count: number
  free: boolean
  cap_applies: boolean
  effective_cap_usd: number | null
  monthly_refusal: boolean
  estimate_above_cap: boolean
}

export interface EstimateParams {
  engine?: string
  model?: string
  force_retranslate?: boolean
  job_cost_cap_usd?: number
  reflect?: boolean
  bulk?: boolean
}

export interface FallbackEngine {
  engine: string
  model?: string
}

export interface TranslateRunStartBody {
  engine?: string
  model?: string
  style_preset?: string
  style_note: string
  locale: string
  force_retranslate: boolean
  context_window: number
  context_window_ahead: number
  batch_size: number
  job_cost_cap_usd?: number
  fallback_chain?: FallbackEngine[]
  reflect?: boolean
  bulk?: boolean
  default_female_pronouns?: boolean // omitted: false
  include_genre_notes?: boolean // omitted: true
}

export interface BulkResumeResult {
  drama_id: number
  jobs: { bulk_job_id: number; state: string }[]
}

/** One bulk batch as last recorded (TranslateBulkJobEntry). No prompts,
 * provider batch id or keys. */
export interface BulkJobEntry {
  bulk_job_id: number
  engine: string
  model: string | null
  kind: string
  stage: string | null
  pipeline_id: string | null
  /** submitting|submitted|scheduled|running|applied|cancelled|failed|auth_error */
  status: string
  pending: boolean
  cancellable: boolean
  line_count: number
  scheduled_for: string | null
  result_summary: Record<string, unknown> | null
  last_error: string | null
  submitted_at: string | null
  updated_at: string | null
}

export interface BulkJobList {
  drama_id: number
  jobs: BulkJobEntry[]
}

export interface BulkCancelResult {
  drama_id: number
  bulk_job: BulkJobEntry
  message: string
}

export interface TranslateRunStarted {
  job_id: string
  drama_id: number
  engine: string
  model: string | null
  target_line_count: number
  fallback_engines: string[]
}

export interface GlossaryTerm {
  id: number
  term_original: string
  term_translation: string
  notes: string
  category: string | null
  policy: string | null
  enforce_exact: boolean
  aliases: string[]
  banned_translations: string[]
}

export type GlossaryTermUpsert = Partial<Omit<GlossaryTerm, 'id'>> & { id?: number }

export interface GlossaryInstructions {
  project_instructions: string
  series_instructions: string
}

export interface GlossaryCatalogues {
  term_categories: { key: string; label: string }[]
  term_policies: { key: string; label: string; example: string }[]
}

export interface CharacterEntry {
  speaker_label: string
  character_name: string
  pronouns: string
  tts_voice: string
  offline_voice: string
  clone_engine: string
  voice_design: string
  has_ref_audio: boolean
  ref_text_present: boolean
  series_character_id: number | null
  series_character_name: string
  line_count: number
}

export interface CharacterUpdate {
  speaker_label: string
  character_name?: string
  pronouns?: string
  tts_voice?: string
  offline_voice?: string
  clone_engine?: string
  voice_design?: string
  ref_text?: string
}

export interface CloneEngines {
  source_language: string
  default_engine: string
  engines: { id: string; label: string; is_default: boolean; language_gated: boolean; local_model: boolean }[]
}

export interface VoiceBankEntry {
  id: number
  name: string
  clone_engine: string
  voice_design: string
  language: string
  notes: string
  ref_text_present: boolean
}
