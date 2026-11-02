import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Section } from './Section'

describe('Section', () => {
  it('names the fold with a heading, so a screen reader can jump to it by heading', () => {
    const html = renderToStaticMarkup(<Section title="Advanced"><p>body</p></Section>)
    expect(html).toMatch(/<summary><h2 class="section-title">Advanced<\/h2>/)
  })
})
