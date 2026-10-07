import { describe, expect, it } from 'vitest'

import { asrBackendOptions } from '../../../api/asrOptions'
import { asrBackendHelp, ASR_BACKEND_HELP, GROQ_HELP, withoutUntouchedBackend } from './transcribeBackendField'

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

describe('ASR backend help', () => {
  it('has a line for every backend the dropdown offers', () => {
    for (const id of asrBackendOptions(true)) expect(ASR_BACKEND_HELP[id], id).toBeTruthy()
  })

  it('lists one line per offered backend, and MOSS only while it is offered', () => {
    expect(asrBackendHelp(asrBackendOptions(false)).split('\n')).toHaveLength(asrBackendOptions(false).length)
    expect(asrBackendHelp(asrBackendOptions(false))).not.toContain('MOSS')
    expect(asrBackendHelp(asrBackendOptions(true))).toContain('MOSS')
  })

  it('has no help line for a backend the dropdown does not offer', () => {
    expect(Object.keys(ASR_BACKEND_HELP).sort()).toEqual([...asrBackendOptions(true)].sort())
  })
})

describe('Groq help', () => {
  it('says the audio is uploaded and a key is needed', () => {
    expect(GROQ_HELP).toContain('uploaded to Groq')
    expect(GROQ_HELP).toContain('Groq API key in Settings')
    expect(GROQ_HELP).toContain('leaves your computer')
  })
})
