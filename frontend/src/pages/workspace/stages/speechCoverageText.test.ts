import { describe, expect, it } from 'vitest'

import type { SpeechCoverageReport } from '../../../types/speechCoverage'
import { coverageBlocker, coverageSummary, coverageTotals, gapRange, rawStatusText } from './speechCoverageText'

const report = (over: Partial<SpeechCoverageReport> = {}): SpeechCoverageReport => ({
  audio_seconds: 600, speech_seconds: 200, covered_seconds: 150, covered_percent: 75,
  vad_threshold: 0.3, min_gap_seconds: 2, raw_available: true, gaps_total: 2, gaps: [],
  failed_reason: null, detail: null, ...over,
})

describe('speech coverage text', () => {
  it('says apart text lost afterwards from nothing heard', () => {
    expect(rawStatusText('lost_after')).toBe('Whisper produced text here but it was lost afterwards')
    expect(rawStatusText('none')).toBe('Whisper produced nothing here')
    expect(rawStatusText('unknown')).toBe('No raw transcript to compare with')
  })

  it('shows a gap range as clocks', () => {
    expect(gapRange(65, 3725)).toBe('1:05–1:02:05')
  })

  it('summarises and totals', () => {
    expect(coverageSummary(report())).toBe('75% of speech covered · 2 gaps')
    expect(coverageSummary(report({ gaps_total: 1 }))).toContain('1 gap')
    expect(coverageSummary(report({ covered_percent: null }))).toBe('no speech found')
    expect(coverageTotals(report())).toBe('2:30 of 3:20 of speech has a line (75%).')
    expect(coverageTotals(report({ speech_seconds: 0 }))).toBe('No speech was found in the audio.')
  })

  it('blocks without audio or while another job runs', () => {
    expect(coverageBlocker(false, false)).toBe('Still needed: audio on this drama.')
    expect(coverageBlocker(true, true)).toBe('Wait for the running job to finish.')
    expect(coverageBlocker(true, false)).toBeNull()
  })
})
