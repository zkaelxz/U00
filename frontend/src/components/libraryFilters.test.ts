import { describe, expect, it } from 'vitest'

import { buildDramaQuery } from '../api/client'
import {
  NO_MORE_FILTERS, listQuery, moreFilterCount, toggleTag, withCurrent,
} from './libraryFilters'

describe('library filters', () => {
  it('counts set filters, one per tag', () => {
    expect(moreFilterCount(NO_MORE_FILTERS)).toBe(0)
    expect(moreFilterCount({ ...NO_MORE_FILTERS, studio: 'S', media_type: 'anime', tags: ['a', 'b'] })).toBe(4)
  })

  it('sends every filter the API takes, and only the set ones', () => {
    const q = listQuery('x', '', 'Favorite', { ...NO_MORE_FILTERS, voice_actor: 'Wei', source_language: 'ja', tags: ['cozy', 'wuxia'] })
    expect(buildDramaQuery(q)).toBe('?search=x&quick_filter=Favorite&voice_actor=Wei&source_language=ja&tag=cozy&tag=wuxia')
    expect(buildDramaQuery(listQuery('', '', '', NO_MORE_FILTERS))).toBe('')
  })

  it('toggles tags in pick order', () => {
    expect(toggleTag(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleTag(['a', 'b'], 'a')).toEqual(['b'])
  })

  it('keeps a stale current value selectable', () => {
    expect(withCurrent(['A'], 'Gone')).toEqual(['Gone', 'A'])
    expect(withCurrent(['A'], 'A')).toEqual(['A'])
    expect(withCurrent(['A'], '')).toEqual(['A'])
  })
})
