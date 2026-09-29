import { describe, expect, it } from 'vitest'

import { advancedSummary, type AdvancedValues } from './sourceForm'

const base: AdvancedValues = {
  beam_size: '5', min_silence_ms: '300', vad_threshold: '0.5', hardsub_interval_sec: '1',
  alignment_method: 'whisper_diff', asr_backend_choice: 'whisper', separation_backend: 'auto',
  separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false, use_groq: false, prompt: '',
}

describe('advancedSummary', () => {
  it('says defaults when nothing differs', () => {
    expect(advancedSummary(base)).toBe('defaults')
  })
  it('lists only the values that differ', () => {
    expect(advancedSummary({ ...base, beam_size: '8', use_groq: true, prompt: ' x ' })).toBe('beam 8 · Groq · initial prompt')
  })
})
