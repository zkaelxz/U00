import type {
  BulkCancelResult,
  BulkJobList,
  BulkResumeResult,
  CharacterEntry,
  CloneEngines,
  CharacterUpdate,
  EstimateParams,
  GlossaryCatalogues,
  GlossaryInstructions,
  GlossaryBulkDeleteResult,
  GlossaryImportBody,
  GlossaryImportResult,
  GlossaryTerm,
  GlossaryTermUpsert,
  TranslatePresetBody,
  TranslatePresetSaved,
  TranslateRunConfig,
  TranslateRunEstimate,
  TranslateRunStartBody,
  TranslateRunStarted,
  VoiceBankEntry,
  TranslatePresetApplied,
  WorkflowTierApplied,
} from '../types/translateStage'
import { apiUrl, getJson, postJson } from './client'

type Fetch = typeof fetch

export function buildEstimateQuery(p: EstimateParams): string {
  const q = new URLSearchParams()
  if (p.engine) q.set('engine', p.engine)
  if (p.model) q.set('model', p.model)
  if (p.force_retranslate) q.set('force_retranslate', 'true')
  if (p.reflect) q.set('reflect', 'true')
  if (p.bulk) q.set('bulk', 'true')
  if (p.job_cost_cap_usd !== undefined) q.set('job_cost_cap_usd', String(p.job_cost_cap_usd))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const getTranslateConfig = (id: number, f?: Fetch) =>
  getJson<TranslateRunConfig>(`/api/translate-run/dramas/${id}/config`, f)
export const getTranslateEstimate = (id: number, p: EstimateParams, f?: Fetch) =>
  getJson<TranslateRunEstimate>(`/api/translate-run/dramas/${id}/estimate${buildEstimateQuery(p)}`, f)
export const startTranslateRun = (id: number, body: TranslateRunStartBody, f?: Fetch) =>
  postJson<TranslateRunStarted>(`/api/translate-run/dramas/${id}/run`, body, f)
export const applyWorkflowTier = (id: number, tier: string, f?: Fetch) =>
  postJson<WorkflowTierApplied>(`/api/translate-run/dramas/${id}/workflow-tier`, { tier }, f)
export const applyTranslatePreset = (id: number, presetId: number, f?: Fetch) =>
  postJson<TranslatePresetApplied>(`/api/translate-run/dramas/${id}/apply-preset`, { preset_id: presetId }, f)
export const saveTranslatePreset = (body: TranslatePresetBody, f?: Fetch) =>
  postJson<TranslatePresetSaved>('/api/translate-run/presets', body, f)
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
