import { describe, expect, it } from 'vitest'

import { withoutUntouchedBackend } from './transcribeBackendField'

describe('withoutUntouchedBackend', () => {
  it('drops the backend when the user left it on the loaded value', () => {
    expect(withoutUntouchedBackend({ asr_backend_choice: 'qwen3_asr_long', beam_size: 8 }, 'qwen3_asr_long')).toEqual({
      beam_size: 8,
    })
  })
  it('keeps a backend the user changed', () => {
    expect(withoutUntouchedBackend({ asr_backend_choice: 'whisper', beam_size: 8 }, 'qwen3_asr_long')).toEqual({
      asr_backend_choice: 'whisper',
      beam_size: 8,
    })
  })
})
