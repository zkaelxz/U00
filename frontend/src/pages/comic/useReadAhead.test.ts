import { describe, expect, it } from 'vitest'

import type { ComicPageInfo } from '../../types/comic'
import { ApiError } from '../../api/client'
import { needsReadAhead, nothingToReadAhead } from './useReadAhead'

const page = (id: number, has_regions: boolean, has_rendered = true) => ({ id, has_regions, has_rendered }) as ComicPageInfo

describe('needsReadAhead', () => {
  it('is true when a page ahead has no text yet', () => {
    expect(needsReadAhead([page(2, true), page(3, false)], new Set())).toBe(true)
  })
  it('is false when every page ahead is translated', () => {
    expect(needsReadAhead([page(2, true), page(3, true)], new Set())).toBe(false)
  })
  it('is false for pages already tried, so a page with no text is not retried on every turn', () => {
    expect(needsReadAhead([page(3, false)], new Set([3]))).toBe(false)
  })
  it('is true when a page has text but no typeset image yet', () => {
    expect(needsReadAhead([page(2, true, false)], new Set())).toBe(true)
    expect(needsReadAhead([page(2, true, false)], new Set([2]))).toBe(false)
  })
  it('is false at the end of the chapter', () => {
    expect(needsReadAhead([], new Set())).toBe(false)
  })
})

describe('nothingToReadAhead', () => {
  it('is true only for the server saying the pages ahead have nothing to do', () => {
    const err = (details: unknown) => new ApiError(400, { code: 'unsupported_operation', message: 'x', details })
    expect(nothingToReadAhead(err({ reason: 'NOTHING_TO_READ_AHEAD' }))).toBe(true)
    expect(nothingToReadAhead(err({ reason: 'no_key' }))).toBe(false)
    expect(nothingToReadAhead(new Error('x'))).toBe(false)
  })
})
