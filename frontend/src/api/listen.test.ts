import { describe, expect, it } from 'vitest'

import { listenTrackUrl } from './listen'

describe('listenTrackUrl', () => {
  it('points at the Listen route, not the Dub one', () => {
    expect(listenTrackUrl(7)).toBe('/api/listen/dramas/7/track')
  })
})
