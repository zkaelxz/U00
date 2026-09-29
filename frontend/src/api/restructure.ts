// Structural line changes (/api/restructure/..., Migration Slice 45). Every
// write is refused (409) while a job runs on the drama or when the drama's
// line ids differ from `expected_line_ids`.
import type { HistoryItem, ReviewLine, ReviewLinesPage } from '../types/review'
import type {
  ResegmentPreview,
  ResegmentStart,
  ResegmentStarted,
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

export const startResegment = (id: number, body: ResegmentStart, f?: Fetch) => {
  const payload: Record<string, unknown> = {
    expected_line_ids: body.expected_line_ids,
    confirm: body.confirm,
    use_llm: body.use_llm,
  }
  if (body.use_llm && body.engine) payload.engine = body.engine
  if (body.use_llm && body.model) payload.model = body.model
  return postJson<ResegmentStarted>(`${base(id)}/resegment`, payload, f)
}

export const listSnapshots = (id: number, f?: Fetch) => getJson<HistoryItem[]>(`${base(id)}/history`, f)

export const restoreSnapshot = (id: number, historyId: number, expectedLineIds: number[], f?: Fetch) =>
  postJson<RestoreVersionResult>(`${base(id)}/history/${historyId}/restore`, { expected_line_ids: expectedLineIds }, f)

// Interim until a line-index endpoint exists: every line of the drama, in
// order, read through the Review list in pages of 200 (the route's maximum).
export const ALL_LINES_PAGE_SIZE = 200

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
