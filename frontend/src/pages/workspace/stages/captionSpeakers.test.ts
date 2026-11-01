import { describe, expect, it } from 'vitest'

import type { TranslateEngine } from '../../../types/translate'
import { canStartReading, captionEngine, readyVisionEngines } from './captionSpeakers'

const engine = (name: string, key_configured: boolean): TranslateEngine => ({
  name, label: name, free: false, models: null, key_configured,
})

describe('captionSpeakers', () => {
  const all = [engine('deepseek', true), engine('claude', false), engine('gemini', true), engine('ollama', true)]

  it('offers only picture engines that can run', () => {
    expect(readyVisionEngines(all).map((e) => e.name)).toEqual(['gemini', 'ollama'])
  })

  it('keeps the remembered engine while it can run, else the first that can', () => {
    expect(captionEngine(all, 'ollama')).toBe('ollama')
    expect(captionEngine(all, 'claude')).toBe('gemini')
  })

  it('has no engine when none can run', () => {
    expect(captionEngine([engine('deepseek', true), engine('claude', false)], '')).toBe('')
  })

  it('starts only when the estimate fits the cap', () => {
    expect(canStartReading(null)).toBe(false)
    expect(canStartReading({ fits_cap: false })).toBe(false)
    expect(canStartReading({ fits_cap: true })).toBe(true)
  })
})
