import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FanjiaoSubtitleHint } from './SeriesPanel'

describe('FanjiaoSubtitleHint', () => {
  it('links out to the subtitle site in a new tab and points to the Source stage import', () => {
    const html = renderToStaticMarkup(<FanjiaoSubtitleHint />)
    expect(html).toContain('href="https://fun.zhufree.fun/sub-download"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('Import subtitle file')
  })
})
