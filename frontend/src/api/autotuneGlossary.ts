import type { TranscribeConfig } from '../types/workspace'
import type {
  AutotuneRunRequest,
  AutotuneRunResult,
  AutotuneStatus,
  NovelGlossaryApplyRequest,
  NovelGlossaryApplyResult,
  NovelGlossaryRunResult,
  NovelGlossaryStatus,
} from '../types/autotuneGlossary'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

// Auto-tune min silence (api/routers/transcribe_routes.py, batch 2C).
// GET is 404 when no run is held in this app session.
export const getAutotune = (id: number, f?: Fetch) =>
  getJson<AutotuneStatus>(`/api/transcribe/dramas/${id}/autotune`, f)

export const startAutotune = (id: number, body: AutotuneRunRequest, f?: Fetch) =>
  postJson<AutotuneRunResult>(`/api/transcribe/dramas/${id}/autotune`, body, f)

export const applyAutotune = (id: number, candidateMs: number, f?: Fetch) =>
  postJson<TranscribeConfig>(`/api/transcribe/dramas/${id}/autotune/apply`, { candidate_ms: candidateMs }, f)

// Glossary from the attached novel (api/routers/glossary_routes.py, batch 2C).
export const getNovelGlossary = (id: number, f?: Fetch) =>
  getJson<NovelGlossaryStatus>(`/api/glossary/dramas/${id}/from-novel`, f)

export const startNovelGlossary = (id: number, f?: Fetch) =>
  postJson<NovelGlossaryRunResult>(`/api/glossary/dramas/${id}/from-novel`, undefined, f)

export const applyNovelGlossary = (id: number, body: NovelGlossaryApplyRequest, f?: Fetch) =>
  postJson<NovelGlossaryApplyResult>(`/api/glossary/dramas/${id}/from-novel/apply`, body, f)
