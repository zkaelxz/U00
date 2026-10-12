// Hand-written mirrors of api/schemas/translate.py and characters.py (Translate stage:
// translate-run, glossary and characters models). Booleans and numbers only, never keys.

import type { LibraryPreset } from './library'
import type { TranslateEngine } from './translate'

export interface TranslateRunConfig {
  drama_id: number
  translation_engine: string
  // The owner's saved choice for this title; null/omitted = never chosen.
  default_female_pronouns?: boolean | null
  include_genre_notes?: boolean | null
  translate_by_sentence?: boolean | null
  // Engines whose request can switch thinking (DeepSeek, Ollama), and the
  // title's remembered "think harder" choice.
  thinking_switch_engines?: string[]
  title_thinking?: boolean
  engines: TranslateEngine[]
  // guidance: what the style asks the translator for (parity X04)
  style_presets: { key: string; label: string; guidance?: string }[]
  default_style_preset: string
  locales: string[]
  workflow_tiers: WorkflowTier[]
  defaults: { context_window: number; context_window_ahead: number; batch_size: number }
  // {engine: {model: default batch size}}; '' is the engine's own default model.
  batch_size_defaults?: Record<string, Record<string, number>>
  // Settings > Defaults for new dramas (optional: older servers omit them).
  default_locale?: string
  default_style_note?: string
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
  // X24: whether the Ollama server answers. Probed only when the drama's
  // saved engine is Ollama; null (or missing on older servers) = not checked.
  // A boolean only: the server never sends the URL.
  ollama_reachable?: boolean | null
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
  thinking?: boolean
  // True when thinking is on: hidden reasoning is billed but cannot be predicted.
  estimate_is_lower_bound?: boolean
}

export interface EstimateParams {
  engine?: string
  model?: string
  force_retranslate?: boolean
  job_cost_cap_usd?: number
  reflect?: boolean
  bulk?: boolean
  thinking?: boolean
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

export interface TranslateRunStarted {
  job_id: string
  drama_id: number
  engine: string
  model: string | null
  target_line_count: number
  fallback_engines: string[]
}

// Lines a glossary change affects (GET .../glossary-affected). hand_edited is
// true unless the English is exactly what the last recorded translate run
// produced; unknown provenance counts as hand-edited.
export interface GlossaryAffectedMatch {
  term_id: number
  term_original: string
  term_translation: string
  reason: 'source' | 'banned'
}

export interface GlossaryAffectedLine {
  id: number
  idx: number
  start: number | null
  end: number | null
  zh: string
  en: string
  hand_edited: boolean
  matched_terms: GlossaryAffectedMatch[]
}

export interface GlossaryAffectedPreview {
  drama_id: number
  has_glossary: boolean
  terms: { id: number; term_original: string; term_translation: string }[]
  selected_term_ids: number[]
  lines: GlossaryAffectedLine[]
  hand_edited_count: number
  preview_hash: string
  estimate: TranslateRunEstimate
  estimate_with_hand_edited: TranslateRunEstimate
}

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

// Parity T03: POST /api/glossary/dramas/{id}/import.
export interface GlossaryImportBody {
  text: string
  filename?: string
  overwrite_existing?: boolean
  confirm?: boolean
}

export interface GlossaryImportResult {
  added: string[]
  overwritten: string[]
  skipped_existing: string[]
  invalid: string[]
  warnings: string[]
}

// Parity X13: POST /api/glossary/dramas/{id}/terms/bulk-delete.
export interface GlossaryBulkDeleteResult {
  deleted: number[]
  not_found: number[]
}

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
  clone_engine: string
  // Why the stored engine can no longer generate (it was removed); "" when fine.
  clone_engine_removed?: string
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
// Parity X03: POST /api/translate-run/dramas/{id}/apply-preset. The engine
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
}

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

// GET /api/glossary/dramas/{id}/variants and /name-clusters: read-only, offline.
export interface GlossaryVariantExample {
  line_idx: number
  snippet: string
}

export interface GlossaryVariantCandidate {
  text: string
  count: number
  /** 0..1, 1 = the same sound. */
  score: number
  is_alias: boolean
  is_term: boolean
  examples: GlossaryVariantExample[]
}

export interface GlossaryVariants {
  term: string
  /** False when a reading library for the title's language is missing; `reason` says which. */
  available: boolean
  reason: string
  lines_scanned: number
  truncated: boolean
  candidates: GlossaryVariantCandidate[]
}

export interface GlossaryNameSpelling {
  text: string
  count: number
  sources: string[]
  is_alias: boolean
  is_term: boolean
}

export interface GlossaryNameCluster {
  suggested_original: string
  /** Set when one of the spellings is already a term or alias. */
  existing_term: string | null
  total: number
  spellings: GlossaryNameSpelling[]
  examples: GlossaryVariantExample[]
}

export interface GlossaryNameClusters {
  available: boolean
  reason: string
  lines_scanned: number
  truncated: boolean
  clusters: GlossaryNameCluster[]
  /** Downloaded VTuber names found in the lines; empty unless the title opted in. */
  known_vtuber_names: GlossaryNameCluster[]
}

export interface SpellingProposal {
  text: string
  count: number
  is_alias: boolean
  is_term: boolean
  examples: GlossaryVariantExample[]
}

export interface SpellingSuggestResult {
  term: string | null
  english: string | null
  proposals: SpellingProposal[]
  /** True when the model's answer could not be read; nothing was proposed or changed. */
  unusable: boolean
  lines_scanned: number
  truncated: boolean
}

export interface SpellingSuggestStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  run_id: string | null
  result: SpellingSuggestResult | null
}

export interface NameConsistencyRow {
  line_idx: number
  source: string
  english: string
}

export interface NameConsistency {
  term: string
  english: string
  available: boolean
  reason: string
  lines_with_name: number
  untranslated: number
  consistent: number
  differing: NameConsistencyRow[]
  other_words: string[]
  lines_scanned: number
  /** The scan stopped at its budget; the counts cover only `lines_scanned` lines. */
  truncated: boolean
  /** More differing lines exist than are listed. */
  more_rows: boolean
}

// GET /api/glossary/name-report: the name check across every title the caller may see.
export type NameMark = 'right' | 'wrong' | 'skip'

export interface NameReportVariant {
  text: string
  count: number
}

export interface NameReportTitle {
  drama_id: number
  title: string
  count: number
  in_glossary: boolean
  glossary_term: string | null
  glossary_english: string | null
}

export interface NameReportExample {
  drama_id: number
  line_idx: number
  snippet: string
}

export interface NameReportGroup {
  key: string
  canonical: string
  total: number
  variants: NameReportVariant[]
  /** The real number of titles; `titles` lists the first few. */
  titles_total: number
  titles: NameReportTitle[]
  examples: NameReportExample[]
  mark: NameMark | null
}

export interface NameReport {
  titles_total: number
  titles_scanned: number
  titles_unavailable: number
  titles_failed: number
  reasons: string[]
  /** A cap stopped the scan or trimmed the groups. */
  truncated: boolean
  /** The time budget ended the scan or the grouping early; the report is partial. */
  stopped_early: boolean
  singletons_omitted: number
  groups_omitted: number
  groups: NameReportGroup[]
  counts: { unresolved: number; right: number; wrong: number; skip: number }
}

export interface NameReportStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  result: NameReport | null
}
