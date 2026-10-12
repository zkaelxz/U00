import { describe, expect, it } from 'vitest'

import { GLOSSARY_TABS, emptyTabText, instructionsSummary, isGlossaryTab } from './glossaryTabs'

describe('glossary tabs', () => {
  it('has one level of six tabs in the agreed order', () => {
    expect(GLOSSARY_TABS.map((t) => t.label)).toEqual(['Terms', 'Find terms', 'Names', 'Packs', 'Instructions', 'Re-translate'])
  })

  it('knows its own ids', () => {
    expect(isGlossaryTab('names')).toBe(true)
    expect(isGlossaryTab('settings')).toBe(false)
    expect(isGlossaryTab(null)).toBe(false)
  })

  it('says whether instructions are set, with a character count', () => {
    expect(instructionsSummary('')).toBe('not set')
    expect(instructionsSummary('   \n')).toBe('not set')
    expect(instructionsSummary('Keep honorifics')).toBe('set, 15 characters')
    expect(instructionsSummary('x')).toBe('set, 1 character')
  })

  it('gives an empty-state message only where a tab has nothing to show', () => {
    expect(emptyTabText('terms', { hasSeries: false, termCount: 0 })).toMatch(/series/)
    expect(emptyTabText('terms', { hasSeries: true, termCount: 0 })).toBeNull()
    expect(emptyTabText('terms', { hasSeries: false, termCount: 2 })).toBeNull()
    expect(emptyTabText('retranslate', { hasSeries: false, termCount: 0 })).toMatch(/series/)
    expect(emptyTabText('retranslate', { hasSeries: true, termCount: 0 })).toBeNull()
    expect(emptyTabText('find', { hasSeries: false, termCount: 0 })).toBeNull()
    expect(emptyTabText('terms', { hasSeries: false, termCount: null })).toBeNull()
    expect(emptyTabText('instructions', { hasSeries: false, termCount: 0 })).toBeNull()
  })
})
