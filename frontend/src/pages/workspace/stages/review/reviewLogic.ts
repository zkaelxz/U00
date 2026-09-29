import { ApiError } from '../../../../api/client'
import type { JobRecord } from '../../../../types/jobs'
import type { ResegmentPreview } from '../../../../types/restructure'
import type { LineFilter, LinePatch, ReviewLine, ReviewMatch } from '../../../../types/review'
import { lineNumber } from '../../../../lineNumber'

export interface LineDraft {
  zh: string
  en: string
  speaker: string
  start: string
  end: string
  sfx: boolean
}

export const PAGE_SIZE = 40

export function draftFromLine(line: ReviewLine): LineDraft {
  return {
    zh: line.zh,
    en: line.en,
    speaker: line.speaker ?? '',
    start: String(line.start),
    end: String(line.end),
    sfx: line.sfx,
  }
}

// Only fields that differ are sent, each with the old value it was loaded with
// so the server can refuse (409) if someone else changed that field meanwhile.
// Returns a message when the draft is invalid, null when nothing changed.
export function buildPatch(line: ReviewLine, draft: LineDraft): LinePatch | string | null {
  const patch: LinePatch = {}
  const expected: Record<string, unknown> = {}
  for (const key of ['zh', 'en'] as const) {
    if (draft[key] !== line[key]) {
      patch[key] = draft[key]
      expected[key] = line[key]
    }
  }
  if (draft.speaker.trim() !== (line.speaker ?? '')) {
    patch.speaker = draft.speaker.trim()
    expected.speaker = line.speaker
  }
  if (draft.sfx !== line.sfx) {
    patch.sfx = draft.sfx
    expected.sfx = line.sfx
  }
  for (const key of ['start', 'end'] as const) {
    if (draft[key].trim() === '' || Number.isNaN(Number(draft[key]))) return `Enter a number for ${key}.`
    const n = Number(draft[key])
    if (n !== line[key]) {
      patch[key] = n
      expected[key] = line[key]
    }
  }
  if (Object.keys(patch).length === 0) return null
  if ((patch.end ?? line.end) <= (patch.start ?? line.start)) return 'End must be after start.'
  patch.expected = expected
  return patch
}

// A draft that would send something (or is invalid) is dirty: navigation saves it first.
export function isDirty(line: ReviewLine, draft: LineDraft): boolean {
  return buildPatch(line, draft) !== null
}

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

// The line's panel slot: the AI panel (LineAi) or a study tool (LineTools, R17-R19).
export type ToolMode = 'alternatives' | 'grammar' | 'pronounce'
export type PanelMode = 'improve' | 'explain' | ToolMode

export const isToolMode = (m: PanelMode): m is ToolMode =>
  m === 'alternatives' || m === 'grammar' || m === 'pronounce'

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

export type Step = { id: number } | { page: 'next' | 'prev' } | null

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

export function charCount(text: string): number {
  return Array.from(text).length
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
export const JOB_RUNNING_MESSAGE = 'A job is running on this drama. Structure edits wait until it finishes.'

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

export function resegmentSummary(p: ResegmentPreview): string {
  const notes = `${p.notes} note${p.notes === 1 ? '' : 's'}`
  return `${p.line_count_before} → ${p.line_count_after} lines; ${p.changed.length} change; ${p.translated} translated, ${p.flagged} flagged, ${notes} would be split`
}

// Lines someone edited while the version switch ran keep their own English.
export function keptNote(n: number): string {
  if (n <= 0) return ''
  return n === 1 ? ' 1 line was edited meanwhile and kept.' : ` ${n} lines were edited meanwhile and kept.`
}
