import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { pagesSummary } from '../comicStage'
import { ComicPagesPanel } from './ComicPagesPanel'

describe('ComicPagesPanel', () => {
  it('counts pages and chapters', () => {
    expect(pagesSummary(null)).toBe('Checking…')
    expect(pagesSummary({ pages: 0, chapters: 0 })).toBe('No pages yet')
    expect(pagesSummary({ pages: 1, chapters: 0 })).toBe('1 page')
    expect(pagesSummary({ pages: 13, chapters: 2 })).toBe('13 pages, 2 chapters')
  })

  it('never mentions a novel', () => {
    expect(renderToStaticMarkup(<ComicPagesPanel dramaId={5} />)).not.toMatch(/novel/i)
  })
})
