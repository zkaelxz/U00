import { describe, expect, it } from 'vitest'

import { AI_OFF, aiReason, aiRequestFields, comicImportText, effectiveEngine, engineLabel, skippedTitle } from './extractionFormat'

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

describe('comic import result', () => {
  const base = { kind: 'comic_import' as const, needs_review: false, pages_added: 12, skipped: [], skipped_count: 0 }

  it('says how many pages were added, or that nothing was', () => {
    expect(comicImportText(base)).toBe('Added 12 pages to the drama.')
    expect(comicImportText({ ...base, pages_added: 1 })).toBe('Added 1 page to the drama.')
    expect(comicImportText({ ...base, needs_review: true, pages_added: 0 })).toMatch(/nothing was added/)
  })

  it('titles the left-out list, noting when it is cut short', () => {
    expect(skippedTitle(base)).toBeNull()
    const one = { display_url: 'https://a.example/i.png', reason: 'too small' }
    expect(skippedTitle({ ...base, skipped: [one], skipped_count: 1 })).toBe('1 image left out')
    expect(skippedTitle({ ...base, skipped: [one], skipped_count: 150 })).toBe('150 images left out (first 1 shown)')
  })
})
