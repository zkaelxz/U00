import { describe, expect, it } from 'vitest'

import type { TranslateEngine } from '../../types/translate'
import { engineNeeds, initialTranslator, showGetStarted, translatorOptions } from './getStartedLogic'

const eng = (name: string, key_configured: boolean, free = false): TranslateEngine =>
  ({ name, label: `${name}.`, free, models: null, key_configured })

describe('showGetStarted', () => {
  it('shows only for a loaded empty library that was not dismissed', () => {
    expect(showGetStarted(0, false)).toBe(true)
    expect(showGetStarted(0, true)).toBe(false)
    expect(showGetStarted(1, false)).toBe(false)
    expect(showGetStarted(null, false)).toBe(false)
    expect(showGetStarted(undefined, false)).toBe(false)
  })
})

describe('translator options', () => {
  const list = [eng('claude', false), eng('gemini', true), eng('ollama', true, true), eng('nllb', true, true)]
  it('says what each needs and whether it is ready', () => {
    const o = translatorOptions(list)
    expect(o.map((x) => x.readyText)).toEqual(['Needs a key', 'Key added', 'Ready', 'Ready'])
    expect(o[0].needs).toContain('API key')
    expect(o[2].needs).toContain('Free')
    expect(o[1].label).toBe('Gemini')
  })
  it('falls back for unknown engines', () => {
    expect(engineNeeds('mystery')).toContain('API key')
  })
  it('preselects the saved default, else the first', () => {
    expect(initialTranslator(list, 'ollama')).toBe('ollama')
    expect(initialTranslator(list, 'zzz')).toBe('claude')
    expect(initialTranslator([], null)).toBe('')
  })
})
