import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { ownActiveId } from './reviewLogic'
import {
  backStatus, NO_PLACES, placeFallback, placeKey, readPlaces, spotForMove, spotToOpen, storedPlaces, viewFor, withSpot,
  type Places, type Spot,
} from './reviewPlace'

const mk = (id: number, idx: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx, start: idx, end: idx + 1, zh: `句${idx}`, en: `Line ${idx}`, speaker: null,
  speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, ...over,
})

const inLines: Spot = { lineId: 512, lineNumber: 50, page: 2 }
const inQueue: Spot = { lineId: 307, lineNumber: 7, page: 1 }
const both: Places = { queue: inQueue, lines: inLines }

describe('reading the kept places', () => {
  it('is kept per title', () => {
    expect(placeKey(3)).toBe('review.at.3')
  })
  it('reads both spots back, or either one alone', () => {
    expect(readPlaces({ queue: { ...inQueue }, lines: { ...inLines } })).toEqual(both)
    expect(readPlaces({ lines: { ...inLines } })).toEqual({ queue: null, lines: inLines })
    expect(readPlaces({ queue: { ...inQueue } })).toEqual({ queue: inQueue, lines: null })
  })
  it('checks each spot on its own', () => {
    for (const bad of [
      'lines', 12, [512, 50, 2], { lineNumber: 50, page: 2 }, { lineId: '512', lineNumber: 50, page: 2 },
      { lineId: 512, lineNumber: 50.5, page: 2 }, { lineId: 512, lineNumber: 0, page: 2 }, { lineId: 512, lineNumber: 50, page: 0 },
      { lineId: -1, lineNumber: 50, page: 2 },
    ]) expect(readPlaces({ queue: bad, lines: { ...inLines } }), JSON.stringify(bad)).toEqual({ queue: null, lines: inLines })
  })
  it('reads any other shape as no place, the single place of before too', () => {
    for (const bad of [null, 'lines', 12, [inLines], {}, { lineId: 512, lineNumber: 50, page: 2, view: 'lines' }]) {
      expect(readPlaces(bad), JSON.stringify(bad)).toEqual(NO_PLACES)
    }
  })
  it('reads a number too large to write into an address as no place', () => {
    // 1e21 is an integer to Number.isInteger, and the page address would carry it as `1e+21`.
    for (const huge of [1e21, 2 ** 53, Number.MAX_VALUE]) {
      expect(readPlaces({ queue: { ...inQueue, page: huge }, lines: { ...inLines, lineId: huge } }), String(huge)).toEqual(NO_PLACES)
    }
    expect(readPlaces({ lines: { ...inLines, page: Number.MAX_SAFE_INTEGER } }).lines?.page).toBe(Number.MAX_SAFE_INTEGER)
  })
  it('stores only the spots there are', () => {
    expect(storedPlaces(both)).toEqual({ queue: inQueue, lines: inLines })
    expect(storedPlaces({ queue: null, lines: inLines })).toEqual({ lines: inLines })
    expect(storedPlaces(NO_PLACES)).toEqual({})
  })
})

describe('which spot a view opens on', () => {
  it('opens All on the lines spot, and Simple’s queue on the queue spot', () => {
    expect(viewFor('all', false)).toBe('lines')
    expect(viewFor('risk', true)).toBe('queue')
    expect(spotToOpen(both, 'all', true, false)).toEqual({ view: 'lines', spot: inLines })
    expect(spotToOpen(both, 'risk', true, false)).toEqual({ view: 'queue', spot: inQueue })
  })
  it('opens any other view, and Full’s riskiest-first filter, as before', () => {
    expect(viewFor('flagged', true)).toBeNull()
    expect(viewFor('risk', false)).toBeNull()
    expect(spotToOpen(both, 'flagged', false, false)).toBeNull()
    expect(spotToOpen(both, 'risk', false, false)).toBeNull()
  })
  it('has nothing to open for a view with no spot', () => {
    expect(spotToOpen({ queue: inQueue, lines: null }, 'all', true, false)).toBeNull()
    expect(spotToOpen({ queue: null, lines: inLines }, 'risk', true, false)).toBeNull()
  })
  it('lets a link that names lines, or a move already asked for, win', () => {
    expect(spotToOpen(both, 'all', false, true)).toBeNull()
    expect(spotToOpen(both, 'risk', true, true)).toBeNull()
  })
})

describe('what a move keeps', () => {
  const line = mk(512, 49)
  it('keeps the line, its number as shown and its page, for the view it was made in', () => {
    expect(spotForMove(line, { queue: false, all: true, page: 2 })).toEqual({ view: 'lines', spot: inLines })
    expect(spotForMove(mk(307, 6), { queue: true, all: false, page: 1 })).toEqual({ view: 'queue', spot: inQueue })
  })
  it('keeps a line found under a filter or a search in the lines, on its page of All', () => {
    expect(spotForMove(mk(600, 84), { queue: false, all: false, page: 1 }))
      .toEqual({ view: 'lines', spot: { lineId: 600, lineNumber: 85, page: 3 } })
  })
  it('replaces its own view’s spot and keeps the other', () => {
    const moved: Spot = { lineId: 312, lineNumber: 12, page: 1 }
    expect(withSpot(both, 'queue', moved)).toEqual({ queue: moved, lines: inLines })
    expect(withSpot(both, 'lines', moved)).toEqual({ queue: inQueue, lines: moved })
    expect(withSpot(both, 'queue', null)).toEqual({ queue: null, lines: inLines })
  })
})

describe('finding the kept line when it is not on its page', () => {
  const all = Array.from({ length: 60 }, (_, i) => mk(1000 + i, i))
  it('finds it by id first', () => {
    expect(placeFallback([...all, mk(512, 60)], inLines)?.id).toBe(512)
  })
  it('opens the line that has its number now when the id is gone', () => {
    expect(placeFallback(all, inLines)).toBe(all[49])
  })
  it('opens the last line of a title that got shorter', () => {
    expect(placeFallback(all.slice(0, 30), inLines)).toBe(all[29])
  })
  it('finds nothing in a title with no lines', () => {
    expect(placeFallback([], inLines)).toBeNull()
  })
})

describe('saying where the list reopened', () => {
  it('names the line by its number as shown', () => {
    expect(backStatus(mk(512, 179), 3)).toBe('Back where you stopped, on line 180.')
    expect(backStatus(mk(512, 179), undefined)).toBe('Back where you stopped, on line 180.')
  })
  it('says nothing when the list would have opened on that line anyway', () => {
    expect(backStatus(mk(512, 0), 512)).toBeNull()
  })
})

describe('the line a load opens on by itself', () => {
  const lines = [mk(1, 0), mk(2, 1, { flag: 'x' }), mk(3, 2)]
  it('keeps the active line while it is listed', () => {
    expect(ownActiveId(lines, 3, { first: true, queue: false })).toBe(3)
    expect(ownActiveId(lines, 3, { first: false, queue: true })).toBe(3)
  })
  it('starts All on its starting line and the queue on its first card', () => {
    expect(ownActiveId(lines, null, { first: true, queue: false })).toBe(2)
    expect(ownActiveId(lines, null, { first: true, queue: true })).toBe(1)
  })
  it('moves to the first line on a later load', () => {
    expect(ownActiveId(lines, 99, { first: false, queue: false })).toBe(1)
    expect(ownActiveId([], 99, { first: false, queue: false })).toBeNull()
  })
})
