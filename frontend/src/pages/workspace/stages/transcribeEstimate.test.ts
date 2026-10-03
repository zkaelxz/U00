import { describe, expect, it } from 'vitest'

import { diarizeEstimate, roughDuration, roughRange, transcribeEstimate, whisperEstimateSeconds } from './transcribeEstimate'

const base = { audioSeconds: 3600, whisperSize: 'large-v3', useGpu: false, useGroq: false, detectSpeakers: false }

describe('roughDuration', () => {
  it('rounds to minutes and hours', () => {
    expect(roughDuration(30)).toBe('under a minute')
    expect(roughDuration(240)).toBe('about 4 min')
    expect(roughDuration(3600)).toBe('about 1 h')
    expect(roughDuration(4800)).toBe('about 1 h 20 min')
  })
})

describe('roughRange', () => {
  it('uses friendly units and never a single false-precision number', () => {
    expect(roughRange(30, 50)).toBe('under a minute')
    expect(roughRange(2400, 4200)).toBe('about 40-70 min')
    expect(roughRange(7200, 10800)).toBe('about 2-3 h')
    expect(roughRange(3000, 3100)).toBe('about 50 min')
  })
})

describe('whisperEstimateSeconds', () => {
  it('scales the range by model, and CPU is slower than real time for large-v3', () => {
    const cpu = whisperEstimateSeconds(base)!
    expect(cpu).toEqual({ low_s: 5400, high_s: 14400 })
    expect(cpu.low_s).toBeGreaterThan(3600)
    expect(whisperEstimateSeconds({ ...base, whisperSize: 'tiny' })!.high_s).toBeLessThan(3600)
  })
  it('is about 10x faster on the GPU for large models', () => {
    const gpu = whisperEstimateSeconds({ ...base, useGpu: true })!
    expect(gpu.low_s).toBeLessThan(5400 / 8)
  })
  it('fast mode shortens it', () => {
    expect(whisperEstimateSeconds({ ...base, fastMode: true })!.high_s).toBe(7200)
  })
  it('has no estimate for an unknown model or length', () => {
    expect(whisperEstimateSeconds({ ...base, whisperSize: 'custom' })).toBeNull()
    expect(whisperEstimateSeconds({ ...base, audioSeconds: null })).toBeNull()
    expect(whisperEstimateSeconds({ ...base, audioSeconds: 0 })).toBeNull()
  })
  it('a measured speed overrides the table; a bad one is ignored', () => {
    expect(whisperEstimateSeconds({ ...base, measuredSpeed: 2 })).toEqual({ low_s: 1440, high_s: 2340 })
    expect(whisperEstimateSeconds({ ...base, measuredSpeed: -1 })).toEqual({ low_s: 5400, high_s: 14400 })
    expect(whisperEstimateSeconds({ ...base, whisperSize: 'custom', measuredSpeed: 2 })).not.toBeNull()
  })
})

describe('transcribeEstimate', () => {
  it('says rough, the model and CPU when GPU is off or unknown', () => {
    expect(transcribeEstimate(base)).toBe('Rough estimate: about 1.5-4 h for this audio (large-v3 on CPU).')
    expect(transcribeEstimate({ ...base, useGpu: null })).toContain('on CPU')
    expect(transcribeEstimate({ ...base, whisperSize: 'tiny' })).toBe('Rough estimate: about 6-20 min for this audio (tiny on CPU).')
  })
  it('names the GPU and adds speaker detection when it runs too', () => {
    expect(transcribeEstimate({ ...base, useGpu: true })).toBe('Rough estimate: about 9-35 min for this audio (large-v3 on GPU).')
    expect(transcribeEstimate({ ...base, useGpu: true, detectSpeakers: true })).toContain(', plus 1 h to 2 h to detect speakers.')
  })
  it('mentions the download only when the model is not cached', () => {
    expect(transcribeEstimate({ ...base, modelCached: false })).toContain('First use also downloads the model.')
    expect(transcribeEstimate({ ...base, modelCached: true })).not.toContain('downloads')
  })
  it('says when it is based on the last run', () => {
    expect(transcribeEstimate({ ...base, measuredSpeed: 2 })).toContain('based on your last run')
  })
  it('gives nothing without a duration, for an unknown model or for a Groq (cloud) run', () => {
    expect(transcribeEstimate({ ...base, audioSeconds: null })).toBeNull()
    expect(transcribeEstimate({ ...base, whisperSize: 'custom' })).toBeNull()
    expect(transcribeEstimate({ ...base, useGroq: true })).toBeNull()
  })
})

describe('diarizeEstimate', () => {
  it('is 1x-2x the audio, or a generic line without a duration', () => {
    expect(diarizeEstimate(600)).toBe('Takes approx. 10 min to 20 min.')
    expect(diarizeEstimate(null)).toMatch(/a minute to a few minutes/)
  })
})
