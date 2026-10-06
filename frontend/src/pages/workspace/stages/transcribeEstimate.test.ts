import { describe, expect, it } from 'vitest'

import { diarizeEstimate, etaStage, formatLeft, isNoPercentStage, liveEtaSeconds, measuredRunSeconds, roughDuration, roughRange, transcribeEstimate, whisperEstimateSeconds } from './transcribeEstimate'

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
  it('the median of several runs narrows the range around the same time', () => {
    const one = whisperEstimateSeconds({ ...base, measuredSpeed: 2, measuredRuns: 1 })!
    const many = whisperEstimateSeconds({ ...base, measuredSpeed: 2, measuredRuns: 5 })!
    expect(many.low_s).toBeGreaterThan(one.low_s)
    expect(many.high_s).toBeLessThan(one.high_s)
    expect(many.low_s).toBeLessThan(1800)
    expect(many.high_s).toBeGreaterThan(1800)
  })
})

describe('stage medians', () => {
  const measured = { ...base, measuredSpeed: 2, measuredRuns: 5 } // 1800 s of Whisper work
  const stages = { load: 60, decode_vad: 40, separate: 300, align: 100 }
  it('adds load and decode, but separation and alignment only when the run has them', () => {
    expect(measuredRunSeconds({ ...measured, measuredStages: stages })).toBe(1900)
    expect(measuredRunSeconds({ ...measured, measuredStages: stages, separateVocals: true })).toBe(2200)
    expect(measuredRunSeconds({ ...measured, measuredStages: stages, separateVocals: true, realignLong: true })).toBe(2300)
  })
  it('an older server with no stage medians gives the speed alone', () => {
    expect(measuredRunSeconds(measured)).toBe(1800)
    expect(measuredRunSeconds({ ...measured, measuredStages: {} })).toBe(1800)
  })
  it('has no measured time without a usable speed', () => {
    expect(measuredRunSeconds({ ...base, measuredStages: stages })).toBeNull()
  })
})

describe('measured speaker detection', () => {
  it('uses the recorded speed once there is one, with the runs wording', () => {
    expect(diarizeEstimate(3600)).toBe('Takes approx. 1 h to 2 h.')
    expect(diarizeEstimate(3600, 4, 3)).toBe('Takes approx. 14 min to 17 min, based on your last 3 runs.')
  })
  it('falls back to the fixed range without a speed', () => {
    expect(diarizeEstimate(3600, null, 1)).toBe(diarizeEstimate(3600))
  })
  it('feeds the transcribe caption too', () => {
    const text = transcribeEstimate({ ...base, detectSpeakers: true, measuredDiarizeSpeed: 4, measuredDiarizeRuns: 3 })!
    expect(text).toContain('plus 14 min to 17 min to detect speakers')
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
    expect(transcribeEstimate({ ...base, measuredSpeed: 2, measuredRuns: 4 })).toContain('based on your last 4 runs')
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

// Steady 1%/10 s from t=0 (p=0.05 at the first sample).
const steady = (n: number, step = 10): { t: number; p: number }[] =>
  Array.from({ length: n }, (_, i) => ({ t: i * step, p: 0.05 + i * 0.01 }))

describe('liveEtaSeconds', () => {
  it('shows nothing before 30 s after the first percent, or with one reading', () => {
    expect(liveEtaSeconds(steady(3), 25)).toBeNull()
    expect(liveEtaSeconds(steady(1), 100)).toBeNull()
    expect(liveEtaSeconds([], 100)).toBeNull()
  })
  it('uses the recorded-speed prior only before the live readings settle', () => {
    // 3600 s expected, 2% done: about 3528 s left, instead of nothing.
    expect(liveEtaSeconds([{ t: 0, p: 0.02 }], 10, 3600)).toBeCloseTo(3528)
    expect(liveEtaSeconds(steady(3), 25, 3600)).not.toBeNull()
    expect(liveEtaSeconds([], 10, 3600)).toBeNull()
    expect(liveEtaSeconds([{ t: 0, p: 0.02 }], 10, null)).toBeNull()
    // Steady readings win over the prior.
    expect(liveEtaSeconds(steady(6), 50, 100000)).toBe(liveEtaSeconds(steady(6), 50))
  })
  it('extrapolates from time since the first percent and the progress made since', () => {
    const left = liveEtaSeconds(steady(6), 50)! // p=0.10 at t=50: 5 points per 50 s
    expect(left).toBeGreaterThan(800)
    expect(left).toBeLessThan(1000)
    expect(formatLeft(left)).toMatch(/^about \d+ min left$/)
  })
  it('shrinks as progress grows', () => {
    const a = liveEtaSeconds(steady(6), 50)!
    const b = liveEtaSeconds(steady(12), 110)!
    expect(b).toBeLessThan(a)
  })
  it('hides when the readings are wildly unstable', () => {
    const wild = [
      { t: 0, p: 0.05 }, { t: 10, p: 0.06 }, { t: 20, p: 0.2 }, { t: 30, p: 0.21 },
      { t: 40, p: 0.5 }, { t: 50, p: 0.51 },
    ]
    expect(liveEtaSeconds(wild, 50)).toBeNull()
  })
  it('hides when progress is not advancing', () => {
    const stuck = steady(6)
    expect(liveEtaSeconds(stuck, 50 + 200)).toBeNull()
    expect(liveEtaSeconds([{ t: 0, p: 0.05 }, { t: 40, p: 0.05 }], 45)).toBeNull()
  })
})

describe('stage detection', () => {
  it('flags the stages that have no percent of their own', () => {
    expect(isNoPercentStage('Loading model (elapsed 5s). This stage can take several minutes; no progress is available.')).toBe(true)
    expect(isNoPercentStage('Re-transcribing with Qwen3-ASR (step 2 of 2; no percent until the first batch finishes) (elapsed 5s).')).toBe(true)
    expect(isNoPercentStage('Transcribing... 12%')).toBe(false)
  })
  it('keeps the Qwen3 steps apart', () => {
    expect(etaStage('Transcribing (step 1 of 2)... 12%')).toBe('1')
    expect(etaStage('Re-transcribing with Qwen3-ASR (step 2 of 2)... 10%')).toBe('2')
    expect(etaStage('Transcribing... 12%')).toBe('0')
  })
})
