import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { TranslateEngine } from '../../types/translate'
import { EnginePicker } from './ReaderEngine'

const eng = (name: string, extra: Partial<TranslateEngine> = {}): TranslateEngine =>
  ({ name, label: name, free: false, models: null, key_configured: true, ...extra })

describe('EnginePicker', () => {
  it('lists engines that cannot run as disabled options with the reason and a fix', () => {
    const out = renderToStaticMarkup(
      <EnginePicker
        engines={[eng('claude')]}
        off={[eng('deepseek', { key_configured: false, package_installed: false }), eng('ollama', { key_configured: false, free: true })]}
        engine="claude"
        onChange={() => undefined}
      />,
    )
    expect(out).toMatch(/<option value="deepseek" disabled="">DeepSeek \(not installed\)<\/option>/)
    expect(out).toMatch(/<option value="ollama" disabled="">Ollama \(local\) \(not running\)<\/option>/)
    expect(out).toMatch(/<option value="claude"( selected="")?>Claude/)
    expect(out).toContain('href="#/diagnostics?card=packages"')
    expect(out).toContain('Install the package in Diagnostics')
    expect(out).toContain('Start Ollama on this PC.')
  })

  it('still says what to do when no engine can run', () => {
    const out = renderToStaticMarkup(
      <EnginePicker engines={[]} off={[eng('claude', { key_configured: false })]} engine="" onChange={() => undefined} />,
    )
    expect(out).toContain('No AI engine is set up')
    expect(out).toContain('Claude (no key)')
    expect(out).toContain('Add a key in Settings')
  })
})
