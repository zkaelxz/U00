import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { WatchBookmark } from '../../types/reader'
import { Bookmarks, withBookmark } from './Bookmarks'

const render = (canEdit: boolean) =>
  renderToStaticMarkup(<Bookmarks dramaId={1} mediaRef={{ current: null }} canEdit={canEdit} onPlay={() => {}} />)

describe('Bookmarks', () => {
  it('offers the tap and says the list is private to whoever may save', () => {
    const html = render(true)
    expect(html).toContain('Bookmark this moment')
    expect(html).toContain('Each account sees only its own.')
  })

  it('draws nothing for someone who cannot save and has none', () => {
    expect(render(false)).toBe('')
  })

  it('puts a restored bookmark back in time order, ties by id', () => {
    const mk = (id: number, at_seconds: number) => ({ id, at_seconds, note: '' }) as WatchBookmark
    expect(withBookmark([mk(1, 5), mk(3, 20)], mk(2, 10)).map((b) => b.id)).toEqual([1, 2, 3])
    expect(withBookmark([mk(4, 10)], mk(2, 10)).map((b) => b.id)).toEqual([2, 4])
  })
})
