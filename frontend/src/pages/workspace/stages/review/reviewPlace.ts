/** The line Review's list was last on for a title: its stored shape, which kept place applies as the list opens, and what a move keeps. */
import { idxFromLineNumber, lineNumber } from '../../../../lineNumber'
import type { LineFilter, ReviewLine } from '../../../../types/review'
import { QUEUE_FILTER } from './checkQueueModel'
import { pageForPosition } from './reviewLogic'

// The check queue (Need a look), or the lines: All, any other filter or a search, whose lines are all in All too.
export type PlaceView = 'queue' | 'lines'

export interface ReviewPlace {
  lineId: number
  // As shown (lineNumber), so a line that was merged, split or transcribed again can still be found by its place.
  lineNumber: number
  page: number
  view: PlaceView
}

type PlaceLine = Pick<ReviewLine, 'id' | 'idx'>

export const placeKey = (dramaId: number) => `review.at.${dramaId}`

const whole = (n: unknown, min: number): n is number => Number.isInteger(n) && (n as number) >= min

/** A stored place, or null for anything of another shape (an older or hand-edited value reads as no place). */
export function readPlace(value: unknown): ReviewPlace | null {
  if (typeof value !== 'object' || value === null) return null
  const { lineId, lineNumber: n, page, view } = value as Record<string, unknown>
  if (!whole(lineId, 1) || !whole(n, 1) || !whole(page, 1) || (view !== 'queue' && view !== 'lines')) return null
  return { lineId, lineNumber: n, page, view }
}

/**
 * Which kept place applies as the list opens on `filter`: `now` opens on it at once, `onAll` waits for the first switch
 * to All (Simple starts on the queue, and a place kept in the lines belongs to All). A link that names lines wins over both.
 */
export function openingPlace(place: ReviewPlace | null, filter: LineFilter, linkNamesLines: boolean): { now: ReviewPlace | null; onAll: ReviewPlace | null } {
  if (!place || linkNamesLines) return { now: null, onAll: null }
  if (filter === 'all' && place.view === 'lines') return { now: place, onAll: null }
  if (filter === QUEUE_FILTER) return place.view === 'queue' ? { now: place, onAll: null } : { now: null, onAll: place }
  return { now: null, onAll: null }
}

/** What a move onto `line` keeps; nothing for the line the page picked by itself, which is not the person's place. */
export function placeForMove(line: PlaceLine, at: { ownPick: boolean; queue: boolean; all: boolean; page: number }): ReviewPlace | null {
  if (at.ownPick) return null
  // A filter's or a search's page is not All's; a line's number is its place in All.
  const page = at.queue || at.all ? at.page : pageForPosition(line.idx)
  return { lineId: line.id, lineNumber: lineNumber(line.idx), page, view: at.queue ? 'queue' : 'lines' }
}

/** The line to open when the kept one is not on its page: by id, else the line that has its number now, else the last line of a title that got shorter. */
export function placeFallback<L extends PlaceLine>(all: L[], place: ReviewPlace): L | null {
  const idx = idxFromLineNumber(place.lineNumber)
  const last = all[all.length - 1]
  return all.find((l) => l.id === place.lineId) ?? all.find((l) => l.idx === idx) ?? (last && idx > last.idx ? last : null)
}

/** The status sentence for reopening on `line`; null when the list would have opened on it anyway. */
export function backStatus(line: PlaceLine, wouldOpenId: number | null | undefined): string | null {
  return line.id === wouldOpenId ? null : `Back where you stopped, on line ${lineNumber(line.idx)}.`
}
