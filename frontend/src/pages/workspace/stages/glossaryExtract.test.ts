import { describe, expect, it } from 'vitest'

import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import {
  SOURCE_TEXT,
  buildOverrides,
  editProposal,
  extractionProgressText,
  missingTranslationText,
  missingTranslations,
  proposalValues,
  reviewSource,
  startTranslationLabel,
  type Edits,
} from './glossaryExtract'

const prop = (term: string, en: string, category: string | null = 'person_name', policy: string | null = 'keep_pinyin'): NovelGlossaryProposal => ({
  term, suggested_translation: en, category, policy, reason: '', already_in_glossary: false,
})

const WEI = prop('魏婴', 'Wei Ying')
const LAN = prop('蓝湛', 'Lan Zhan')
const EMPTY = prop('空', '', null, null)

describe('glossary extraction helpers', () => {
  it('has per-source copy and test ids', () => {
    expect(SOURCE_TEXT.novel.testId).toBe('novel-glossary')
    expect(SOURCE_TEXT.lines.testId).toBe('lines-glossary')
    expect(SOURCE_TEXT.lines.intro('claude')).toContain('source lines')
    expect(SOURCE_TEXT.novel.storageKey).not.toBe(SOURCE_TEXT.lines.storageKey)
  })

  it('progress: the novel run has a percentage, the lines run does not', () => {
    expect(extractionProgressText('novel', 'running', 0.42)).toBe('Reading the novel… 42%')
    expect(extractionProgressText('lines', 'running', 0.1)).toBe('Scanning the lines…')
    expect(extractionProgressText('lines', 'queued', 0)).toBe('Waiting to start…')
  })

  it('review uses the novel when attached, else the lines', () => {
    expect(reviewSource(true)).toBe('novel')
    expect(reviewSource(false)).toBe('lines')
  })

  it('edits are keyed by term and drop out when back to the proposal', () => {
    let e: Edits = {}
    e = editProposal(e, WEI, 'translation', 'Wei Wuxian')
    expect(proposalValues(WEI, e)).toEqual({ translation: 'Wei Wuxian', category: 'person_name', policy: 'keep_pinyin' })
    expect(proposalValues(LAN, e).translation).toBe('Lan Zhan')
    e = editProposal(e, WEI, 'policy', 'hybrid')
    e = editProposal(e, WEI, 'translation', 'Wei Ying')
    expect(e).toEqual({ 魏婴: { translation: 'Wei Ying', category: 'person_name', policy: 'hybrid' } })
    e = editProposal(e, WEI, 'policy', 'keep_pinyin')
    expect(e).toEqual({})
  })

  it('overrides carry only chosen terms and changed fields, trimmed; none when unedited', () => {
    const proposals = [WEI, LAN, EMPTY]
    expect(buildOverrides(proposals, ['魏婴', '蓝湛'], {})).toBeUndefined()
    let e: Edits = {}
    e = editProposal(e, WEI, 'translation', '  Wei Wuxian ')
    e = editProposal(e, LAN, 'category', null)
    e = editProposal(e, EMPTY, 'translation', 'Void')
    expect(buildOverrides(proposals, ['魏婴', '蓝湛'], e)).toEqual({
      魏婴: { translation: 'Wei Wuxian' },
      蓝湛: { category: null },
    })
    // a whitespace-only change trims back to the proposal: no override
    const ws = editProposal({}, WEI, 'translation', 'Wei Ying ')
    expect(buildOverrides(proposals, ['魏婴'], ws)).toBeUndefined()
  })

  it('a chosen term with no translation blocks adding until filled in', () => {
    const proposals = [WEI, EMPTY]
    expect(missingTranslations(proposals, ['魏婴', '空'], {})).toEqual(['空'])
    expect(missingTranslations(proposals, ['魏婴'], {})).toEqual([])
    const blank = editProposal({}, WEI, 'translation', '   ')
    expect(missingTranslations(proposals, ['魏婴'], blank)).toEqual(['魏婴'])
    expect(missingTranslations(proposals, ['空'], editProposal({}, EMPTY, 'translation', 'Void'))).toEqual([])
    expect(missingTranslationText(['空'])).toBe('Add a translation for 空 first.')
    expect(missingTranslationText(['a', 'b'])).toBe('Add a translation for 2 terms first.')
  })

  it('start label names how many terms are added first', () => {
    expect(startTranslationLabel(0)).toBe('Start translation')
    expect(startTranslationLabel(1)).toBe('Add 1 term and start translation')
    expect(startTranslationLabel(3)).toBe('Add 3 terms and start translation')
  })
})
