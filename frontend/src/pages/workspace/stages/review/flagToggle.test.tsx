import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FlagNote, FlagToggle } from './LineRow'

const NOTE = 'Reading speed · 11.5 characters/second -- more than ~8.5/s is hard to read in time. Shorten the line.'

describe('FlagNote', () => {
  it('shows the complete note as plain text, with no toggle, and keeps it in the title', () => {
    const html = renderToStaticMarkup(<FlagNote text={NOTE} />)
    expect(html).not.toContain('<button')
    expect(html).not.toContain('aria-expanded')
    expect(html).toContain('Shorten the line.')
    expect(html).toContain(`title="${NOTE.replace('>', '&gt;')}"`)
  })
})

describe('FlagToggle (phone)', () => {
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
