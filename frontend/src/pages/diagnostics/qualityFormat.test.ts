import { describe, expect, it } from 'vitest'

import type { QualityCheck, QualityClip } from '../../types/diagnostics'
import { clipStatus, clipText, gateStatus, marginText, metricLabel, qualitySummary, ranText } from './qualityFormat'

const clip = (over: Partial<QualityClip> = {}): QualityClip => ({
  id: 'title59-transcription', kind: 'transcription', metric: 'cer', score: 0.642, baseline: 0.642,
  status: 'pass', failed: false, runs: 5, fixed_decoding: true, ...over,
})

const check = (over: Partial<QualityCheck> = {}): QualityCheck => ({
  found: true, unreadable: false, when: '2026-11-22 10:15', age_days: 2, fresh: true, passed: true,
  machine: 'DESKTOP-V100', gpu: 'Tesla V100', repeats: 5, margin: 0.02, max_age_days: 7, clips: [clip()], ...over,
})

describe('quality card', () => {
  it('shows each score against its baseline, green or red', () => {
    expect(clipText(clip())).toBe('0.642 (baseline 0.642, median of 5 runs)')
    expect(clipStatus(clip())).toEqual({ tone: 'ok', label: 'OK' })
    expect(clipStatus(clip({ status: 'fail', score: 0.6 }))).toEqual({ tone: 'bad', label: 'Below baseline' })
    expect(clipStatus(clip({ status: 'fail', failed: true, score: null }))).toEqual({ tone: 'bad', label: 'Failed' })
    expect(clipText(clip({ failed: true, score: null }))).toBe('Failed to run')
    expect(clipStatus(clip({ status: 'none', baseline: null })).label).toBe('No baseline')
    expect(clipText(clip({ baseline: null, runs: 1 }))).toBe('0.642 (no baseline, fixed decoding)')
  })

  it('names the metric in plain words', () => {
    expect(metricLabel(clip({ metric: 'der', kind: 'diarization' }))).toBe('Speaker error (lower is better)')
    expect(metricLabel(clip({ metric: null, kind: 'translation' }))).toBe('translation')
  })

  it('judges the release gate by pass and the 7-day rule', () => {
    expect(gateStatus(check()).label).toBe('Passed')
    expect(gateStatus(check({ fresh: false, age_days: 9 })).label).toBe('Too old')
    expect(gateStatus(check({ passed: false })).tone).toBe('bad')
    expect(gateStatus(check({ found: false })).label).toBe('No check yet')
    expect(gateStatus(check({ unreadable: true })).label).toBe('Unreadable')
  })

  it('says when and where it ran', () => {
    expect(qualitySummary(check())).toBe('Passed, 2 days ago')
    expect(qualitySummary(check({ found: false, age_days: null }))).toBe('No check yet')
    expect(ranText(check())).toBe('Last check 2026-11-22 10:15 on DESKTOP-V100 (Tesla V100), 2 days ago.')
    expect(ranText(check({ age_days: 0, machine: null }))).toBe('Last check 2026-11-22 10:15, today.')
    expect(marginText(check())).toBe('Red means worse than the baseline by more than 0.02.')
  })
})
