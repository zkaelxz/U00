import type { TranscribeConfig } from '../types/workspace'
import type {
  AutotuneRunRequest,
  GlossaryDismissals,
  GlossaryDismissResult,
  AutotuneRunResult,
  AutotuneStatus,
  NovelGlossaryApplyResult,
  NovelGlossaryRunResult,
  NovelGlossaryStatus,
} from '../types/autotuneGlossary'
import type { GlossaryProposalsApplyRequest, LinesGlossaryRunResult } from '../types/glossaryHelpers'
import type { JobCancelResult } from '../types/jobs'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

// Auto-tune min silence (api/routers/transcribe_routes.py, batch 2C).
// GET answers status "idle" when no run is held in this app session.
export const getAutotune = (id: number, f?: Fetch) =>
  getJson<AutotuneStatus>(`/api/transcribe/dramas/${id}/autotune`, f)

export const startAutotune = (id: number, body: AutotuneRunRequest, f?: Fetch) =>
  postJson<AutotuneRunResult>(`/api/transcribe/dramas/${id}/autotune`, body, f)

export const applyAutotune = (id: number, candidateMs: number, f?: Fetch) =>
  postJson<TranscribeConfig>(`/api/transcribe/dramas/${id}/autotune/apply`, { candidate_ms: candidateMs }, f)

// Glossary from the attached novel (api/routers/glossary_routes.py, batch 2C).
export const getNovelGlossary = (id: number, f?: Fetch) =>
  getJson<NovelGlossaryStatus>(`/api/glossary/dramas/${id}/from-novel`, f)

// fresh: ignore the replies cached by an earlier run over the same novel
// text and engine and ask the model again; sent only when true.
export const startNovelGlossary = (id: number, f?: Fetch, opts: { fresh?: boolean } = {}) =>
  postJson<NovelGlossaryRunResult>(
    `/api/glossary/dramas/${id}/from-novel${opts.fresh ? '?fresh=true' : ''}`,
    undefined,
    f,
  )

export const applyNovelGlossary = (id: number, body: GlossaryProposalsApplyRequest, f?: Fetch) =>
  postJson<NovelGlossaryApplyResult>(`/api/glossary/dramas/${id}/from-novel/apply`, body, f)

// Glossary from the drama's source lines (glossary_routes.py, parity X10).
// Same status/apply shapes as from-novel; GET answers status "idle" when no run is held.
export const getLinesGlossary = (id: number, f?: Fetch) =>
  getJson<NovelGlossaryStatus>(`/api/glossary/dramas/${id}/from-lines`, f)

export const startLinesGlossary = (id: number, f?: Fetch) =>
  postJson<LinesGlossaryRunResult>(`/api/glossary/dramas/${id}/from-lines`, undefined, f)

export const applyLinesGlossary = (id: number, body: GlossaryProposalsApplyRequest, f?: Fetch) =>
  postJson<NovelGlossaryApplyResult>(`/api/glossary/dramas/${id}/from-lines/apply`, body, f)

// Run-scoped cancel: the server stops the extraction only while it is still
// run `runId` (409 otherwise), so a stale Cancel can't stop a newer run.
export const cancelNovelGlossary = (id: number, runId: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/glossary/dramas/${id}/from-novel/cancel`, { run_id: runId }, f)

export const cancelLinesGlossary = (id: number, runId: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/glossary/dramas/${id}/from-lines/cancel`, { run_id: runId }, f)

// The series' ignore list: proposals dismissed so no extraction lists them again.
export const getGlossaryDismissals = (id: number, f?: Fetch) =>
  getJson<GlossaryDismissals>(`/api/glossary/dramas/${id}/dismissals`, f)

export const dismissGlossaryTerms = (id: number, terms: string[], f?: Fetch) =>
  postJson<GlossaryDismissResult>(`/api/glossary/dramas/${id}/dismissals`, { terms }, f)

export const restoreGlossaryTerms = (id: number, terms: string[], f?: Fetch) =>
  postJson<GlossaryDismissResult>(`/api/glossary/dramas/${id}/dismissals/restore`, { terms }, f)
