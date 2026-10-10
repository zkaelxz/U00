import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { RunSettings, runSettingRows } from './RunSettings'

describe('RunSettings', () => {
  it('lists each setting with toggles as on/off', () => {
    const html = renderToStaticMarkup(
      <RunSettings result={{ run_settings: { whisper_size: 'large-v3', beam_size: 5, use_gpu: true, glossary: false } }} />,
    )
    expect(html).toContain('Run settings')
    expect(html).toContain('whisper size')
    expect(html).toContain('large-v3')
    expect(html).toContain('<dd>5</dd>')
    expect(html).toContain('<dd>on</dd>')
    expect(html).toContain('<dd>off</dd>')
  })

  it('renders nothing for an older job with no run settings', () => {
    expect(renderToStaticMarkup(<RunSettings result={{ line_count: 3 }} />)).toBe('')
    expect(renderToStaticMarkup(<RunSettings result={null} />)).toBe('')
  })

  it('ignores a value that is not a flat object of scalars', () => {
    expect(runSettingRows({ run_settings: 'x' })).toEqual([])
    expect(runSettingRows({ run_settings: ['a'] })).toEqual([])
    expect(runSettingRows({ run_settings: { a: { b: 1 }, c: null, d: 2 } })).toEqual([['d', '2']])
  })
})
