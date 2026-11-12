// Hand-written mirrors of api/schemas/translate.py and characters.py (Translate stage:
// translate-run, glossary and characters models). Booleans and numbers only, never keys.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

import type { LibraryPreset, ProfileOptions } from './library'

// The server types these two as free JSON; the Translate stage reads them as below.
export type TranslateRunConfig = Omit<Schemas['TranslateRunConfig'], 'translate_start' | 'last_translate_errors'> & {
  // The form's starting values a profile left on this title ({} if none).
  translate_start: ProfileOptions & { style_preset?: string; engine_model?: string }
  // X01: the last full run's failed batches (null when none or dismissed).
  // `lines` are 0-based line positions; `error` is already redacted.
  last_translate_errors: TranslateBatchError[] | null
}

/** The Translate form's options for retrying the failed lines; the server picks the lines. */
export type RetryFailedBody = Omit<TranslateRunStartBody, 'line_ids' | 'force_retranslate' | 'bulk' | 'reflect'>

export interface RetryFailedStarted extends TranslateRunStarted {
  line_ids: number[]
}

export type EpisodeSummaryOptions = Schemas['EpisodeSummaryOptions']

export type EpisodeSummaryGenerated = Schemas['EpisodeSummaryGenerated']

interface TranslateBatchError {
  batch_index?: number
  lines?: number[]
  error?: string
}

export type TranslateErrorsDismissed = Schemas['TranslateErrorsDismissed']

export type TranslateRunEstimate = Schemas['TranslateRunEstimate']

export interface EstimateParams {
  engine?: string
  model?: string
  force_retranslate?: boolean
  job_cost_cap_usd?: number
  reflect?: boolean
  bulk?: boolean
  thinking?: boolean
  /** The run's own batch size; omitted, the engine's default. */
  batch_size?: number
  /** Estimate only these lines (those the run would translate); omitted, the whole title. */
  line_ids?: number[]
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
  batch_size?: number
  job_cost_cap_usd?: number
  fallback_chain?: FallbackEngine[]
  reflect?: boolean
  bulk?: boolean
  thinking?: boolean // omitted: the title's remembered choice
  default_female_pronouns?: boolean // omitted: false
  include_genre_notes?: boolean // omitted: true
  memory_examples?: boolean // omitted: false
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
  /** submitting|submitted|scheduled|running|applying|needs_resubmit|applied|cancelled|failed|auth_error */
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

export type TranslateRunStarted = Schemas['TranslateRunStarted']

export type GlossaryAffectedLine = Schemas['GlossaryAffectedLine']

export type GlossaryAffectedPreview = Schemas['GlossaryAffectedPreview']

export interface GlossaryAffectedParams {
  term_ids?: number[]
  engine?: string
  model?: string
  reflect?: boolean
  job_cost_cap_usd?: number
}

export type GlossaryAffectedRunBody = Omit<TranslateRunStartBody, 'force_retranslate' | 'bulk'> & {
  line_ids: number[]
  preview_hash: string
  include_hand_edited: boolean
  term_ids?: number[]
}

// Review's "Retranslate these": the Translate run options plus the lines to redo.
export type SourceChangedRunBody = Omit<TranslateRunStartBody, 'force_retranslate' | 'bulk'> & {
  line_ids: number[]
}

export interface SourceChangedRunStarted extends TranslateRunStarted {
  // The lines the run will translate: those still flagged when it started.
  line_ids: number[]
}

export interface GlossaryAffectedRunStarted extends TranslateRunStarted {
  line_ids: number[]
  skipped_hand_edited_count: number
}

export type GlossaryTerm = Schemas['GlossaryTerm']
export type WordReplacement = Schemas['WordReplacement']

export type GlossaryTermUpsert = Partial<Omit<GlossaryTerm, 'id'>> & { id?: number }

// Parity T03: POST /api/glossary/dramas/{id}/import.
export interface GlossaryImportBody {
  text: string
  filename?: string
  overwrite_existing?: boolean
  confirm?: boolean
}

export type GlossaryImportResult = Schemas['GlossaryImportResult']

// POST /api/glossary/dramas/{id}/terms/bulk-delete.
export type GlossaryBulkDeleteResult = Schemas['GlossaryBulkDeleteResult']

export type GlossaryInstructions = Schemas['GlossaryInstructions']
export type InstructionsDraft = Schemas['InstructionsDraft']

export type GlossaryCatalogues = Schemas['GlossaryCatalogues']

export interface CharacterEntry {
  speaker_label: string
  character_name: string
  // Optional: older mocks and servers may omit it.
  voice_actor?: string
  pronouns: string
  clone_engine: string
  // Why the stored engine can no longer generate (it was removed); "" when fine.
  clone_engine_removed?: string
  // Why this speaker won't be cloned as set up (removed engine or missing clip); "" when fine.
  clone_warning?: string
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

// translate_engines.WORKFLOW_TIERS (Draft / Standard / Release).
interface WorkflowTier {
  key: string
  label: string
  translation_engine: string
  engine_model: string | null
  reflect: boolean
  auto_qc: boolean
}

// POST /api/translate-run/dramas/{id}/workflow-tier: the engine is saved on
// the drama; the rest fills the form. Nothing is started.
// POST /api/translate-run/dramas/{id}/apply-preset. The engine
// (when the preset has one) is saved on the drama; the rest fills the form.
export interface TranslatePresetApplied {
  drama_id: number
  preset_id: number
  name: string
  translation_engine: string | null
  engine_model: string | null
  style_preset: string | null
  locale: string | null
  default_female_pronouns: boolean
  include_genre_notes: boolean
  // The profile's Translate run options for the form, and the keys left out
  // because they don't suit the title's language.
  options?: ProfileOptions
  skipped?: string[]
}

export interface WorkflowTierApplied extends Omit<WorkflowTier, 'key'> {
  drama_id: number
  tier: string
}

// POST /api/translate-run/presets. A taken name is a 409 unless
// overwrite is true.
export interface TranslatePresetBody {
  name: string
  translation_engine: string
  engine_model: string | null
  style_preset: string | null
  locale: string | null
  default_female_pronouns: boolean
  include_genre_notes: boolean
  options?: ProfileOptions
  overwrite?: boolean
}

// POST /api/translate-run/presets/{id}/update replaces every field and the name.
export type TranslatePresetUpdateBody = Omit<TranslatePresetBody, 'overwrite'>

export interface TranslatePresetSaved {
  preset: LibraryPreset
  replaced: boolean
}

// GET /api/glossary/dramas/{id}/variants and /name-clusters: read-only, offline.
export type GlossaryVariantExample = Schemas['GlossaryVariantExample']

export type GlossaryVariants = Schemas['GlossaryVariants']

export type GlossaryNameCluster = Schemas['GlossaryNameCluster']

export type GlossaryNameClusters = Schemas['GlossaryNameClusters']

// GET/POST /api/glossary/dramas/{id}/address-terms: the scan is read-only and offline.
export type AddressTerms = Schemas['AddressTerms']

export type AddressTermCandidate = Schemas['AddressTermCandidate']

export type AddressTermChoice = Schemas['AddressTermChoice']

export type AddressTermsSaveResult = Schemas['AddressTermsSaveResult']

export type SpellingSuggestStatus = Schemas['SpellingSuggestStatus']

export type NameConsistency = Schemas['NameConsistency']

// GET /api/glossary/name-report: the name check across every title the caller may see.
export type NameMark = 'right' | 'wrong' | 'skip'

export type NameReportGroup = Schemas['NameReportGroup']

export type NameReport = Schemas['NameReport']

export type NameReportStatus = Schemas['NameReportStatus']
