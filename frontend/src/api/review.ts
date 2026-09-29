import type {
  ApplyResult,
  FindReplaceRequest,
  HistoryItem,
  LineFilter,
  LineExplanation,
  LineImprovement,
  LineNoteCreate,
  LinePatch,
  NoteDeleteResult,
  ReviewJobKind,
  ReviewJobStarted,
  ReviewLine,
  ReviewLinesPage,
  ReviewMatch,
  ReviewNote,
  TmSuggestion,
  VersionItem,
} from '../types/review'
import { deleteJson, getJson, postJson } from './client'

type Fetch = typeof fetch

const review = (id: number) => `/api/review/dramas/${id}`
const lines = (id: number) => `/api/lines/dramas/${id}`

export const listLines = (id: number, page: number, pageSize: number, only: LineFilter, f?: Fetch) =>
  getJson<ReviewLinesPage>(`${review(id)}/lines?page=${page}&page_size=${pageSize}&only=${only}`, f)

export const searchLines = (id: number, term: string, f?: Fetch) =>
  getJson<ReviewLine[]>(`${review(id)}/search?term=${encodeURIComponent(term)}&limit=200`, f)

export const previewFindReplace = (id: number, req: FindReplaceRequest, f?: Fetch) =>
  postJson<ReviewMatch[]>(`${review(id)}/find-replace/preview`, req, f)

export const applyFindReplace = (id: number, matches: ReviewMatch[], f?: Fetch) =>
  postJson<ApplyResult>(
    `${lines(id)}/find-replace/apply`,
    { matches: matches.map(({ id: lineId, old_text, new_text }) => ({ id: lineId, old_text, new_text })) },
    f,
  )

export const patchLine = (id: number, lineId: number, patch: LinePatch, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}`, patch, f)

export const dismissFlag = (id: number, lineId: number, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/dismiss-flag`, undefined, f)

export const acceptTm = (id: number, lineId: number, entryId: number, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/accept-tm`, { entry_id: entryId }, f)

export const listNotes = (id: number, f?: Fetch) => getJson<ReviewNote[]>(`${review(id)}/notes`, f)

export const addNote = (id: number, note: LineNoteCreate, f?: Fetch) =>
  postJson<ReviewNote>(`${lines(id)}/notes`, note, f)

export const deleteNote = (id: number, noteId: number, f?: Fetch) =>
  deleteJson<NoteDeleteResult>(`${lines(id)}/notes/${noteId}`, f)

export const listHistory = (id: number, f?: Fetch) => getJson<HistoryItem[]>(`${review(id)}/history`, f)

export const listVersions = (id: number, f?: Fetch) => getJson<VersionItem[]>(`${review(id)}/versions`, f)

export const listTmSuggestions = (id: number, f?: Fetch) =>
  getJson<TmSuggestion[]>(`${review(id)}/tm-suggestions`, f)

export const startReviewJob = (id: number, kind: ReviewJobKind, f?: Fetch) =>
  postJson<ReviewJobStarted>(`/api/review-jobs/dramas/${id}/${kind}`, {}, f)

// Per-line AI helpers (Slice 50). Neither call writes; the engine and model
// are left to the server's default, and no key ever passes through the browser.
const lineAi = (id: number, lineId: number) => `/api/line-ai/dramas/${id}/lines/${lineId}`

export const improveLine = (id: number, lineId: number, issue: string, f?: Fetch) =>
  postJson<LineImprovement>(`${lineAi(id, lineId)}/improve`, { gemini_free_tier: false, issue: issue.trim() }, f)

export const explainLine = (id: number, lineId: number, f?: Fetch) =>
  postJson<LineExplanation>(`${lineAi(id, lineId)}/explain`, { gemini_free_tier: false }, f)
