import { describe, expect, it } from 'vitest'

import { diarizeEstimate, roughDuration, transcribeEstimate } from './transcribeEstimate'

const base = { durationSeconds: 3600, whisperSize: 'large-v3', useGpu: false, useGroq: false, detectSpeakers: false }

describe('roughDuration', () => {
  it('rounds to minutes and hours', () => {
    expect(roughDuration(30)).toBe('under a minute')
    expect(roughDuration(240)).toBe('about 4 min')
    expect(roughDuration(3600)).toBe('about 1 h')
    expect(roughDuration(4800)).toBe('about 1 h 20 min')
  })
})

describe('transcribeEstimate', () => {
  it('scales with the model and says CPU when GPU is off or unknown', () => {
    expect(transcribeEstimate(base)).toBe('Takes approx. 1 h 30 min to 4 h with large-v3 on the CPU.')
    expect(transcribeEstimate({ ...base, useGpu: null })).toContain('on the CPU')
    expect(transcribeEstimate({ ...base, whisperSize: 'tiny' })).toBe('Takes approx. 6 min to 18 min with tiny on the CPU.')
  })
  it('is faster on the GPU and adds speaker detection when it runs too', () => {
    expect(transcribeEstimate({ ...base, useGpu: true })).toBe('Takes approx. 9 min to 36 min with large-v3 on the GPU.')
    expect(transcribeEstimate({ ...base, useGpu: true, detectSpeakers: true })).toBe(
      'Takes approx. 9 min to 36 min with large-v3 on the GPU, plus 1 h to 2 h to detect speakers.',
    )
  })
  it('uses the slowest factor for an unknown model', () => {
    expect(transcribeEstimate({ ...base, whisperSize: 'custom' })).toContain('1 h 30 min to 4 h')
  })
  it('gives nothing without a duration or for a Groq (cloud) run', () => {
    expect(transcribeEstimate({ ...base, durationSeconds: null })).toBeNull()
    expect(transcribeEstimate({ ...base, durationSeconds: 0 })).toBeNull()
    expect(transcribeEstimate({ ...base, useGroq: true })).toBeNull()
  })
  it('collapses a range that rounds to one value', () => {
    expect(transcribeEstimate({ ...base, durationSeconds: 20, whisperSize: 'tiny' })).toBe(
      'Takes approx. under a minute with tiny on the CPU.',
    )
  })
})

describe('diarizeEstimate', () => {
  it('is 1x-2x the audio, or a generic line without a duration', () => {
    expect(diarizeEstimate(600)).toBe('Takes approx. 10 min to 20 min.')
    expect(diarizeEstimate(null)).toMatch(/a minute to a few minutes/)
  })
})
