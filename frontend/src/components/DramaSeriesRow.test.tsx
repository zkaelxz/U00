import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../api/types'
import { DramaSeriesRow } from './DramaSeriesRow'

const render = (seriesId: number | null) =>
  renderToStaticMarkup(<DramaSeriesRow drama={{ id: 7, series_id: seriesId } as DramaDetail} onChanged={() => {}} />)

describe('DramaSeriesRow', () => {
  it('says a title in no series has no shared glossary and offers to add it', () => {
    const html = render(null)
    expect(html).toContain('No series')
    expect(html).toContain('no shared glossary')
    expect(html).toContain('Add to series…')
  })

  it('links a title in a series to its series page and offers Change', () => {
    const html = render(3)
    expect(html).toContain('href="#/series/3"')
    expect(html).toContain('Change…')
  })
})
