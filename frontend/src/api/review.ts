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
  ConsistencyIssue,
  Coverage,
  EmotionSummary,
  LineOriginalText,
  LineProvenance,
  Pacing,
  ReviewJobBody,
  ReviewJobKind,
  Tendencies,
  VersionCompare,
  ReviewJobStarted,
  ReviewLine,
  ReviewLinesPage,
  ReviewMatch,
  ReviewNote,
  TmSuggestion,
  VersionItem,
  VersionActivateResult,
  BlockedRetryResult,
  FlaggedPosition,
  LineAlternatives,
  LineGrammar,
  ShortenResult,
} from '../types/review'
import { apiUrl, deleteJson, fetchBody, getJson, postJson } from './client'

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

// lineIds (at most 200): only those lines' suggestions.
export const listTmSuggestions = (id: number, f?: Fetch, lineIds?: number[]) =>
  getJson<TmSuggestion[]>(
    `${review(id)}/tm-suggestions${lineIds ? `?${lineIds.map((l) => `line_id=${l}`).join('&')}` : ''}`,
    f,
  )

// body: only the fields the user set; {} leaves engine, model and cap to the server.
export const startReviewJob = (id: number, kind: ReviewJobKind, body: ReviewJobBody = {}, f?: Fetch) =>
  postJson<ReviewJobStarted>(`/api/review-jobs/dramas/${id}/${kind}`, body, f)

// Stored results of the last AI checks (read-only; nothing is re-run).
export const getConsistency = (id: number, f?: Fetch) =>
  getJson<ConsistencyIssue[]>(`${review(id)}/consistency`, f)

export const getEmotions = (id: number, f?: Fetch) => getJson<EmotionSummary>(`${review(id)}/emotions`, f)

export const getTendencies = (id: number, f?: Fetch) => getJson<Tendencies>(`${review(id)}/tendencies`, f)

export const compareVersions = (id: number, leftId: number, rightId: number, f?: Fetch) =>
  getJson<VersionCompare>(`${review(id)}/versions/compare?left_id=${leftId}&right_id=${rightId}`, f)

// Served inline as text/markdown: a plain link, opened by the browser.
export const notesMarkdownUrl = (id: number) => apiUrl(`${review(id)}/notes/markdown`)

export const getCoverage = (id: number, f?: Fetch) => getJson<Coverage>(`${review(id)}/coverage`, f)

export const getPacingFlags = (id: number, f?: Fetch) => getJson<Pacing>(`${review(id)}/pacing-flags`, f)

export const getLineProvenance = (id: number, lineId: number, f?: Fetch) =>
  getJson<LineProvenance>(`${review(id)}/lines/${lineId}/provenance`, f)

export const getLineOriginalText = (id: number, lineId: number, f?: Fetch) =>
  getJson<LineOriginalText>(`${review(id)}/lines/${lineId}/original-text`, f)

// Per-line AI helpers (Slice 50). Neither call writes; the engine and model
// are left to the server's default (Gemini free tier: the saved setting), and no key ever passes through the browser.
const lineAi = (id: number, lineId: number) => `/api/line-ai/dramas/${id}/lines/${lineId}`

export const improveLine = (id: number, lineId: number, issue: string, f?: Fetch) =>
  postJson<LineImprovement>(`${lineAi(id, lineId)}/improve`, { issue: issue.trim() }, f)

export const explainLine = (id: number, lineId: number, f?: Fetch) =>
  postJson<LineExplanation>(`${lineAi(id, lineId)}/explain`, {}, f)

// Review parity R39: make a saved version the current English. It overwrites
// the translation of every line, so the caller confirms first.
export const activateVersion = (id: number, versionId: number, f?: Fetch) =>
  postJson<VersionActivateResult>(`${review(id)}/versions/${versionId}/activate`, { confirm: true }, f)

// Review parity R10: re-translate one content-blocked line with the chosen
// engine (the key is resolved on the PC, never sent from the browser).
export const retryBlockedLine = (id: number, lineId: number, engine: string, f?: Fetch) =>
  postJson<BlockedRetryResult>(`${lines(id)}/lines/${lineId}/retry-blocked`, { engine }, f)

// Review parity R17/R18: read-only, like improve/explain.
export const lineAlternatives = (id: number, lineId: number, f?: Fetch) =>
  postJson<LineAlternatives>(`${lineAi(id, lineId)}/alternatives`, {}, f)

export const lineGrammar = (id: number, lineId: number, f?: Fetch) =>
  postJson<LineGrammar>(`${lineAi(id, lineId)}/grammar`, {}, f)

// Review parity R19: an MP3 of the line's source text (edge-tts on the PC).
export const pronounceLine = (id: number, lineId: number, f?: Fetch) =>
  fetchBody(`${lineAi(id, lineId)}/pronounce`, { method: 'POST', headers: { Accept: 'audio/mpeg' } }, (r) => r.blob(), f)

// Review parity R28: rewrites only "en" of the too-long lines, after a
// line-history snapshot; a line edited meanwhile is skipped as stale. The
// server needs confirm (the UI asks first) and refuses while a job runs.
export const shortenOverlong = (id: number, lineIds?: number[], f?: Fetch) =>
  postJson<ShortenResult>(`${lines(id)}/shorten-overlong`, lineIds ? { line_ids: lineIds, confirm: true } : { confirm: true }, f)

// Review parity R08: the nearest flagged line before/after a line, any page.
export const flaggedAdjacent = (
  id: number,
  direction: 'next' | 'prev',
  fromLineId: number | null,
  pageSize: number,
  only: LineFilter,
  f?: Fetch,
) =>
  getJson<FlaggedPosition>(
    `${review(id)}/flagged-adjacent?direction=${direction}&page_size=${pageSize}&only=${only}` +
      (fromLineId !== null ? `&from_line_id=${fromLineId}` : ''),
    f,
  )
