import { describe, expect, it } from 'vitest'

import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import { GLOSSARY_EXPIRED } from './autotuneGlossary'
import {
  PROPOSALS_CHANGED_TEXT,
  SOURCE_TEXT,
  defaultSuggestSource,
  startCardSuggestLabel,
  suggestBlockers,
  suggestButtonLabel,
  applyRequest,
  buildOverrides,
  editProposal,
  extractionProgressText,
  glossaryApplyErrorText,
  missingTranslationText,
  missingTranslations,
  proposalValues,
  reviewSource,
  scopedValue,
  startTranslationLabel,
  type Edits,
} from './glossaryExtract'

const prop = (term: string, en: string, category: string | null = 'person_name', policy: string | null = 'keep_pinyin'): NovelGlossaryProposal => ({
  term, suggested_translation: en, category, policy, reason: '', already_in_glossary: false, occurrences: 3, alternatives: [], confidence: 'high',
})

const WEI = prop('魏婴', 'Wei Ying')
const LAN = prop('蓝湛', 'Lan Zhan')
const EMPTY = prop('空', '', null, null)

describe('glossary extraction helpers', () => {
  it('has per-source copy and test ids', () => {
    expect(SOURCE_TEXT.novel.testId).toBe('novel-glossary')
    expect(SOURCE_TEXT.lines.testId).toBe('lines-glossary')
    expect(SOURCE_TEXT.lines.label).toBe('Transcript')
    expect(SOURCE_TEXT.novel.label).toBe('Novel')
  })

  it('progress: the novel run has a percentage, the lines run does not', () => {
    expect(extractionProgressText('novel', 'running', 0.42)).toBe('Reading the novel… 42%')
    expect(extractionProgressText('lines', 'running', 0.1)).toBe('Scanning the transcript…')
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

describe('run scoping and the apply body', () => {
  it("reads state kept for another run as the initial value", () => {
    const kept = { run: 'r1', value: new Set(['魏婴']) }
    expect(scopedValue(kept, 'r1', null)).toBe(kept.value)
    expect(scopedValue(kept, 'r2', null)).toBeNull()
    expect(scopedValue({ run: null, value: { 魏婴: 1 } }, 'r1', {})).toEqual({})
    // runs without an id (older server) still keep their own state
    expect(scopedValue({ run: null, value: 3 }, null, 0)).toBe(3)
  })

  it('names the reviewed run and sends only what applies', () => {
    expect(applyRequest(['魏婴'], 'r1', undefined)).toEqual({ terms: ['魏婴'], run_id: 'r1' })
    expect(applyRequest(['魏婴'], 'r1', { 魏婴: { policy: 'hybrid' } }, true)).toEqual({
      terms: ['魏婴'],
      overwrite_existing: true,
      confirm: true,
      overrides: { 魏婴: { policy: 'hybrid' } },
      run_id: 'r1',
    })
    expect(applyRequest(['魏婴'], null, undefined)).toEqual({ terms: ['魏婴'] })
  })

  it('maps a 409 on apply to "review again" and a 400 to "run again"', () => {
    expect(glossaryApplyErrorText({ status: 409 })).toBe(PROPOSALS_CHANGED_TEXT)
    expect(glossaryApplyErrorText({ status: 400 })).toBe(GLOSSARY_EXPIRED)
    expect(glossaryApplyErrorText({ status: 500 })).toBeNull()
    expect(glossaryApplyErrorText(null)).toBeNull()
  })
})

describe('suggest terms sources', () => {
  const blockers = (series: number | null, novel: boolean | null, lines: boolean | null) =>
    suggestBlockers(3, series, novel, lines)

  it('lines only: the transcript runs and the novel says what it needs', () => {
    const b = blockers(7, false, true)
    expect(b.lines).toBeNull()
    expect(b.novel?.text).toBe('Still needed: novel text')
    expect(defaultSuggestSource(b, false)).toBe('lines')
    expect(defaultSuggestSource(b, true)).toBe('lines')
  })

  it('novel only: defaults to the novel', () => {
    const b = blockers(7, true, false)
    expect(b.novel).toBeNull()
    expect(b.lines).toEqual({ text: 'Still needed: transcript lines', link: 'transcribe it on Source', href: '#/drama/3/source' })
    expect(defaultSuggestSource(b, false)).toBe('novel')
  })

  it('both: the title kind decides', () => {
    const b = blockers(7, true, true)
    expect(defaultSuggestSource(b, false)).toBe('lines')
    expect(defaultSuggestSource(b, true)).toBe('novel')
  })

  it('neither: both blocked, the title kind still picks the shown one', () => {
    const b = blockers(7, false, false)
    expect(b.novel).not.toBeNull()
    expect(b.lines).not.toBeNull()
    expect(defaultSuggestSource(b, false)).toBe('lines')
    expect(defaultSuggestSource(b, true)).toBe('novel')
  })

  it('no series blocks both with the series text', () => {
    const b = blockers(null, true, true)
    expect(b.novel?.text).toBe('Still needed: a series for this drama')
    expect(b.lines?.text).toBe('Still needed: a series for this drama')
  })

  it('a source still being read is not blocked yet', () => {
    const b = blockers(7, null, null)
    expect(b.novel).toBeNull()
    expect(b.lines).toBeNull()
  })

  it('button and card labels', () => {
    expect(suggestButtonLabel(false)).toBe('Suggest terms')
    expect(suggestButtonLabel(true)).toBe('Suggest more terms')
    expect(startCardSuggestLabel('lines')).toBe('Suggest terms from the transcript')
    expect(startCardSuggestLabel('novel')).toBe('Suggest terms from the novel')
  })
})
