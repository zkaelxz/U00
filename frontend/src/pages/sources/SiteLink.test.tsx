import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SourceSummary } from '../../types/sources'
import { SiteLink } from './SiteLink'

const s = (homepage: string) => ({ display_name: 'Rawkuma', homepage }) as SourceSummary

describe('SiteLink', () => {
  it('opens the site in a new tab without handing it the opener', () => {
    const html = renderToStaticMarkup(<SiteLink s={s('https://rawkuma.net')} />)
    expect(html).toContain('href="https://rawkuma.net"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('shows nothing for a source with no site', () => {
    expect(renderToStaticMarkup(<SiteLink s={s('')} />)).toBe('')
  })
})
