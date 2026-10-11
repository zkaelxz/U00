import { describe, expect, it } from 'vitest'

import type { GlossaryNameCluster } from '../../../types/translateStage'
import { aliasBlocker, aliasesWith, clusterBlocker, clusterTermBody, matchLabel, sourceLabel } from './glossaryVariantsModel'

const cluster = (over: Partial<GlossaryNameCluster> = {}): GlossaryNameCluster => ({
  suggested_original: 'ヤソー',
  existing_term: null,
  total: 5,
  spellings: [
    { text: 'ヤソー', count: 3, sources: ['katakana'], is_alias: false, is_term: false },
    { text: 'ヤソウ', count: 2, sources: ['katakana'], is_alias: false, is_term: false },
  ],
  examples: [],
  ...over,
})

describe('glossary variants model', () => {
  it('describes a match in words', () => {
    expect(matchLabel(1)).toBe('Sounds the same')
    expect(matchLabel(0.83)).toBe('Very close')
    expect(matchLabel(0.7)).toBe('Close')
  })

  it('appends an alias once, without touching the input', () => {
    const before = ['ヤソ']
    expect(aliasesWith(before, ' ヤソー ')).toEqual(['ヤソ', 'ヤソー'])
    expect(aliasesWith(['ヤソー'], 'ヤソー')).toEqual(['ヤソー'])
    expect(aliasesWith(before, '  ')).toEqual(['ヤソ'])
    expect(before).toEqual(['ヤソ'])
  })

  it('says why a candidate is not offered as new', () => {
    expect(aliasBlocker({ is_alias: true, is_term: false })).toMatch(/alias/)
    expect(aliasBlocker({ is_alias: false, is_term: true })).toMatch(/term/)
    expect(aliasBlocker({ is_alias: false, is_term: false })).toBeNull()
  })

  it('needs a series, a new name and an English name before creating', () => {
    expect(clusterBlocker(cluster(), 'Yaso', false)).toMatch(/series/)
    expect(clusterBlocker(cluster({ existing_term: 'ヤソ' }), 'Yaso', true)).toMatch(/ヤソ/)
    expect(clusterBlocker(cluster(), '  ', true)).toMatch(/English/)
    expect(clusterBlocker(cluster(), 'Yaso', true)).toBeNull()
  })

  it('builds the term from the cluster: other spellings become aliases', () => {
    expect(clusterTermBody(cluster(), ' Yaso ')).toEqual({
      term_original: 'ヤソー',
      term_translation: 'Yaso',
      aliases: ['ヤソウ'],
    })
  })

  it('labels where a name was seen', () => {
    expect(sourceLabel('speaker')).toBe('speaker label')
    expect(sourceLabel('other')).toBe('other')
  })
})
