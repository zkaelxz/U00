import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { TranslateEngine } from '../../types/translate'
import { EngineLine, EnginePicker } from './ReaderEngine'

const picked = { auto: false, canShow: false, show: () => undefined }

const eng = (name: string, extra: Partial<TranslateEngine> = {}): TranslateEngine =>
  ({ name, label: name, free: false, models: null, key_configured: true, model_labels: {}, cloud_models: [], package_installed: true, ...extra })

describe('EnginePicker', () => {
  it('lists engines that cannot run as disabled options with the reason and a fix', () => {
    const out = renderToStaticMarkup(
      <EnginePicker
        engines={[eng('claude')]}
        off={[eng('deepseek', { key_configured: false, package_installed: false }), eng('ollama', { key_configured: false, free: true })]}
        engine="claude"
        chosen={picked}
        onChange={() => undefined}
      />,
    )
    expect(out).toMatch(/<option value="deepseek" disabled="">DeepSeek \(not installed\)<\/option>/)
    expect(out).toMatch(/<option value="ollama" disabled="">Ollama \(not running\)<\/option>/)
    expect(out).toMatch(/<option value="claude"( selected="")?>Claude/)
    expect(out).toContain('href="#/diagnostics?card=packages"')
    expect(out).toContain('Install in Diagnostics')
    expect(out).toContain('Start Ollama on this PC.')
  })

  it('still says what to do when no engine can run', () => {
    const out = renderToStaticMarkup(
      <EnginePicker engines={[]} off={[eng('claude', { key_configured: false })]} engine="" chosen={picked} onChange={() => undefined} />,
    )
    expect(out).toContain('No AI engine is set up')
    expect(out).toContain('Claude (no key)')
    expect(out).toContain('Add a key in Settings')
  })

  it('shows the chosen-for-you line in place of the picker when no engine is sent', () => {
    const out = renderToStaticMarkup(
      <EnginePicker engines={[eng('claude')]} engine={null} chosen={{ ...picked, auto: true }} onChange={() => undefined} />,
    )
    expect(out).toContain('The AI engine is chosen for you.')
    expect(out).not.toContain('<select')
    expect(out).not.toContain('Show options')
    expect(renderToStaticMarkup(<EngineLine engines={[eng('claude')]} engine={null} />)).not.toContain('Change')
  })

  it('offers Show options to an admin on another device', () => {
    const out = renderToStaticMarkup(
      <EnginePicker engines={[eng('claude')]} engine={null} chosen={{ auto: true, canShow: true, show: () => undefined }} onChange={() => undefined} />,
    )
    expect(out).toContain('Show options')
  })
})
