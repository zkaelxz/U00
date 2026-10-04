// Structural line changes (/api/restructure/...). Every
// write is refused (409) while a job runs on the drama or when the drama's
// line ids differ from `expected_line_ids`.
import type { ReviewLine, ReviewLinesPage } from '../types/review'
import type {
  ResegmentLlmPreview,
  ResegmentLlmPreviewStart,
  ResegmentPreview,
  ResegmentStart,
  ResegmentStarted,
  ReassignResult,
  ResplitResult,
  ResplitStart,
  RestoreVersionResult,
  RestructureAddLine,
  RestructureResult,
  RestructureSplit,
} from '../types/restructure'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const base = (id: number) => `/api/restructure/dramas/${id}`

export const addLine = (id: number, body: RestructureAddLine, f?: Fetch) =>
  postJson<RestructureResult>(`${base(id)}/lines/add`, body, f)

export const deleteLine = (id: number, lineId: number, expectedLineIds: number[], f?: Fetch) =>
  postJson<RestructureResult>(
    `${base(id)}/lines/${lineId}/delete`,
    { expected_line_ids: expectedLineIds, confirm: true },
    f,
  )

export const mergeLines = (id: number, lineIds: number[], expectedLineIds: number[], f?: Fetch) =>
  postJson<RestructureResult>(`${base(id)}/merge`, { line_ids: lineIds, expected_line_ids: expectedLineIds }, f)

export const splitLine = (id: number, lineId: number, body: RestructureSplit, f?: Fetch) => {
  // Optional fields are left out rather than sent as null.
  const payload: Record<string, unknown> = {
    expected_line_ids: body.expected_line_ids,
    at_char: body.at_char,
    expected_zh: body.expected_zh,
  }
  if (body.at_time !== undefined && body.at_time !== null) payload.at_time = body.at_time
  if (body.en_at_char !== undefined && body.en_at_char !== null) payload.en_at_char = body.en_at_char
  return postJson<RestructureResult>(`${base(id)}/lines/${lineId}/split`, payload, f)
}

export const previewResegment = (id: number, f?: Fetch) =>
  getJson<ResegmentPreview>(`${base(id)}/resegment/preview`, f)

// The rules re-segmentation; the AI one goes through the preview below.
export const startResegment = (id: number, body: ResegmentStart, f?: Fetch) =>
  postJson<ResegmentStarted>(
    `${base(id)}/resegment`,
    { expected_line_ids: body.expected_line_ids, confirm: body.confirm, use_llm: false },
    f,
  )

// Parity R47: the LLM re-segmentation as a preview job (`resegpreview_<id>`,
// writes no lines); read the result back once the job is done (404 until one
// is ready, or when the lines changed since).
export const startLlmResegmentPreview = (id: number, body: ResegmentLlmPreviewStart, f?: Fetch) => {
  const payload: Record<string, unknown> = {}
  if (body.engine?.trim()) payload.engine = body.engine.trim()
  if (body.model?.trim()) payload.model = body.model.trim()
  return postJson<ResegmentStarted>(`${base(id)}/resegment/preview-llm`, payload, f)
}

export const getLlmResegmentPreview = (id: number, f?: Fetch) =>
  getJson<ResegmentLlmPreview>(`${base(id)}/resegment/preview-llm`, f)

// Applies the stored preview exactly as shown (no second LLM call). The server
// refuses use_llm/engine/model alongside use_preview, so they are never sent.
export const applyLlmResegmentPreview = (id: number, expectedLineIds: number[], confirm: boolean, f?: Fetch) =>
  postJson<ResegmentStarted>(
    `${base(id)}/resegment`,
    { expected_line_ids: expectedLineIds, use_preview: true, confirm },
    f,
  )

export const restoreSnapshot = (id: number, historyId: number, expectedLineIds: number[], f?: Fetch) =>
  postJson<RestoreVersionResult>(`${base(id)}/history/${historyId}/restore`, { expected_line_ids: expectedLineIds }, f)

// Interim until a line-index endpoint exists: every line of the drama, in
// order, read through the Review list in pages of 200 (the route's maximum).
const ALL_LINES_PAGE_SIZE = 200

export async function listAllLines(id: number, f?: Fetch): Promise<ReviewLine[]> {
  const out: ReviewLine[] = []
  for (let page = 1; ; page += 1) {
    const r = await getJson<ReviewLinesPage>(
      `/api/review/dramas/${id}/lines?page=${page}&page_size=${ALL_LINES_PAGE_SIZE}&only=all`,
      f,
    )
    out.push(...r.lines)
    if (r.lines.length === 0 || out.length >= r.total) return out
  }
}

export const resplitLines = (id: number, body: ResplitStart, f?: Fetch) =>
  postJson<ResplitResult>(`${base(id)}/resplit`, body, f)

// Relabels lines from the speaker detection already saved; runs nothing.
export const reassignSpeakersFromSaved = (id: number, f?: Fetch) =>
  postJson<ReassignResult>(`/api/diarization/dramas/${id}/reassign`, {}, f)
