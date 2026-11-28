import { describe, expect, it } from 'vitest'

import { pickDraft } from '../../../hooks/useStageDraft'
import type { TranscribeConfig, TranscribeConfigUpdate } from '../../../types/workspace'
import {
  advancedDraft, defaultSeparateVocals, withSeparateTouched, formFromConfig, promptSummary, switchesSummary, withoutUntouchedSeparateVocals, type ConfigForm,
} from './transcribeConfigForm'
import separateVocalsDefaults from './separateVocalsDefaults.json'

const off = {
  separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false, split_by_sentences: false,
  difficult_audio: false, vocabulary_hint: false, whisper_repeat_guard: false, use_groq: false,
} satisfies Pick<ConfigForm, 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'split_by_sentences' | 'difficult_audio' | 'vocabulary_hint' | 'whisper_repeat_guard' | 'use_groq'>

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

  it('lists Difficult audio between the splitting and name-hint switches', () => {
    expect(switchesSummary({ ...off, difficult_audio: true }, false)).toBe('1 on: Difficult audio')
    expect(switchesSummary({ ...off, difficult_audio: true, split_by_sentences: true, use_groq: true }, false)).toBe(
      '3 on: Split lines by sentences, Difficult audio, Use Groq',
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

describe('formFromConfig', () => {
  const saved = { beam_size: 5, min_silence_ms: 1000, min_pause_sec: 0.35, vad_threshold: 0.5, hallucination_silence_sec: 0, hardsub_interval_sec: 1 } as TranscribeConfig

  it('reads a config from before the option as off', () => {
    expect(formFromConfig(saved).difficult_audio).toBe(false)
  })

  it('carries the saved choice', () => {
    expect(formFromConfig({ ...saved, difficult_audio: true }).difficult_audio).toBe(true)
  })
})

describe('the separate-vocals switch for a title that never chose', () => {
  const update = { whisper_size: 'large-v3', separate_vocals_first: true } as TranscribeConfigUpdate

  it('is left out of the save while untouched, so the title keeps following the model', () => {
    expect(withoutUntouchedSeparateVocals(update, { separate_vocals_chosen: false }, false)).toEqual({ whisper_size: 'large-v3' })
  })

  it('is sent once touched, or when the title already chose', () => {
    expect(withoutUntouchedSeparateVocals(update, { separate_vocals_chosen: false }, true)).toEqual(update)
    expect(withoutUntouchedSeparateVocals(update, { separate_vocals_chosen: true }, false)).toEqual(update)
  })

  it('shows the default of the model in the form: On only for Breeze on local Whisper when it is ready', () => {
    const breeze = { whisper_size: 'breeze-asr-25', asr_backend_choice: 'whisper', use_groq: false }
    expect(defaultSeparateVocals({ separates_on_breeze_by_default: true }, breeze)).toBe(true)
    expect(defaultSeparateVocals({ separates_on_breeze_by_default: false }, breeze)).toBe(false)
    expect(defaultSeparateVocals({ separates_on_breeze_by_default: true }, { ...breeze, whisper_size: 'large-v3' })).toBe(false)
    expect(defaultSeparateVocals({ separates_on_breeze_by_default: true }, { ...breeze, use_groq: true })).toBe(false)
    expect(defaultSeparateVocals({ separates_on_breeze_by_default: true }, { ...breeze, asr_backend_choice: 'qwen3_asr' })).toBe(false)
  })

  // The same table pins the server's effective_separate_vocals (tests/test_breeze_model.py), so the
  // switch shown before a save can't drift from what the run will do.
  it.each(separateVocalsDefaults)('agrees with the server on $whisper_size, $asr_backend_choice, Groq $use_groq, ready $separator_ready',
    ({ separator_ready, separates, ...form }) => {
      expect(defaultSeparateVocals({ separates_on_breeze_by_default: separator_ready }, form)).toBe(separates)
    })
})

describe('the draft of a title that never chose the separate-vocals switch', () => {
  // A zh title on large-v3 (default Off) whose separator is ready, so Breeze's default is On.
  const config = {
    whisper_size: 'large-v3', asr_backend_choice: 'whisper', use_groq: false, separate_vocals_first: false,
    separate_vocals_chosen: false, separates_on_breeze_by_default: true,
    beam_size: 5, min_silence_ms: 1000, min_pause_sec: 0.35, vad_threshold: 0.5, hallucination_silence_sec: 0, hardsub_interval_sec: 1,
  } as TranscribeConfig
  const saved = formFromConfig(config)
  const onBreeze = { ...saved, whisper_size: 'breeze-asr-25' }

  it('keeps a touched Off that equals the saved value, so a reload restores it as touched', () => {
    const draft = advancedDraft({ ...onBreeze, separate_vocals_first: false }, config, true)
    expect(draft).toEqual({ whisper_size: 'breeze-asr-25', separate_vocals_first: false })
    // What the form reads back on a tab switch or reload: the key's presence marks the switch touched.
    const kept = pickDraft(JSON.parse(JSON.stringify(draft)) as Record<string, unknown>, saved)
    expect(kept).toEqual({ whisper_size: 'breeze-asr-25', separate_vocals_first: false })
  })

  it('tells the server a touched switch is a choice, and says nothing for an untouched one', () => {
    const update = { whisper_size: 'breeze-asr-25', separate_vocals_first: false } as TranscribeConfigUpdate
    expect(withSeparateTouched(update, true)).toEqual({ ...update, separate_vocals_touched: true })
    expect(withSeparateTouched(update, false)).toEqual(update)
  })

  it('leaves out an untouched switch that follows the model', () => {
    expect(advancedDraft({ ...onBreeze, separate_vocals_first: true }, config, false)).toEqual({ whisper_size: 'breeze-asr-25' })
  })
})
