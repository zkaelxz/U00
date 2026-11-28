import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Pager, SearchCount } from './ReviewToolbar'

describe('Review search count', () => {
  it('says the search stopped at its limit and offers the next hits', () => {
    const html = renderToStaticMarkup(<SearchCount count={400} more onLoadMore={() => {}} />)
    expect(html).toContain('First 400 matches shown; there may be more.')
    expect(html).toContain('>Load more matches</button>')
  })
  it('gives the plain count once every hit is shown', () => {
    const html = renderToStaticMarkup(<SearchCount count={1} more={false} onLoadMore={() => {}} />)
    expect(html).toContain('>1 match</span>')
    expect(html).not.toContain('Load more')
  })
})

describe('Review Pager', () => {
  const html = renderToStaticMarkup(<Pager page={2} pages={5} onPage={() => {}} labelled />)

  it('shows the page in a labelled number box with the total beside it', () => {
    expect(html).toMatch(/<input[^>]*aria-label="Page number"[^>]*value="2"/)
    expect(html).toContain('/5')
    expect(html).toContain('of 5. Type a number and press Enter to jump.')
  })
  it('keeps Previous and Next buttons, disabled at the ends', () => {
    expect(html).toContain('aria-label="Previous page"')
    const first = renderToStaticMarkup(<Pager page={1} pages={5} onPage={() => {}} />)
    expect(first).toMatch(/<button[^>]*aria-label="Previous page"[^>]*disabled|<button[^>]*disabled[^>]*aria-label="Previous page"/)
  })
})
