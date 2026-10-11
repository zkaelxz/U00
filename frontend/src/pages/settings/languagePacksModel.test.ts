import { describe, expect, it } from 'vitest'

import type { LanguagePackSummary } from '../../types/languagePacks'
import { entryText, groupPacks } from './languagePacksModel'

const pack = (id: string, language: string): LanguagePackSummary => ({
  id, language, version: 1, title: id, description: '', entry_count: 3, styles: { default: '', options: {} },
})

describe('groupPacks', () => {
  const packs = [pack('ja-address', 'ja'), pack('ja-common', 'ja'), pack('ko-address', 'ko'), pack('any-x', 'any')]

  it('groups by language, skips empty languages and marks the defaults that are on', () => {
    const groups = groupPacks(packs, { ja: { language: 'ja', packs: ['ja-common'] } })
    expect(groups.map((g) => g.id)).toEqual(['ja', 'ko', 'any'])
    expect(groups[0].packs.map((p) => [p.pack.id, p.onByDefault])).toEqual([['ja-address', false], ['ja-common', true]])
    expect(groups[1].packs[0].onByDefault).toBe(false)
  })

  it('has no default for packs that fit any language', () => {
    expect(groupPacks(packs, {}).at(-1)?.packs[0].onByDefault).toBeNull()
  })
})

describe('entryText', () => {
  it('uses the style when the English differs by style, else the first rendering', () => {
    expect(entryText('Mr.', 'natural')).toBe('Mr.')
    expect(entryText({ natural: 'Mr.', literal: '-san' }, 'literal')).toBe('-san')
    expect(entryText({ natural: 'Mr.' }, '')).toBe('Mr.')
  })
})
