import type {
  ApplyResult,
  FindReplaceRequest,
  HistoryItem,
  HistorySnapshot,
  LineFilter,
  LineMark,
  LineExplanation,
  LineImprovement,
  LineNoteCreate,
  LinePatch,
  SetLanguageRequest,
  SetLanguageResult,
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
  StepKind,
  LineSpeakerGuesses,
  VersionItem,
  VersionActivateResult,
  BlockedRetryResult,
  FlaggedPosition,
  LineAlternatives,
  LineGrammar,
  ShortenResult,
  SourceChanged,
  KeepEnglishResult,
  EpisodeReviewedResult,
} from '../types/review'
import { apiUrl, deleteJson, getJson, postJson, type Fetch } from './client'

const review = (id: number) => `/api/review/dramas/${id}`
const lines = (id: number) => `/api/lines/dramas/${id}`

export const listLines = (id: number, page: number, pageSize: number, only: LineFilter, f?: Fetch) =>
  getJson<ReviewLinesPage>(`${review(id)}/lines?page=${page}&page_size=${pageSize}&only=${only}`, f)

const LINES_PAGE_SIZE = 200 // the server's page-size cap

// Every line of the title, in order, paged because the server caps a page.
export async function listAllLines(id: number): Promise<ReviewLine[]> {
  const rows: ReviewLine[] = []
  for (let page = 1; ; page++) {
    const r = await listLines(id, page, LINES_PAGE_SIZE, 'all')
    rows.push(...r.lines)
    if (rows.length >= r.total || r.lines.length === 0) break
  }
  return rows
}

// The ids of the lines Review's risk view lists (the Library's "Check N lines"), so Watch can count the same ones.
export async function listRiskLineIds(id: number): Promise<Set<number>> {
  const ids = new Set<number>()
  for (let page = 1; ; page++) {
    const r = await listLines(id, page, LINES_PAGE_SIZE, 'risk')
    for (const l of r.lines) ids.add(l.id)
    if (ids.size >= r.total || r.lines.length === 0) break
  }
  return ids
}

// The most hits one search request returns; a full answer means there may be more after its last line.
export const SEARCH_LIMIT = 200

/** `after`: only hits in lines after this one (a line position), for Load more. A position, not a
 *  count, so a hit edited away since it was shown does not make the next answer skip one. */
export const searchLines = (id: number, term: string, f?: Fetch, after?: number) =>
  getJson<ReviewLine[]>(
    `${review(id)}/search?term=${encodeURIComponent(term)}&limit=${SEARCH_LIMIT}${after === undefined ? '' : `&after=${after}`}`,
    f,
  )

export const previewFindReplace = (id: number, req: FindReplaceRequest, f?: Fetch) =>
  postJson<ReviewMatch[]>(`${review(id)}/find-replace/preview`, req, f)

// The server's cap on matches in one Apply request (LinesFindReplaceApplyRequest).
export const MAX_APPLY_MATCHES = 1000

/** An Apply as the client ran it: the server's counts, plus the matches never sent (`rest`) and
 *  why (`error`) when a request after the first failed. Empty `rest`: every match was sent. */
export interface ApplyOutcome extends ApplyResult {
  rest: ReviewMatch[]
  error: unknown
}

/** Applies the matches in requests of MAX_APPLY_MATCHES, so a rename across a whole novel goes through.
 *  A failed first request throws (nothing was changed). A later one cannot undo the requests already
 *  saved, so the outcome keeps the matches it did not send. Those, and only those, are what "Apply
 *  the rest" sends: a fresh Preview would match the replaced text again ("Lin" in "Lin Zhou"). Sending
 *  them twice is safe, since each carries the text it replaces and a changed line comes back stale. */
export async function applyFindReplace(id: number, matches: ReviewMatch[], f?: Fetch): Promise<ApplyOutcome> {
  const total: ApplyOutcome = { applied: 0, stale: 0, applied_ids: [], stale_ids: [], rest: [], error: null }
  for (let start = 0; start < matches.length; start += MAX_APPLY_MATCHES) {
    let r: ApplyResult
    try {
      r = await postJson<ApplyResult>(
        `${lines(id)}/find-replace/apply`,
        {
          matches: matches
            .slice(start, start + MAX_APPLY_MATCHES)
            .map(({ id: lineId, old_text, new_text }) => ({ id: lineId, old_text, new_text })),
        },
        f,
      )
    } catch (e) {
      if (start === 0) throw e
      return { ...total, rest: matches.slice(start), error: e }
    }
    total.applied += r.applied
    total.stale += r.stale
    total.applied_ids.push(...r.applied_ids)
    total.stale_ids.push(...r.stale_ids)
  }
  return total
}

/** The sentence for an Apply that stopped partway, or '' when every match was sent. */
export const unsentNote = (r: ApplyOutcome): string => {
  const n = r.rest.length
  return n ? ` Stopped partway: ${n} line${n === 1 ? ' was' : 's were'} not changed. Apply the rest sends only ${n === 1 ? 'that one' : 'those'}.` : ''
}

export const patchLine = (id: number, lineId: number, patch: LinePatch, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}`, patch, f)

export const setLinesLanguage = (id: number, req: SetLanguageRequest, f?: Fetch) =>
  postJson<SetLanguageResult>(`${lines(id)}/set-language`, req, f)

export const setReviewed = (id: number, lineId: number, reviewed: boolean, expectedEn?: string, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/reviewed`, { reviewed, expected_en: expectedEn }, f)

// Your verdict on a line (null clears it); the server refuses it, 409, if the English changed since you looked.
export const setMark = (id: number, lineId: number, mark: LineMark | null, expectedEn?: string, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/mark`, { mark, expected_en: expectedEn }, f)

// A thumbs-down: marks the line wrong and keeps the better wording for the learned style; the line's English is not changed.
export const sendLineFeedback = (id: number, lineId: number, better: string, expectedEn?: string, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/feedback`, { better: better.trim() || null, expected_en: expectedEn }, f)

// Records every translated, unflagged, unchecked line of the title as approved.
export const markEpisodeReviewed = (id: number, f?: Fetch) =>
  postJson<EpisodeReviewedResult>(`${lines(id)}/mark-reviewed`, undefined, f)

export const dismissFlag = (id: number, lineId: number, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/dismiss-flag`, undefined, f)

export const getSourceChanged = (id: number, f?: Fetch) =>
  getJson<SourceChanged>(`${review(id)}/source-changed`, f)

// Clears the source_changed flag only; text and English stay as they are.
export const keepEnglish = (id: number, lineIds: number[], f?: Fetch) =>
  postJson<KeepEnglishResult>(`${lines(id)}/source-changed/keep-english`, { line_ids: lineIds }, f)

// expectedEn: the line's English as shown; the server refuses (409) if it changed.
export const acceptTm = (id: number, lineId: number, entryId: number, expectedEn: string, f?: Fetch) =>
  postJson<ReviewLine>(`${lines(id)}/lines/${lineId}/accept-tm`, { entry_id: entryId, expected_en: expectedEn }, f)

export const listNotes = (id: number, f?: Fetch) => getJson<ReviewNote[]>(`${review(id)}/notes`, f)

export const addNote = (id: number, note: LineNoteCreate, f?: Fetch) =>
  postJson<ReviewNote>(`${lines(id)}/notes`, note, f)

export const deleteNote = (id: number, noteId: number, f?: Fetch) =>
  deleteJson<NoteDeleteResult>(`${lines(id)}/notes/${noteId}`, f)

export const listHistory = (id: number, f?: Fetch) => getJson<HistoryItem[]>(`${review(id)}/history`, f)

export const getHistorySnapshot = (id: number, historyId: number, f?: Fetch) =>
  getJson<HistorySnapshot>(`${review(id)}/history/${historyId}`, f)

export const listVersions = (id: number, f?: Fetch) => getJson<VersionItem[]>(`${review(id)}/versions`, f)

// lineIds (at most 200): only those lines' suggestions.
export const listTmSuggestions = (id: number, f?: Fetch, lineIds?: number[]) =>
  getJson<TmSuggestion[]>(
    `${review(id)}/tm-suggestions${lineIds ? `?${lineIds.map((l) => `line_id=${l}`).join('&')}` : ''}`,
    f,
  )

export const listSpeakerSuggestions = (id: number, lineIds: number[], f?: Fetch) =>
  getJson<{ lines: LineSpeakerGuesses[] }>(
    `${review(id)}/speaker-suggestions?${lineIds.map((l) => `line_id=${l}`).join('&')}`,
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

// Per-line AI helpers. None of them writes. The engine and model are the
// caller's pick, or the server's default (the title's engine; Gemini free tier:
// the saved setting) when left out; no key ever passes through the browser.
const lineAi = (id: number, lineId: number) => `/api/line-ai/dramas/${id}/lines/${lineId}`

export interface LineAiPick {
  engine?: string
  model?: string
}

const pickBody = (pick?: LineAiPick) => ({
  ...(pick?.engine ? { engine: pick.engine } : {}),
  ...(pick?.engine && pick.model ? { model: pick.model } : {}),
})

export const improveLine = (id: number, lineId: number, issue: string, pick?: LineAiPick, f?: Fetch) =>
  postJson<LineImprovement>(`${lineAi(id, lineId)}/improve`, { issue: issue.trim(), ...pickBody(pick) }, f)

export const explainLine = (id: number, lineId: number, pick?: LineAiPick, f?: Fetch) =>
  postJson<LineExplanation>(`${lineAi(id, lineId)}/explain`, pickBody(pick), f)

// Review parity R39: make a saved version the current English. It overwrites
// the translation of every line, so the caller confirms first.
export const activateVersion = (id: number, versionId: number, f?: Fetch) =>
  postJson<VersionActivateResult>(`${review(id)}/versions/${versionId}/activate`, { confirm: true }, f)

// Review parity R10: re-translate one content-blocked line with the chosen
// engine (the key is resolved on the PC, never sent from the browser).
export const retryBlockedLine = (id: number, lineId: number, engine: string, f?: Fetch) =>
  postJson<BlockedRetryResult>(`${lines(id)}/lines/${lineId}/retry-blocked`, { engine }, f)

// Review parity R17/R18: read-only, like improve/explain.
export const lineAlternatives = (id: number, lineId: number, pick?: LineAiPick, f?: Fetch) =>
  postJson<LineAlternatives>(`${lineAi(id, lineId)}/alternatives`, pickBody(pick), f)

export const lineGrammar = (id: number, lineId: number, pick?: LineAiPick, f?: Fetch) =>
  postJson<LineGrammar>(`${lineAi(id, lineId)}/grammar`, pickBody(pick), f)

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
  kind: StepKind = 'flagged',
) =>
  getJson<FlaggedPosition>(
    `${review(id)}/flagged-adjacent?direction=${direction}&page_size=${pageSize}&only=${only}` +
      (kind === 'flagged' ? '' : `&kind=${kind}`) +
      (fromLineId !== null ? `&from_line_id=${fromLineId}` : ''),
    f,
  )
