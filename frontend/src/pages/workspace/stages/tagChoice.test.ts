import { describe, expect, it } from 'vitest'

import type { NarrationEstimate } from '../../../types/dub'
import { chapterRange } from '../translateForm'
import { EMPTY_TAG_CHOICE, estimateText, needsCostConfirm, tagChoiceProblem, tagRunBody } from './tagChoice'

const est = (over: Partial<NarrationEstimate>): NarrationEstimate => ({
  engine: 'claude', model: 'm', chunk_count: 3, chapters: 1, estimated_usd: 1, has_key: true,
  cap_applies: true, monthly_cap_set: false, effective_cap_usd: null, monthly_refusal: false, estimate_above_cap: false,
  ...over,
})

describe('chapter choice', () => {
  it('blank means all chapters and one box leaves that side open', () => {
    expect(chapterRange('', '')).toEqual({})
    expect(chapterRange('3', '')).toEqual({ chapter_from: 3 })
    expect(chapterRange('2', '2')).toEqual({ chapter_from: 2, chapter_to: 2 })
  })

  it('refuses text, zero and a first chapter after the last', () => {
    for (const [a, b] of [['x', ''], ['0', ''], ['5', '2'], ['1.5', '']]) expect(chapterRange(a, b)).toBeNull()
    expect(tagChoiceProblem({ ...EMPTY_TAG_CHOICE, from: '5', to: '2' })).toMatch(/first cannot be after the last/)
  })
})

describe('the chunk and tag request', () => {
  it('always names the engine, so no other one is used for the person', () => {
    expect(tagRunBody(EMPTY_TAG_CHOICE, 'claude')).toEqual({ engine: 'claude' })
    expect(tagRunBody({ ...EMPTY_TAG_CHOICE, engine: 'ollama', model: ' qwen ', from: '1', to: '3' }, 'claude'))
      .toEqual({ engine: 'ollama', model: 'qwen', chapter_from: 1, chapter_to: 3 })
  })

  it('one narrator is sent as such', () => {
    expect(tagRunBody({ ...EMPTY_TAG_CHOICE, oneNarrator: true }, 'claude')).toMatchObject({ one_narrator: true })
  })
})

describe('the cost confirmation', () => {
  it('is due only for a paid run, with a key, no monthly cap and a cost', () => {
    expect(needsCostConfirm(est({}))).toBe(true)
    expect(needsCostConfirm(est({ monthly_cap_set: true }))).toBe(false)
    expect(needsCostConfirm(est({ cap_applies: false }))).toBe(false)
    expect(needsCostConfirm(est({ has_key: false }))).toBe(false)
    expect(needsCostConfirm(null)).toBe(false)
  })

  it('says what a run covers and costs', () => {
    expect(estimateText(est({ chunk_count: 1200, chapters: 4, estimated_usd: 2.5 }))).toBe('1,200 lines in 4 chapters. Estimated cost about $2.50.')
    expect(estimateText(est({ has_key: false }))).toMatch(/nothing is spent/)
  })
})
