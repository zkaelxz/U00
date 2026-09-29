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
  GlossaryTerm,
  GlossaryTermUpsert,
  TranslateErrorsDismissed,
  TranslatePresetBody,
  TranslatePresetSaved,
  TranslateRunConfig,
  TranslateRunEstimate,
  TranslateRunStartBody,
  TranslateRunStarted,
  VoiceBankEntry,
  WorkflowTierApplied,
} from '../types/translateStage'
import { deleteJson, getJson, postJson } from './client'

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
// X01: clears only the last run's failed-batch record; lines are untouched.
export const dismissTranslateErrors = (id: number, f?: Fetch) =>
  postJson<TranslateErrorsDismissed>(`/api/translate-run/dramas/${id}/errors/dismiss`, {}, f)
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
export const deleteGlossaryTerm = (id: number, termId: number, f?: Fetch) =>
  deleteJson<{ deleted: boolean }>(`/api/glossary/dramas/${id}/terms/${termId}?confirm=true`, f)

/** Deletes terms one by one (there is no bulk endpoint); reports which failed. */
export async function deleteGlossaryTerms(
  id: number,
  termIds: number[],
  f?: Fetch,
): Promise<{ deleted: number[]; failed: { id: number; error: unknown }[] }> {
  const deleted: number[] = []
  const failed: { id: number; error: unknown }[] = []
  for (const termId of termIds) {
    try {
      await deleteGlossaryTerm(id, termId, f)
      deleted.push(termId)
    } catch (error) {
      failed.push({ id: termId, error })
    }
  }
  return { deleted, failed }
}
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
