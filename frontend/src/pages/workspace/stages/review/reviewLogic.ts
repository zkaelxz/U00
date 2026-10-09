import { ApiError } from '../../../../api/client'
import type { SpeakerTimeSummary } from '../../../../types/workspace'
import type { JobRecord } from '../../../../types/jobs'
import { JOB_RUNNING_MESSAGE } from './reviewResegment'
import type { LineFilter, LinePatch, ReviewLine, ReviewMatch } from '../../../../types/review'
import { lineNumber } from '../../../../lineNumber'

export const PAGE_SIZE = 40

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${(seconds - m * 60).toFixed(2).padStart(5, '0')}`
}

// Whole seconds for a media duration ("24:10", "1:02:03").
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '–'
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

export function pageCount(total: number, pageSize: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

/** 1-based page holding the line at 0-based position `pos`. */
export function pageForPosition(pos: number, pageSize: number = PAGE_SIZE): number {
  return Math.floor(Math.max(0, pos) / pageSize) + 1
}

export const CONFLICT_MESSAGE = 'This line changed elsewhere. Reload and try again.'

// Stale ids from an apply, shown as the line numbers the user sees.
export function staleLabels(staleIds: number[], matches: ReviewMatch[]): string[] {
  return staleIds.map((id) => {
    const m = matches.find((x) => x.id === id)
    return m ? `#${lineNumber(m.idx)}` : `line id ${id}`
  })
}

export const AI_UNAVAILABLE_MESSAGE =
  'AI help is not set up. Add an API key under Settings, then try again.'
export const AI_STALE_MESSAGE = 'The line changed after this suggestion was made. Ask again.'

// A suggestion is applied like any other edit: only "en" is sent, with the
// value it was made against as the expected old value (a mismatch is a 409).
export function suggestionPatch(line: ReviewLine, suggestion: string): LinePatch | null {
  if (suggestion === line.en) return null
  return { en: suggestion, expected: { en: line.en } }
}

// The line's panel slot: the AI panel (LineAi) or a study tool (LineTools, R17-R18).
export type ToolMode = 'alternatives' | 'grammar'
export type PanelMode = 'improve' | 'explain' | ToolMode

export const isToolMode = (m: PanelMode): m is ToolMode =>
  m === 'alternatives' || m === 'grammar'

// The suggestion was made for current_en; if the row shows something else now
// it is stale and must not be applied.
export function suggestionIsStale(line: ReviewLine, currentEn: string): boolean {
  return line.en !== currentEn
}

// ---- active line and navigation ----

/** First flagged line, else first untranslated, else the first line. */
export function initialActiveId(lines: ReviewLine[]): number | null {
  const pick =
    lines.find((l) => l.flag) ?? lines.find((l) => l.zh.trim() && !l.en.trim()) ?? lines[0]
  return pick ? pick.id : null
}

type Step = { id: number } | { page: 'next' | 'prev' } | null

/** The line `delta` rows away from `activeId`, or a page change at an edge. */
export function stepFrom(lines: ReviewLine[], activeId: number | null, delta: 1 | -1, canPage: { next: boolean; prev: boolean }): Step {
  if (lines.length === 0) return null
  const i = lines.findIndex((l) => l.id === activeId)
  if (i === -1) return { id: (delta > 0 ? lines[0] : lines[lines.length - 1]).id }
  const j = i + delta
  if (j >= 0 && j < lines.length) return { id: lines[j].id }
  if (delta > 0 && canPage.next) return { page: 'next' }
  if (delta < 0 && canPage.prev) return { page: 'prev' }
  return null
}

/** Next/previous flagged line on the page after `fromIndex` (-1 = from the edge). */
export function nextFlaggedId(lines: ReviewLine[], fromIndex: number, delta: 1 | -1): number | null {
  if (fromIndex === -1) {
    const pick = delta > 0 ? lines.find((l) => l.flag) : [...lines].reverse().find((l) => l.flag)
    return pick ? pick.id : null
  }
  for (let j = fromIndex + delta; j >= 0 && j < lines.length; j += delta) if (lines[j].flag) return lines[j].id
  return null
}

/**
 * Where previous/next flagged looks (R08). A page of the "all" or "flagged"
 * view holds every flagged line near the start row, so it is searched first,
 * then the server from the page's edge. A page of a filtered view
 * ("untranslated") leaves flagged lines out, so the server is asked from the
 * focused row itself and none in between is skipped. `fromId` null = from
 * the far end.
 */
export function flaggedStep(
  lines: ReviewLine[],
  filter: LineFilter,
  fromIndex: number,
  delta: 1 | -1,
): { id: number } | { fromId: number | null } {
  if (filter !== 'all' && filter !== 'flagged' && fromIndex !== -1) return { fromId: lines[fromIndex].id }
  const id = nextFlaggedId(lines, fromIndex, delta)
  if (id !== null) return { id }
  const edge = delta > 0 ? lines[lines.length - 1] : lines[0]
  return { fromId: edge?.id ?? null }
}

// ---- structure edits ----

/** UTF-16 caret offset -> code-point offset (the server counts Python characters). */
export function codePointOffset(text: string, utf16Offset: number): number {
  return Array.from(text.slice(0, Math.max(0, utf16Offset))).length
}

/** The two pieces a split at code-point offset `at` makes. */
export function splitPieces(text: string, at: number): [string, string] {
  const chars = Array.from(text)
  return [chars.slice(0, at).join(''), chars.slice(at).join('')]
}

/** The translation pieces the server stores for a cut at `at`: it trims the
 *  first piece's end and the second piece's both ends (services/restructure_service.split_line). */
export function splitTranslationPieces(text: string, at: number): [string, string] {
  const [first, second] = splitPieces(text, at)
  return [first.trimEnd(), second.trim()]
}

export function charCount(text: string): number {
  return Array.from(text).length
}

const BREAK_PUNCT = new Set(Array.from(',.!?;:，。！？；：、…'))

/**
 * Code-point offsets where a cut reads naturally: right after a space run or
 * punctuation, before the next word. A cut that would leave either piece empty
 * is never offered.
 */
export function cutBoundaries(text: string): number[] {
  const chars = Array.from(text)
  const out: number[] = []
  for (let k = 1; k < chars.length; k++) {
    const prev = chars[k - 1]
    if ((/\s/.test(prev) || BREAK_PUNCT.has(prev)) && !/\s/.test(chars[k])) out.push(k)
  }
  return out
}

/** The boundary nearest `target` (ties go earlier); `target` itself when the text has none. */
export function snapCut(text: string, target: number): number {
  const len = charCount(text)
  const clamped = Math.min(Math.max(1, target), Math.max(1, len - 1))
  let best = clamped
  let bestDist = Infinity
  for (const k of cutBoundaries(text)) {
    const d = Math.abs(k - clamped)
    if (d < bestDist) {
      best = k
      bestDist = d
    }
  }
  return best
}

/** Where to cut the translation so it breaks at the same fraction of the text as the source did. */
export function proportionalCut(zhLen: number, zhAt: number, en: string): number {
  const enLen = charCount(en)
  const frac = zhLen > 0 ? zhAt / zhLen : 0.5
  return snapCut(en, Math.round(enLen * frac))
}

/** The previous (-1) or next (1) boundary from `at`, or the line's edge when there is none. */
export function stepToBoundary(text: string, at: number, dir: -1 | 1): number {
  const bounds = cutBoundaries(text)
  const hit = dir === 1 ? bounds.find((k) => k > at) : [...bounds].reverse().find((k) => k < at)
  const len = charCount(text)
  return hit ?? (dir === 1 ? Math.max(1, len - 1) : 1)
}

/** A cut time in proportion to the text before the split (the server's default is similar). */
export function estimateSplitTime(line: Pick<ReviewLine, 'start' | 'end' | 'zh'>, at: number): number {
  const n = charCount(line.zh)
  const frac = n > 0 ? Math.min(1, Math.max(0, at / n)) : 0.5
  return Math.round((line.start + (line.end - line.start) * frac) * 100) / 100
}

/** Default timing for a line added after `line` (null = at the start), before `next`. */
export function gapForNewLine(line: Pick<ReviewLine, 'end'> | null, next: Pick<ReviewLine, 'start'> | null): { start: number; end: number } {
  const start = line ? line.end : 0
  const room = next ? next.start - start : Infinity
  const end = room >= 0.5 ? Math.min(start + 2, next ? next.start : start + 2) : start + 2
  return { start: round2(start), end: round2(end) }
}

const round2 = (n: number) => Math.round(n * 100) / 100

/**
 * Interim guard until a line-index endpoint exists: before a structure edit the
 * whole id list is re-read; the edit only goes ahead when the loaded page still
 * sits where it was (filter "all": same ids at the same positions; other views:
 * the same ids, still in the same order).
 */
export function pageStillMatches(allIds: number[], pageIds: number[], filter: LineFilter | 'search', page: number, pageSize: number = PAGE_SIZE): boolean {
  if (filter === 'all') {
    const start = (page - 1) * pageSize
    const slice = allIds.slice(start, start + pageIds.length)
    return slice.length === pageIds.length && slice.every((id, i) => id === pageIds[i])
  }
  let last = -1
  for (const id of pageIds) {
    const pos = allIds.indexOf(id)
    if (pos <= last) return false
    last = pos
  }
  return true
}

/** `count` adjacent ids starting at `fromId`, or null when there are not enough. */
export function adjacentRun(allIds: number[], fromId: number, count: number): number[] | null {
  const i = allIds.indexOf(fromId)
  if (i === -1 || count < 2 || i + count > allIds.length) return null
  return allIds.slice(i, i + count)
}

/** Joined text as the server's merge builds it (zh concatenated, en space-joined). */
export function mergedText(lines: Pick<ReviewLine, 'zh' | 'en'>[]): { zh: string; en: string } {
  let zh = ''
  let en = ''
  lines.forEach((l, i) => {
    zh = i === 0 ? l.zh : zh.trimEnd() + l.zh.trim()
    en = i === 0 ? l.en : (en.trimEnd() + ' ' + l.en.trim()).trim()
  })
  return { zh, en }
}

export const MAX_MERGE_LINES = 50

export const LINES_CHANGED_MESSAGE = 'Lines changed since this page loaded. Reload and try again.'
/** What the server gave back to undo one structural change (the snapshot taken before it, and
 *  a fingerprint of the lines it left, so an undo refuses if they were edited since). */
export interface UndoHandle {
  historyId: number
  fingerprint: string
}

export function undoHandleOf(r: { history_id?: number | null; lines_fingerprint?: string | null }): UndoHandle | null {
  return r.history_id && r.lines_fingerprint ? { historyId: r.history_id, fingerprint: r.lines_fingerprint } : null
}

export const UNDO_CHANGED_MESSAGE = 'The lines changed since. Restore from Records → Line history instead.'
export const UNDO_GONE_MESSAGE = 'This change can no longer be undone here. Records → Line history has the saved versions.'
// Not "use Records instead": a Records restore would delete that note or tag without asking.
export const UNDO_NOTES_MESSAGE =
  'A note or emotion tag was added to a line this undo would remove, so nothing was changed. Restoring from Records would delete it: move or copy the note first.'

/** The Records restore warning: it removes those lines and deletes their notes and tags. */
export function restoreLossText(lines: number): string {
  return lines === 1
    ? '1 line this would remove has a note or emotion tag. Restoring deletes them. Move or copy the note first to keep it.'
    : `${lines} lines this would remove have notes or emotion tags. Restoring deletes them. Move or copy the notes first to keep them.`
}

export type UndoKind = 'split' | 'merge' | 'delete' | 'resplit'

/** What an undo really brings back: notes and emotion tags live outside the snapshot, so a
 *  deleted line's are gone and a merge's stay on the line they were merged into (except where
 *  that line had its own tag, or a note on the same term, which won). */
export function undoDoneMessage(kind: UndoKind): string {
  if (kind === 'delete')
    return 'Undone. The line is back with its text, translation, timing, speaker and flag, but not its notes or emotion tag.'
  if (kind === 'merge')
    return 'Undone. The merged lines are back. Their notes and emotion tags stay on the line they were merged into, unless that line already had its own tag or a note on the same word.'
  return 'Undone. The lines are back as they were before.'
}

/** A refused undo in plain text, and whether the offer is still worth keeping (a running job
 *  only delays it), or null to show the generic banner. */
export function undoRefusal(e: unknown): { text: string; keepOffer: boolean } | null {
  if (!(e instanceof ApiError)) return null
  if (e.status === 404) return { text: UNDO_GONE_MESSAGE, keepOffer: false }
  if (e.status !== 409) return null
  if ((e.details as { reason?: unknown } | undefined)?.reason === 'notes_on_removed_lines')
    return { text: UNDO_NOTES_MESSAGE, keepOffer: false }
  return /job/i.test(e.message) ? { text: JOB_RUNNING_MESSAGE, keepOffer: true } : { text: UNDO_CHANGED_MESSAGE, keepOffer: false }
}

/** Plain text for a structure-edit failure, or null to show the generic banner. */
export function structureErrorText(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  return /job/i.test(e.message) ? JOB_RUNNING_MESSAGE : LINES_CHANGED_MESSAGE
}

// Interim until JobRecord carries drama_id: job ids are "<kind>_<dramaId>".
export function jobRunsOnDrama(jobs: Pick<JobRecord, 'job_id' | 'status'>[], dramaId: number): boolean {
  return jobs.some((j) => {
    if (j.status !== 'running' && j.status !== 'queued') return false
    const cut = j.job_id.lastIndexOf('_')
    return cut !== -1 && j.job_id.slice(cut + 1) === String(dramaId)
  })
}

export function lineRange(lines: Pick<ReviewLine, 'idx'>[]): string {
  if (lines.length === 0) return ''
  const a = lineNumber(lines[0].idx)
  const b = lineNumber(lines[lines.length - 1].idx)
  return a === b ? `#${a}` : `#${a}–#${b}`
}

export function chipLabel(filter: LineFilter, count: number | null, short: boolean): string {
  const n = count === null ? '' : ` ${count}`
  if (filter === 'all') return `All${n}`
  if (filter === 'flagged') return short ? `⚑${n}` : `Flagged${n}`
  return short ? `Untr.${n}` : `Untranslated${n}`
}

export function emptyMessage(filter: LineFilter, term: string): string {
  if (term) return `No lines match “${term}”.`
  if (filter === 'flagged') return 'No flagged lines. Nice.'
  if (filter === 'untranslated') return 'Every line has English.'
  return 'No lines yet. Transcribe on Source first.'
}

/** One line per speaker, e.g. "Anna  3:40 · 62% · 41 turns", biggest first. */
export function speakerTimeLines(s: SpeakerTimeSummary): string[] {
  return s.speakers.map((x) => `${x.label}  ${formatDuration(x.seconds)} · ${x.percent}% · ${x.turns} turn${x.turns === 1 ? '' : 's'}`)
}

/** Footer for the speaker time list: total speech and audio no turn covers. */
export function speakerTimeFooter(s: SpeakerTimeSummary): string {
  const gap = s.uncovered_seconds === null ? '' : `; ${formatDuration(s.uncovered_seconds)} of the audio has no speaker turn`
  return `${formatDuration(s.total_speech_seconds)} of speech in the saved detection${gap}.`
}

// Lines someone edited while the version switch ran keep their own English.
export function keptNote(n: number): string {
  if (n <= 0) return ''
  return n === 1 ? ' 1 line was edited meanwhile and kept.' : ` ${n} lines were edited meanwhile and kept.`
}

