import { describe, expect, it } from 'vitest'

import type { ComicPageInfo } from '../../types/comic'
import { needsReadAhead } from './useReadAhead'

const page = (id: number, has_regions: boolean) => ({ id, has_regions }) as ComicPageInfo

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
  it('is false at the end of the chapter', () => {
    expect(needsReadAhead([], new Set())).toBe(false)
  })
})
