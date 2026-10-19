import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { DeveloperModeOff } from '../components/DeveloperModeOff'
import BenchmarkPage from './Benchmark'

describe('Benchmark Lab behind Developer Mode', () => {
  it('the off notice links to the Developer Mode card in Settings', () => {
    const html = renderToStaticMarkup(<DeveloperModeOff />)
    expect(html).toContain('Developer Mode is off.')
    expect(html).toContain('href="#/settings?section=developer-mode"')
  })

  it('shows only the title and a loading line until the setting is known, so no benchmark call starts', () => {
    const html = renderToStaticMarkup(<BenchmarkPage compare="claude:a,claude:b" />)
    expect(html).toContain('Benchmark Lab')
    expect(html).toContain('Loading…')
    expect(html).not.toContain('Run a benchmark')
  })
})
