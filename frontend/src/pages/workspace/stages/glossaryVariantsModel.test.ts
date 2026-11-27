import { describe, expect, it } from 'vitest'

import type { GlossaryNameCluster } from '../../../types/translateStage'
import { aliasBlocker, aliasesWith, clusterBlocker, clusterTermBody, matchLabel, sourceLabel } from './glossaryVariantsModel'

const cluster = (over: Partial<GlossaryNameCluster> = {}): GlossaryNameCluster => ({
  suggested_original: 'ナロー',
  existing_term: null,
  total: 5,
  spellings: [
    { text: 'ナロー', count: 3, sources: ['katakana'], is_alias: false, is_term: false },
    { text: 'ナロウ', count: 2, sources: ['katakana'], is_alias: false, is_term: false },
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
    const before = ['ナロ']
    expect(aliasesWith(before, ' ナロー ')).toEqual(['ナロ', 'ナロー'])
    expect(aliasesWith(['ナロー'], 'ナロー')).toEqual(['ナロー'])
    expect(aliasesWith(before, '  ')).toEqual(['ナロ'])
    expect(before).toEqual(['ナロ'])
  })

  it('says why a candidate is not offered as new', () => {
    expect(aliasBlocker({ is_alias: true, is_term: false })).toMatch(/alias/)
    expect(aliasBlocker({ is_alias: false, is_term: true })).toMatch(/term/)
    expect(aliasBlocker({ is_alias: false, is_term: false })).toBeNull()
  })

  it('needs a series, a new name and an English name before creating', () => {
    expect(clusterBlocker(cluster(), 'Nalo', false)).toMatch(/series/)
    expect(clusterBlocker(cluster({ existing_term: 'ナロ' }), 'Nalo', true)).toMatch(/ナロ/)
    expect(clusterBlocker(cluster(), '  ', true)).toMatch(/English/)
    expect(clusterBlocker(cluster(), 'Nalo', true)).toBeNull()
  })

  it('builds the term from the cluster: other spellings become aliases', () => {
    expect(clusterTermBody(cluster(), ' Nalo ')).toEqual({
      term_original: 'ナロー',
      term_translation: 'Nalo',
      aliases: ['ナロウ'],
    })
  })

  it('labels where a name was seen', () => {
    expect(sourceLabel('speaker')).toBe('speaker label')
    expect(sourceLabel('other')).toBe('other')
  })
})
