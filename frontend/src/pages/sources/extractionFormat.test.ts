import { describe, expect, it } from 'vitest'

import { AI_OFF, aiReason, aiRequestFields, effectiveEngine, engineLabel } from './extractionFormat'

const engines = { engines: ['claude', 'gemini', 'ollama'], default: 'claude' }

describe('AI fallback choice', () => {
  it('sends nothing while off', () => {
    expect(aiRequestFields(AI_OFF, engines)).toEqual({})
    expect(effectiveEngine({ on: false, engine: 'gemini' }, engines)).toBeNull()
    expect(aiReason(AI_OFF, engines)).toBeNull()
    expect(aiReason(AI_OFF, null)).toBeNull()
  })

  it('waits for the engine list once on', () => {
    expect(aiReason({ on: true, engine: null }, null)).toMatch(/Loading/)
  })

  it('uses the saved default until an engine is picked', () => {
    expect(aiRequestFields({ on: true, engine: null }, engines)).toEqual({ use_ai: true, engine: 'claude' })
    expect(aiRequestFields({ on: true, engine: 'ollama' }, engines)).toEqual({ use_ai: true, engine: 'ollama' })
  })

  it('ignores a picked engine the server no longer offers', () => {
    expect(effectiveEngine({ on: true, engine: 'deepl' }, engines)).toBe('claude')
  })

  it('asks for an engine when there is no usable default', () => {
    const none = { engines: ['gemini'], default: null }
    expect(aiReason({ on: true, engine: null }, none)).toBe('Still needed: an AI engine.')
    expect(aiRequestFields({ on: true, engine: null }, none)).toEqual({})
    expect(aiReason({ on: true, engine: null }, { engines: [], default: null })).toMatch(/No AI engine/)
  })

  it('labels engines', () => {
    expect(engineLabel('ollama')).toBe('Ollama (on this PC)')
    expect(engineLabel('claude')).toBeTruthy()
  })
})
