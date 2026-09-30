import { describe, expect, it } from 'vitest'

import type { Coverage, EmotionSummary, ReviewLine } from '../../../../types/review'
import {
  checkFormSummary,
  checkJobBody,
  coverageGroups,
  emotionCounts,
  emotionLines,
  emotionText,
  EMPTY_CHECK_FORM,
  EMPTY_FIX_FORM,
  fixFlaggedBody,
  fixFormSummary,
  glossaryText,
  mergeSearchHits,
  pacingFindings,
  spendText,
  termsText,
} from './reviewResults'

const line = (id: number, idx: number): ReviewLine => ({
  id, idx, start: 0, end: 1, zh: 'z', en: 'e', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null,
})

describe('AI check bodies (R50/R33)', () => {
  it('sends only what was chosen; audio cues only for emotion', () => {
    for (const k of ['consistency', 'emotion', 'notes', 'flag'] as const) expect(checkJobBody(k, EMPTY_CHECK_FORM)).toEqual({})
    const f = { engine: ' claude ', model: 'm1', audioCues: false }
    expect(checkJobBody('emotion', f)).toEqual({ engine: 'claude', model: 'm1', use_audio_cues: false })
    expect(checkJobBody('consistency', f)).toEqual({ engine: 'claude', model: 'm1' })
    expect(checkJobBody('emotion', { ...EMPTY_CHECK_FORM, audioCues: true })).toEqual({ use_audio_cues: true })
  })
  it('adds bulk only when asked, and never for fix-flagged (R49)', () => {
    expect(checkJobBody('flag', EMPTY_CHECK_FORM, true)).toEqual({ bulk: true })
    expect(checkJobBody('flag', EMPTY_CHECK_FORM, false)).toEqual({})
    expect(checkJobBody('fix-flagged', EMPTY_CHECK_FORM, true)).toEqual({})
  })
  it('summarises the choices, with the audio default following the drama', () => {
    expect(checkFormSummary(EMPTY_CHECK_FORM, 'gemini', true)).toBe('Gemini engine · audio cues')
    expect(checkFormSummary(EMPTY_CHECK_FORM, '', false)).toBe('Default engine')
    expect(checkFormSummary({ engine: 'claude', model: 'm1', audioCues: false }, 'gemini', true)).toBe('Claude · m1')
  })
})

describe('fix-flagged body', () => {
  it('sends nothing when every option is left at its default', () => {
    expect(fixFlaggedBody(EMPTY_FIX_FORM)).toEqual({})
  })
  it('sends only the fields that were set', () => {
    expect(fixFlaggedBody({ engine: 'openai', model: '', cap: '' })).toEqual({ engine: 'openai' })
    expect(fixFlaggedBody({ engine: '', model: ' gpt ', cap: '0.25 ' })).toEqual({ model: 'gpt', job_cost_cap_usd: 0.25 })
    expect(fixFlaggedBody({ engine: '', model: '', cap: '0' })).toEqual({ job_cost_cap_usd: 0 })
  })
  it('rejects a negative or non-numeric cap in plain words', () => {
    expect(fixFlaggedBody({ ...EMPTY_FIX_FORM, cap: '-1' })).toMatch(/0 or more/)
    expect(fixFlaggedBody({ ...EMPTY_FIX_FORM, cap: 'abc' })).toMatch(/number of dollars/)
  })
  it('rejects what a number field would silently drop', () => {
    for (const cap of ['5$', '1,5', '-', '$5']) expect(fixFlaggedBody({ ...EMPTY_FIX_FORM, cap })).toMatch(/number of dollars/)
  })
  it('shows spend against the monthly cap, or says there is none', () => {
    expect(spendText(1.5, 10)).toBe('Spent this month: $1.50 of $10.00.')
    expect(spendText(0, 0)).toBe('Spent this month: $0.00 (no monthly cap).')
    expect(spendText(2, -1)).toBe('Spent this month: $2.00 (no monthly cap).')
  })
  it('summarises the choice', () => {
    expect(fixFormSummary(EMPTY_FIX_FORM, 'Gemini')).toBe('Gemini engine · no cost cap')
    expect(fixFormSummary({ engine: 'openai', model: 'm', cap: '2' }, 'Gemini')).toBe('OpenAI · m · cap $2')
  })
})

describe('coverage and pacing findings', () => {
  const cov: Coverage = {
    long_lines: [{ idx: 4, id: 40, duration: 14.26, char_count: 3 }],
    large_gaps: [{ after_idx: 1, before_idx: 2, after_id: 11, before_id: 12, gap_seconds: 5 }],
    blank_zh: [],
    blank_en: [{ idx: 0, id: 10, zh: '你好' }],
  }
  it('links each finding by line id and shows idx + 1', () => {
    const groups = coverageGroups(cov)
    expect(groups.map((g) => g.title)).toEqual(['Long lines', 'Large gaps', 'Not translated'])
    expect(groups.every((g) => g.hint.length > 0)).toBe(true)
    expect(groups[0].items[0]).toMatchObject({ lineId: 40, where: '#5', text: '14.3s for 3 characters' })
    expect(groups[1].items[0]).toMatchObject({ lineId: 11, where: 'after #2', text: '5.0s silent' })
    expect(groups[2].items[0]).toMatchObject({ lineId: 10, where: '#1', text: '你好' })
  })
  it('keeps a finding whose line is gone, without a link', () => {
    const [g] = coverageGroups({ ...cov, long_lines: [{ idx: 2, id: null }], large_gaps: [], blank_en: [] })
    expect(g.items[0].lineId).toBeNull()
  })
  it('labels pacing issues in plain words', () => {
    const out = pacingFindings([
      { id: 7, idx: 6, issue: 'too_long_for_slot', detail: '~3.0s needed, only 1.0s available' },
      { id: null, idx: 1, issue: 'odd_thing', detail: null },
    ])
    expect(out[0]).toMatchObject({ lineId: 7, where: '#7', text: 'Too long for its time slot: ~3.0s needed, only 1.0s available' })
    expect(out[1]).toMatchObject({ lineId: null, text: 'odd thing' })
  })
})

describe('consistency search hits', () => {
  it('merges results by line id (not by list position) in line order', () => {
    const merged = mergeSearchHits([[line(9, 5), line(3, 1)], [line(3, 1), line(4, 2)]])
    expect(merged.map((l) => l.id)).toEqual([3, 4, 9])
  })
})

describe('emotion', () => {
  const s: EmotionSummary = {
    drama_id: 1, total: 3, by_emotion: { calm: 1, anger: 2 }, high_risk: 1,
    lines: [
      { line_idx: 0, emotion: 'calm', intensity: 0.2, note: '' },
      { line_idx: 4, emotion: 'anger', intensity: 0.9, note: 'shouting' },
      { line_idx: 2, emotion: 'anger', intensity: 0.9, note: '' },
    ],
  }
  it('counts, most common first', () => {
    expect(emotionCounts(s)).toBe('anger 2 · calm 1')
  })
  it('lists the strongest tags first, then in line order', () => {
    expect(emotionLines(s).map((t) => t.line_idx)).toEqual([2, 4, 0])
  })
})

describe('provenance text', () => {
  it('reads variable-shape rows defensively', () => {
    expect(emotionText({ emotion: 'sad', intensity: 0.5 })).toBe('sad (0.5)')
    expect(emotionText(null)).toBe('')
    expect(glossaryText([{ term_original: '白鹤', term_translation: 'Baihe' }, { x: 1 }, 'str'])).toEqual(['白鹤 → Baihe'])
    expect(termsText([{ term: 'a' }, {}])).toEqual(['a'])
  })
})
