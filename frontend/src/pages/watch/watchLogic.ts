// Pure helpers for the Watch page: which line is being spoken, where to pick up again, the page's keys and the
// fix-this-line save.
import { ApiError } from '../../api/client'
import { languageLabel, mediaTypeLabel } from '../../labels'
import { lineNumber } from '../../lineNumber'
import type { LinePatch, ReviewLine } from '../../types/review'
import type { TitleKind } from '../workspace/titleKind'
import { activeIndex } from '../reader/readAlongLogic'
import { lineEditPatch } from '../reader/readerLineEdit'

/** A position this close to the start is not worth offering to go back to. */
export const RESUME_MIN_S = 5
/** Nor one this close to the end: the viewer finished it. */
export const RESUME_END_GAP_S = 10
export const SAVE_EVERY_MS = 5000

export const positionKey = (dramaId: number) => `watch.at.${dramaId}`

type Timed = Pick<ReviewLine, 'idx' | 'start'>

/** The lines in the order they are spoken. */
export function spokenOrder<T extends Timed>(lines: readonly T[]): T[] {
  return [...lines].sort((a, b) => a.start - b.start || a.idx - b.idx)
}

/** The line being spoken, by the Reader's read-along rule: the last line that has started stays current through the
 *  pause after it (and after the last line), so the card never goes blank between two lines. Null before the first. */
export function currentLine<T extends Timed>(ordered: readonly T[], time: number): T | null {
  const i = activeIndex(ordered, time)
  return i === null ? null : ordered[i]
}

/** Where to seek on arrival, or null to start at the beginning. */
export function resumePoint(saved: number, duration: number): number | null {
  if (!Number.isFinite(saved) || saved < RESUME_MIN_S) return null
  if (!Number.isFinite(duration) || duration <= 0) return null
  return saved <= duration - RESUME_END_GAP_S ? saved : null
}

/** The previous or next line's index; it stays on the first and the last, and before the first line both go to it. */
export function stepIndex(current: number | null, step: 1 | -1, count: number): number | null {
  if (count === 0) return null
  if (current === null) return 0
  return Math.min(count - 1, Math.max(0, current + step))
}

/** "1:02.5": a line start to the tenth of a second, which is as close as people place a line by ear. */
export function clockTenths(seconds: number): string {
  const tenths = Math.max(0, Math.floor(seconds * 10 + 1e-6))
  const h = Math.floor(tenths / 36000)
  const m = Math.floor((tenths % 36000) / 600)
  const s = (tenths % 600) / 10
  const sec = s.toFixed(1).padStart(4, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

export const lineHeading = (l: Timed) => `Line ${lineNumber(l.idx)} · ${clockTenths(l.start)}`
export const rowMeta = (l: Timed) => `${lineNumber(l.idx)} · ${clockTenths(l.start)}`

/** "Audio drama · Chinese to English": the kind of title and its languages, from the shared labels. */
export function kindBadge(mediaType: string | null | undefined, sourceLanguage: string | null | undefined): string {
  const languages = sourceLanguage ? `${languageLabel(sourceLanguage)} to ${languageLabel('en')}` : ''
  return [mediaTypeLabel(mediaType), languages].filter(Boolean).join(' · ')
}

/** Novels and comics are read; everything else with sound can be watched or listened to. */
export const isReadOnlyKind = (kind: TitleKind) => kind === 'novel' || kind === 'comic'

/** The page's name in the breadcrumb: a video title is watched, an audio one listened to. A title of neither kind
 *  goes by what it has to play. */
export function watchVerb(kind: TitleKind, original: 'video' | 'audio' | null | undefined): 'Watch' | 'Listen' {
  if (kind === 'video') return 'Watch'
  if (kind === 'audio') return 'Listen'
  return original === 'video' ? 'Watch' : 'Listen'
}

// Controls that use the arrow keys or typed letters themselves.
const OWN_KEYS = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="slider"], [role="listbox"], [role="combobox"]'

export type WatchKey = 'fix' | 'prev' | 'next'

/** The page's own keys, from a useShortcut combo: E fixes the line, Up and Down move between lines. */
export function watchKey(combo: string, target: { closest?: (selector: string) => unknown } | null): WatchKey | null {
  if (target?.closest?.(OWN_KEYS)) return null
  if (combo === 'e') return 'fix'
  if (combo === 'arrowup') return 'prev'
  if (combo === 'arrowdown') return 'next'
  return null
}

/** Keys inside the fix field: Ctrl+Enter (Cmd+Enter on a Mac) saves, Esc cancels. */
export function fieldKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; isComposing?: boolean }): 'save' | 'cancel' | null {
  if (e.isComposing) return null
  if (e.key === 'Escape') return 'cancel'
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.altKey) return 'save'
  return null
}

export const CONFLICT_TEXT = 'This line was changed while you were fixing it. What it says now is shown below, and your text is still in the box. Save again if it still needs your fix.'
export const CONFLICT_UNREAD_TEXT = 'This line was changed while you were fixing it, and its new text could not be loaded. Your text is still in the box. Try Save again in a moment.'

/** An open fix: the line, the English the viewer saw when it opened, and what is typed. */
export interface LineFix {
  lineId: number
  seen: string
  draft: string
  conflict: boolean
  /** After a refused save, the line's English as read back then; null when it could not be read. */
  now: string | null
  error: unknown
}

export const openFix = (line: Pick<ReviewLine, 'id' | 'en'>): LineFix =>
  ({ lineId: line.id, seen: line.en, draft: line.en, conflict: false, now: null, error: null })

export type FixResult =
  | { kind: 'unchanged' }
  | { kind: 'saved'; line: ReviewLine }
  | { kind: 'conflict'; fix: LineFix; lines: ReviewLine[] | null }
  | { kind: 'failed'; fix: LineFix }

/**
 * Sends the fix the way the Reader's pencil does: the English that was on screen rides along as `expected`, so a line
 * changed meanwhile is refused with 409 instead of overwritten. On that refusal the lines are read again and the
 * draft stays, now measured against the new text, so saving again sends the fix over what is really there.
 */
export async function saveFix(fix: LineFix, deps: {
  patch: (lineId: number, body: LinePatch) => Promise<ReviewLine>
  reload: () => Promise<ReviewLine[]>
}): Promise<FixResult> {
  const body = lineEditPatch(fix.seen, fix.draft)
  if (!body) return { kind: 'unchanged' }
  try {
    return { kind: 'saved', line: await deps.patch(fix.lineId, body) }
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 409)) return { kind: 'failed', fix: { ...fix, conflict: false, now: null, error: e } }
    let lines: ReviewLine[] | null = null
    try {
      lines = await deps.reload()
    } catch {
      // The alert still tells the viewer to check the line; the next save is refused again until a reload works.
    }
    const fresh = lines?.find((l) => l.id === fix.lineId)
    return { kind: 'conflict', lines, fix: { ...fix, seen: fresh ? fresh.en : fix.seen, conflict: true, now: fresh ? fresh.en : null, error: null } }
  }
}

/** How far to scroll a list so a row is fully inside it, moving as little as possible (0 when it already is). Done by
 *  hand rather than with scrollIntoView, which also scrolls the page when the list itself is partly off screen. */
export function nearestScroll(row: { top: number; bottom: number }, box: { top: number; bottom: number }): number {
  if (row.top < box.top) return row.top - box.top
  if (row.bottom > box.bottom) return Math.min(row.bottom - box.bottom, row.top - box.top)
  return 0
}
