import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { MediaTabPanel } from './MediaTabPanel'

describe('MediaTabPanel', () => {
  it('hides itself when another tab is selected but still renders its content', () => {
    const html = renderToStaticMarkup(<MediaTabPanel id="tools" active="add"><input aria-label="Kept" /></MediaTabPanel>)
    expect(html).toContain('id="media-panel-tools"')
    expect(html).toContain('hidden=""')
    expect(html).toContain('aria-label="Kept"')
  })

  it('is shown and labelled by its tab when selected', () => {
    const html = renderToStaticMarkup(<MediaTabPanel id="add" active="add"><p>x</p></MediaTabPanel>)
    expect(html).not.toContain('hidden')
    expect(html).toContain('aria-labelledby="media-tab-add"')
    expect(html).toContain('role="tabpanel"')
  })

  it('takes the id prefix of another stage tabs', () => {
    const html = renderToStaticMarkup(<MediaTabPanel id="glossary" active="translate" prefix="translate"><p>x</p></MediaTabPanel>)
    expect(html).toContain('id="translate-panel-glossary"')
    expect(html).toContain('aria-labelledby="translate-tab-glossary"')
    expect(html).toContain('hidden=""')
  })

  it('says so instead of showing a blank tab when every section is empty', () => {
    const html = renderToStaticMarkup(<MediaTabPanel id="tools" active="tools">{[null, false]}</MediaTabPanel>)
    expect(html).toContain('data-testid="media-tab-empty"')
    expect(html).toContain('Nothing to set up here for this title.')
  })
})
