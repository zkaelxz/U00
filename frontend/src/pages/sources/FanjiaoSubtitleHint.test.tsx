import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FanjiaoSubtitleHint, MissevanTrackingHint } from './SeriesPanel'

describe('FanjiaoSubtitleHint', () => {
  it('links out to the subtitle site in a new tab and points to the Source stage import', () => {
    const html = renderToStaticMarkup(<FanjiaoSubtitleHint />)
    expect(html).toContain('href="https://fun.zhufree.fun/sub-download"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('Import subtitle file')
  })
})

describe('MissevanTrackingHint', () => {
  it('says up front that MissEvan is tracking only and where to add audio or subtitles', () => {
    const html = renderToStaticMarkup(<MissevanTrackingHint />)
    expect(html).toContain('tracking only')
    expect(html).toContain('Workspace')
  })
})
