import { describe, expect, it } from 'vitest'

import { promptSummary, switchesSummary, type ConfigForm } from './transcribeConfigForm'

const off = {
  separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false, split_by_sentences: false,
  vocabulary_hint: false, whisper_repeat_guard: false, use_groq: false,
} satisfies Pick<ConfigForm, 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'split_by_sentences' | 'vocabulary_hint' | 'whisper_repeat_guard' | 'use_groq'>

describe('switchesSummary', () => {
  it('says All off with nothing on', () => {
    expect(switchesSummary(off, false)).toBe('All off')
  })

  it('names the one that is on', () => {
    expect(switchesSummary({ ...off, use_groq: true }, false)).toBe('1 on: Use Groq')
  })

  it('names three in the order the card lists them', () => {
    expect(switchesSummary({ ...off, use_groq: true, separate_vocals_first: true, split_by_sentences: true }, false)).toBe(
      '3 on: Separate vocals first, Split lines by sentences, Use Groq',
    )
  })

  it('does not list the Developer Mode switch while it is hidden', () => {
    expect(switchesSummary({ ...off, whisper_repeat_guard: true }, false)).toBe('All off')
    expect(switchesSummary({ ...off, whisper_repeat_guard: true }, true)).toBe('1 on: Whisper repeat guard')
  })
})

describe('promptSummary', () => {
  it('is empty with nothing set', () => {
    expect(promptSummary('', '')).toBe('')
    expect(promptSummary('  ', '  ')).toBe('')
  })

  it('counts extra names split on 、 or commas', () => {
    expect(promptSummary('沈清疑、云隐宗', '')).toBe('Extra names: 2')
    expect(promptSummary('A, B, C', '')).toBe('Extra names: 3')
  })

  it('adds the custom prompt', () => {
    expect(promptSummary('沈清疑、云隐宗', 'full prompt')).toBe('Extra names: 2 · custom prompt set')
    expect(promptSummary('', 'full prompt')).toBe('custom prompt set')
  })
})
