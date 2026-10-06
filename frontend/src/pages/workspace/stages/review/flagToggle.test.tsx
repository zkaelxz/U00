import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FlagToggle } from './LineRow'

const NOTE = 'Reading speed · 11.5 characters/second -- more than ~8.5/s is hard to read in time. Shorten the line.'

describe('FlagToggle', () => {
  it('is a button carrying the complete note in its title, collapsed by default', () => {
    const html = renderToStaticMarkup(
      <FlagToggle text={NOTE} open={false} onToggle={() => {}} controls="n">chip</FlagToggle>,
    )
    expect(html).toContain('<button type="button"')
    expect(html).toContain(`title="${NOTE.replace('>', '&gt;')}"`)
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('aria-controls')
  })

  it('reports expanded and points at the note when open', () => {
    const html = renderToStaticMarkup(
      <FlagToggle text={NOTE} open onToggle={() => {}} controls="flag-note-7">chip</FlagToggle>,
    )
    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain('aria-controls="flag-note-7"')
  })
})
