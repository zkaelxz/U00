import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { ownActiveId } from './reviewLogic'
import { backStatus, openingPlace, placeFallback, placeForMove, placeKey, readPlace, type ReviewPlace } from './reviewPlace'

const mk = (id: number, idx: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx, start: idx, end: idx + 1, zh: `句${idx}`, en: `Line ${idx}`, speaker: null,
  speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, ...over,
})

const inLines: ReviewPlace = { lineId: 512, lineNumber: 50, page: 2, view: 'lines' }
const inQueue: ReviewPlace = { lineId: 307, lineNumber: 7, page: 1, view: 'queue' }

describe('reading a kept place', () => {
  it('is kept per title', () => {
    expect(placeKey(3)).toBe('review.at.3')
  })
  it('reads the stored shape back', () => {
    expect(readPlace({ lineId: 512, lineNumber: 50, page: 2, view: 'lines' })).toEqual(inLines)
    expect(readPlace({ ...inQueue })).toEqual(inQueue)
  })
  it('reads any other shape as no place', () => {
    for (const bad of [
      null, 'lines', 12, [512, 50, 2, 'lines'], {},
      { lineNumber: 50, page: 2, view: 'lines' },
      { lineId: 512, lineNumber: 50, page: 2, view: 'flagged' },
      { lineId: '512', lineNumber: 50, page: 2, view: 'lines' },
      { lineId: 512, lineNumber: 50.5, page: 2, view: 'lines' },
      { lineId: 512, lineNumber: 0, page: 2, view: 'lines' },
      { lineId: 512, lineNumber: 50, page: 0, view: 'lines' },
      { lineId: -1, lineNumber: 50, page: 2, view: 'lines' },
    ]) expect(readPlace(bad), JSON.stringify(bad)).toBeNull()
  })
})

describe('which kept place applies as the list opens', () => {
  it('opens All on a place kept in the lines, and the queue on a place kept in the queue', () => {
    expect(openingPlace(inLines, 'all', false)).toEqual({ now: inLines, onAll: null })
    expect(openingPlace(inQueue, 'risk', false)).toEqual({ now: inQueue, onAll: null })
  })
  it('keeps a place in the lines for the first switch to All when the list opens on the queue', () => {
    expect(openingPlace(inLines, 'risk', false)).toEqual({ now: null, onAll: inLines })
  })
  it('opens All as before on a place kept in the queue', () => {
    expect(openingPlace(inQueue, 'all', false)).toEqual({ now: null, onAll: null })
  })
  it('lets a link that names lines win', () => {
    expect(openingPlace(inLines, 'all', true)).toEqual({ now: null, onAll: null })
    expect(openingPlace(inLines, 'flagged', true)).toEqual({ now: null, onAll: null })
    expect(openingPlace(inQueue, 'risk', true)).toEqual({ now: null, onAll: null })
  })
  it('has nothing to open without a place', () => {
    expect(openingPlace(null, 'all', false)).toEqual({ now: null, onAll: null })
  })
})

describe('what a move keeps', () => {
  const line = mk(512, 49)
  it('keeps nothing for the line the page picked by itself', () => {
    expect(placeForMove(line, { ownPick: true, queue: false, all: true, page: 2 })).toBeNull()
    expect(placeForMove(line, { ownPick: true, queue: true, all: false, page: 1 })).toBeNull()
  })
  it('keeps the line, its number as shown, its page and its view', () => {
    expect(placeForMove(line, { ownPick: false, queue: false, all: true, page: 2 })).toEqual(inLines)
    expect(placeForMove(mk(307, 6), { ownPick: false, queue: true, all: false, page: 1 })).toEqual(inQueue)
  })
  it('keeps a line found under a filter or a search on its page of All', () => {
    expect(placeForMove(mk(600, 84), { ownPick: false, queue: false, all: false, page: 1 }))
      .toEqual({ lineId: 600, lineNumber: 85, page: 3, view: 'lines' })
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
