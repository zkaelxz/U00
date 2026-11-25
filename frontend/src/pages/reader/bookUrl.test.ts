import { describe, expect, it } from 'vitest'

import { bookUrl } from './bookUrl'

describe('bookUrl', () => {
  it('points at the English e-book export of the title', () => {
    expect(bookUrl(7)).toBe('/api/export/dramas/7/epub?field=en')
  })
})
