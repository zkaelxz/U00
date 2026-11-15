import { describe, expect, it } from 'vitest'

import { sourceText } from './settingSources'

const row = { key: 'beam_size', label: 'Beam size', group: 'transcribe', value: 5, series_value: null, differs_from_series: false }

describe('sourceText', () => {
  it('names the three sources', () => {
    expect(sourceText({ ...row, source: 'settings_default' })).toBe('From Settings (the title has none)')
    expect(sourceText({ ...row, source: 'series', series_value: 5 })).toBe('Same as the series default')
    expect(sourceText({ ...row, source: 'title' })).toBe('Set on this title')
  })

  it('shows the series default a title overrides', () => {
    expect(sourceText({ ...row, source: 'title', series_value: true, differs_from_series: true }))
      .toBe('Set on this title (the series default is On)')
  })
})
