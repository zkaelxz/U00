// Hand-written mirrors of api/schemas.py (Translate stage: translate-run,
// glossary and characters models). Booleans and numbers only, never keys.

import type { LibraryPreset } from './library'
import type { TranslateEngine } from './translate'

export interface TranslateRunConfig {
  drama_id: number
  translation_engine: string
  engines: TranslateEngine[]
  style_presets: { key: string; label: string }[]
  default_style_preset: string
  locales: string[]
  workflow_tiers: WorkflowTier[]
  defaults: { context_window: number; context_window_ahead: number; batch_size: number }
  project_instructions: string | null
  series_instructions: string | null
  line_count: number
  untranslated_count: number
  monthly_cap_usd: number
  month_spend: number
  cap_applies_by_engine: Record<string, boolean>
  bulk_supported_engines: string[]
  // X01: the last full run's failed batches (null when none or dismissed).
  // `lines` are 0-based line positions; `error` is already redacted.
  last_translate_errors?: TranslateBatchError[] | null
}

export interface TranslateBatchError {
  batch_index?: number
  lines?: number[]
  error?: string
}

export interface TranslateErrorsDismissed {
  drama_id: number
  dismissed: boolean
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
  // Optional: older mocks and servers may omit it.
  voice_actor?: string
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
  // Optional: older mocks and servers may omit them. series_pronouns is
  // the linked series character's default (C07); sample_lines are up to
  // two short source lines (C04).
  series_pronouns?: string
  sample_lines?: string[]
}

export interface CharacterUpdate {
  speaker_label: string
  character_name?: string
  voice_actor?: string
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

// Parity X02: translate_engines.WORKFLOW_TIERS (Draft / Standard / Release).
export interface WorkflowTier {
  key: string
  label: string
  translation_engine: string
  engine_model: string | null
  reflect: boolean
  auto_qc: boolean
}

// POST /api/translate-run/dramas/{id}/workflow-tier: the engine is saved on
// the drama; the rest fills the form. Nothing is started.
export interface WorkflowTierApplied extends Omit<WorkflowTier, 'key'> {
  drama_id: number
  tier: string
}

// Parity X22: POST /api/translate-run/presets. A taken name is a 409 unless
// overwrite is true.
export interface TranslatePresetBody {
  name: string
  translation_engine: string
  engine_model: string | null
  style_preset: string | null
  locale: string | null
  default_female_pronouns: boolean
  include_genre_notes: boolean
  overwrite?: boolean
}

export interface TranslatePresetSaved {
  preset: LibraryPreset
  replaced: boolean
}
