import { describe, expect, it } from 'vitest'

import { advisorNote, runLine, whisperNotUsedReason, whisperOnlyControlReason } from './transcribeRun'

const base = { backend: 'whisper', whisperSize: 'large-v3', useGroq: false, useGpu: true, gpuRuntime: 'ok' }

describe('run line', () => {
  it('names the Whisper model and the GPU', () => {
    expect(runLine(base)).toBe('This run will use: Whisper · Whisper large-v3 · GPU on')
    expect(runLine({ ...base, useGpu: false })).toBe('This run will use: Whisper · Whisper large-v3 · GPU off')
  })

  it('does not claim the GPU when the setting is on but the CUDA runtime is missing', () => {
    expect(runLine({ ...base, gpuRuntime: 'missing' })).toContain('GPU off (CUDA libraries missing, runs on CPU)')
  })

  it('says GPU unknown while the setting has not loaded', () => {
    expect(runLine({ ...base, useGpu: null })).toContain('GPU unknown')
  })

  it('shows the Qwen3 model instead of the Whisper size, and a missing cuBLAS does not change Qwen3', () => {
    const line = runLine({ ...base, backend: 'qwen3_asr_long', gpuRuntime: 'missing' })
    expect(line).toBe('This run will use: Qwen3 ASR on long windows · Qwen3-ASR 1.7B · GPU on')
    expect(line).not.toContain('large-v3')
  })

  it('names both models when Whisper cuts the lines and Qwen3 writes the text', () => {
    expect(runLine({ ...base, backend: 'qwen3_asr' })).toContain('Whisper large-v3 cuts lines, Qwen3-ASR 1.7B writes the text')
  })

  it('names Groq as the cloud run', () => {
    expect(runLine({ ...base, useGroq: true })).toContain('Groq cloud')
  })

  it('describes an aligned transcript, not an ASR backend', () => {
    expect(runLine({ ...base, backend: 'qwen3_asr_long', haveTranscript: true, alignmentMethod: 'whisper_diff' })).toContain('Whisper alignment')
    expect(runLine({ ...base, haveTranscript: true, alignmentMethod: 'qwen3_forced_align' })).toContain('Qwen3 forced aligner')
  })
})

describe('Whisper-only controls', () => {
  it('are off with the reason for the backends that skip Whisper', () => {
    expect(whisperNotUsedReason('qwen3_asr_long')).toBe('Not used with Qwen3 ASR')
    expect(whisperNotUsedReason('qwen3_asr_vad')).toBe('Not used with Qwen3 ASR')
    expect(whisperNotUsedReason('whisper')).toBeNull()
    expect(whisperNotUsedReason('qwen3_asr')).toBeNull()
  })

  it('leave the repeat guard on for anime-whisper, which uses it', () => {
    expect(whisperOnlyControlReason('anime_whisper', 'repeat_guard')).toBeNull()
    expect(whisperOnlyControlReason('anime_whisper', 'fast_mode')).toBe('Not used with Anime-whisper')
    expect(whisperOnlyControlReason('qwen3_asr_long', 'repeat_guard')).toBe('Not used with Qwen3 ASR')
  })
})

describe('advisor note', () => {
  it('recommends large-v3 with separation off, with the evidence when separation is on', () => {
    expect(advisorNote('whisper', false)).toContain('Whisper large-v3')
    expect(advisorNote('whisper', false)).not.toContain('142')
    expect(advisorNote('whisper', true)).toContain('142 lines against 184')
  })

  it('warns about Qwen3 on long audio', () => {
    expect(advisorNote('qwen3_asr_long', false)).toContain('timing uncertain')
    expect(advisorNote('whisper', false)).not.toContain('timing uncertain')
  })
})
