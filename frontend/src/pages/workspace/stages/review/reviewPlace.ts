/** The lines Review's list was last on for a title, one per view: their stored shape, which one a view opens on, and what a move keeps. */
import { idxFromLineNumber, lineNumber } from '../../../../lineNumber'
import type { LineFilter, ReviewLine } from '../../../../types/review'
import { QUEUE_FILTER } from './checkQueueModel'
import { pageForPosition } from './reviewLogic'

export interface Spot {
  lineId: number
  // As shown (lineNumber), so a line that was merged, split or transcribed again can still be found by its place.
  lineNumber: number
  page: number
}

// A search or any filter but the queue keeps its line under `lines`, because every line is in All too. The queue has
// its own spot so that working through it does not lose the reading place in All.
export interface Places {
  queue: Spot | null
  lines: Spot | null
}

export type PlaceView = keyof Places

type PlaceLine = Pick<ReviewLine, 'id' | 'idx'>

export const placeKey = (dramaId: number) => `review.at.${dramaId}`

export const NO_PLACES: Places = { queue: null, lines: null }

// Safe, not just integral: 1e21 is an integer, and it would be written into the page address as `1e+21`.
const whole = (n: unknown, min: number): n is number => Number.isSafeInteger(n) && (n as number) >= min

function readSpot(value: unknown): Spot | null {
  if (typeof value !== 'object' || value === null) return null
  const { lineId, lineNumber: n, page } = value as Record<string, unknown>
  return whole(lineId, 1) && whole(n, 1) && whole(page, 1) ? { lineId, lineNumber: n, page } : null
}

/** The stored places, each checked on its own; anything of another shape reads as no place. */
export function readPlaces(value: unknown): Places {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return NO_PLACES
  const { queue, lines } = value as Record<string, unknown>
  return { queue: readSpot(queue), lines: readSpot(lines) }
}

/** What is stored: only the spots there are. */
export function storedPlaces(places: Places): Partial<Record<PlaceView, Spot>> {
  return { ...(places.queue ? { queue: places.queue } : {}), ...(places.lines ? { lines: places.lines } : {}) }
}

/** The view whose spot a filter opens on: All the lines spot, Simple's queue the queue spot, any other filter none. */
export function viewFor(filter: LineFilter, simple: boolean): PlaceView | null {
  if (filter === 'all') return 'lines'
  return simple && filter === QUEUE_FILTER ? 'queue' : null
}

/**
 * The kept spot a view opens on, as the list opens or when the person switches to it. `asked`: a link names lines (as
 * the list opens) or a move is already asked for (the main Check button's first card, a go-to); that wins.
 */
export function spotToOpen(places: Places, filter: LineFilter, simple: boolean, asked: boolean): { view: PlaceView; spot: Spot } | null {
  const view = asked ? null : viewFor(filter, simple)
  const spot = view ? places[view] : null
  return view && spot ? { view, spot } : null
}

/** The spot a move onto `line` keeps, for the view it was made in. */
export function spotForMove(line: PlaceLine, at: { queue: boolean; all: boolean; page: number }): { view: PlaceView; spot: Spot } {
  // A filter's or a search's page is not All's; a line's number is its place in All.
  const page = at.queue || at.all ? at.page : pageForPosition(line.idx)
  return { view: at.queue ? 'queue' : 'lines', spot: { lineId: line.id, lineNumber: lineNumber(line.idx), page } }
}

/** The places with one view's spot replaced (null forgets it); the other view's stays as it is. */
export const withSpot = (places: Places, view: PlaceView, spot: Spot | null): Places => ({ ...places, [view]: spot })

/** The line to open when the kept one is not on its page: by id, else the line that has its number now, else the last line of a title that got shorter. */
export function placeFallback<L extends PlaceLine>(all: L[], spot: Spot): L | null {
  const idx = idxFromLineNumber(spot.lineNumber)
  const last = all[all.length - 1]
  return all.find((l) => l.id === spot.lineId) ?? all.find((l) => l.idx === idx) ?? (last && idx > last.idx ? last : null)
}

/** The status sentence for reopening on `line`; null when the list would have opened on it anyway. */
export function backStatus(line: PlaceLine, wouldOpenId: number | null | undefined): string | null {
  return line.id === wouldOpenId ? null : `Back where you stopped, on line ${lineNumber(line.idx)}.`
}
