import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Bookmarks } from './Bookmarks'

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
})
