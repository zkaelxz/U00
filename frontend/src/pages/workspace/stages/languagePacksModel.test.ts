import { describe, expect, it } from 'vitest'

import type { LanguagePackEntry, TitleLanguagePack } from '../../../types/languagePacks'
import { choiceAfter, entryRendering, packsSummary, termFromEntry } from './languagePacksModel'

const pack = (over: Partial<TitleLanguagePack> = {}): TitleLanguagePack => ({
  id: 'ja-address', version: 1, language: 'ja', title: 'Japanese honorifics', description: 'd', entry_count: 2,
  styles: { default: 'romanised', options: { romanised: 'Keep romanised', natural: 'Natural English' } },
  enabled: false, style: 'romanised', ...over,
})
const senpai: LanguagePackEntry = {
  source: '先輩', en: { romanised: 'senpai', natural: 'senior' }, category: 'honorific',
  note: 'Said instead of the name', context: 'junior to senior',
}

describe('language packs model', () => {
  it('renders an entry in the pack style, falling back to the default', () => {
    expect(entryRendering(senpai, pack())).toBe('senpai')
    expect(entryRendering(senpai, pack({ style: 'natural' }))).toBe('senior')
    expect(entryRendering(senpai, pack({ style: 'gone' }))).toBe('senpai')
    expect(entryRendering({ ...senpai, en: 'plain' }, pack())).toBe('plain')
  })

  it('builds the stored choice from the packs that are on', () => {
    const packs = [pack({ enabled: true }), pack({ id: 'ja-common', enabled: false, style: null })]
    expect(choiceAfter(packs, { id: 'ja-common', enabled: true })).toEqual({ 'ja-address': 'romanised', 'ja-common': null })
    expect(choiceAfter(packs, { id: 'ja-address', enabled: false })).toEqual({})
    expect(choiceAfter(packs, { id: 'ja-address', style: 'natural' })).toEqual({ 'ja-address': 'natural' })
  })

  it('summarises how many are on', () => {
    expect(packsSummary([pack(), pack({ id: 'b' })])).toBe('starter packs, all off')
    expect(packsSummary([pack({ enabled: true }), pack({ id: 'b' })])).toBe('starter packs, 1 on')
  })

  it('copies an entry as a glossary term in the current style', () => {
    expect(termFromEntry(senpai, pack({ style: 'natural' }))).toEqual({
      term_original: '先輩', term_translation: 'senior', notes: 'junior to senior. Said instead of the name',
      category: 'honorific', policy: 'contextual',
    })
    expect(termFromEntry({ ...senpai, category: 'phrase', en: 'Welcome back' }, pack()).policy).toBe('translate_meaning')
  })
})
