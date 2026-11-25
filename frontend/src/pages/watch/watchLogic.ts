// Pure helpers for the Watch page: which line is being spoken, where to pick up again, the page's keys, following
// the sound in the list, and the fix-this-line save with its parked copy.
import { ApiError } from '../../api/client'
import { languageLabel, mediaTypeLabel } from '../../labels'
import { lineNumber } from '../../lineNumber'
import type { LineMark, LinePatch, ReviewLine } from '../../types/review'
import type { TitleKind } from '../workspace/titleKind'
import { activeIndex, START_SLACK_S } from '../reader/readAlongLogic'
import { lineEditPatch } from '../reader/readerLineEdit'

/** A position this close to the start is not worth offering to go back to. */
const RESUME_MIN_S = 5
/** Nor one this close to the end: the viewer finished it. */
const RESUME_END_GAP_S = 10
export const SAVE_EVERY_MS = 5000

export const positionKey = (dramaId: number) => `watch.at.${dramaId}`
/** When this browser last wrote `positionKey` (ms since 1970), to tell it from a newer place saved on another device. */
export const positionTimeKey = (dramaId: number) => `watch.atTime.${dramaId}`
/** The account's place as this browser last sent or heard it, in seconds. */
export const serverSeenKey = (dramaId: number) => `watch.serverSeen.${dramaId}`
/** The account's server save, sent at most this often while playing (and on every pause and leave). */
export const SERVER_SAVE_EVERY_MS = 15000

/** A saved place: seconds into the title, and when it was saved (ms since 1970; null when not known). */
export interface SavedPlace { seconds: number; at: number | null }

/** Which saved place to go on from: this browser's, or the account's from the server when that one is newer. A place
 *  this browser saved before it kept times (`at` null) is kept, as it was before the server had places. */
/** The account's place, or null when it is the one this browser already sent or heard: the server's time also moves
 *  when the Reader saves a page, which would make an old place look newer than this browser's. */
export function freshServerPlace(server: SavedPlace | null, seen: number | null): SavedPlace | null {
  return server && seen !== null && Math.abs(server.seconds - seen) < 0.5 ? null : server
}

export function newerPlace(local: SavedPlace, server: SavedPlace | null): number {
  if (!server || !(server.seconds > 0)) return local.seconds
  if (!(local.seconds > 0)) return server.seconds
  if (local.at === null) return local.seconds
  return (server.at ?? 0) > local.at ? server.seconds : local.seconds
}

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

/** A stretch with no line this long (an intro, music, a long pause) is worth a Skip quiet button; shorter ones are
 *  ordinary pauses between lines. */
export const QUIET_MIN_S = 4
/** Where Skip quiet lands before the next line starts, so its first word is not clipped. */
export const QUIET_LEAD_S = 0.5

/** Where Skip quiet would go from this time: just before the next line, when no line is being spoken and the next one
 *  is at least QUIET_MIN_S away. Null while a line is being spoken, in a short pause, and after the last line. Only
 *  the line that started last is checked, so a longer line it overlaps is not noticed; the skip stays inside quiet
 *  that the lines show. */
export function quietSkip(ordered: readonly Pick<ReviewLine, 'start' | 'end'>[], time: number): number | null {
  const i = activeIndex(ordered, time)
  const next = ordered[(i ?? -1) + 1]
  if (!next) return null
  const from = i === null ? 0 : Math.max(time, ordered[i].end)
  if (time < from || next.start - time < QUIET_MIN_S) return null
  return next.start - QUIET_LEAD_S
}

/** The line Loop line repeats: the one being spoken, unless it ends at or before its start, which would put the sound
 *  back to its start at every frame and stop it. */
export function loopLineAt<T extends Timed & Pick<ReviewLine, 'end'>>(ordered: readonly T[], time: number): T | null {
  const line = currentLine(ordered, time)
  return line && line.end > line.start ? line : null
}

/** Where to seek on arrival, or null to start at the beginning. */
export function resumePoint(saved: number, duration: number): number | null {
  if (!Number.isFinite(saved) || saved < RESUME_MIN_S) return null
  if (!Number.isFinite(duration) || duration <= 0) return null
  return saved <= duration - RESUME_END_GAP_S ? saved : null
}

/** The previous or next line's index; it stays on the first and the last, and before the first line both go to it.
 *  Back skips lines that start together with the current one: the current line is the last of such a group, so a seek
 *  to an earlier one's start would land on the current line again. */
export function stepIndex(ordered: readonly Pick<ReviewLine, 'start'>[], current: number | null, step: 1 | -1): number | null {
  if (ordered.length === 0) return null
  if (current === null) return 0
  if (step === 1) return Math.min(ordered.length - 1, current + 1)
  const from = ordered[current].start - START_SLACK_S
  for (let i = current - 1; i >= 0; i--) if (ordered[i].start < from) return i
  return current
}

/** The line a fix opens on: the one under the playhead at the moment of the press, not the one last drawn, which can
 *  be a frame behind the sound; the drawn one only when there is no playhead. */
export function lineToFix<T extends Timed>(ordered: readonly T[], playhead: number | null, drawn: T | null): T | null {
  return playhead === null ? drawn : currentLine(ordered, playhead)
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

/** A spot to come back to: a line you marked, or one a check flagged. */
export const needsAttention = (l: Pick<ReviewLine, 'flag' | 'mark'>) => !!l.flag || !!l.mark

/** The nearest such line after (1) or before (-1) the one at `from`, in the order the lines are spoken; null when there is
 *  none that way. With no line yet (null), after means from the start and before means from the end. */
export function attentionIndex(ordered: Pick<ReviewLine, 'flag' | 'mark'>[], from: number | null, direction: 1 | -1): number | null {
  for (let i = from === null ? (direction === 1 ? 0 : ordered.length - 1) : from + direction; i >= 0 && i < ordered.length; i += direction) {
    if (needsAttention(ordered[i])) return i
  }
  return null
}

/** The lines still to check, in spoken order: the ones Review's risk view lists that nobody has said look right since. */
export function linesToCheck<T extends Pick<ReviewLine, 'id' | 'reviewed'>>(ordered: T[], riskIds: ReadonlySet<number>): T[] {
  return ordered.filter((l) => riskIds.has(l.id) && !l.reviewed)
}

/** The position in `checks` of the next (or previous) line to check after the spoken line at `from` in `ordered`. */
export function checkStep(checks: Pick<ReviewLine, 'id'>[], lineIndex: ReadonlyMap<number, number>, from: number | null, direction: 1 | -1): number | null {
  const at = checks.map((l) => lineIndex.get(l.id) ?? -1)
  if (direction === 1) {
    const i = at.findIndex((n) => from === null || n > from)
    return i < 0 ? null : i
  }
  for (let i = at.length - 1; i >= 0; i--) if (from === null || at[i] < from) return i
  return null
}

export type Verdict = 'good' | 'wrong' | 'mixed'

/** What pressing a verdict on a line does: Good checks the line (pressed again, unchecks it); Wrong and Mixed set that
 *  mark (pressed again, clear it). Checking clears a mark and marking unchecks, both on the server. */
export function verdictChange(line: Pick<ReviewLine, 'reviewed' | 'mark'>, verdict: Verdict): { kind: 'reviewed'; to: boolean } | { kind: 'mark'; to: LineMark | null } {
  if (verdict === 'good') return { kind: 'reviewed', to: !line.reviewed }
  return { kind: 'mark', to: line.mark === verdict ? null : verdict }
}

export const lineHeading = (l: Timed) => `Line #${lineNumber(l.idx)} · ${clockTenths(l.start)}`
export const rowMeta = (l: Timed) => `#${lineNumber(l.idx)} · ${clockTenths(l.start)}`

/** How many colours a speaker can take before one repeats; the CSS defines exactly this many. */
export const SPEAKER_COLOURS = 6

/** Each speaker's colour slot, by first line spoken: hashing the name could give two of a drama's few speakers the
 *  same colour, and a rename must not recolour the lines. Lines with no speaker get none. */
export function speakerSlots(ordered: readonly Pick<ReviewLine, 'speaker'>[]): Map<string, number> {
  const slots = new Map<string, number>()
  for (const l of ordered) if (l.speaker && !slots.has(l.speaker)) slots.set(l.speaker, slots.size % SPEAKER_COLOURS)
  return slots
}

/** "Audio drama · Chinese to English": the kind of title and its languages, from the shared labels; empty when the
 *  title has neither. */
export function titleKindAndLanguages(mediaType: string | null | undefined, sourceLanguage: string | null | undefined): string {
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
// A slider (the seek bar) answers the arrows, Home, End and Page Up/Down but no letters, so E and L still work there.
const OWN_LETTERS = 'input:not([type="range"]), textarea, select, [contenteditable]:not([contenteditable="false"]), [role="listbox"], [role="combobox"]'

export type WatchKey = 'fix' | 'prev' | 'next' | 'play' | 'loop' | 'good' | 'wrong' | 'mixed' | 'nextmarked' | 'prevmarked'

/** The page's own keys, from a useShortcut combo: E fixes the line, Up and Down move between lines, L turns Loop line
 *  on or off, and Alt+Space plays or pauses from anywhere, a button or the fix field included, as in Review. */
export function watchKey(combo: string, target: { closest?: (selector: string) => unknown } | null): WatchKey | null {
  if (combo === 'alt+ ') return 'play'
  const letter = combo.length === 1 && 'elgxmnp'.includes(combo)
  if (target?.closest?.(letter ? OWN_LETTERS : OWN_KEYS)) return null
  if (combo === 'e') return 'fix'
  if (combo === 'l') return 'loop'
  if (combo === 'g') return 'good'
  if (combo === 'x') return 'wrong'
  if (combo === 'm') return 'mixed'
  if (combo === 'n') return 'nextmarked'
  if (combo === 'p') return 'prevmarked'
  if (combo === 'arrowup') return 'prev'
  if (combo === 'arrowdown') return 'next'
  return null
}

/** Keys inside the fix form: Ctrl+Enter (Cmd+Enter on a Mac) saves, Esc cancels. */
export function fieldKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; isComposing?: boolean }): 'save' | 'cancel' | null {
  if (e.isComposing) return null
  if (e.key === 'Escape') return 'cancel'
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.altKey) return 'save'
  return null
}

export const CONFLICT_TEXT = 'This line changed. The new text is below; yours is still in the box. Save again if you still want it.'
export const CONFLICT_UNREAD_TEXT = 'This line changed and its new text could not load. Yours is still in the box. Try Save again soon.'

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

/** Save would send something. */
export const fixIsDirty = (fix: Pick<LineFix, 'seen' | 'draft'> | null): boolean => fix !== null && lineEditPatch(fix.seen, fix.draft) !== null

/** Where focus goes when the fix box closes or a save comes back. After Save and play it goes to the card, so the
 *  next Space pauses rather than pressing Fix this line again; after a refused save it goes back into the box, since
 *  the button pressed was disabled while saving. */
export function focusAfterFix(outcome: FixResult['kind'] | 'cancel'): 'card' | 'field' | 'fix-button' {
  if (outcome === 'cancel') return 'fix-button'
  return outcome === 'conflict' || outcome === 'failed' ? 'field' : 'card'
}

/** A dirty fix kept in this tab while the page is left or put away, until its save is known to have gone through. */
interface ParkedFix {
  lineId: number
  seen: string
  draft: string
  /** Its last Save was refused because the line changed, and it has not been saved since. */
  refused: boolean
}

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const parkKey = (dramaId: number) => `baihe.watch.draft.${dramaId}`

export function parkFix(dramaId: number, fix: Pick<LineFix, 'lineId' | 'seen' | 'draft' | 'conflict'>, store: Store | null): void {
  try {
    store?.setItem(parkKey(dramaId), JSON.stringify({ lineId: fix.lineId, seen: fix.seen, draft: fix.draft, refused: fix.conflict }))
  } catch {
    // Storage full or blocked: the save sent on leaving is the only copy.
  }
}

export function unparkFix(dramaId: number, store: Store | null): void {
  try {
    store?.removeItem(parkKey(dramaId))
  } catch {
    // Nothing to clear.
  }
}

/** The parked fix for this title, or null; anything else under its key is dropped. */
export function readParkedFix(dramaId: number, store: Store | null): ParkedFix | null {
  try {
    const raw = store?.getItem(parkKey(dramaId))
    if (raw == null) return null
    const p = JSON.parse(raw) as Partial<ParkedFix> | null
    if (p && typeof p.lineId === 'number' && typeof p.seen === 'string' && typeof p.draft === 'string') {
      return { lineId: p.lineId, seen: p.seen, draft: p.draft, refused: p.refused === true }
    }
  } catch {
    // Unreadable: dropped below.
  }
  unparkFix(dramaId, store)
  return null
}

/** What a parked fix comes to on the next visit: its line is gone; the line already says it (the save went through
 *  and only its answer was lost), so there is nothing to bring back; or it opens again. */
export function parkedOutcome(parked: Pick<ParkedFix, 'draft'>, line: Pick<ReviewLine, 'en'> | undefined): 'gone' | 'saved' | 'open' {
  if (!line) return 'gone'
  return fixIsDirty({ seen: line.en, draft: parked.draft }) ? 'open' : 'saved'
}

/** A parked fix opened again. One never refused is measured against the English seen when it was first opened, so a
 *  line changed since is refused on Save and shown under It now says. A refused one opens refused again, against what
 *  the line says now, which is shown under It now says, so Save then sends it over that. */
export const unparkedFix = (p: ParkedFix, line: Pick<ReviewLine, 'en'>): LineFix => p.refused
  ? { lineId: p.lineId, seen: line.en, draft: p.draft, conflict: true, now: line.en, error: null }
  : { ...openFix({ id: p.lineId, en: p.seen }), draft: p.draft }

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

/** How far down the list the spoken row's top is put, so several of the coming lines show under it. */
export const FOLLOW_AT = 0.3
/** Following waits this long after the person last scrolled the list themselves. */
export const FOLLOW_PAUSE_MS = 5000

/** How far to scroll the list so the row's top sits FOLLOW_AT down it, kept inside what the list can scroll (0 when
 *  there is nothing to move). `rowOffset` is the row's top less the list's visible top. Done by hand rather than with
 *  scrollIntoView, which also scrolls the page when the list itself is partly off screen. */
export function followScroll(rowOffset: number, view: { height: number; scrollTop: number; maxScroll: number }): number {
  const target = Math.min(Math.max(0, view.scrollTop + rowOffset - view.height * FOLLOW_AT), Math.max(0, view.maxScroll))
  const delta = Math.round(target - view.scrollTop)
  return Math.abs(delta) < 1 ? 0 : delta
}

/** Following is paused while the person's own scrolling of the list is recent. */
export const followPaused = (scrolledAt: number | null, now: number): boolean => scrolledAt !== null && now - scrolledAt < FOLLOW_PAUSE_MS

/** A scroll of the list the page did not start is the person's. The page's own counts as its own from its start until
 *  its scrollend where the browser fires that event (which clears it), else for OWN_SCROLL_MS. */
export const OWN_SCROLL_MS = 1000
export const ownScrollUntil = (now: number, endIsFired: boolean): number => (endIsFired ? Infinity : now + OWN_SCROLL_MS)
