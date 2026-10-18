import { describe, expect, it } from 'vitest'

import type { TranslateEngine } from '../../../types/translate'
import { aiUnavailableText, engineGate, ollamaAnswer } from './engineReady'
import { startOptions } from './StartFromPicker'

const eng = (name: string, over: Partial<TranslateEngine> = {}): TranslateEngine => ({
  name, label: name, free: false, models: null, key_configured: true, package_installed: true, ...over,
})

describe('engineGate', () => {
  it('is null while the engine list is unknown or the engine is not in it', () => {
    expect(engineGate(null, 'claude', null)).toBeNull()
    expect(engineGate([eng('gemini')], 'claude', null)).toBeNull()
  })

  it('says a hosted engine without a key needs one, and points to Settings', () => {
    const g = engineGate([eng('claude', { key_configured: false })], 'claude', null)
    expect(g).toMatchObject({ id: 'engine_key', action: 'settings' })
    expect(g?.reason).toMatch(/Claude has no API key/)
  })

  it('names the missing package and sends the fix to Diagnostics > Packages', () => {
    const g = engineGate([eng('deepseek', { key_configured: false, package_installed: false })], 'deepseek', null)
    expect(g).toMatchObject({ id: 'engine_package', action: 'diagnostics', packages: ['openai'] })
  })

  it('follows the cause for Ollama: stopped is a recheck with a download link, not a key', () => {
    const list = [eng('ollama', { key_configured: false })]
    const g = engineGate(list, 'ollama', false)
    expect(g).toMatchObject({ id: 'engine_ollama', action: 'recheck' })
    expect(g?.reason).not.toMatch(/key/i)
    expect(g?.link?.href).toBe('https://ollama.com/download')
  })

  it('treats an unknown Ollama answer as ready, and a running one too', () => {
    const list = [eng('ollama', { key_configured: false })]
    expect(engineGate(list, 'ollama', null)).toBeNull()
    expect(engineGate(list, 'ollama', true)).toBeNull()
  })

  it('is null for an engine that can run', () => {
    expect(engineGate([eng('claude')], 'claude', null)).toBeNull()
  })
})

describe('ollamaAnswer', () => {
  it('prefers the stage probe, then the shared readiness, else unknown', () => {
    const readiness = { ollama_reachable: true } as Parameters<typeof ollamaAnswer>[0]
    expect(ollamaAnswer(readiness, false)).toBe(false)
    expect(ollamaAnswer(readiness, null)).toBe(true)
    expect(ollamaAnswer(null, undefined)).toBeNull()
  })
})

describe('aiUnavailableText', () => {
  it('uses the cause when the gate knows it and the key hint otherwise', () => {
    const g = engineGate([eng('claude', { package_installed: false })], 'claude', null)
    expect(aiUnavailableText(g)).toMatch(/anthropic package/)
    expect(aiUnavailableText(null)).toMatch(/Add an API key/)
  })
})

describe('startOptions with engines', () => {
  const tiers = [
    { key: 'standard', label: 'Standard', translation_engine: 'claude', engine_model: '', reflect: false, auto_qc: false },
    { key: 'draft', label: 'Draft', translation_engine: 'deepseek', engine_model: '', reflect: false, auto_qc: false },
  ]
  it('marks a tier whose engine cannot run, as the engine pickers do', () => {
    const engines = [eng('claude', { key_configured: false }), eng('deepseek')]
    expect(startOptions(tiers, null, engines).tiers.map((t) => t.label)).toEqual(['Standard (no key)', 'Draft'])
  })
  it('leaves the labels alone without an engine list', () => {
    expect(startOptions(tiers, null).tiers.map((t) => t.label)).toEqual(['Standard', 'Draft'])
  })
})
