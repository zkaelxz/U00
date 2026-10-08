import { ApiError } from '../../../../api/client'
import type { JobRecord } from '../../../../types/jobs'
import type { ResplitResult } from '../../../../types/restructure'
import type { ReviewLine } from '../../../../types/review'

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

/** "Split 31 lines into 118; speakers re-assigned" from a re-split summary. */
export function resplitSummary(r: ResplitResult): string {
  const n = r.split_lines ?? 0
  if (n === 0) return r.note || 'No line is over the length limits. Nothing changed.'
  const pieces = (r.line_count ?? 0) - (r.lines_before ?? 0) + n
  const parts = [`Split ${n} line${n === 1 ? '' : 's'} into ${pieces}`]
  if (r.timing === 'aligned') parts.push(`${r.aligned_lines ?? 0} timed from the audio`)
  if (r.speakers_reassigned) parts.push('speakers re-assigned')
  if (r.cleared_translations) parts.push(`${r.cleared_translations} translation${r.cleared_translations === 1 ? '' : 's'} cleared`)
  return parts.join('; ') + '.' + (r.note ? ` ${r.note}` : '')
}

/** The server asks for confirm=true when a long line already has English. */
export function resplitNeedsConfirm(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && /confirm/i.test(e.message)
}

// Lines someone edited while the version switch ran keep their own English.
export function keptNote(n: number): string {
  if (n <= 0) return ''
  return n === 1 ? ' 1 line was edited meanwhile and kept.' : ` ${n} lines were edited meanwhile and kept.`
}
