import type {
  BulkResumeResult,
  CharacterEntry,
  CharacterUpdate,
  EstimateParams,
  GlossaryCatalogues,
  GlossaryInstructions,
  GlossaryTerm,
  GlossaryTermUpsert,
  TranslateRunConfig,
  TranslateRunEstimate,
  TranslateRunStartBody,
  TranslateRunStarted,
} from '../types/translateStage'
import { getJson, postJson } from './client'

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
export const resumeBulkTranslations = (id: number, f?: Fetch) =>
  postJson<BulkResumeResult>(`/api/translate-run/dramas/${id}/bulk/resume`, {}, f)

export const getGlossaryTerms = (id: number, f?: Fetch) =>
  getJson<GlossaryTerm[]>(`/api/glossary/dramas/${id}/terms`, f)
export const saveGlossaryTerm = (id: number, term: GlossaryTermUpsert, f?: Fetch) =>
  postJson<GlossaryTerm>(`/api/glossary/dramas/${id}/terms`, term, f)
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
