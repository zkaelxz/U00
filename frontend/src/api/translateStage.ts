import type {
  BulkCancelResult,
  BulkJobList,
  BulkResumeResult,
  CharacterEntry,
  CloneEngines,
  CharacterUpdate,
  EpisodeSummaryGenerated,
  EpisodeSummaryOptions,
  EstimateParams,
  GlossaryAffectedParams,
  GlossaryAffectedPreview,
  GlossaryAffectedRunBody,
  GlossaryAffectedRunStarted,
  GlossaryCatalogues,
  GlossaryInstructions,
  GlossaryBulkDeleteResult,
  GlossaryImportBody,
  GlossaryImportResult,
  GlossaryNameClusters,
  GlossaryTerm,
  GlossaryTermUpsert,
  GlossaryVariants,
  NameConsistency,
  NameMark,
  NameReportStatus,
  RetryFailedBody,
  RetryFailedStarted,
  SpellingSuggestStatus,
  SourceChangedRunBody,
  SourceChangedRunStarted,
  TranslateErrorsDismissed,
  TranslatePresetBody,
  TranslatePresetUpdateBody,
  TranslatePresetSaved,
  TranslateRunConfig,
  TranslateRunEstimate,
  TranslateRunStartBody,
  TranslateRunStarted,
  VoiceBankEntry,
  TranslatePresetApplied,
  WorkflowTierApplied,
} from '../types/translateStage'
import type { JobCancelResult } from '../types/jobs'
import type { LibraryPreset, ProfileField } from '../types/library'
import type { TranslateEngine } from '../types/translate'
import { apiUrl, fetchBody, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export function buildEstimateQuery(p: EstimateParams): string {
  const q = new URLSearchParams()
  if (p.engine) q.set('engine', p.engine)
  if (p.model) q.set('model', p.model)
  if (p.force_retranslate) q.set('force_retranslate', 'true')
  if (p.reflect) q.set('reflect', 'true')
  if (p.bulk) q.set('bulk', 'true')
  if (p.thinking !== undefined) q.set('thinking', String(p.thinking))
  if (p.job_cost_cap_usd !== undefined) q.set('job_cost_cap_usd', String(p.job_cost_cap_usd))
  if (p.batch_size !== undefined) q.set('batch_size', String(p.batch_size))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const getTranslateConfig = (id: number, f?: Fetch) =>
  getJson<TranslateRunConfig>(`/api/translate-run/dramas/${id}/config`, f)
export const getTranslateEstimate = (id: number, p: EstimateParams, f?: Fetch) =>
  getJson<TranslateRunEstimate>(`/api/translate-run/dramas/${id}/estimate${buildEstimateQuery(p)}`, f)
export const startTranslateRun = (id: number, body: TranslateRunStartBody, f?: Fetch) =>
  postJson<TranslateRunStarted>(`/api/translate-run/dramas/${id}/run`, body, f)
export function buildGlossaryAffectedQuery(p: GlossaryAffectedParams): string {
  const q = new URLSearchParams()
  for (const id of p.term_ids ?? []) q.append('term_ids', String(id))
  if (p.engine) q.set('engine', p.engine)
  if (p.model) q.set('model', p.model)
  if (p.reflect) q.set('reflect', 'true')
  if (p.job_cost_cap_usd !== undefined) q.set('job_cost_cap_usd', String(p.job_cost_cap_usd))
  const s = q.toString()
  return s ? `?${s}` : ''
}
/** Read-only: no engine call, no change. */
export const getGlossaryAffected = (id: number, p: GlossaryAffectedParams, f?: Fetch) =>
  getJson<GlossaryAffectedPreview>(`/api/translate-run/dramas/${id}/glossary-affected${buildGlossaryAffectedQuery(p)}`, f)
/** 409 when the lines or glossary changed since the preview. */
export const startGlossaryAffectedRun = (id: number, body: GlossaryAffectedRunBody, f?: Fetch) =>
  postJson<GlossaryAffectedRunStarted>(`/api/translate-run/dramas/${id}/glossary-affected/run`, body, f)
/** Translates the given lines still flagged source_changed; 409 when none is. */
export const startSourceChangedRun = (id: number, body: SourceChangedRunBody, f?: Fetch) =>
  postJson<SourceChangedRunStarted>(`/api/translate-run/dramas/${id}/source-changed/run`, body, f)
/** Translates exactly the lines the last run failed on, with the form's engine. */
export const retryFailedLines = (id: number, body: RetryFailedBody, f?: Fetch) =>
  postJson<RetryFailedStarted>(`/api/translate-run/dramas/${id}/retry-failed/run`, body, f)
/** Redoes the lines flagged for a missing glossary name with the Settings stronger engine. */
export const redoGlossaryFlagged = (id: number, f?: Fetch) =>
  postJson<RetryFailedStarted>(`/api/translate-run/dramas/${id}/glossary-flagged/run`, {}, f)
export const getEpisodeSummaryOptions = (id: number, f?: Fetch) =>
  getJson<EpisodeSummaryOptions>(`/api/translate-run/dramas/${id}/episode-summary`, f)
/** Spends on the chosen engine; returns the text without saving it. */
export const generateEpisodeSummary = (id: number, engine: string, f?: Fetch) =>
  postJson<EpisodeSummaryGenerated>(`/api/translate-run/dramas/${id}/episode-summary/generate`, { engine }, f)
export const applyWorkflowTier = (id: number, tier: string, f?: Fetch) =>
  postJson<WorkflowTierApplied>(`/api/translate-run/dramas/${id}/workflow-tier`, { tier }, f)
export const applyTranslatePreset = (id: number, presetId: number, f?: Fetch) =>
  postJson<TranslatePresetApplied>(`/api/translate-run/dramas/${id}/apply-preset`, { preset_id: presetId }, f)

// X01: clears only the last run's failed-batch record; lines are untouched.
export const dismissTranslateErrors = (id: number, f?: Fetch) =>
  postJson<TranslateErrorsDismissed>(`/api/translate-run/dramas/${id}/errors/dismiss`, {}, f)
export const saveTranslatePreset = (body: TranslatePresetBody, f?: Fetch) =>
  postJson<TranslatePresetSaved>('/api/translate-run/presets', body, f)
// Profile management (Library Tools). Editing replaces every field, so it is PC only like an overwrite.
export const updateTranslatePreset = (id: number, body: TranslatePresetUpdateBody, f?: Fetch) =>
  postJson<{ preset: LibraryPreset }>(`/api/translate-run/presets/${id}/update`, body, pcOnlyFetch(f))
export const setDefaultTranslatePreset = (id: number, isDefault: boolean, f?: Fetch) =>
  postJson<LibraryPreset>(`/api/translate-run/presets/${id}/default`, { is_default: isDefault }, f)
export const getProfileEditorFields = (f?: Fetch) =>
  getJson<{ engines: TranslateEngine[]; style_presets: { key: string; label: string }[]; fields: ProfileField[] }>(
    '/api/translate-run/profile-fields', f)
export const resumeBulkTranslations = (id: number, f?: Fetch) =>
  postJson<BulkResumeResult>(`/api/translate-run/dramas/${id}/bulk/resume`, {}, f)
export const listBulkTranslations = (id: number, f?: Fetch) =>
  getJson<BulkJobList>(`/api/translate-run/dramas/${id}/bulk`, f)
export const cancelBulkTranslation = (id: number, bulkJobId: number, f?: Fetch) =>
  postJson<BulkCancelResult>(`/api/translate-run/dramas/${id}/bulk/${bulkJobId}/cancel`, {}, f)

export const getGlossaryTerms = (id: number, f?: Fetch) =>
  getJson<GlossaryTerm[]>(`/api/glossary/dramas/${id}/terms`, f)
export const saveGlossaryTerm = (id: number, term: GlossaryTermUpsert, f?: Fetch) =>
  postJson<GlossaryTerm>(`/api/glossary/dramas/${id}/terms`, term, f)
/** Other spellings of `term` in the source lines (read-only, no engine, no cost). */
export const getGlossaryVariants = (id: number, term: string, f?: Fetch) =>
  getJson<GlossaryVariants>(`/api/glossary/dramas/${id}/variants?term=${encodeURIComponent(term)}`, f)
/** Names that recur under several sound-alike spellings (read-only, no engine, no cost). */
export const getGlossaryNameClusters = (id: number, f?: Fetch) =>
  getJson<GlossaryNameClusters>(`/api/glossary/dramas/${id}/name-clusters`, f)
/** Deletes the named terms in one request (by id); ids no longer in the glossary come back in not_found. */
export const deleteGlossaryTerms = (id: number, termIds: number[], f?: Fetch) =>
  postJson<GlossaryBulkDeleteResult>(`/api/glossary/dramas/${id}/terms/bulk-delete`, { term_ids: termIds, confirm: true }, f)
export const importGlossary = (id: number, body: GlossaryImportBody, f?: Fetch) =>
  postJson<GlossaryImportResult>(`/api/glossary/dramas/${id}/import`, body, f)
/** A plain download link (Content-Disposition: attachment). */
export const glossaryCsvUrl = (id: number) => apiUrl(`/api/glossary/dramas/${id}/export.csv`)
export const getGlossaryCatalogues = (f?: Fetch) =>
  getJson<GlossaryCatalogues>('/api/glossary/catalogues', f)
export const getInstructions = (id: number, f?: Fetch) =>
  getJson<GlossaryInstructions>(`/api/glossary/dramas/${id}/instructions`, f)
export const saveInstructions = (id: number, scope: 'project' | 'series', text: string, f?: Fetch) =>
  postJson<GlossaryInstructions>(`/api/glossary/dramas/${id}/instructions/${scope}`, { text }, f)

export const getCharacters = (id: number, f?: Fetch) =>
  getJson<CharacterEntry[]>(`/api/characters/dramas/${id}`, f)
export const saveCharacter = (id: number, update: CharacterUpdate, f?: Fetch) =>
  postJson<CharacterEntry>(`/api/characters/dramas/${id}/character`, update, f)
export const getCloneEngines = (id: number, f?: Fetch) =>
  getJson<CloneEngines>(`/api/characters/dramas/${id}/clone-engines`, f)
export const getVoiceBank = (f?: Fetch) => getJson<VoiceBankEntry[]>('/api/characters/voice-bank', f)
export const applyVoiceBankEntry = (id: number, speakerLabel: string, voiceBankId: number, f?: Fetch) =>
  postJson<CharacterEntry>(
    `/api/characters/dramas/${id}/voice-bank/apply`,
    { speaker_label: speakerLabel, voice_bank_id: voiceBankId },
    f,
  )

/** One model call (paid unless the engine is free) proposing how a known name may be spelled in the lines. */
export const startSpellingSuggestions = (id: number, termId: number, english: string, f?: Fetch) =>
  postJson<{ job_id: string; engine: string }>(`/api/glossary/dramas/${id}/spelling-suggestions`, { term_id: termId, english }, f)
export const getSpellingSuggestions = (id: number, f?: Fetch) =>
  getJson<SpellingSuggestStatus>(`/api/glossary/dramas/${id}/spelling-suggestions`, f)
export const cancelSpellingSuggestions = (id: number, runId: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/glossary/dramas/${id}/spelling-suggestions/cancel`, { run_id: runId }, f)
/** Lines where the term's name is not rendered as `english` (read-only, no engine, no cost). */
export const getNameConsistency = (id: number, termId: number, english: string, f?: Fetch) =>
  getJson<NameConsistency>(
    `/api/glossary/dramas/${id}/name-consistency?term_id=${termId}&english=${encodeURIComponent(english)}`, f)

/** Starts the name check over every title the caller may see (read-only; one runs at a time). */
export const startNameReport = (f?: Fetch) => postJson<{ job_id: string }>('/api/glossary/name-report', {}, f)
export const getNameReport = (f?: Fetch) => getJson<NameReportStatus>('/api/glossary/name-report', f)
export const cancelNameReport = (f?: Fetch) => postJson<JobCancelResult>('/api/glossary/name-report/cancel', {}, f)
/** Marks a name group right, wrong or skip; "clear" removes the mark. */
export const markNameGroup = (key: string, mark: NameMark | 'clear', f?: Fetch) =>
  postJson<{ key: string; mark: NameMark | null }>('/api/glossary/name-report/marks', { key, mark }, f)
/** The unresolved groups as plain text (names, counts and example lines only). */
export const getNameReportText = (f?: Fetch) =>
  fetchBody('/api/glossary/name-report/export.md', {}, (r) => r.text(), f)
