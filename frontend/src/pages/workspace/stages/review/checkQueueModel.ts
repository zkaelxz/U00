/** What the Simple view's check queue shows: which view it opens on, its counts, its reasons and its wording. */
import { humanizeValue } from '../../../../components/labels'
import { lineNumber } from '../../../../lineNumber'
import { plural } from '../../../../labels'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { RISK_CONFIDENCE_BELOW } from './reviewLogic'

// The queue is the server's risk view: translated, unchecked lines with a flag or a low Whisper score, riskiest first.
export const QUEUE_FILTER: LineFilter = 'risk'

/**
 * The view Review opens on. A link that names lines wins over the queue, so it never opens on a list without
 * the line it points at: ?focus=flagged opens Flagged, ?find= (a Ctrl+K line hit) and ?focus=all open All.
 */
export function initialLinesFilter(link: { focus?: string; find?: string } | null, simple: boolean): LineFilter {
  if (link?.focus === 'flagged') return 'flagged'
  if (!simple || link?.focus === 'all' || link?.find) return 'all'
  return QUEUE_FILTER
}

// Flags whose fix is a glossary entry: the term the English missed, or a name the AI check doubted.
const GLOSSARY_FLAGS = new Set(['glossary_missing', 'name_uncertain'])
export const opensGlossary = (line: Pick<ReviewLine, 'flag'>) => !!line.flag && GLOSSARY_FLAGS.has(line.flag)

/** Why an unflagged line is in the queue: only Whisper's score put it there. Null for a flagged line, which shows its flag. */
export function confidenceReason(line: Pick<ReviewLine, 'flag' | 'confidence'>): string | null {
  if (line.flag || line.confidence == null || line.confidence >= RISK_CONFIDENCE_BELOW) return null
  return `Whisper was unsure of this line (${Math.round(line.confidence * 100)}% sure)`
}

const shortReason = (line: ReviewLine) => (line.flag ? humanizeValue(line.flag).toLowerCase() : 'Whisper was unsure')

/** The next two cards after the one open, for the phone's one-card view; null at the end of the list. */
export function nextText(shown: ReviewLine[], activeId: number | null): string | null {
  const at = shown.findIndex((l) => l.id === activeId)
  const next = shown.slice(at + 1, at + 3)
  if (at === -1 || next.length === 0) return null
  return `Next: ${next.map((l) => `line ${lineNumber(l.idx)}, ${shortReason(l)}`).join(' · ')}`
}

/**
 * The card to open once `id` is checked and has left the queue: the one after it. The last card of a page with more
 * pages behind it is followed by the line that reload brings up from the next page, which lands last; only the very
 * last card of the queue goes back to the one before it.
 */
export function nextAfterCheck(shown: ReviewLine[], id: number, morePages: boolean): number | 'last' | undefined {
  const at = shown.findIndex((l) => l.id === id)
  return shown[at + 1]?.id ?? (morePages ? 'last' : shown[at - 1]?.id)
}

/** "2 of 5 to look at": the open card's place in the whole queue, across pages. */
export function positionText(shown: ReviewLine[], activeId: number | null, page: number, pageSize: number, total: number): string | null {
  const at = shown.findIndex((l) => l.id === activeId)
  return at === -1 ? null : `${(page - 1) * pageSize + at + 1} of ${total} to look at`
}

export interface QueueCounts {
  all: number | null
  risk: number
  untranslated: number
  unreviewed: number
}

export const queueCounts = (page: ReviewLinesPage, all: number | null): QueueCounts => ({
  all, risk: page.risk_count ?? 0, untranslated: page.untranslated_count, unreviewed: page.unreviewed_count ?? 0,
})

/** Lines with English and no warning: not in the queue and not waiting for a translation. */
export const fineCount = (c: QueueCounts): number | null => (c.all === null ? null : Math.max(0, c.all - c.risk - c.untranslated))

export const fineText = (c: QueueCounts): string | null => {
  const fine = fineCount(c)
  return fine === null || c.all === null ? null : `${fine} of ${c.all} look fine`
}

/** Unchecked lines outside the queue: what Mark the rest would look at (its exact count is the server's). */
export const restUnchecked = (c: QueueCounts): number => Math.max(0, c.unreviewed - c.risk)

export function otherFineText(c: QueueCounts): string | null {
  const fine = fineCount(c)
  if (!fine || c.risk === 0) return null
  return fine === 1 ? 'The other line has no warnings.' : `The other ${fine} lines have no warnings.`
}

export const untranslatedText = (n: number): string | null =>
  n > 0 ? `${plural(n, 'line')} ${n === 1 ? 'has' : 'have'} no English yet.` : null

// The words Mark episode reviewed already uses for the same wait.
export const JOB_RUNNING_TEXT = 'Another job is running on this title. Try again when it finishes.'
export const UNSURE_FIRST = 'Look at the lines Whisper was unsure of first: this would mark them checked too.'
export const WHOLE_LIST_FIRST =
  'Work through the list until it fits on one page. Until then the app can’t tell whether this would also mark lines that still need a look.'

/**
 * Why Mark the rest can't run now, or null when it can. Mark episode reviewed skips flagged lines only, so an
 * unflagged low-score line in the queue would be marked unseen. The list counts can't tell those lines apart
 * (flagged_count also holds checked and untranslated lines), so the queue page itself is read: it must hold the
 * whole queue, every line flagged.
 */
export function markRestBlocker(queuePage: ReviewLinesPage, jobRunning: boolean): string | null {
  if (jobRunning) return JOB_RUNNING_TEXT
  if (queuePage.lines.length < queuePage.total) return WHOLE_LIST_FIRST
  if (queuePage.lines.some((l) => !l.flag)) return UNSURE_FIRST
  return null
}

export const checkedText = (line: Pick<ReviewLine, 'idx'>) => `Line #${lineNumber(line.idx)} marked checked.`
