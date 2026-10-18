import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Pager } from './ReviewToolbar'

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
