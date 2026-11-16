import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { TypesetComicsCard } from './TypesetComicsCard'

describe('TypesetComicsCard', () => {
  it('says it is free and keeps edits, and asks before it starts', () => {
    const html = renderToStaticMarkup(<TypesetComicsCard />)
    expect(html).toContain('Free: nothing is read or translated again')
    expect(html).toContain('your English and box edits are kept')
    expect(html).toContain('Typeset all comics again…')
    expect(html).not.toContain('Confirm: typeset')
  })
})
